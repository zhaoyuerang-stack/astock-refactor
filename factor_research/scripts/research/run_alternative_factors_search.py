"""Run evolutionary search focusing exclusively on Alternative factors (shareholder behavior and cash flows).

Usage:
  python3 scripts/research/run_alternative_factors_search.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 1. Monkey-patch ALLOWED_FACTORS in the registry to restrict search space to Path A
import factory.autoresearch.registry as reg

alternative_names = {
    "holder_count_chg",
    "holdertrade_net",
    "large_order_net_ratio",
    "northbound_accumulation",
    "northbound_hold_level",
    "northbound_flow_strength",
    "roe",
    "net_profit_yoy",
    "revenue_yoy",
    "bp_proxy",
    "ep_proxy",
    "momentum",
    "illiquidity"
}

# Modify dict in-place so all modules holding references see the change
original_keys = list(reg.ALLOWED_FACTORS.keys())
for k in original_keys:
    if k not in alternative_names:
        reg.ALLOWED_FACTORS.pop(k)

print("=" * 80)
print("  Evolving Alternative Factors Search (Path A: Shareholders & Flows)")
print("=" * 80)
print(f"Restricted Allowed Factors to: {sorted(reg.ALLOWED_FACTORS.keys())}\n")

from factory.autoresearch.repositories import CandidateRepository, ExperimentLog, ReviewQueue
from services.actions.autoresearch_search import run_autoresearch_island_search


def main():
    repository = CandidateRepository()
    review_queue = ReviewQueue()
    experiment_log = ExperimentLog()

    # Generate custom seeds for Path A
    # Filter seeds to only include combinations of alternative/fundamental factors
    from factory.autoresearch.generator import generate_seed_candidates
    all_seeds = list(generate_seed_candidates(limit=250))
    path_a_seeds = []
    for c in all_seeds:
        valid = True
        for term in c.ast.get("terms", []):
            if term.get("factor") not in reg.ALLOWED_FACTORS:
                valid = False
                break
        if valid:
            path_a_seeds.append(c)

    print(f"Generated {len(path_a_seeds)} Path A alternative seed candidates.")

    print("\nRunning Large Scale Island Search (Path A)...", flush=True)
    search_res = run_autoresearch_island_search(
        islands=4,
        generations=5,
        population=8,
        top_k=5,
        final_stage="l3",
        use_llm=False,
        start="2018-01-01",
        sample_dates=120,
        repository=repository,
        experiment_log=experiment_log,
        review_queue=review_queue,
        turnover_weight=0.15,
        corr_weight=0.30,
        use_algebraic_proxies=True,
        multi_fidelity=True,
        mf_level1_dates=20,
        mf_level1_ic_min=0.02,
        mf_level2_dates=60,
        mf_level2_keep_ratio=0.5,
        regime_aware=True,
    )

    print(f"\nSearch complete. Evaluated: {search_res.evaluated}.", flush=True)
    print(f"Champions found: {len(search_res.champions)}", flush=True)
    print("-" * 80, flush=True)
    print(f"{'Fingerprint':<12} {'Island':<6} {'Gen':<4} {'ICIR':<8} {'Fitness':<8} {'Turnover':<8} {'Formula'}", flush=True)
    print("-" * 80, flush=True)
    for c in search_res.champions:
        cand = repository.get(c.fingerprint)
        exec_str = ""
        if cand and "execution" in cand.ast:
            exec_str = f" [Exec: {cand.ast['execution']}]"
        print(f"{c.fingerprint[:12]:<12} {c.island:<6} {c.generation:<4} {c.icir:>+7.4f} {c.fitness:>7.4f} {c.turnover:>7.4f} {c.expr}{exec_str}", flush=True)
    print("-" * 80, flush=True)


if __name__ == "__main__":
    main()
