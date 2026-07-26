"""Candidate-pool policy filters.

Policy filters constrain a host strategy. They are not standalone alpha factors.
"""
from __future__ import annotations

import pandas as pd


def _row_pct_rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True)


def loser_reversal_filter(
    close: pd.DataFrame,
    *,
    lookback: int = 20,
    vol_window: int = 20,
) -> pd.DataFrame:
    """Higher is safer; low scores are death-bucket exclusion candidates."""
    momentum = close.pct_change(lookback, fill_method=None)
    volatility = close.pct_change(fill_method=None).rolling(vol_window).std()
    return 0.75 * _row_pct_rank(momentum) + 0.25 * _row_pct_rank(volatility)
