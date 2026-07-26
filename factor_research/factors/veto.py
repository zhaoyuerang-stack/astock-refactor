"""Backward-compatible veto names.

New policy filters belong in ``policy.candidate_filters``.
New illiquidity components belong in ``factors.illiquidity_components``.
"""
from __future__ import annotations

import pandas as pd

from factors.illiquidity_components import salience_covariance_score
from policy.candidate_filters import loser_reversal_filter


def loser_veto_reversal(
    close: pd.DataFrame,
    *,
    lookback: int = 20,
    vol_window: int = 20,
) -> pd.DataFrame:
    return loser_reversal_filter(close, lookback=lookback, vol_window=vol_window)


def salience_covariance_veto(
    close: pd.DataFrame,
    *,
    W: int = 20,
    theta: float = 0.1,
    delta: float = 0.7,
) -> pd.DataFrame:
    return salience_covariance_score(close, W=W, theta=theta, delta=delta)
