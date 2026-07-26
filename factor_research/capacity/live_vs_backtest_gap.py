"""Live vs. Backtest Gap analysis.

Compares simulated live paper-trading results with historical backtest targets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def analyze_live_vs_backtest_gap(
    live_returns: pd.Series,
    backtest_returns: pd.Series,
    window: int = 60
) -> pd.DataFrame:
    """Analyze rolling gap and tracking error between live returns and backtest baseline."""
    common_idx = live_returns.index.intersection(backtest_returns.index)
    df = pd.DataFrame(index=common_idx)
    
    if len(common_idx) == 0:
        df["returns_gap"] = 0.0
        df["tracking_error"] = 0.0
        return df

    gap = live_returns.loc[common_idx] - backtest_returns.loc[common_idx]
    
    df["returns_gap"] = gap.rolling(window, min_periods=min(5, window)).mean() * 252
    df["tracking_error"] = gap.rolling(window, min_periods=min(5, window)).std() * np.sqrt(252)
    
    return df
