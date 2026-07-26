"""Run evolutionary search using classic Alpha101 seeds and penalizing turnover and correlation.

Usage:
  python3 scripts/research/run_alphas_evolution_search.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.autoresearch.repositories import CandidateRepository, ExperimentLog, ReviewQueue
from services.actions.autoresearch_search import run_autoresearch_island_search


def main():
    print("=" * 80)
    print("  Evolving Classic Alphas Seed Evolution Search")
    print("=" * 80)

    repository = CandidateRepository()
    review_queue = ReviewQueue()
    experiment_log = ExperimentLog()

    # Strictly isolate search data from holdout boundary (ADR-021) to prevent leakage
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    from governance.holdout import assert_search_clean, boundary
    from strategies.small_cap import load_price_panels
    
    start_date = "2018-01-01"
    HOLDOUT = boundary()
    
    print(f"Loading prices and truncating to < {HOLDOUT.date()} to protect holdout...")
    close, volume, amount = load_price_panels(start_date)
    
    close = close.loc[close.index < HOLDOUT]
    volume = volume.loc[volume.index < HOLDOUT]
    amount = amount.loc[amount.index < HOLDOUT]
    
    assert_search_clean(close.index[-1], label="Alphas Evolution Search")
    forward_ret = precompute_forward_returns(close)

    print("\nRunning Large Scale Island Search with 8 islands, 10 generations, population 12...", flush=True)
    search_res = run_autoresearch_island_search(
        islands=8,
        generations=10,
        population=12,
        top_k=5,
        final_stage="l3",
        use_llm=False,  # Force using our interleaved classic alphas seeds in _SEEDS
        start=start_date,
        sample_dates=120,
        repository=repository,
        experiment_log=experiment_log,
        review_queue=review_queue,
        turnover_weight=0.15,  # Penalize high turnover to tame classic alphas
        corr_weight=0.30,      # Penalize correlation with the existing book
        use_algebraic_proxies=True,
        multi_fidelity=True,
        mf_level1_dates=20,
        mf_level1_ic_min=0.02,
        mf_level2_dates=60,
        mf_level2_keep_ratio=0.5,
        close=close,
        volume=volume,
        amount=amount,
        forward_ret=forward_ret,
        regime_aware=True,
    )

    print(f"\nSearch complete. Evaluated: {search_res.evaluated}.", flush=True)
    print(f"Champions found: {len(search_res.champions)}", flush=True)
    print("-" * 80, flush=True)
    print(f"{'Fingerprint':<12} {'Island':<6} {'Gen':<4} {'ICIR':<8} {'Fitness':<8} {'Turnover':<8} {'Formula'}", flush=True)
    print("-" * 80, flush=True)
    for c in search_res.champions:
        print(f"{c.fingerprint[:12]:<12} {c.island:<6} {c.generation:<4} {c.icir:>+7.4f} {c.fitness:>7.4f} {c.turnover:>7.4f} {c.expr}", flush=True)
    print("-" * 80, flush=True)

if __name__ == "__main__":
    main()
