"""Backward-compatible portfolio composer wrappers.

New code should import from ``portfolio.portfolio_composer``.
"""
from __future__ import annotations

import pandas as pd

from portfolio.portfolio_composer import (
    capped_portfolio_weight,
    compose_portfolio_returns,
    equal_weight_portfolio,
    portfolio_metrics,
    regime_adaptive_portfolio,
    risk_parity_portfolio,
)


def equal_weight(returns: pd.DataFrame) -> pd.Series:
    return equal_weight_portfolio(returns)


def risk_parity(returns: pd.DataFrame, lookback: int = 252) -> pd.Series:
    return risk_parity_portfolio(returns, lookback)


def capped_weight(returns: pd.DataFrame, defensive: set, cap: float = 0.30):
    return capped_portfolio_weight(returns, defensive, cap)


def regime_adaptive(
    returns: pd.DataFrame,
    vol: pd.DataFrame,
    regime_signal: pd.Series,
) -> pd.Series:
    return regime_adaptive_portfolio(returns, vol, regime_signal)


def compose(
    returns: dict[str, pd.Series],
    method: str = "equal_weight",
    regime_signal: pd.Series | None = None,
    defensive: set | None = None,
    cap: float = 0.30,
) -> tuple[pd.Series, pd.DataFrame]:
    return compose_portfolio_returns(returns, method, regime_signal, defensive, cap)


def metrics(returns: pd.Series) -> dict:
    return portfolio_metrics(returns)
