"""Transform functions for the factor expression system.

Each transform is a pure function:  pd.DataFrame → pd.DataFrame
They operate on (date × code) wide-format DataFrames.
"""
import numpy as np
import pandas as pd

from factors.alpha.base import register_transform

# ---------------------------------------------------------------------------
# Core transforms
# ---------------------------------------------------------------------------

def zscore_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional z-score (row-wise, date by date)."""
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-8, axis=0)


@register_transform("zscore")
def zscore(df: pd.DataFrame) -> pd.DataFrame:
    """Backward-compatible DSL name for row-wise cross-sectional z-score."""
    return zscore_cross_section(df)


@register_transform("mad_clip")
def mad_clip(df: pd.DataFrame, n: float = 5.0) -> pd.DataFrame:
    """Row-wise MAD outlier clipping."""
    med = df.median(axis=1)
    mad = df.sub(med, axis=0).abs().median(axis=1)
    return df.clip(lower=med - n * mad, upper=med + n * mad, axis=0)


@register_transform("rank")
def rank_transform(df: pd.DataFrame, ascending: bool = True) -> pd.DataFrame:
    """Cross-sectional percentile rank (0–1)."""
    ranked = df.rank(axis=1, pct=True, ascending=ascending)
    return ranked


@register_transform("shift")
def shift(df: pd.DataFrame, periods: int = 1) -> pd.DataFrame:
    """Lag values to prevent look-ahead bias."""
    return df.shift(periods)


@register_transform("rolling_mean")
def rolling_mean(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Rolling mean over time (within each code)."""
    return df.rolling(window).mean()


@register_transform("rolling_std")
def rolling_std(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """Rolling standard deviation over time (within each code)."""
    return df.rolling(window).std()


@register_transform("log1p")
def log1p(df: pd.DataFrame) -> pd.DataFrame:
    """log(1 + x)."""
    return np.log1p(df.clip(lower=0))


@register_transform("neg")
def neg(df: pd.DataFrame) -> pd.DataFrame:
    """Negate."""
    return -df


# ---------------------------------------------------------------------------
# Neutralization (A股 essential)
# ---------------------------------------------------------------------------

@register_transform("neutralize")
def neutralize(df: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional group neutralization.

    For each date, subtract the group-mean from each stock's factor value.
    This removes sector/market-cap biases so the factor captures pure
    stock-specific alpha (WorldQuant-style).

    Parameters
    ----------
    df : pd.DataFrame
        Factor values (date × code).
    groups : pd.DataFrame
        Group labels (date × code), same index/columns as df.
        E.g., industry codes or market-cap quantile bins.
    """
    common_idx = df.index.intersection(groups.index)
    common_cols = df.columns.intersection(groups.columns)
    if len(common_idx) == 0 or len(common_cols) == 0:
        return df
    result = df.copy()
    for dt in common_idx:
        vals = df.loc[dt, common_cols]
        grp = groups.loc[dt, common_cols]
        group_mean = vals.groupby(grp).transform("mean")
        result.loc[dt, common_cols] = vals - group_mean
    return result
