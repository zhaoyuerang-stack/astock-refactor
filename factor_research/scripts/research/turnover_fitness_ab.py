"""换手惩罚 A/B 真实搜索:turnover_weight=0(基线) vs 0.15,只变换手项。

同种子同数据,novelty=corr=0 隔离,唯一变量 = 适应度是否含换手惩罚。
两臂冠军的换手全部事后用同一方法测量。
关键问题:① 换手惩罚是否真把冠军换手压下来;② 是否误伤 edge(ICIR 不应崩)。

Run:
    cd factor_research && python3 scripts/research/turnover_fitness_ab.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

START = "2018-01-01"


def main():
    from factory.autoresearch.novelty import (
        candidate_factor_panel,
        sample_behavior_dates,
        topn_turnover,
    )
    from factory.autoresearch.repositories import CandidateRepository, ExperimentLog
    from services.actions.autoresearch import _load_validation_data
    from services.actions.autoresearch_search import run_autoresearch_island_search

    close, volume, amount, forward_ret = _load_validation_data(START)
    behavior_idx = sample_behavior_dates(close.index, 60)

    def measure(champs, repo):
        rows = []
        for c in champs:
            cand = repo.get(c.fingerprint)
            turn = None
            if cand is not None:
                try:
                    panel = candidate_factor_panel(cand.ast, close, volume, behavior_idx)
                    turn = topn_turnover(panel, 25)
                except Exception:
                    pass
            rows.append({"fp": c.fingerprint[:10], "expr": c.expr, "icir": round(c.icir, 3),
                         "turnover": None if turn is None else round(turn, 3)})
        return rows

    arms = {}
    for label, tw in (("A_baseline_tw0", 0.0), ("B_turnover_tw0.15", 0.15)):
        repo = CandidateRepository(ROOT / "data" / "autoresearch" / f"tab_{label}_cand.jsonl")
        log = ExperimentLog(ROOT / "data" / "autoresearch" / f"tab_{label}_exp.jsonl")
        print(f"\n==== 臂 {label} (turnover_weight={tw}) ====")
        resp = run_autoresearch_island_search(
            islands=4, generations=3, population=8, top_k=5,
            use_llm=False, start=START, rng_seed=7,
            novelty_weight=0.0, corr_weight=0.0, turnover_weight=tw,  # 隔离换手项
            close=close, volume=volume, amount=amount, forward_ret=forward_ret,
            repository=repo, experiment_log=log,
        )
        rows = measure(resp.champions, repo)
        arms[label] = rows
        for r in rows:
            print(f"  {r['fp']} icir={r['icir']:+.2f} turnover={r['turnover']}  {r['expr'][:60]}")
        tv = [r["turnover"] for r in rows if r["turnover"] is not None]
        iv = [abs(r["icir"]) for r in rows]
        if tv:
            print(f"  → 均值换手 {sum(tv)/len(tv):.3f}  均值|ICIR| {sum(iv)/len(iv):.3f}")

    def stats(rows):
        tv = [r["turnover"] for r in rows if r["turnover"] is not None]
        iv = [abs(r["icir"]) for r in rows]
        return {"mean_turnover": round(sum(tv)/len(tv), 3) if tv else None,
                "mean_abs_icir": round(sum(iv)/len(iv), 3) if iv else None, "n": len(rows)}
    a, b = stats(arms["A_baseline_tw0"]), stats(arms["B_turnover_tw0.15"])
    print("\n==== A/B 对比 ====")
    print(f"  基线(tw=0):    换手 {a['mean_turnover']}  |ICIR| {a['mean_abs_icir']}")
    print(f"  换手(tw=0.15): 换手 {b['mean_turnover']}  |ICIR| {b['mean_abs_icir']}")
    lower_turn = a["mean_turnover"] is not None and b["mean_turnover"] is not None and b["mean_turnover"] < a["mean_turnover"]
    edge_kept = b["mean_abs_icir"] is not None and a["mean_abs_icir"] is not None and b["mean_abs_icir"] >= 0.7 * a["mean_abs_icir"]
    verdict = ("换手惩罚生效:冠军换手下降" + ("且 edge 基本保留" if edge_kept else ",但 edge 明显下降需调权重")) if lower_turn else "未见换手下降(需查)"
    print(f"  判定: {verdict}")

    out = ROOT / "reports" / "research" / "turnover_fitness_ab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"arms": arms, "stats": {"baseline": a, "turnover": b}, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
