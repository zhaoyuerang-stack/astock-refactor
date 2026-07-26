"""Regime-gated LIVE 模式与 DSL 进化引擎集成测试。

Run:
    python3 -m unittest tests/test_regime_gate.py
"""
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.engine import (
    BacktestConfig,
    BacktestEngine,
    CostModel,
    PricePanel,
    Signal,
    _factor_to_weights,
)
from factors.autoresearch_dsl import clear_factor_cache, compute_dsl_factor
from portfolio.regime_gate import REGIME_GATED_DEFAULT, apply_regime_gate  # noqa: E402


class TestLegacyRegimeGate(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(REGIME_GATED_DEFAULT)

    def test_apply_regime_gate_switches_by_regime(self):
        idx = pd.bdate_range("2024-01-01", periods=10)
        equity = pd.Series(0.01, index=idx)        # equity 每天 +1%
        large_cap = pd.Series(-0.02, index=idx)    # large-cap 每天 -2%
        regime = pd.Series([1, 1, 0, 0, 1, 0, 1, 1, 0, 0], index=idx)  # 受宠/失宠
        gated = apply_regime_gate(equity, regime, large_cap)
        # 受宠日取 equity(+0.01),失宠日取 large-cap(-0.02)
        for dt in idx:
            expect = 0.01 if regime[dt] > 0 else -0.02
            self.assertLess(abs(gated[dt] - expect), 1e-12)

    def test_apply_regime_gate_aligns_on_intersection(self):
        idx1 = pd.bdate_range("2024-01-01", periods=8)
        idx2 = pd.bdate_range("2024-01-03", periods=8)  # 错位
        equity = pd.Series(0.01, index=idx1)
        large_cap = pd.Series(-0.02, index=idx2)
        regime = pd.Series(1, index=idx1)
        gated = apply_regime_gate(equity, regime, large_cap)
        self.assertEqual(len(gated), len(idx1.intersection(idx2)))  # 只在交集上


class TestRegimeGateDSL(unittest.TestCase):
    def setUp(self):
        # Create a test panel of 150 days and 5 stocks
        # Phase 1: Days 0-49: steady rise (+1% return per day) -> Bull
        # Phase 2: Days 50-99: steady fall (-2% return per day) -> Bear
        # Phase 3: Days 100-149: steady rise (+1% return per day) -> Bull
        n_days = 150
        n_stocks = 5
        self.dates = pd.bdate_range("2021-01-04", periods=n_days)
        self.codes = [f"{600000 + i:06d}" for i in range(n_stocks)]

        rets = np.zeros((n_days, n_stocks))
        rets[0:50, :] = 0.01
        rets[50:100, :] = -0.02
        rets[100:150, :] = 0.01

        self.close = pd.DataFrame(100.0 * np.exp(np.cumsum(rets, axis=0)), index=self.dates, columns=self.codes)
        self.volume = pd.DataFrame(1000000.0, index=self.dates, columns=self.codes)
        self.amount = self.close * self.volume

    def test_regime_gate_zeroes_on_bear_market(self):
        # Compute regime mask manually to know which dates are bull/bear
        mkt_ret = self.close.pct_change(fill_method=None).fillna(0.0).mean(axis=1)
        mkt_idx = (1 + mkt_ret).cumprod()
        mkt_ma = mkt_idx.rolling(16).mean()
        bull_mask = mkt_idx > mkt_ma

        # Define factor AST
        ast_gated = {
            "type": "linear_combo",
            "direction": "positive",
            "terms": [
                {
                    "factor": "momentum",
                    "params": {"window": 5},
                    "transforms": ["regime_gate"],
                    "weight": 1.0
                }
            ]
        }

        ast_ungated = {
            "type": "linear_combo",
            "direction": "positive",
            "terms": [
                {
                    "factor": "momentum",
                    "params": {"window": 5},
                    "transforms": [],
                    "weight": 1.0
                }
            ]
        }

        clear_factor_cache()
        gated_factor = compute_dsl_factor(self.close, self.volume, ast=ast_gated)
        clear_factor_cache()
        ungated_factor = compute_dsl_factor(self.close, self.volume, ast=ast_ungated)

        # Verify factor values are zeroed out on bear dates
        bear_dates = bull_mask.index[~bull_mask]
        bull_dates = bull_mask.index[bull_mask]

        # Bear dates should have all 0.0 factor values
        for dt in bear_dates:
            np.testing.assert_array_almost_equal(gated_factor.loc[dt].values, 0.0)

        # Bull dates should be equal to ungated factor
        pd.testing.assert_frame_equal(gated_factor.loc[bull_dates], ungated_factor.loc[bull_dates])

    def test_factor_to_weights_and_backtest_with_regime_gate(self):
        # Compute regime mask manually to find bear dates
        mkt_ret = self.close.pct_change(fill_method=None).fillna(0.0).mean(axis=1)
        mkt_idx = (1 + mkt_ret).cumprod()
        mkt_ma = mkt_idx.rolling(16).mean()
        bull_mask = mkt_idx > mkt_ma

        # Define factor AST with regime_gate
        ast_gated = {
            "type": "linear_combo",
            "direction": "positive",
            "terms": [
                {
                    "factor": "momentum",
                    "params": {"window": 5},
                    "transforms": ["regime_gate"],
                    "weight": 1.0
                }
            ]
        }

        clear_factor_cache()
        gated_factor = compute_dsl_factor(self.close, self.volume, ast=ast_gated)

        # Convert to weights (rebalance daily)
        weights = _factor_to_weights(gated_factor, top_n=2, direction=1, rebalance_freq="1D", close=self.close)

        # For effective dates corresponding to bear dates, weight should be zeroed out
        for dt in weights.index:
            pos = self.close.index.get_loc(dt)
            rd = self.close.index[pos - 1]
            is_rd_bull = bull_mask.loc[rd]

            if not is_rd_bull:
                self.assertAlmostEqual(weights.loc[dt].sum(), 0.0)

        # Run Backtest
        price_panel = PricePanel(close=self.close, volume=self.volume, amount=self.amount)
        config = BacktestConfig(
            start=str(self.dates[0].date()),
            cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
            leverage=1.25
        )
        engine = BacktestEngine(prices=price_panel, config=config)
        signal = Signal(weights=weights)
        result = engine.run(signal)

        # Find continuous bear dates range in the backtest (effective dates)
        # Effective dates are the trading day after rebalance dates.
        bear_effective_dates = []
        for dt in result.returns.index:
            pos = self.close.index.get_loc(dt)
            if pos == 0:
                continue
            rd = self.close.index[pos - 1]
            if not bull_mask.loc[rd]:
                # If the previous day was also a bear day, it should be in cash
                # (so we have fully liquidated by this day)
                pos_rd = self.close.index.get_loc(rd)
                if pos_rd > 0 and not bull_mask.loc[self.close.index[pos_rd - 1]]:
                    bear_effective_dates.append(dt)

        # Verify that during fully liquidated bear periods, returns are 0.0, turnover is 0.0, and cost is 0.0
        for dt in bear_effective_dates:
            self.assertAlmostEqual(result.returns.loc[dt], 0.0)
            self.assertAlmostEqual(result.turnover.loc[dt], 0.0)
            self.assertAlmostEqual(result.cost.loc[dt], 0.0)


if __name__ == "__main__":
    unittest.main()
