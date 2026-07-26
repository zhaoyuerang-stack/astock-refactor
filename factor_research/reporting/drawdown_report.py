"""Drawdown Analysis.

Computes max drawdown, rolling drawdowns, underwater duration,
and peak-to-trough recovery stats.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def generate_drawdown_report(returns: pd.Series) -> dict[str, Any]:
    """Analyze the drawdown profile of returns."""
    cum_returns = (1 + returns).cumprod()
    running_max = cum_returns.cummax()
    drawdowns = cum_returns / running_max - 1.0

    max_dd = float(drawdowns.min())
    
    # Identify drawdown events (where drawdown < 0)
    is_in_dd = drawdowns < 0
    dd_events: list[dict[str, Any]] = []
    
    peak_date = None
    trough_date = None
    trough_val = 0.0

    for date, in_dd in is_in_dd.items():
        if in_dd:
            if peak_date is None:
                # Find the actual peak before this drawdown
                peak_date = date
                trough_date = date
                trough_val = drawdowns.loc[date]
            else:
                if drawdowns.loc[date] < trough_val:
                    trough_val = drawdowns.loc[date]
                    trough_date = date
        else:
            if peak_date is not None:
                # Ended drawdown event
                recovery_days = (date - peak_date).days
                dd_events.append({
                    "peak": peak_date.strftime("%Y-%m-%d"),
                    "trough": trough_date.strftime("%Y-%m-%d"),
                    "recovery": date.strftime("%Y-%m-%d"),
                    "depth": float(trough_val),
                    "days": recovery_days
                })
                peak_date = None
                
    # Sort events by depth
    dd_events = sorted(dd_events, key=lambda x: x["depth"])

    return {
        "max_drawdown": max_dd,
        "average_drawdown": float(drawdowns.mean()),
        "worst_drawdown_events": dd_events[:5]
    }
