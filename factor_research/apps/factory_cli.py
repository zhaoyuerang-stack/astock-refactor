"""factory_cli — Strategy Factory 主操作命令。

支持：
  generate [--source mutate] [--count N]   产生 hypothesis 入池
  status                                    池子各状态分布
  inspect <hyp_id_prefix>                   单 hypothesis 详情
  queue [--limit N]                         drafted → queued
  run-l0 [--limit N] [--sample N]           跑 L0 IC scan
  survivors [--protocol l0]                 列出通过某关的 hypothesis
"""
import argparse
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def cmd_generate(args):
    from factory.lines.line1_generation import generate_all_mutations, mutate_factor
    from factory.pool import HypothesisPool

    pool = HypothesisPool()

    if args.source == "mutate" and args.factor:
        hyps = list(mutate_factor(args.factor))
    elif args.source == "mutate":
        hyps = generate_all_mutations()
    else:
        print(f"未支持的 source: {args.source}", file=sys.stderr)
        return 1

    if args.count and len(hyps) > args.count:
        hyps = hyps[: args.count]

    added, dup = pool.add_many(iter(hyps))
    # 持久化诚实搜索数(§5.1):本次新增(去重后)候选计入 autoresearch 血缘累计 → DSR 惩罚来源。
    if added > 0:
        from governance.trial_ledger import record_trials
        cum = record_trials("autoresearch", added, context=f"factory_cli mutate ({args.source})")
        print(f"  [trial_ledger] autoresearch 累计搜索 = {cum}")
    print(f"generated={len(hyps)}  added={added}  dup={dup}  pool_total={len(pool)}")
    return 0


def cmd_mi_audit(args):
    """L-1 MI 关: 信息冗余簇识别."""
    from metasearch.factor_mi_audit import (
        audit_hypothesis_pool,
        cluster_by_redundancy,
        mi_matrix,
    )

    print(f"L−1 MI audit (max_hyps={args.max_hyps}, threshold={args.threshold} bits)")
    print("Loading data lake...")
    t0 = time.time()
    close, volume, amount = _load_data_panel("2018-01-01")
    print(f"  {close.shape}, {time.time()-t0:.1f}s")

    ics = audit_hypothesis_pool(close, volume, amount, max_hyps=args.max_hyps)
    if not ics:
        print("⚠ no candidates with valid IC")
        return 0

    print("Computing MI matrix...")
    t1 = time.time()
    mat = mi_matrix(ics)
    print(f"  {mat.shape}, {time.time()-t1:.1f}s")

    # Distribution summary
    off = [mat.iloc[i,j] for i in range(len(mat)) for j in range(i+1, len(mat))]
    import numpy as np
    off = np.array(off)
    print("\n  MI distribution (off-diag):")
    print(f"    min/med/max: {off.min():.2f} / {np.median(off):.2f} / {off.max():.2f}")
    print(f"    redundant pairs (>{args.threshold}): {(off > args.threshold).sum()}")

    # Clusters
    clusters = cluster_by_redundancy(mat, threshold=args.threshold)
    print(f"\n  Info clusters: {len(clusters)} (from {len(ics)} candidates)")
    for i, c in enumerate(clusters):
        marker = "🔴" if len(c) > 1 else "🟢"
        if len(c) > 1:
            print(f"  {marker} Cluster {i+1} (n={len(c)}, redundant):")
            for n in c:
                print(f"      {n}")
        else:
            print(f"  {marker} Independent: {c[0]}")

    saving = 1 - len(clusters) / len(ics)
    print(f"\n💡 L-1 算力节省: {saving:.0%} ({len(ics)} → {len(clusters)} 独立)")
    return 0


def cmd_status(args):
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    counts = pool.count_by_status()
    print(f"Hypothesis Pool ({len(pool)} total):")
    for status, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status:14s} {n}")

    decisions = log.count_by_decision()
    if decisions:
        print()
        print("Experiment Log:")
        for d, n in sorted(decisions.items(), key=lambda x: -x[1]):
            print(f"  {d:10s} {n}")
    return 0


def cmd_inspect(args):
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    matches = [h for h in pool.all() if h.id.startswith(args.id_prefix)]
    if not matches:
        print(f"no hypothesis matches id prefix '{args.id_prefix}'", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print("multiple matches; refine prefix:", file=sys.stderr)
        for h in matches[:10]:
            print(f"  {h.id}  {h.name}")
        return 1
    h = matches[0]
    print(f"id:       {h.id}")
    print(f"name:     {h.name}")
    print(f"factor:   {h.factor_fn_name}  {h.factor_params}")
    print(f"status:   {h.status.value}")
    print(f"source:   {h.source}  parent={h.parent_hypothesis_id}")
    if h.thesis:
        print(f"thesis:   {h.thesis.mechanism}")
        if h.thesis.citation:
            print(f"          [{h.thesis.citation}]")
    print()
    exps = log.list_by_hypothesis(h.id)
    if exps:
        print(f"Experiments ({len(exps)}):")
        for e in exps:
            ic = e.result.details.get("ic_ir") if e.result.details else None
            ic_str = f"ICIR={ic:+.3f}" if ic is not None else ""
            err = f" ERR: {e.result.error[:60]}" if e.result.error else ""
            print(f"  [{e.protocol.value}] {e.decision.value}  {ic_str}  ({e.cost_spent_seconds:.1f}s){err}")
    return 0


def cmd_queue(args):
    from factory.ontology import HypothesisStatus
    from factory.pool import HypothesisPool

    pool = HypothesisPool()
    drafts = pool.list_by_status(HypothesisStatus.DRAFTED)
    if args.limit:
        drafts = drafts[: args.limit]
    for h in drafts:
        pool.update_status(h.id, HypothesisStatus.QUEUED)
    print(f"queued {len(drafts)} hypothesis(es)")
    return 0


def _load_data_panel(start: str):
    from lake.load_lake import load_prices, load_raw_close
    from lake.units import implied_amount

    px = load_prices(start=start, fields=("close", "volume"))
    raw = load_raw_close(start=start)
    close, volume = px["close"], px["volume"]
    # canonical lake volume unit = share; amount CNY = shares × raw CNY/share
    amount = implied_amount(volume, raw)
    return close, volume, amount


def cmd_run_l0(args):
    from factory.lines.line2_validation import precompute_forward_returns, run_l0
    from factory.ontology import Decision, HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    queued = pool.list_by_status(HypothesisStatus.QUEUED)
    if args.limit:
        queued = queued[: args.limit]
    if not queued:
        print("no queued hypotheses; run 'queue' first")
        return 0

    # 知识图谱 intake gate:已证伪候选(SKIP)直接弃,不花数据加载/IC 算力
    from knowledge.graph import load_graph
    kg = load_graph()
    runnable = []
    for h in queued:
        skip, reason = kg.should_skip(h)
        if skip:
            print(f"  ⏭ KG SKIP {h.name}: {reason}")
            pool.update_status(h.id, HypothesisStatus.DISCARDED)
        else:
            runnable.append(h)
    queued = runnable
    if not queued:
        print("all queued hypotheses gated by knowledge graph; nothing to run")
        return 0

    print(f"loading data lake (start={args.start})...")
    t0 = time.time()
    close, volume, amount = _load_data_panel(args.start)
    forward_ret = precompute_forward_returns(close, horizon=args.horizon)
    vintage_id = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    print(f"  loaded {close.shape}, vintage={vintage_id}, {time.time()-t0:.1f}s")

    print(f"running L0 IC scan on {len(queued)} hypotheses...")
    t1 = time.time()
    n_pass = n_fail = n_err = 0
    for h in queued:
        exp = run_l0(
            h, close, volume, amount, forward_ret, vintage_id,
            sample_dates=args.sample if args.sample else None,
        )
        log.append(exp)
        if exp.decision == Decision.PROMOTE:
            pool.update_status(h.id, HypothesisStatus.L0_PASSED)
            n_pass += 1
        else:
            if exp.result.error:
                n_err += 1
            pool.update_status(h.id, HypothesisStatus.DISCARDED)
            n_fail += 1
            # L0 弱 IC = regime/区间依赖 → DEPRIORITIZE + 保质期(非永久 SKIP)
            kg.record_from_validation(h, passed=False,
                                      metrics=dict(exp.result.metrics or {}),
                                      stage="L0", action="DEPRIORITIZE")

    dt = time.time() - t1
    print(f"L0 done: {n_pass} PASSED, {n_fail} DISCARDED ({n_err} errored), "
          f"{dt:.1f}s total ({dt/len(queued):.2f}s/hyp)")
    print(f"  [knowledge] {kg.summary()}")
    return 0


def cmd_run_l3(args):
    from factory.lines.line2_validation import run_l3
    from factory.ontology import Decision, HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    l2_passed = pool.list_by_status(HypothesisStatus.L2_PASSED)
    if args.limit:
        l2_passed = l2_passed[: args.limit]
    if not l2_passed:
        print("no L2_PASSED hypotheses; run 'run-l2' first")
        return 0

    print(f"loading data lake (start={args.start})...")
    t0 = time.time()
    close, volume, amount = _load_data_panel(args.start)
    vintage_id = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    print(f"  loaded {close.shape}, vintage={vintage_id}, {time.time()-t0:.1f}s")

    print(f"running L3 walk-forward on {len(l2_passed)} hypotheses...")
    t1 = time.time()
    n_pass = n_fail = n_err = 0
    for h in l2_passed:
        l0_exps = [e for e in log.list_by_hypothesis(h.id)
                   if e.protocol.value == "l0_ic_scan" and e.decision.value == "promote"]
        if not l0_exps:
            continue
        direction_str = l0_exps[-1].result.details.get("direction", "long")
        direction = 1 if direction_str == "long" else -1

        exp = run_l3(h, close, volume, amount, direction, vintage_id, start=args.start)
        log.append(exp)
        if exp.decision == Decision.PROMOTE:
            if h.status == HypothesisStatus.L2_PASSED:
                pool.update_status(h.id, HypothesisStatus.L3_PASSED)
            n_pass += 1
        else:
            if exp.result.error:
                n_err += 1
            n_fail += 1

    dt = time.time() - t1
    print(f"L3 done: {n_pass} PASSED, {n_fail} SHELVE ({n_err} errored), "
          f"{dt:.1f}s ({dt/len(l2_passed):.1f}s/hyp)")
    return 0


def cmd_wf_summary(args):
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    me = [e for e in log.iter_all() if e.protocol.value == "l3_walk_forward"]
    by_hyp = {}
    for e in me:
        by_hyp[e.hypothesis_id] = e

    rows = []
    for hyp_id, e in by_hyp.items():
        h = pool.get(hyp_id)
        if h is None or not e.result.details:
            continue
        rows.append((h, e))

    if not rows:
        print("no L3 results yet; run 'run-l3' first")
        return 0

    rows.sort(key=lambda x: -x[1].result.metrics.get("avg_sharpe", -9))

    # Collect all years
    all_years = set()
    for _, e in rows:
        all_years.update(e.result.details.get("per_year", {}).keys())
    years = sorted(all_years)

    print(f"L3 walk-forward yearly Sharpe ({len(rows)} candidates):")
    header = f"  {'name':38s} {'avg_sh':>6s} {'pos%':>5s} "
    header += " ".join(f"{y:>5}" for y in years)
    print(header)
    print("  " + "-" * (45 + 6 * len(years)))
    for h, e in rows[: args.limit or 30]:
        m = e.result.metrics
        avg_sh = m.get("avg_sharpe", 0)
        pos_r = m.get("positive_year_ratio", 0)
        per_year = e.result.details.get("per_year", {})
        cells = ""
        for y in years:
            yd = per_year.get(y) or per_year.get(int(y)) or per_year.get(str(y))
            if yd and yd.get("n", 0) >= 50:
                cells += f"{yd.get('sharpe', 0):>+5.1f} "
            else:
                cells += "    . "
        decision = "PASS" if e.decision.value == "promote" else "SHLV"
        print(f"  {h.name[:38]:38s} {avg_sh:>+6.2f} {pos_r:>4.0%} {cells} [{decision}]")
    return 0


def cmd_run_l2(args):
    from factory.lines.line2_validation import run_l2
    from factory.ontology import Decision, HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    l1_passed = pool.list_by_status(HypothesisStatus.L1_PASSED)
    if args.limit:
        l1_passed = l1_passed[: args.limit]
    if not l1_passed:
        print("no L1_PASSED hypotheses; run 'run-l1' first")
        return 0

    print(f"loading data lake (start={args.start})...")
    t0 = time.time()
    close, volume, amount = _load_data_panel(args.start)
    vintage_id = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    print(f"  loaded {close.shape}, vintage={vintage_id}, {time.time()-t0:.1f}s")

    print(f"running L2 multi-regime on {len(l1_passed)} hypotheses...")
    t1 = time.time()
    n_pass = n_fail = n_err = 0
    for h in l1_passed:
        l0_exps = [e for e in log.list_by_hypothesis(h.id)
                   if e.protocol.value == "l0_ic_scan" and e.decision.value == "promote"]
        if not l0_exps:
            continue
        direction_str = l0_exps[-1].result.details.get("direction", "long")
        direction = 1 if direction_str == "long" else -1

        exp = run_l2(h, close, volume, amount, direction, vintage_id, start=args.start)
        log.append(exp)
        if exp.decision == Decision.PROMOTE:
            # 推进 status 仅当 L1_PASSED → L2_PASSED 合法
            if h.status == HypothesisStatus.L1_PASSED:
                pool.update_status(h.id, HypothesisStatus.L2_PASSED)
            n_pass += 1
        else:
            if exp.result.error:
                n_err += 1
            n_fail += 1

    dt = time.time() - t1
    print(f"L2 done: {n_pass} PASSED, {n_fail} SHELVE ({n_err} errored), "
          f"{dt:.1f}s ({dt/len(l1_passed):.1f}s/hyp)")
    return 0


def cmd_regimes(args):
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    me = [e for e in log.iter_all() if e.protocol.value == "l2_multi_regime"]
    by_hyp = {}
    for e in me:
        by_hyp[e.hypothesis_id] = e   # latest wins

    rows = []
    for hyp_id, e in by_hyp.items():
        if args.id and not hyp_id.startswith(args.id):
            continue
        h = pool.get(hyp_id)
        if h is None or not e.result.details:
            continue
        rows.append((h, e))

    if not rows:
        print("no L2 results yet; run 'run-l2' first")
        return 0

    # Sort by global sharpe desc
    rows.sort(key=lambda x: -x[1].result.metrics.get("global_sharpe", -9))

    print(f"L2 multi-regime breakdown ({len(rows)}):")
    print(f"  {'name':40s} {'glob_sh':>7s}  "
          f"{'bull_a':>7s} {'bear_a':>7s} {'chop_a':>7s} {'cris_a':>7s}  regime_dep")
    print("  " + "-" * 92)
    for h, e in rows:
        m = e.result.metrics
        d = e.result.details
        per = d.get("per_regime", {})
        bull_a = per.get("bull", {}).get("annual", 0)
        bear_a = per.get("bear", {}).get("annual", 0)
        chop_a = per.get("chop", {}).get("annual", 0)
        cris_a = per.get("crisis", {}).get("annual", 0)
        dep = d.get("regime_dependent_on") or ""
        name = h.name[:40]
        print(f"  {name:40s} {m.get('global_sharpe', 0):7.2f}  "
              f"{bull_a:+7.1%} {bear_a:+7.1%} {chop_a:+7.1%} {cris_a:+7.1%}  {dep}")
    return 0


def cmd_run_marginal(args):
    from factory.lines.line3_marginal import (
        GRADE_PRIORITY,
        evaluate_candidate,
    )
    from factory.ontology import HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog
    from portfolio.strategy_runners import run_all_live

    pool = HypothesisPool()
    log = ExperimentLog()
    # marginal eval 接受任何已通过 L1 的候选（L1/L2/L3 都行）
    eligible_statuses = (
        HypothesisStatus.L1_PASSED,
        HypothesisStatus.L2_PASSED,
        HypothesisStatus.L3_PASSED,
    )
    candidates = []
    for s in eligible_statuses:
        candidates.extend(pool.list_by_status(s))
    if args.limit:
        candidates = candidates[: args.limit]
    if not candidates:
        print("no L1+_PASSED hypotheses; run 'run-l1' first")
        return 0

    print(f"loading data lake (start={args.start})...")
    t0 = time.time()
    close, volume, amount = _load_data_panel(args.start)
    vintage_id = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    print(f"  loaded {close.shape}, vintage={vintage_id}, {time.time()-t0:.1f}s")

    print("running all LIVE strategies for baseline...")
    t1 = time.time()
    live_returns = run_all_live(start=args.start)
    print(f"  {time.time()-t1:.1f}s  {len(live_returns)} LIVE strategies")

    print(f"running Line 3 marginal eval (regime-aware + DEFENSIVE) "
          f"on {len(candidates)} candidates...")
    t2 = time.time()
    grade_counts = {g: 0 for g in GRADE_PRIORITY}
    grade_counts["error"] = 0
    rows = []
    for h in candidates:
        l0_exps = [e for e in log.list_by_hypothesis(h.id)
                   if e.protocol.value == "l0_ic_scan"
                   and e.decision.value == "promote"]
        if not l0_exps:
            continue
        direction_str = l0_exps[-1].result.details.get("direction", "long")
        direction = 1 if direction_str == "long" else -1

        exp, report = evaluate_candidate(
            h, direction, live_returns, close, volume, amount, vintage_id,
            start=args.start,
        )
        log.append(exp)
        if report is None:
            grade_counts["error"] += 1
            continue
        grade_counts[report.grade] = grade_counts.get(report.grade, 0) + 1
        rows.append((report, h))

    dt = time.time() - t2
    print(f"marginal eval done: {dt:.1f}s ({dt/max(len(candidates),1):.1f}s/hyp)")
    print(f"  grade distribution: {grade_counts}")

    # Top candidates: grade priority first, then regime_score
    if rows:
        rows.sort(key=lambda x: (
            GRADE_PRIORITY.get(x[0].grade, 9),
            -x[0].regime_weighted_score,
        ))
        print()
        print("Top candidates by grade + regime_score:")
        for report, h in rows[:12]:
            tag = (f"Δsh={report.delta_sharpe:+.3f} "
                   f"reg_sc={report.regime_weighted_score:+.3f} "
                   f"bear_imp={report.bear_improvement:+.1%} "
                   f"corr={report.avg_corr_to_live:.2f}")
            print(f"  [{report.grade:7s}] {tag}  {h.name}")
        # Highlight any LIVE_D defensive findings
        defensives = [r for r, h in rows if r.grade == "LIVE_D"]
        if defensives:
            print()
            print(f"DEFENSIVE ASSETS ({len(defensives)}):")
            for report in defensives:
                print(f"  ⭐ {report.candidate_name}")
                print(f"     bear_imp={report.bear_improvement:+.1%} "
                      f"bear_ann={report.bear_annual:+.1%} corr={report.avg_corr_to_live:.2f}")
                print(f"     {report.recommendation[:100]}")
    return 0


def cmd_graded(args):
    from factory.lines.line3_marginal import GRADE_PRIORITY
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    me = [e for e in log.iter_all() if e.protocol.value == "marginal_eval"]
    if not me:
        print("no marginal evals yet; run 'run-marginal' first")
        return 0

    by_hyp = {}
    for e in me:
        by_hyp[e.hypothesis_id] = e   # latest wins

    rows = []
    for hyp_id, e in by_hyp.items():
        h = pool.get(hyp_id)
        if h is None or not e.result.details:
            continue
        grade = e.result.details.get("grade", "SHELVE")
        m = e.result.metrics
        rows.append((
            grade,
            float(m.get("regime_weighted_score", 0)),
            float(m.get("delta_sharpe", 0)),
            float(m.get("bear_improvement", 0)),
            float(m.get("avg_corr_to_live", 1.0)),
            h,
            e,
        ))

    rows.sort(key=lambda r: (
        GRADE_PRIORITY.get(r[0], 9),
        -r[1],
    ))

    print(f"Marginal-eval results ({len(rows)}):")
    print(f"  {'grade':8s} {'reg_sc':>7s} {'Δsharpe':>8s} {'bear_imp':>9s} {'corr':>6s}  name")
    for grade, reg_sc, dsh, bear_imp, corr, h, _ in rows[: args.limit or 30]:
        print(f"  {grade:8s} {reg_sc:+7.3f} {dsh:+8.3f} "
              f"{bear_imp:+9.1%} {corr:6.2f}  {h.name}  [{h.id[:8]}]")
    return 0


def cmd_survivors(args):
    from factory.ontology import HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    target_status = {
        "l0": HypothesisStatus.L0_PASSED,
        "l1": HypothesisStatus.L1_PASSED,
        "l2": HypothesisStatus.L2_PASSED,
        "l3": HypothesisStatus.L3_PASSED,
    }[args.protocol]
    target_proto = {
        "l0": "l0_ic_scan",
        "l1": "l1_quick_bt",
        "l2": "l2_multi_regime",
        "l3": "l3_walk_forward",
    }[args.protocol]
    sort_key = {
        "l0": "ic_ir",
        "l1": "sharpe",
        "l2": "sharpe",
        "l3": "sharpe",
    }[args.protocol]

    pool = HypothesisPool()
    log = ExperimentLog()
    survivors = pool.list_by_status(target_status)
    rows = []
    for h in survivors:
        exps = [x for x in log.list_by_hypothesis(h.id)
                if x.protocol.value == target_proto and x.decision.value == "promote"]
        if not exps:
            continue
        e = exps[-1]  # 最新的 promote
        metric = (
            e.result.details.get(sort_key) if e.result.details and sort_key in e.result.details
            else e.result.metrics.get(sort_key)
        ) or 0
        rows.append((abs(metric) if args.protocol == "l0" else metric, metric, h, e))
    rows.sort(key=lambda r: -r[0])
    print(f"{args.protocol.upper()} survivors ({len(rows)}):")
    for _, metric, h, e in rows[: args.limit or 20]:
        if args.protocol == "l0":
            tag = f"ICIR={metric:+.3f}"
        else:
            m = e.result.metrics
            tag = (f"ann={m.get('annual', 0):.1%} "
                   f"sharpe={m.get('sharpe', 0):.2f} "
                   f"maxdd={m.get('maxdd', 0):.1%}")
        print(f"  {tag}  {h.name}  [{h.id[:8]}]")
    return 0


def cmd_run_l1(args):
    from factory.lines.line2_validation import run_l1
    from factory.ontology import Decision, HypothesisStatus
    from factory.pool import HypothesisPool
    from factory.repositories import ExperimentLog

    pool = HypothesisPool()
    log = ExperimentLog()
    l0_passed = pool.list_by_status(HypothesisStatus.L0_PASSED)
    if args.limit:
        l0_passed = l0_passed[: args.limit]
    if not l0_passed:
        print("no L0_PASSED hypotheses; run 'run-l0' first")
        return 0

    print(f"loading data lake (start={args.start})...")
    t0 = time.time()
    close, volume, amount = _load_data_panel(args.start)
    vintage_id = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    print(f"  loaded {close.shape}, vintage={vintage_id}, {time.time()-t0:.1f}s")

    print(f"running L1 quick BT on {len(l0_passed)} hypotheses...")
    t1 = time.time()
    n_pass = n_fail = n_err = 0
    for h in l0_passed:
        # Lookup direction from L0 experiment
        l0_exps = [e for e in log.list_by_hypothesis(h.id)
                   if e.protocol.value == "l0_ic_scan"]
        if not l0_exps:
            continue
        direction_str = l0_exps[-1].result.details.get("direction", "long")
        direction = 1 if direction_str == "long" else -1

        exp = run_l1(h, close, volume, amount, direction, vintage_id, start=args.start)
        log.append(exp)
        if exp.decision == Decision.PROMOTE:
            pool.update_status(h.id, HypothesisStatus.L1_PASSED)
            n_pass += 1
        else:
            if exp.result.error:
                n_err += 1
            pool.update_status(h.id, HypothesisStatus.DISCARDED)
            n_fail += 1

    dt = time.time() - t1
    print(f"L1 done: {n_pass} PASSED, {n_fail} DISCARDED ({n_err} errored), "
          f"{dt:.1f}s ({dt/len(l0_passed):.1f}s/hyp)")
    return 0


def cmd_knowledge(args):
    """巡检知识图谱:summary + findings + 待重测(过期)。"""
    from knowledge.graph import load_graph
    kg = load_graph()
    print(kg.summary())
    findings = sorted(kg._findings.values(), key=lambda f: f.created, reverse=True)
    if args.limit:
        findings = findings[: args.limit]
    for f in findings:
        gates = " ".join(f"[{g.action}]" for g in f.gates) or "[no-gate]"
        flag = "⏰过期" if f.is_expired else ""
        print(f"  {f.created} {gates} {f.statement}  exp={f.expires} {flag}")
    expired = kg.check_expiry()
    if expired:
        print(f"\n待重测({len(expired)}):{[f.id for f in expired]}")
    return 0


def cmd_promote(args):
    """L3_PASSED 候选 → workflow phase1~4 验证+登记(唯一登记闸门)。"""
    from workflow.promote import promote_pool_l3
    reports = promote_pool_l3(version=args.version, run_marginal=args.marginal,
                              force=args.force)
    n_reg = sum(1 for r in reports if r and r.registered)
    print(f"\npromote done: {n_reg}/{len(reports)} 登记入册")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="factory_cli", description="Strategy Factory CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("generate", help="produce hypotheses into pool")
    p.add_argument("--source", default="mutate", choices=["mutate"])
    p.add_argument("--factor", help="specific factor fn name (optional)")
    p.add_argument("--count", type=int, help="cap output")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("status", help="pool + log summary")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("mi-audit",
                       help="L-1 关: 信息冗余簇识别 (跑 L0 之前过滤)")
    p.add_argument("--max-hyps", type=int, default=50)
    p.add_argument("--threshold", type=float, default=2.0,
                   help="MI 冗余阈值 (bits), 默认 2.0 = log2(8)*2/3")
    p.set_defaults(func=cmd_mi_audit)

    p = sub.add_parser("inspect", help="show single hypothesis")
    p.add_argument("id_prefix", help="hypothesis id prefix (≥4 chars)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("queue", help="DRAFTED → QUEUED")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("run-l0", help="run L0 IC scan on QUEUED")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--horizon", type=int, default=20)
    p.add_argument("--limit", type=int)
    p.add_argument("--sample", type=int, help="downsample dates for speed")
    p.set_defaults(func=cmd_run_l0)

    p = sub.add_parser("run-l1", help="run L1 quick backtest on L0_PASSED")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_run_l1)

    p = sub.add_parser("survivors", help="list passed hypotheses at chosen gate")
    p.add_argument("--protocol", default="l0", choices=["l0", "l1", "l2", "l3"])
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_survivors)

    p = sub.add_parser("run-l2",
                       help="L2 multi-regime split on L1_PASSED")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_run_l2)

    p = sub.add_parser("regimes",
                       help="show L2 per-regime breakdown for a hypothesis or all L1_PASSED")
    p.add_argument("--id", help="hypothesis id prefix; omit for all")
    p.set_defaults(func=cmd_regimes)

    p = sub.add_parser("run-l3",
                       help="L3 walk-forward (yearly OOS) on L2_PASSED")
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_run_l3)

    p = sub.add_parser("wf-summary",
                       help="show year-by-year sharpe for all L3 results")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_wf_summary)

    p = sub.add_parser("run-marginal",
                       help="Line 3 marginal contribution eval on L1_PASSED")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_run_marginal)

    p = sub.add_parser("graded",
                       help="show marginal-eval results sorted by grade & delta")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_graded)

    p = sub.add_parser("knowledge",
                       help="巡检知识图谱(findings / 待重测)")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_knowledge)

    p = sub.add_parser("promote",
                       help="L3_PASSED → workflow phase1~4 验证+登记(唯一登记闸门)")
    p.add_argument("--version", default="v1.0")
    p.add_argument("--marginal", action="store_true", help="登记后算边际贡献")
    p.add_argument("--force", action="store_true", help="phase1/2 不过也强制登记(标候选)")
    p.set_defaults(func=cmd_promote)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
