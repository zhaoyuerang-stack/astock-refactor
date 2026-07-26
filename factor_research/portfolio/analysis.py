"""Portfolio analysis tools.

- Contribution decomposition: how much alpha/risk each strategy contributes
- Correlation matrix + rolling correlation
- Regime breakdown
"""
import numpy as np
import pandas as pd


def correlation_matrix(returns: dict[str, pd.Series]) -> pd.DataFrame:
    """Pairwise daily return correlation matrix."""
    df = pd.DataFrame(returns).dropna()
    return df.corr()


def rolling_correlation(returns: dict[str, pd.Series], window: int = 252):
    """Rolling correlation between two strategies."""
    df = pd.DataFrame(returns).dropna()
    if df.shape[1] < 2:
        return pd.Series(dtype=float)
    a, b = df.columns[0], df.columns[1]
    return df[a].rolling(window).corr(df[b])


def contribution_decompose(
    returns: dict[str, pd.Series],
    weights: dict[str, float] = None,
) -> pd.DataFrame:
    """Decompose portfolio return into per-strategy contributions.

    Returns DataFrame with columns:
      - weight, ann_contrib, risk_contrib, marginal_sharpe
    """
    df = pd.DataFrame(returns).dropna()
    n = df.shape[1]

    if weights is None:
        weights = {c: 1.0 / n for c in df.columns}
    w = pd.Series(weights).reindex(df.columns).fillna(0)

    # Annualized contribution
    ann = df.mean() * 252
    ann_contrib = ann * w

    # Risk contribution (simplified: proportional to weight × vol × avg corr)
    vol = df.std() * np.sqrt(252)
    corr_mat = df.corr()
    avg_corr = corr_mat.mean(axis=1)
    risk_contrib = w * vol * avg_corr
    risk_contrib = risk_contrib / risk_contrib.sum()  # normalize to 1

    # Marginal Sharpe: (portfolio Sharpe with strategy) - (portfolio Sharpe without)
    port_ret = (df * w).sum(axis=1)
    port_sharpe = _sharpe(port_ret)

    marginal = {}
    for col in df.columns:
        others = [c for c in df.columns if c != col]
        if not others:
            marginal[col] = port_sharpe
            continue
        w_other = {c: 1.0 / len(others) for c in others}
        port_without = (df[others] * pd.Series(w_other)).sum(axis=1)
        marginal[col] = port_sharpe - _sharpe(port_without)

    result = pd.DataFrame({
        "weight": w,
        "annual": ann,
        "vol": vol,
        "ann_contrib": ann_contrib,
        "risk_contrib_pct": risk_contrib,
        "marginal_sharpe": pd.Series(marginal),
        "avg_corr_to_others": avg_corr,
    })
    result["ann_contrib_pct"] = result["ann_contrib"] / result["ann_contrib"].sum()
    return result.sort_values("marginal_sharpe", ascending=False)


def regime_breakdown(
    returns: dict[str, pd.Series],
    regime_signal: pd.Series,
) -> pd.DataFrame:
    """Performance breakdown by regime (ON vs OFF).

    Returns DataFrame with per-strategy annualized return in each regime.
    """
    df = pd.DataFrame(returns).dropna()
    common = df.index.intersection(regime_signal.dropna().index)

    on_mask = regime_signal.loc[common] > 0.5
    off_mask = ~on_mask

    rows = []
    for col in df.columns:
        r_on = df.loc[common, col][on_mask]
        r_off = df.loc[common, col][off_mask]
        rows.append({
            "strategy": col,
            "regime_ON_annual": _annualize(r_on),
            "regime_OFF_annual": _annualize(r_off),
            "regime_ON_days_pct": on_mask.mean(),
            "regime_OFF_days_pct": off_mask.mean(),
        })

    return pd.DataFrame(rows).set_index("strategy")


def _sharpe(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 50 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(252))


def _annualize(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 10:
        return 0.0
    cum = (1 + r).prod()
    return float(cum ** (252 / len(r)) - 1)
