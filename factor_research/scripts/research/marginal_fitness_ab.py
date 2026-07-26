"""边际适应度 A/B 真实搜索:corr_weight=0(基线) vs 0.3(边际),只变适应度。

同 rng_seed + 同确定性种子 + 同数据 → 唯一变量 = 适应度是否含"对在册组合相关"。
两臂冠军的"对在册相关"全部**事后用同一方法**测量(基线臂搜索时不算相关,
但事后同样量),保证苹果对苹果。

预期:边际臂把冠军的 corr_to_book 压下来,搜索从 0.76 红海推向去相关区。

Run:
    cd factor_research && python3 scripts/research/marginal_fitness_ab.py
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
        max_return_correlation,
        sample_behavior_dates,
        topn_long_return,
    )
    from factory.autoresearch.repositories import CandidateRepository, ExperimentLog
    from services.actions.autoresearch import _load_validation_data
    from services.actions.autoresearch_search import (
        active_book_panels,
        run_autoresearch_island_search,
    )

    close, volume, amount, forward_ret = _load_validation_data(START)

    # 事后测量用:在册参考腿的收益代理(behavior_idx 上,与引擎同口径)
    behavior_idx = sample_behavior_dates(close.index, 60)
    book = [p.loc[p.index.intersection(behavior_idx)] for p in active_book_panels(close, volume, amount)]
    ref_returns = [topn_long_return(p, forward_ret, 25) for p in book]
    print(f"在册参考腿: {len(ref_returns)} 条(small-cap + illiquidity ACTIVE 集)")

    def measure(champs, repo):
        rows = []
        for c in champs:
            cand = repo.get(c.fingerprint)
            corr = None
            if cand is not None:
                try:
                    panel = candidate_factor_panel(cand.ast, close, volume, behavior_idx)
                    corr = max_return_correlation(topn_long_return(panel, forward_ret, 25), ref_returns)
                except Exception:
                    pass
            rows.append({"fp": c.fingerprint[:10], "expr": c.expr, "icir": round(c.icir, 3),
                         "corr_to_book": None if corr is None else round(corr, 3)})
        return rows

    arms = {}
    for label, cw in (("A_baseline_cw0", 0.0), ("B_marginal_cw0.3", 0.3)):
        repo = CandidateRepository(ROOT / "data" / "autoresearch" / f"ab_{label}_cand.jsonl")
        log = ExperimentLog(ROOT / "data" / "autoresearch" / f"ab_{label}_exp.jsonl")
        print(f"\n==== 臂 {label} (corr_weight={cw}) ====")
        resp = run_autoresearch_island_search(
            islands=4, generations=3, population=8, top_k=5,
            use_llm=False, start=START, rng_seed=7,
            corr_weight=cw,
            close=close, volume=volume, amount=amount, forward_ret=forward_ret,
            repository=repo, experiment_log=log,
        )
        rows = measure(resp.champions, repo)
        arms[label] = rows
        vals = [r["corr_to_book"] for r in rows if r["corr_to_book"] is not None]
        for r in rows:
            print(f"  {r['fp']} icir={r['icir']:+.2f} corr_to_book={r['corr_to_book']}  {r['expr'][:60]}")
        if vals:
            print(f"  → 均值 corr_to_book={sum(vals)/len(vals):+.3f}  max={max(vals):+.3f}  去相关数(<0.3)={sum(v<0.3 for v in vals)}/{len(vals)}")

    # 对比
    def stats(rows):
        v = [r["corr_to_book"] for r in rows if r["corr_to_book"] is not None]
        return {"mean": round(sum(v)/len(v), 3), "max": round(max(v), 3),
                "n_decorrelated_lt0.3": sum(x < 0.3 for x in v), "n": len(v)} if v else {}
    a, b = stats(arms["A_baseline_cw0"]), stats(arms["B_marginal_cw0.3"])
    print("\n==== A/B 对比 ====")
    print(f"  基线(cw=0):   均值 {a.get('mean')}  max {a.get('max')}  去相关 {a.get('n_decorrelated_lt0.3')}/{a.get('n')}")
    print(f"  边际(cw=0.3): 均值 {b.get('mean')}  max {b.get('max')}  去相关 {b.get('n_decorrelated_lt0.3')}/{b.get('n')}")
    verdict = "边际适应度生效:冠军对在册相关下降" if (a.get("mean") is not None and b.get("mean") is not None and b["mean"] < a["mean"]) else "未见明显下降(需查)"
    print(f"  判定: {verdict}")

    out = ROOT / "reports" / "research" / "marginal_fitness_ab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"arms": arms, "stats": {"baseline": a, "marginal": b}, "verdict": verdict},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
