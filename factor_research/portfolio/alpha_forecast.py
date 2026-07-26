"""Alpha Forecast Synthesis.

Combines multiple raw factor inputs into unified, winsorized,
and standardized expected return forecasts (Alpha).
"""
from __future__ import annotations

import pandas as pd


def synthesize_alpha(
    factors: dict[str, pd.DataFrame],
    weights: dict[str, float] | None = None,
    winsorize_limits: float = 3.0,
    decay_halflife: int | None = None
) -> pd.DataFrame:
    """Synthesize multiple alpha factors into a single expected return forecast.

    Parameters
    ----------
    factors : dict of {factor_name: factor_df}
        DataFrames are index (date) x columns (asset codes).
    weights : dict of {factor_name: weight_fraction}, optional
        Blended factor weights. If omitted, equal weight is used.
    winsorize_limits : float
        Z-score limit to truncate extreme factor outliers.
    decay_halflife : int, optional
        If provided, applies exponential decay window to historical signals.
    """
    if not factors:
        raise ValueError("Must provide at least one factor DataFrame")

    keys = list(factors.keys())
    ref_df = factors[keys[0]]
    dates = ref_df.index
    assets = ref_df.columns

    if weights is None:
        weights = {k: 1.0 / len(keys) for k in keys}

    # Normalize weights
    total_w = sum(weights.get(k, 0.0) for k in keys)
    if total_w > 0:
        norm_weights = {k: weights.get(k, 0.0) / total_w for k in keys}
    else:
        norm_weights = {k: 1.0 / len(keys) for k in keys}

    combined = pd.DataFrame(0.0, index=dates, columns=assets)

    for k in keys:
        df = factors[k]
        # Cross-sectional standardization: (X - mean) / std
        mean_cs = df.mean(axis=1)
        std_cs = df.std(axis=1)
        
        # Avoid division by zero
        std_cs = std_cs.replace(0.0, 1.0)
        
        norm_df = df.sub(mean_cs, axis=0).div(std_cs, axis=0)

        # Winsorize at limit
        if winsorize_limits > 0:
            norm_df = norm_df.clip(-winsorize_limits, winsorize_limits)

        # Apply weights
        combined = combined.add(norm_df * norm_weights[k], fill_value=0.0)

    # Optional halflife decay over time (row-wise smoothing)
    if decay_halflife is not None and decay_halflife > 0:
        combined = combined.ewm(halflife=decay_halflife).mean()

    return combined
