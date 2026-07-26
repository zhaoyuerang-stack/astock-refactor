"""换手 A/B 的 L1 净年化对账:把"毛 vs 净"从推断变数字。

毛 ICIR 显示换手臂(tw=0.15)edge 比基线低 17%。但毛口径不含成本。本脚本把
两臂冠军走 canonical L0→L1(start=2018,真实成本),比**净年化**——验证低毛 IC
低换手的换手臂,在 ~12pp/年成本后是否反超高毛 IC 高换手的基线。

Run:
    cd factor_research && python3 scripts/research/turnover_ab_l1_net.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

START = "2018-01-01"
ARMS = {
    "A_baseline_tw0": "data/autoresearch/tab_A_baseline_tw0_cand.jsonl",
    "B_turnover_tw0.15": "data/autoresearch/tab_B_turnover_tw0.15_cand.jsonl",
}


def main():
    from factory.autoresearch.pipeline import run_validation_pipeline
    from factory.autoresearch.repositories import CandidateRepository, ExperimentLog, ReviewQueue
    from services.actions.autoresearch import _load_validation_data

    ab = json.load(open(ROOT / "reports" / "research" / "turnover_fitness_ab.json"))
    close, volume, amount, forward_ret = _load_validation_data(START)

    def l1_net(ast, repo, log, rq):
        from factory.autoresearch.validator import validate_candidate_ast
        cand = validate_candidate_ast(ast)
        res = run_validation_pipeline(
            cand, close=close, volume=volume, amount=amount, forward_ret=forward_ret,
            vintage_id="turnover-ab-l1", repository=repo, experiment_log=log, review_queue=rq,
            max_stage="l1",
        )
        exps = res.metrics.get("experiments", [])
        l1 = next((e for e in exps if e["protocol"] == "l1_quick_bt"), None)
        if l1 is None:
            return None  # 死在 L0
        m = l1["metrics"]
        return {"net_annual": m.get("annual"), "sharpe": m.get("sharpe"),
                "maxdd": m.get("maxdd"), "decision": l1["decision"]}

    out = {}
    for arm, relpath in ARMS.items():
        repo = CandidateRepository(ROOT / relpath)
        log = ExperimentLog(ROOT / "data" / "autoresearch" / f"l1net_{arm}_exp.jsonl")
        rq = ReviewQueue(ROOT / "data" / "autoresearch" / f"l1net_{arm}_rq.jsonl")
        rows = []
        print(f"\n==== {arm} (L0→L1 net, 真实成本) ====")
        for r in ab["arms"][arm]:
            cand = repo.get(r["fp"]) or next((c for c in repo.all() if c.fingerprint.startswith(r["fp"])), None)
            if cand is None:
                print(f"  {r['fp']} (AST 未找到,跳过)"); continue
            net = l1_net(cand.ast, repo, log, rq)
            row = {"fp": r["fp"], "gross_icir": r["icir"], "turnover_proxy": r["turnover"],
                   "net_annual": None if not net else net["net_annual"],
                   "l1_decision": None if not net else net["decision"]}
            rows.append(row)
            na = "L0死" if not net else f"{net['net_annual']:+.1%}"
            extra = "" if not net else f" sh={net['sharpe']:.2f} dd={net['maxdd'] * 100:.1f}%"
            print(f"  {r['fp']} gross_icir={r['icir']:+.2f} turn={r['turnover']:.2f} → 净年化 {na}{extra}")
        out[arm] = rows

    def mean_net(rows):
        v = [r["net_annual"] for r in rows if r["net_annual"] is not None]
        return sum(v)/len(v) if v else None
    na, nb = mean_net(out["A_baseline_tw0"]), mean_net(out["B_turnover_tw0.15"])
    print("\n==== 净年化对账 ====")
    print(f"  基线(高毛IC 0.62/高换手 0.58): 均值净年化 {na:+.1%}" if na is not None else "  基线: 全 L0 死")
    print(f"  换手(低毛IC 0.52/低换手 0.30): 均值净年化 {nb:+.1%}" if nb is not None else "  换手: 全 L0 死")
    if na is not None and nb is not None:
        verdict = (f"换手臂净反超基线 {nb-na:+.1%}:低换手净胜,毛 ICIR 是误导口径"
                   if nb > na else f"换手臂净仍低 {nb-na:+.1%}:0.15 权重可能过激,需调")
        print(f"  判定: {verdict}")

    rep = ROOT / "reports" / "research" / "turnover_ab_l1_net.json"
    rep.write_text(json.dumps({"arms": out, "mean_net": {"baseline": na, "turnover": nb}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {rep}")


if __name__ == "__main__":
    main()
