"""Unit tests for classic Alpha101 factor computation and evolution integration.

Run:
    python3 -m unittest tests/test_classic_alphas_evolution.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from factors.autoresearch_dsl import compute_dsl_factor
from factory.autoresearch import (
    CandidateRepository,
    DSLValidationError,
    ExperimentLog,
    ReviewQueue,
    generate_seed_candidates,
    validate_candidate_ast,
)
from factory.autoresearch.islands import run_island_search
from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns

SEARCHABLE_ALPHA_FACTORS = (
    "alpha_001", "alpha_002", "alpha_003", "alpha_006", "alpha_008",
    "alpha_009", "alpha_012", "alpha_013", "alpha_014", "alpha_015",
    "alpha_017", "alpha_018", "alpha_019", "alpha_021", "alpha_023",
    "alpha_025", "alpha_028", "alpha_030", "alpha_032", "alpha_034",
    "alpha_037", "alpha_038", "alpha_040", "alpha_044", "alpha_050",
    "alpha_055",
)
RETIRED_ALPHA_FACTORS = (
    "alpha_005", "alpha_020", "alpha_022", "alpha_024", "alpha_033", "alpha_049",
)


def _synthetic_panel(n_days: int = 420, n_stocks: int = 25):
    """Deterministic synthetic panel for testing."""
    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_stocks)]
    drift = np.linspace(-0.0015, 0.0015, n_stocks)
    rets = drift + rng.normal(0.0, 0.01, size=(n_days, n_stocks))
    close = pd.DataFrame(100.0 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(
        rng.integers(200_000, 5_000_000, size=(n_days, n_stocks)).astype(float),
        index=dates,
        columns=codes,
    )
    amount = close * volume
    return close, volume, amount


class TestClassicAlphasEvolution(unittest.TestCase):

    def setUp(self):
        self.close, self.volume, self.amount = _synthetic_panel()
        self.forward_ret = precompute_forward_returns(self.close)

    def test_compute_all_searchable_alpha101_factors(self):
        """Verify that every non-degenerate Alpha101 factor validates and computes."""
        for factor in SEARCHABLE_ALPHA_FACTORS:
            ast = {
                "type": "linear_combo",
                "terms": [
                    {
                        "factor": factor,
                        "params": {},
                        "transforms": ["mad_clip", "zscore", "rank"],
                        "weight": 1.0,
                    }
                ],
                "direction": "positive",
                "thesis": {
                    "mechanism": "Testing classic alphas.",
                    "citation": "unit test"
                }
            }
            # Verify validation passes
            try:
                validate_candidate_ast(ast)
            except Exception as e:
                self.fail(f"AST validation failed for factor {factor}: {e}")

            # Verify computation works
            try:
                out = compute_dsl_factor(self.close, self.volume, ast=ast)
                self.assertEqual(out.shape, self.close.shape)
                # Check that we have non-NaN values in the latter part of the panel
                # (allowing for rolling window warmup periods up to 250 days)
                valid_count = out.iloc[260:].notna().sum().sum()
                self.assertGreater(valid_count, 0, f"Factor {factor} returned all NaNs in latter portion")
            except Exception as e:
                self.fail(f"Computation failed for factor {factor}: {e}")

    def test_degenerate_alpha101_factors_are_not_searchable(self):
        """Retired duplicate factors remain unavailable to AutoResearch candidates."""
        for factor in RETIRED_ALPHA_FACTORS:
            ast = {
                "type": "linear_combo",
                "terms": [{
                    "factor": factor,
                    "params": {},
                    "transforms": ["zscore"],
                    "weight": 1.0,
                }],
                "direction": "positive",
                "thesis": {"mechanism": "test", "citation": "unit test"},
            }
            with self.assertRaisesRegex(DSLValidationError, f"unknown factor: {factor}"):
                validate_candidate_ast(ast)

    def test_seed_candidate_generation(self):
        """Verify that seed candidate generator successfully generates classic alpha seeds."""
        seeds = list(generate_seed_candidates(limit=25))
        self.assertGreater(len(seeds), 10)

        # Check if alpha seeds are present
        alpha_seeds = [
            c for c in seeds
            if any(t["factor"].startswith("alpha_") for t in c.ast.get("terms", []))
        ]
        self.assertGreater(len(alpha_seeds), 0, "No classic alpha seeds were generated")

    def test_island_search_with_alphas(self):
        """Verify that islands evolution search runs successfully with classic alpha factors in pool/seeds."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Retrieve initial seeds containing alpha factors
            seeds = list(generate_seed_candidates(limit=6))

            result = run_island_search(
                self.close, self.volume, self.amount, self.forward_ret,
                vintage_id="test_alphas",
                n_islands=2,
                generations=1,
                population=4,
                top_k=2,
                rng_seed=42,
                seeds=seeds,
                repository=CandidateRepository(root / "candidates.jsonl"),
                experiment_log=ExperimentLog(root / "experiment_log.jsonl"),
                review_queue=ReviewQueue(root / "review_queue.jsonl"),
            )

            self.assertGreater(result.evaluated, 0)
            self.assertEqual(len(result.champions), 2)


if __name__ == "__main__":
    unittest.main()
