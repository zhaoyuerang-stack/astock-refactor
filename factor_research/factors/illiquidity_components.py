"""Illiquidity and salience factor components."""
from __future__ import annotations

import pandas as pd


def salience_covariance_score(
    close: pd.DataFrame,
    *,
    W: int = 20,
    theta: float = 0.1,
    delta: float = 0.7,
) -> pd.DataFrame:
    """Faded Salience Covariance score; higher is safer."""
    returns = close.pct_change(fill_method=None)
    market_returns = returns.mean(axis=1)

    r_diff = returns.sub(market_returns, axis=0).abs()
    r_sum = returns.abs().add(market_returns.abs(), axis=0) + theta
    salience = r_diff / r_sum

    ranks = {}
    valid_count = pd.DataFrame(0, index=salience.index, columns=salience.columns)
    for j in range(W):
        valid_count += salience.shift(j).notna().astype(int)

    for s in range(W):
        better_count = pd.DataFrame(0, index=salience.index, columns=salience.columns)
        for j in range(W):
            if j == s:
                continue
            better_count += (salience.shift(j) > salience.shift(s)).astype(int)
        ranks[s] = (better_count + 1).where(salience.shift(s).notna())

    denom = delta * (1 - delta ** valid_count) / (1 - delta)
    est_return = pd.DataFrame(0.0, index=returns.index, columns=returns.columns)
    for s in range(W):
        weight_s = (delta ** ranks[s]) / denom
        r_lag = returns.shift(s)
        est_return += weight_s * r_lag.fillna(0.0)

    avg_return = returns.rolling(W).mean()
    st_cov = est_return - avg_return
    return -st_cov
