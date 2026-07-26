"""Challenger & Benchmark Model Management.

Defines benchmarks and compares candidate strategy performance with challenger models
to ensure true active alpha.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


class ChallengerComparison:
    def __init__(self, strategy_id: str, challenger_id: str):
        self.strategy_id = strategy_id
        self.challenger_id = challenger_id
        self.metrics: dict[str, Any] = {}
        self.outperformed = False

    def compare(self, strategy_returns: pd.Series, challenger_returns: pd.Series):
        # Align indexes
        common_idx = strategy_returns.index.intersection(challenger_returns.index)
        s_ret = strategy_returns.loc[common_idx]
        c_ret = challenger_returns.loc[common_idx]

        if len(common_idx) == 0:
            return

        # Core performance
        s_ann = float(s_ret.mean() * 252)
        c_ann = float(c_ret.mean() * 252)
        s_vol = float(s_ret.std() * np.sqrt(252))
        c_vol = float(c_ret.std() * np.sqrt(252))
        
        s_sharpe = s_ann / s_vol if s_vol > 0 else 0.0
        c_sharpe = c_ann / c_vol if c_vol > 0 else 0.0

        # Active return & tracking error
        active_ret = s_ret - c_ret
        ann_active_ret = float(active_ret.mean() * 252)
        tracking_error = float(active_ret.std() * np.sqrt(252))
        info_ratio = ann_active_ret / tracking_error if tracking_error > 0 else 0.0

        self.metrics = {
            "strategy_sharpe": s_sharpe,
            "challenger_sharpe": c_sharpe,
            "active_return": ann_active_ret,
            "tracking_error": tracking_error,
            "information_ratio": info_ratio
        }

        # Outperformance check
        self.outperformed = s_sharpe > c_sharpe + 0.1 or info_ratio > 0.3

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "challenger_id": self.challenger_id,
            "outperformed": self.outperformed,
            "metrics": self.metrics
        }


def run_benchmark_challenger(
    strategy_id: str,
    strategy_returns: pd.Series,
    prices: Any
) -> ChallengerComparison:
    """Generate a naive equal-weight challenger and run comparison."""
    # Build a simple equal-weight challenger of the universe
    # Using prices.close to get daily returns average across all stocks
    mkt_ret = prices.close.pct_change().mean(axis=1).fillna(0.0)
    
    comp = ChallengerComparison(strategy_id, "Market_Equal_Weight_Challenger")
    comp.compare(strategy_returns, mkt_ret)
    return comp
