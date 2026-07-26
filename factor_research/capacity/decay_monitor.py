"""Factor Decay Monitoring.

Monitors rolling Information Coefficient (IC) and alpha returns over time
to identify decaying strategies.
"""
from __future__ import annotations

import pandas as pd


def monitor_ic_decay(
    ic_series: pd.Series,
    short_window: int = 60,
    long_window: int = 252
) -> pd.DataFrame:
    """Calculate rolling short-term and long-term ICs to check for decay.

    Returns a DataFrame containing:
        - short_ic: rolling short-term mean IC
        - long_ic: rolling long-term mean IC
        - decay_ratio: short_ic / long_ic
    """
    df = pd.DataFrame(index=ic_series.index)
    df["short_ic"] = ic_series.rolling(short_window, min_periods=min(5, short_window)).mean()
    df["long_ic"] = ic_series.rolling(long_window, min_periods=min(10, long_window)).mean()
    
    # Avoid division by zero
    df["decay_ratio"] = df["short_ic"] / df["long_ic"].apply(lambda x: x if abs(x) > 1e-5 else (1e-5 if x >= 0 else -1e-5))
    df["breach"] = (df["short_ic"] * df["long_ic"] < 0) | (df["decay_ratio"].abs() < 0.3)
    return df
