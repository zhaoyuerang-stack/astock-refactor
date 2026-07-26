"""Scheduled Factor Search & 9-Gate Evaluation pipeline step.

This script runs during weekly maintenance to automatically discover new factors,
evaluate them through 9-Gate audits, and write reports for manual review.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from core.analysis.nine_gates import NineGatesEvaluator, NineGatesReport
from core.engine import PricePanel, Signal
from factory.autoresearch.repositories import CandidateRepository, ExperimentLog, ReviewQueue
from governance.holdout import (
    assert_search_clean,
    boundary,
    candidate_identity,
    current_data_fingerprint,
    validate_on_holdout,
)
from governance.trial_ledger import (  # LOOP_ENGINEERING §5.1(record 下沉到 orchestrator)
    honest_n_trials,
    record_trials,
)
from services.actions.autoresearch_search import run_autoresearch_walk_forward
from strategies.small_cap import load_price_panels

_DEFAULT_TOP_N = 25
_DEFAULT_REBALANCE_DAYS = 20

# 每次运行的 append-only 摘要:研究枯竭信号的数据源
# (services.read.research_exhaustion 消费:连续 N 次无产出 → 收件箱推「外探还是调向」)。
# 此前零晋级只打印即退出,「连续几周无产出」这一枯竭事实系统自己看不见。
_RUNS_SUMMARY = ROOT / "reports" / "research" / "factor_search_runs.jsonl"


def _append_run_summary(**record) -> None:
    """落一条运行摘要。best-effort:失败只打印,绝不影响搜索/审计主流程退出码。"""
    import json
    from datetime import datetime

    record = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **record}
    try:
        _RUNS_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        with _RUNS_SUMMARY.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  ⚠️ 运行摘要写入失败(不影响主流程): {e}", file=sys.stderr)


def _candidate_exec_params(ast: dict) -> tuple[int, int]:
    """从候选 AST 的 execution 块取 (top_n, rebalance_days)(WS4)。

    islands 搜索把 portfolio_size / rebalance_freq 写进 ``ast["execution"]``
    (见 factory/autoresearch/islands.py:106-113)。此前审计/holdout 权重硬写
    25/20,把搜出的持仓数丢弃;现改为读候选自己搜出的值。候选从未变异出
    execution 块时退回 canonical StrategyConfig 默认 (25, 20)——这是"无搜索
    信息"的合法默认,不是"忽略已搜出的值"。
    """
    ex = (ast or {}).get("execution") or {}
    size = ex.get("portfolio_size")
    top_n = int(size) if size else _DEFAULT_TOP_N
    freq = ex.get("rebalance_freq")
    rebalance_days = _DEFAULT_REBALANCE_DAYS
    if isinstance(freq, str) and freq[:-1].isdigit() and freq[-1:] in ("D", "d"):
        rebalance_days = int(freq[:-1])
    elif isinstance(freq, (int, float)) and freq:
        rebalance_days = int(freq)
    return top_n, rebalance_days


def build_weights_for_candidate(ast, factor_df, close, *, veto_factor, veto_q=0.30, top_n_override=None):
    """构造调仓权重;top_n 来自审计层 size 选择(top_n_override)或候选搜出值(WS4)。

    rebalance_days 恒取候选搜出值(_candidate_exec_params)。top_n_override 由
    审计层 sweep_audit_size 决定的可交易性最优持仓数注入;为 None 时退回候选
    自己搜出的 portfolio_size(此前硬写 25,已修)。
    """
    from strategies.small_cap import build_rebalance_weights

    top_n, rebalance_days = _candidate_exec_params(ast)
    if top_n_override is not None:
        top_n = int(top_n_override)
    return build_rebalance_weights(
        factor_df,
        close,
        top_n=top_n,
        rebalance_days=rebalance_days,
        veto_factor=veto_factor,
        veto_q=veto_q,
    )


_AUDIT_SIZE_GRID = (10, 25, 50, 100)


def _pick_audit_size(sweep: dict) -> int:
    """净成本后夏普为主;近似平手(在最优净夏普的 5% 内)取高容量(WS4 item1)。

    关键:**不是**选最大 size。L0 已按 size 无关的 rank-IC 选定"哪个因子";此处只
    在给定因子上按可交易性(净收益 + 容量)挑持仓数。这样避免了"往 L0 适应度加容量
    项 → 退化成选最大 N → 抛弃集中的小盘 alpha"的 L2 陷阱(见 DECISIONS ADR-032)。
    """
    if not sweep:
        return _DEFAULT_TOP_N
    best = max(v["net_sharpe"] for v in sweep.values())
    band = best - 0.05 * abs(best)  # 净夏普在最优 5% 内视为平手,由容量打破
    near = {sz: v for sz, v in sweep.items() if v["net_sharpe"] >= band}
    return int(max(near, key=lambda sz: near[sz]["capacity_aum"]))


def sweep_audit_size(
    ast,
    factor_df,
    close,
    volume,
    amount,
    *,
    veto_factor,
    veto_q=0.30,
    start="2018-01-01",
    grid=_AUDIT_SIZE_GRID,
    trial_scope="autoresearch",
    ledger_path=None,
):
    """审计层扫 size 网格,按净成本后夏普 + 容量选持仓数(WS4 item1,ADR-032)。

    为什么在审计层而非 L0 适应度:L0 edge = 全截面 rank-IC,与 size 无关,只回答
    "哪个因子好";而成本与容量只有 Gate5/6(审计层)建模。往 L0 适应度加容量项会
    退化成"选最大 N"、抛弃集中的小盘 alpha(系统唯一有效源)——见 ADR-032。

    item3(诚实多重检验):best-of-k size 是搜索自由度,**函数内强制**把 len(grid)
    记入 trial 账本(耦合保证"扫了必记",不靠调用方自觉),再供 DSR 惩罚。

    在 <holdout boundary 的面板上扫(调用方已截断),size 选择绝不偷看金库。
    返回 (chosen_size, sweep{size: {net_sharpe, net_annual, capacity_aum}})。
    """
    import pandas as pd

    from capacity.dollar_capacity import estimate_dollar_capacity
    from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal

    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    adv = amount.rolling(20, min_periods=5).mean()
    sweep: dict[int, dict] = {}
    for s in grid:
        w = build_weights_for_candidate(
            ast, factor_df, close, veto_factor=veto_factor, veto_q=veto_q, top_n_override=s,
        )
        if not w:  # 该 size 建不出仓(池不足)→ 不进可选集(但仍算一次尝试,见记账)
            continue
        res = BacktestEngine(
            prices=prices, config=BacktestConfig(start=start, cost=cost, leverage=1.0),
        ).run(Signal(weights=w))
        w_df = pd.DataFrame(w).T.fillna(0.0)
        cap = estimate_dollar_capacity(w_df, adv)
        sweep[int(s)] = {
            "net_sharpe": round(float(res.sharpe), 3),
            "net_annual": round(float(res.annual), 4),
            "capacity_aum": round(float(cap), 0),
        }
    # item3:扫描即多重检验。保守计入全部 len(grid)(含建不出仓的 size)= 搜索自由度,
    # 供 honest_n_trials → DSR 惩罚(R-EVIDENCE-001 ④)。
    record_trials(trial_scope, len(grid), context="audit size sweep", path=ledger_path)
    return _pick_audit_size(sweep), sweep


def main():
    print("=" * 80)
    print("  Scheduled AutoResearch & 9-Gate Audit Execution")
    print("=" * 80)

    # 1. Initialize repositories
    repository = CandidateRepository()
    review_queue = ReviewQueue()
    experiment_log = ExperimentLog()
    pending_before = {item["fingerprint"] for item in review_queue.pending()}

    # 2. Run Island Search to evolve factors
    print("\n[Step 1] Running Multi-Island Evolutionary Search...", flush=True)
    try:
        # True meta-WF:演化只见训练截止日,冠军在 2024 OOS 一次评分;
        # 2025+ holdout 金库仍完全保留给晋级前唯一一次消费。
        HOLDOUT = boundary()
        _c, _v, _a = load_price_panels("2018-01-01")
        meta_oos_end = _c.index[_c.index < HOLDOUT][-1]
        meta_cutoff = meta_oos_end - pd.DateOffset(years=1)
        assert_search_clean(meta_oos_end, label="周度元级WF OOS")
        print(
            f"  [meta-WF] train≤{meta_cutoff.date()} / "
            f"OOS=({meta_cutoff.date()},{meta_oos_end.date()}] / "
            f"holdout≥{HOLDOUT.date()} 保留",
            flush=True,
        )
        # We run 5 islands, 3 generations, population of 6
        # Set use_llm=True so it uses AI if keys are present, otherwise falls back
        # islands=5(原3)让 _ISLAND_THEMES 第5个主题"股东行为与资金流"轮得到
        # (i % len(_ISLAND_THEMES) 轮询,<5 个岛永远摸不到第5个主题)
        search_res = run_autoresearch_walk_forward(
            cutoff=str(meta_cutoff.date()),
            oos_end=str(meta_oos_end.date()),
            # WS6:调度搜索默认跨 regime 生存(min |ICIR| 只聚合截断面板内可用段,
            # ADR-026/ADR-033)——堵"晴天因子"(单段行情堆出来的高 ICIR)。
            regime_aware=True,
            islands=5,
            generations=3,
            population=6,
            final_stage="l3",
            use_llm=True,
            start="2018-01-01",
            sample_dates=120,
            repository=repository,
            experiment_log=experiment_log,
            review_queue=review_queue,
            close=_c,
            volume=_v,
            amount=_a,
        )
        print(f"  Evolved factors complete. Evaluated: {search_res.evaluated}.")
        oos_promoted = {
            champion.fingerprint
            for champion in search_res.champions
            if champion.oos_decision == "promote"
        }
        # trial 记账已下沉到 run_autoresearch_island_search(§5.1 chokepoint),此处不再重复,
        # 避免双计。honest_n_trials("autoresearch") 仍由 9-Gate 在下方读取。
        
        # Copy newly evolved candidate experiments to the immutable ResearchLedger
        try:
            import time

            from research_ledger.ledger import LedgerEntry, ResearchLedger
            ledger = ResearchLedger()
            logged_count = 0
            for e in experiment_log.iter_all():
                exp_id = f"EXP_AUTO_{e.fingerprint[:12]}"
                if ledger.get_by_id(exp_id) is None:
                    entry = LedgerEntry(
                        experiment_id=exp_id,
                        parent_experiment_id="VINTAGE_AUTO",
                        hypothesis_text=e.reason or f"AutoResearch evolved candidate: {e.fingerprint[:8]}",
                        llm_prompt_hash=None,
                        factor_ast_hash=e.fingerprint,
                        code_commit_hash="git_head",
                        data_snapshot_hash="data_lake",
                        universe_version="CSI_1000",
                        cost_model_version="v1.25",
                        random_seed=42,
                        tried_parameters={},
                        result_metrics=e.metrics,
                        rejection_reason=e.reason if e.decision.value != "promote" else None,
                        reviewer="AI AutoResearch",
                        run_at=time.strftime("%Y-%m-%d %H:%M:%S")
                    )
                    ledger.log_experiment(entry)
                    logged_count += 1
            if logged_count > 0:
                print(f"  Logged {logged_count} search outcomes to the immutable ResearchLedger.")
        except Exception as err:
            print(f"  ⚠️ Failed to sync experiments to ResearchLedger: {err}")
    except Exception as e:
        print(f"❌ Island Search failed to run: {e}", file=sys.stderr)
        _append_run_summary(status="search_failed", error=f"{type(e).__name__}: {str(e)[:200]}")
        sys.exit(1)

    # 3. Fetch promoted candidates that passed L3
    # Check the review queue for any candidates waiting for human review
    promoted = [
        item for item in review_queue.pending()
        if item["fingerprint"] not in pending_before
        and item["fingerprint"] in oos_promoted
    ]
    if not promoted:
        print("\n[Step 2] No candidates passed L3 validation in this run. No reports to audit.")
        _append_run_summary(
            status="no_candidates",
            evaluated=int(search_res.evaluated),
            n_promoted_l3=0,
            n_holdout_ok=0,
        )
        sys.exit(0)

    print(f"\n[Step 2] Found {len(promoted)} candidates promoted to review. Running 9-Gate audits...", flush=True)

    # Load data for evaluation. 保留全样本面板(close_full)仅供晋级前唯一一次 holdout 校验;
    # 9-Gate 是选择层,只看 < boundary 的切片(§5.2:补上"评估半边"的洞,loop 不得触碰金库)。
    close_full, volume_full, amount_full = load_price_panels("2018-01-01")
    close = close_full[close_full.index < HOLDOUT]
    volume = volume_full[volume_full.index < HOLDOUT]
    amount = amount_full[amount_full.index < HOLDOUT]
    assert_search_clean(close.index, label="9-Gate 评估")
    prices = PricePanel(close=close, volume=volume, amount=amount)

    # §5.3 缝④:预算一次在册 ACTIVE 组合(<boundary)收益,供逐候选边际真 alpha 判冗余。
    try:
        from portfolio.strategy_runners import run_active
        book_search = {k: v[v.index < HOLDOUT] for k, v in run_active(start="2018-01-01").items()}
    except Exception as _bk_err:
        book_search = None
        print(f"  [marginal] 在册组合加载失败,跳过边际判定: {_bk_err}", flush=True)

    n_holdout_ok = 0
    marginal_verdicts: list[str] = []
    for item in promoted:
        from factory.autoresearch.models import Candidate, CandidateStatus
        cand = Candidate(
            fingerprint=item["fingerprint"],
            ast=item["candidate"],
            status=CandidateStatus(item["status"]),
        )
        fp = cand.fingerprint
        print(f"\nEvaluating candidate {fp[:8]}...", flush=True)

        try:
            # Build factor and weights from AST
            from factory.autoresearch.pipeline import ast_to_hypothesis
            from workflow.from_factory import hypothesis_to_spec
            
            hyp = ast_to_hypothesis(cand)
            spec = hypothesis_to_spec(hyp)
            
            # Resolve weights (top_n/rebalance come from the candidate via build_weights_for_candidate)
            from factors.veto import salience_covariance_veto
            
            # WS4: weights use the candidate's own searched portfolio_size/rebalance
            # (ast["execution"]), not a hardcoded 25/20 — otherwise the searched size is discarded.
            veto = salience_covariance_veto(close).shift(1)
            # Wrap factor_builder to match NineGatesEvaluator expectations (takes PricePanel)
            def wrapped_builder(prices_obj, spec=spec):
                return spec.factor_builder(
                    prices_obj.close, 
                    prices_obj.volume, 
                    prices_obj.amount, 
                    prices_obj.close.index
                )

            factor_df = spec.factor_builder(close, volume, amount, close.index)
            # WS4 item1 (ADR-032): choose portfolio_size at the AUDIT layer by net-of-cost
            # sharpe + capacity (Gate5/6 model cost/capacity; L0 rank-IC is size-blind). The
            # sweep records its k-width to the trial ledger (honest multiple testing) internally.
            chosen_size, size_sweep = sweep_audit_size(
                cand.ast, factor_df, close, volume, amount, veto_factor=veto, veto_q=0.30,
            )
            print(
                f"  [size-sweep] top_n={chosen_size} ← "
                + ", ".join(
                    f"{k}:sh{v['net_sharpe']}/cap{v['capacity_aum']:.0f}"
                    for k, v in sorted(size_sweep.items())
                ),
                flush=True,
            )
            # item2 provenance:记录"为什么这个持仓数"(每档净收益/容量 + 选中值)
            review_queue.record_fields(
                fp,
                audit_top_n=int(chosen_size),
                audit_size_sweep={str(k): v for k, v in size_sweep.items()},
            )
            scheduled = build_weights_for_candidate(
                cand.ast, factor_df, close, veto_factor=veto, veto_q=0.30, top_n_override=chosen_size,
            )

            signal = Signal(
                weights=scheduled,
                family=f"autoresearch_{fp[:8]}",
                version="v1.0"
            )

            # Economic thesis from candidate
            thesis = cand.ast.get("thesis", {
                "mechanism": "AutoResearch evolved mathematical combination.",
                "citation": "islands mutation"
            })

            # Run 9-Gate evaluator
            evaluator = NineGatesEvaluator(
                prices=prices,
                factor_df=factor_df,
                factor_builder=wrapped_builder,
                thesis=thesis,
                n_trials=honest_n_trials("autoresearch"),  # 读账本累计,替代手填 10(§5.1)
                forward_days=20
            )

            reports = evaluator.evaluate_all(signal, start="2018-01-01")
            passed_all = all(r.passed for r in reports)

            # §5.2 晋级前唯一一次 holdout 校验:可部署形态(因子+veto 权重)的全样本收益,
            # validate_on_holdout 只读 ≥boundary 段。best-effort——失败不吞 9-Gate 报告;
            # 崩(夏普<0.6)→holdout_failed,仅作 review 证据,本脚本不自动晋级(文档 §6)。
            from core.engine import BacktestConfig, BacktestEngine, CostModel
            ret_full = None  # §5.3 缝④:供 holdout 与 marginal 复用;holdout try 内赋值
            try:
                veto_full = salience_covariance_veto(close_full).shift(1)
                factor_full = spec.factor_builder(close_full, volume_full, amount_full, close_full.index)
                weights_full = build_weights_for_candidate(
                    cand.ast, factor_full, close_full, veto_factor=veto_full, veto_q=0.30,
                    top_n_override=chosen_size,
                )
                ho_cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
                ret_full = BacktestEngine(
                    prices=PricePanel(close=close_full, volume=volume_full, amount=amount_full),
                    config=BacktestConfig(start="2018-01-01", cost=ho_cost, leverage=1.0),
                ).run(Signal(weights=weights_full)).returns
                data_fp = current_data_fingerprint()
                holdout_id = candidate_identity(f"autoresearch_{fp[:8]}", fp, data_fp)
                ho = validate_on_holdout(
                    holdout_id,
                    ret_full,
                    spec_hash=fp,
                    data_fingerprint=data_fp,
                )
                ho_sharpe_ok = isinstance(ho.get("sharpe"), (int, float)) and ho["sharpe"] >= 0.6
                # §5.2 缝②:金库 DSR 算得动则必须显著(跨候选多重检验);短段算不动退回夏普门兜底
                ho_ok = ho_sharpe_ok and ho.get("holdout_dsr_sig") is not False
                print(f"  [holdout] 段夏普 {ho.get('sharpe', 0):.2f} (n={ho.get('n')}, "
                      f"金库试过{ho.get('holdout_trials')}候选/DSR_p={ho.get('holdout_dsr_p')}, "
                      f"偷看{ho.get('peek_count')}次) → {'通过' if ho_ok else 'holdout_failed'}", flush=True)
                review_queue.record_fields(
                    fp,
                    holdout_id=holdout_id,
                    holdout_spec_hash=fp,
                    holdout_data_fingerprint=data_fp,
                    holdout_sharpe=round(float(ho.get("sharpe", 0) or 0), 3),
                    holdout_ok=bool(ho_ok),
                    holdout_peek=int(ho.get("peek_count", 1)),
                    holdout_dsr_p=ho.get("holdout_dsr_p"),
                    holdout_trials=ho.get("holdout_trials"),
                )
            except Exception as ho_err:
                ho, ho_ok = {"note": f"{type(ho_err).__name__}: {str(ho_err)[:80]}"}, False
                print(f"  [holdout] 校验异常(跳过,不影响 9-Gate 报告): {ho_err}", flush=True)

            # §5.3 缝④:边际真 alpha — 候选去掉对在册组合暴露后是否还赚钱(高相关+残差弱=冗余同质变体)
            mg = {"marginal_verdict": "未计算"}
            if ret_full is not None and book_search is not None:
                try:
                    from governance.marginal import marginal_alpha
                    mg = marginal_alpha(ret_full[ret_full.index < HOLDOUT], book_search)
                    print(f"  [marginal] {mg.get('marginal_verdict')} "
                          f"(corr={mg.get('corr_to_book')}, 残差夏普={mg.get('residual_sharpe')})", flush=True)
                    review_queue.record_fields(
                        fp,
                        marginal_verdict=mg.get("marginal_verdict"),
                        marginal_resid_sharpe=mg.get("residual_sharpe"),
                        marginal_corr=mg.get("corr_to_book"),
                    )
                except Exception as mg_err:
                    mg = {"marginal_verdict": f"未能计算: {type(mg_err).__name__}"}

            # Build markdown report
            report = NineGatesReport(
                factor_name=f"autoresearch_{fp[:8]}",
                run_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
                passed_all=passed_all,
                reports=reports
            )

            report_dir = ROOT / "reports" / "research"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"autoresearch_{fp[:8]}_9_gates_report.md"
            
            ho_md = (
                f"\n\n## Holdout 金库校验(LOOP_ENGINEERING §5.2)\n"
                f"- 金库边界 **{HOLDOUT.date()}**(≥此为 holdout,搜索/9-Gate 从未使用)\n"
            )
            if ho.get("note"):
                ho_md += f"- {ho['note']}\n- 判定: ⚠️ 未能校验,需人工复核\n"
            else:
                _dsr_tag = (' ✅显著' if ho.get('holdout_dsr_sig') else
                            (' ❌不显著' if ho.get('holdout_dsr_sig') is False else ' (短段未算)'))
                ho_md += (
                    f"- holdout 段: 年化 {ho.get('annual', 0):+.1%} / 夏普 {ho.get('sharpe', 0):.2f} / "
                    f"回撤 {ho.get('maxdd', 0):+.1%}(n={ho.get('n')})\n"
                    f"- 多重检验(§5.2 缝②): 金库已试 {ho.get('holdout_trials')} 候选, DSR_p={ho.get('holdout_dsr_p')}{_dsr_tag}, "
                    f"偷看 {ho.get('peek_count')} 次\n"
                    f"- 判定: {'✅ 通过(夏普≥0.6 且金库多重检验过关)' if ho_ok else '❌ **holdout_failed** — 不予晋级'}\n"
                )
            ho_md += (
                f"\n## 边际真 alpha(§5.3 缝④)\n"
                f"- 判定: {mg.get('marginal_verdict')}(对在册组合 corr={mg.get('corr_to_book')}, "
                f"去暴露后残差夏普={mg.get('residual_sharpe')})\n"
            )
            report_path.write_text(report.to_markdown() + ho_md, encoding="utf-8")
            print(f"  ✅ 9-Gate audit report saved to:\n  {report_path}")
            # 枯竭信号口径:候选过 L3 且 holdout 校验 OK 才算「本次运行有产出」
            n_holdout_ok += 1 if ho_ok else 0
            marginal_verdicts.append(str(mg.get("marginal_verdict")))

        except Exception as e:
            print(f"  ❌ Failed to evaluate candidate {fp[:8]}: {e}", file=sys.stderr)

    _append_run_summary(
        status="audited",
        evaluated=int(search_res.evaluated),
        n_promoted_l3=len(promoted),
        n_holdout_ok=n_holdout_ok,
        marginal_verdicts=marginal_verdicts,
    )
    print("\n🎉 Scheduled factor search and evaluation completed!")


if __name__ == "__main__":
    main()
