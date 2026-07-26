"""AutoResearch 闭环实弹验证:P1 新颖性 + P3 失败反思是否真的改变了搜索质量。

预注册判据(运行前写定,见 reports/research/autoresearch_closed_loop.json):
  C1 新一代冠军对上一代冠军(L0强/L1死族)的行为 novelty > 0.5
  C2 ≥1 个冠军 OOS ICIR ≥ 0.3 且 L1 存活(成本后年化 > 0)
  C3 若全死,死因分布相对基线(momentum-L1 死亡谷)发生迁移

Run:
    cd factor_research && python3 scripts/research/autoresearch_closed_loop.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CUTOFF = "2024-12-31"
OOS_STATS_START = "2025-01-01"
OOS_PANEL_START = "2024-06-02"  # OOS 面板预热(rolling60/MA 窗口)


def _prior_champion_asts(experiment_log, repository, icir_threshold=0.5):
    """上一代冠军族:L0 PROMOTE 且 |ICIR|>=阈值、随后死于 L1 的候选 AST。"""
    asts = []
    for result in experiment_log.iter_all():
        exps = result.metrics.get("experiments", [])
        l0 = next((e for e in exps if e.get("protocol") == "l0_ic_scan"), None)
        l1 = next((e for e in exps if e.get("protocol") == "l1_quick_bt"), None)
        if not l0 or not l1 or l1.get("decision") != "discard":
            continue
        icir = l0.get("details", {}).get("ic_ir") or l0.get("metrics", {}).get("ICIR")
        try:
            if abs(float(icir)) < icir_threshold:
                continue
        except (TypeError, ValueError):
            continue
        cand = repository.get(result.fingerprint)
        if cand is not None:
            asts.append(cand.ast)
    return asts


def main():
    from factory.autoresearch import ast_to_hypothesis
    from factory.autoresearch.novelty import (
        candidate_factor_panel,
        novelty_score,
        sample_behavior_dates,
    )
    from factory.autoresearch.reflection import build_failure_ledger, ledger_to_prompt
    from factory.autoresearch.repositories import CandidateRepository, ExperimentLog
    from factory.lines.line2_validation.l1_quick_bt import run_l1
    from services.actions.autoresearch import _load_validation_data
    from services.actions.autoresearch_search import run_autoresearch_walk_forward

    repository = CandidateRepository()
    experiment_log = ExperimentLog()

    # 0) 留痕:本次注入的失败台账(运行前快照)
    ledger_prompt = ledger_to_prompt(build_failure_ledger(experiment_log, repository))
    print("==== 注入的失败台账 ====")
    print(ledger_prompt or "(空)")

    prior_asts = _prior_champion_asts(experiment_log, repository)
    print(f"\n上一代冠军族(L0强/L1死): {len(prior_asts)} 个")

    # 1) 走样外搜索:训练只见 ≤cutoff,LLM 播种带失败台账
    close, volume, amount, _ = _load_validation_data("2018-01-01")
    resp = run_autoresearch_walk_forward(
        cutoff=CUTOFF,
        use_llm=True,
        repository=repository,
        experiment_log=experiment_log,
        close=close,
        volume=volume,
        amount=amount,
    )
    print(f"\n==== 搜索完成: evaluated={resp.evaluated} seeded_by={resp.seeded_by} ====")

    # 2) 行为 novelty(C1):对上一代冠军族,用 ≤cutoff 数据算
    train_close = close.loc[:CUTOFF]
    train_volume = volume.loc[:CUTOFF]
    dates = sample_behavior_dates(train_close.index, 60)
    references = []
    for ast in prior_asts:
        try:
            references.append(candidate_factor_panel(ast, train_close, train_volume, dates))
        except Exception:
            continue

    # 3) OOS L1(C2):2025 起真实成本快速回测(面板物理切片 + 预热)
    oos_close = close.loc[OOS_PANEL_START:]
    oos_volume = volume.reindex(index=oos_close.index)
    oos_amount = amount.reindex(index=oos_close.index)

    champions = []
    for c in resp.champions:
        cand = repository.get(c.fingerprint)
        nov = None
        if cand is not None and references:
            try:
                panel = candidate_factor_panel(cand.ast, train_close, train_volume, dates)
                nov = novelty_score(panel, references)
            except Exception:
                pass
        l1_metrics, l1_decision = {}, "skipped"
        if cand is not None and c.oos_decision == "promote":
            # F-2 铁律:进 L1 必须 l0_passed——冠军已在 OOS 过了 L0,这里如实推进状态
            from factory.autoresearch.pipeline import _hyp_with_status
            from factory.ontology import HypothesisStatus

            hyp = _hyp_with_status(ast_to_hypothesis(cand), HypothesisStatus.L0_PASSED)
            direction = 1 if (c.oos_ic_mean or 0) >= 0 else -1
            exp = run_l1(
                hyp, oos_close, oos_volume, oos_amount,
                direction=direction, vintage_id=resp.vintage_id, start=OOS_STATS_START,
            )
            l1_metrics, l1_decision = exp.result.metrics, exp.decision.value
        champions.append({
            "fingerprint": c.fingerprint,
            "expr": c.expr,
            "train_icir": c.train_icir,
            "oos_icir": c.oos_icir,
            "oos_decision": c.oos_decision,
            "novelty_vs_prior_gen": nov,
            "l1_oos": {"decision": l1_decision, **{k: l1_metrics.get(k) for k in ("annual", "sharpe", "maxdd", "n")}},
        })
        print(f"\n冠军 {c.fingerprint[:10]}  train_icir={c.train_icir:+.2f}  oos_icir={c.oos_icir:+.2f}"
              f"  novelty={nov if nov is None else f'{nov:.2f}'}")
        print(f"  expr: {c.expr[:120]}")
        print(f"  L1(OOS 2025-): {l1_decision} {l1_metrics.get('annual')}")

    # 4) 预注册判据对账
    c1 = [ch for ch in champions if (ch["novelty_vs_prior_gen"] or 0) > 0.5]
    c2 = [ch for ch in champions
          if (ch["oos_icir"] or 0) and abs(ch["oos_icir"]) >= 0.3
          and ch["l1_oos"]["decision"] == "promote" and (ch["l1_oos"].get("annual") or 0) > 0]
    verdict = {
        "C1_novel_champions": len(c1),
        "C2_l1_survivors": len(c2),
        "criteria": {
            "C1": f"{len(c1)}/{len(champions)} 冠军 novelty>0.5",
            "C2": f"{len(c2)} 个冠军 OOS ICIR≥0.3 且 L1 存活",
        },
    }
    print("\n==== 预注册判据 ====")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    out = ROOT / "reports" / "research" / "autoresearch_closed_loop.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "cutoff": CUTOFF,
        "vintage_id": resp.vintage_id,
        "seeded_by": resp.seeded_by,
        "evaluated": resp.evaluated,
        "ledger_prompt": ledger_prompt,
        "prior_champion_count": len(prior_asts),
        "champions": champions,
        "verdict": verdict,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
