"""Canonical portfolio composition algorithms."""
from __future__ import annotations

import numpy as np
import pandas as pd


def equal_weight_portfolio(returns: pd.DataFrame) -> pd.Series:
    n = returns.shape[1]
    weights = np.full(n, 1.0 / n)
    return (returns * weights).sum(axis=1)


def risk_parity_portfolio(returns: pd.DataFrame, lookback: int = 252) -> pd.Series:
    rolling_vol = returns.rolling(lookback, min_periods=63).std()
    inv_vol = 1.0 / rolling_vol.replace(0, np.nan)
    weights = inv_vol.div(inv_vol.sum(axis=1), axis=0)
    weights = weights.shift(1)
    return (returns * weights).sum(axis=1)


def capped_portfolio_weight(returns: pd.DataFrame, defensive: set, cap: float = 0.30) -> tuple[pd.Series, pd.Series]:
    cols = list(returns.columns)
    d = [c for c in cols if c in defensive]
    g = [c for c in cols if c not in defensive]
    w = pd.Series(0.0, index=cols)
    if d and g:
        w[d] = cap / len(d)
        w[g] = (1.0 - cap) / len(g)
    else:
        w[:] = 1.0 / len(cols)
    return (returns * w).sum(axis=1), w


def regime_adaptive_portfolio(
    returns: pd.DataFrame,
    vol: pd.DataFrame,
    regime_signal: pd.Series,
) -> pd.Series:
    regime = regime_signal.reindex(returns.index).fillna(0)
    n = returns.shape[1]
    bull_w = pd.DataFrame(1.0 / n, index=returns.index, columns=returns.columns)
    vol_mean = vol.rolling(252).mean().iloc[-1]
    lowest_vol = vol_mean.idxmin()
    bear_w = pd.DataFrame(1.0 / n, index=returns.index, columns=returns.columns)
    bear_w[lowest_vol] = min(0.5, 2.0 / n)
    weights = bull_w.mul(regime, axis=0) + bear_w.mul(1 - regime, axis=0)
    weights = weights.div(weights.sum(axis=1), axis=0).fillna(1.0 / n)
    weights = weights.shift(1).fillna(1.0 / n)
    return (returns * weights).sum(axis=1)


def compose_portfolio_returns(
    returns: dict[str, pd.Series],
    method: str = "equal_weight",
    regime_signal: pd.Series | None = None,
    defensive: set | None = None,
    cap: float = 0.30,
) -> tuple[pd.Series, pd.DataFrame]:
    df = pd.DataFrame(returns).dropna()
    if df.shape[1] < 2:
        return df.iloc[:, 0], pd.DataFrame({"weight": [1.0]})

    static_w = None
    if method == "risk_parity":
        port_ret = risk_parity_portfolio(df)
    elif method == "regime_adaptive":
        if regime_signal is None:
            raise ValueError("regime_signal required for regime_adaptive")
        vol = df.rolling(252).std()
        port_ret = regime_adaptive_portfolio(df, vol, regime_signal)
    elif method == "capped":
        port_ret, static_w = capped_portfolio_weight(df, defensive or set(), cap)
    else:
        port_ret = equal_weight_portfolio(df)

    if static_w is not None:
        weights = pd.DataFrame([static_w.values], columns=df.columns, index=["weight"])
    else:
        weights = pd.DataFrame(1.0 / df.shape[1], index=df.index, columns=df.columns)
    return port_ret.dropna(), weights


def portfolio_metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    if len(r) < 50:
        return {"annual": 0, "maxdd": 0, "sharpe": 0, "calmar": 0}
    ann = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = ann / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    calmar = ann / abs(maxdd) if maxdd < 0 else 0.0
    return {"annual": ann, "vol": vol, "maxdd": maxdd, "sharpe": sharpe, "calmar": calmar, "n_days": len(r)}
