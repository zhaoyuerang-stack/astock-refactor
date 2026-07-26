"""Test Institutional Quant OS upgrades."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from capacity.dollar_capacity import estimate_dollar_capacity
from portfolio.constraints import PortfolioConstraints
from portfolio.optimizer import PortfolioOptimizer
from research_ledger.ledger import LedgerEntry, ResearchLedger


class TestInstitutionalUpgrades(unittest.TestCase):
    def test_portfolio_optimizer_and_rebalance(self):
        alpha = np.array([0.05, 0.02, 0.08])
        initial_weights = np.array([0.33, 0.33, 0.33])
        exposures = np.array([[1.0, 0.5], [0.8, -0.2], [1.2, 0.4]]) # assets x factors
        factor_cov = np.array([[0.01, 0.002], [0.002, 0.008]])
        specific_var = np.array([0.02, 0.015, 0.03])

        optimizer = PortfolioOptimizer(
            constraints=PortfolioConstraints(max_weight=1.0)
        )
        weights = optimizer.optimize(
            alpha=alpha,
            initial_weights=initial_weights,
            exposures=exposures,
            factor_cov=factor_cov,
            specific_var=specific_var
        )
        self.assertEqual(len(weights), 3)
        self.assertAlmostEqual(np.sum(weights), 1.0, places=4)

    def test_research_ledger(self):
        ledger = ResearchLedger()
        entry = LedgerEntry(
            experiment_id="EXP_TEST",
            parent_experiment_id=None,
            hypothesis_text="Test hypothesis text",
            llm_prompt_hash="abc",
            factor_ast_hash="def",
            code_commit_hash="commit_hash",
            data_snapshot_hash="data_hash",
            universe_version="v1",
            cost_model_version="v2",
            random_seed=42,
            tried_parameters={"param": 1},
            result_metrics={"sharpe": 1.5},
            rejection_reason=None,
            reviewer="Reviewer",
            run_at="2026-06-16 12:00:00"
        )
        ledger.log_experiment(entry)
        
        retrieved = ledger.get_by_id("EXP_TEST")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.hypothesis_text, "Test hypothesis text")

    def test_capacity_and_execution_compliance(self):
        w_df = pd.DataFrame([[0.5, 0.5]], index=[pd.Timestamp("2026-06-16")], columns=["A", "B"])
        adv_df = pd.DataFrame([[10000000.0, 20000000.0]], index=[pd.Timestamp("2026-06-16")], columns=["A", "B"])
        cap = estimate_dollar_capacity(w_df, adv_df)
        self.assertTrue(cap > 0)


if __name__ == "__main__":
    unittest.main()
