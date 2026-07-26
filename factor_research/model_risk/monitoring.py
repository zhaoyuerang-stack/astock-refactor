"""Live Model Drift and Performance Monitoring.

Tracks model decay, live IC drift, backtest vs. live tracking error,
and triggers alerts on performance breach.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


class PerformanceMonitor:
    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self.alerts: list[dict[str, Any]] = []

    def check_ic_decay(self, recent_ic_series: pd.Series, baseline_ic: float, threshold_pct: float = 0.5):
        """Monitor if the recent IC is decaying significantly below the historical baseline."""
        if len(recent_ic_series) == 0:
            return
        
        recent_ic_mean = float(recent_ic_series.mean())
        decay_ratio = recent_ic_mean / baseline_ic if baseline_ic != 0 else 1.0

        if decay_ratio < threshold_pct:
            self.alerts.append({
                "metric": "ic_decay",
                "severity": "WARNING",
                "message": f"IC decayed to {recent_ic_mean:.4f} (baseline: {baseline_ic:.4f}, decay: {decay_ratio:.1%})"
            })

    def check_live_vs_backtest_gap(
        self,
        live_returns: pd.Series,
        backtest_returns: pd.Series,
        max_tracking_error: float = 0.05
    ):
        """Track difference between live trading returns and historical backtest returns."""
        common_idx = live_returns.index.intersection(backtest_returns.index)
        if len(common_idx) < 5:
            return

        live_aligned = live_returns.loc[common_idx]
        backtest_aligned = backtest_returns.loc[common_idx]

        gap = live_aligned - backtest_aligned
        ann_gap_mean = float(gap.mean() * 252)
        tracking_error = float(gap.std() * np.sqrt(252))

        if tracking_error > max_tracking_error:
            self.alerts.append({
                "metric": "live_vs_backtest_gap",
                "severity": "CRITICAL",
                "message": f"Live vs Backtest tracking error is {tracking_error:.2%}, exceeding limit {max_tracking_error:.%}"
            })

        if ann_gap_mean < -0.05:
            self.alerts.append({
                "metric": "underperformance_gap",
                "severity": "WARNING",
                "message": f"Live returns underperforming backtest by {ann_gap_mean:.2%} annualized"
            })

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "has_alerts": len(self.alerts) > 0,
            "alerts": self.alerts
        }
