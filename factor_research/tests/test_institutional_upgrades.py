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
from model_risk.independent_validation import (
    analyze_parameter_stability,
    validate_strategy_performance,
)
from model_risk.model_inventory import ModelCard, ModelInventory
from portfolio.constraints import PortfolioConstraints
from portfolio.optimizer import PortfolioOptimizer
from research_ledger.ledger import LedgerEntry, ResearchLedger


class TestInstitutionalUpgrades(unittest.TestCase):
    def test_model_risk_inventory(self):
        inventory = ModelInventory()
        card = ModelCard(
            strategy_id="test_strat/v1",
            economic_hypothesis="Test Hypothesis",
            data_sources=["test_source"],
            train_period="2018-2020",
            oos_period="2021-2022",
            applicable_regimes=["BULL"],
            capacity_limit=10000000.0,
            style_exposures={"Beta": 1.0},
            forbidden_conditions=["PANIC"],
            known_failure_cases=["2018"],
            owner="Researcher",
            approver="Risk Officer"
        )
        inventory.register_card(card)
        
        retrieved = inventory.get_card("test_strat/v1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.economic_hypothesis, "Test Hypothesis")
        self.assertEqual(retrieved.approval_status, "PENDING")

    def test_independent_validation(self):
        returns = pd.Series(np.random.normal(0.001, 0.01, 100))
        report = validate_strategy_performance("test_strat/v1", returns, target_sharpe=0.1)
        self.assertIn("oos_sharpe", report.metrics)

        stability_report = analyze_parameter_stability("test_strat/v1", 1.5, [1.3, 1.4, 1.6])
        self.assertTrue(stability_report.passed)

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
