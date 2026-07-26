"""Turnover & Transaction Cost reporting.

Measures portfolio turnover, trade distributions, and execution drag.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def generate_turnover_report(
    weights: pd.DataFrame,
    cost_series: pd.Series
) -> dict[str, Any]:
    """Calculate average turnover, cumulative cost, and cost drag statistics."""
    # Turnover calculation: sum(|w_t - w_{t-1}|) / 2
    trades = weights.diff().fillna(0.0)
    daily_turnover = trades.abs().sum(axis=1) / 2.0

    total_cost = float(cost_series.sum())
    mean_daily_cost = float(cost_series.mean())

    return {
        "annual_turnover": float(daily_turnover.mean() * 252),
        "mean_daily_turnover": float(daily_turnover.mean()),
        "max_daily_turnover": float(daily_turnover.max()),
        "total_transaction_cost_cny": total_cost,
        "mean_daily_cost_cny": mean_daily_cost,
        "annualized_cost_drag": float(mean_daily_cost * 252)
    }
