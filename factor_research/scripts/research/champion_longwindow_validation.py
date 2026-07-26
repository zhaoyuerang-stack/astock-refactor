"""闭环冠军的诚实长窗验证 + 注册册查重。

闭环验证只做了 2025-2026 短窗 L1(妖股友好年),不等价长窗成本闸门。
本脚本把去镜像后的冠军推过 canonical L0→L1→L2(start=2018 长窗,真实成本),
并对照注册册标注"对上一代新颖 ≠ 对注册册新颖"的查重结论。

Run:
    cd factor_research && python3 scripts/research/champion_longwindow_validation.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 去镜像后的 4 个判别性冠军(volume_ratio+revenue_yoy 的正负两版已折叠为一)
CHAMPIONS = [
    {"label": "fundamental_momentum", "expr": "momentum(60)+revenue_yoy",
     "ast": {"type": "linear_combo", "direction": "negative", "terms": [
         {"factor": "momentum", "params": {"window": 60}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
         {"factor": "revenue_yoy", "params": {}, "transforms": ["mad_clip", "zscore"], "weight": 0.5}]}},
    {"label": "lowattn_growth", "expr": "volume_ratio(30)+revenue_yoy",
     "ast": {"type": "linear_combo", "direction": "positive", "terms": [
         {"factor": "volume_ratio", "params": {"window": 30}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
         {"factor": "revenue_yoy", "params": {}, "transforms": ["mad_clip", "zscore"], "weight": 0.5}]}},
    {"label": "illiq_lowvol20", "expr": "illiquidity(65)-volatility(20)",
     "ast": {"type": "linear_combo", "direction": "positive", "terms": [
         {"factor": "illiquidity", "params": {"window": 65}, "transforms": ["mad_clip", "rank", "zscore"], "weight": 1},
         {"factor": "volatility", "params": {"window": 20}, "transforms": ["rank", "zscore"], "weight": -1}]}},
    {"label": "illiq_lowvol60", "expr": "illiquidity(65)-volatility(60)",
     "ast": {"type": "linear_combo", "direction": "positive", "terms": [
         {"factor": "illiquidity", "params": {"window": 65}, "transforms": ["mad_clip", "rank", "zscore"], "weight": 1},
         {"factor": "volatility", "params": {"window": 60}, "transforms": ["mad_clip", "rank", "zscore"], "weight": -1}]}},
]

# 注册册查重:冠军用到的因子家族中,哪些已作为独立母策略在册
REGISTERED_FACTOR_FAMILIES = {
    "illiquidity": "illiquidity(在册 v1.0~v3.1)",
    "volatility": "size-low-vol(在册 v1.0~v1.1)",
}


def main():
    from factory.autoresearch import validate_candidate_ast
    from factory.autoresearch.repositories import CandidateRepository, ExperimentLog, ReviewQueue
    from services.actions.autoresearch import _run_candidates

    candidates, meta = [], []
    for ch in CHAMPIONS:
        ast = {**ch["ast"], "thesis": {"mechanism": f"闭环冠军长窗复核: {ch['expr']}",
                                       "citation": "autoresearch closed-loop champion"}}
        cand = validate_candidate_ast(ast)
        candidates.append(cand)
        factors = [t["factor"] for t in ch["ast"]["terms"]]
        rediscovers = [REGISTERED_FACTOR_FAMILIES[f] for f in factors if f in REGISTERED_FACTOR_FAMILIES]
        meta.append({"label": ch["label"], "expr": ch["expr"], "fingerprint": cand.fingerprint,
                     "rediscovery_flags": rediscovers})

    print("==== 去镜像后判别性冠军 + 注册册查重 ====")
    for m in meta:
        flag = ("⚠ 复用在册因子家族: " + "; ".join(m["rediscovery_flags"])) if m["rediscovery_flags"] else "✓ 因子家族未在册"
        print(f"  [{m['label']}] {m['expr']}  fp={m['fingerprint'][:10]}\n     {flag}")

    # 长窗 canonical 验证:L0→L1→L2,start=2018(真实成本,无第二口径)
    repo = CandidateRepository(ROOT / "data" / "autoresearch" / "champion_lw_candidates.jsonl")
    log = ExperimentLog(ROOT / "data" / "autoresearch" / "champion_lw_experiments.jsonl")
    rq = ReviewQueue(ROOT / "data" / "autoresearch" / "champion_lw_review.jsonl")
    print("\n==== 长窗 canonical 验证 L0→L1→L2 (start=2018, 真实成本) ====")
    resp = _run_candidates(
        candidates, max_stage="l2", start="2018-01-01",
        repository=repo, experiment_log=log, review_queue=rq,
    )

    by_fp = {r.fingerprint: r for r in resp.results}
    out_rows = []
    for m in meta:
        r = by_fp.get(m["fingerprint"])
        verdict = {"label": m["label"], "expr": m["expr"], "fingerprint": m["fingerprint"],
                   "rediscovery_flags": m["rediscovery_flags"],
                   "status": r.status if r else "missing",
                   "decision": r.decision if r else "missing",
                   "reason": r.reason if r else "",
                   "protocols": r.protocols if r else []}
        out_rows.append(verdict)
        print(f"\n  [{m['label']}] {m['expr']}")
        print(f"     status={verdict['status']} decision={verdict['decision']}")
        print(f"     reason={verdict['reason'][:120]}")

    survivors = [v for v in out_rows if str(v["status"]).startswith(("l1_passed", "l2_passed"))]
    new_survivors = [v for v in survivors if not v["rediscovery_flags"]]
    print("\n==== 结论 ====")
    print(f"  长窗存活(过 L1): {len(survivors)}/{len(out_rows)}")
    print(f"  其中机制级真新(因子家族未在册)的存活: {len(new_survivors)}")

    out = ROOT / "reports" / "research" / "champion_longwindow_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "window": "L0->L1->L2 @ start=2018 (real cost)",
        "champions": out_rows,
        "survivors": len(survivors),
        "novel_survivors": len(new_survivors),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
