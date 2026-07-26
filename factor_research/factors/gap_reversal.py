"""Gap reversal factors — exploit A-share retail overreaction to overnight gaps.

Disposition: dormant — 零消费者(未接 catalog/DSL/白名单,无脚本引用);复活须先走 probe-signal-source 体检(R-ARCH-005 精神)。

A-share is retail-dominated (~80% volume). Retail investors overreact to
overnight price gaps: chasing gap-ups and panic-selling gap-downs. This
creates predictable mean-reversion over the following 1-5 days.

All factors use open price (previously unused in this codebase).
"""
import numpy as np
import pandas as pd

from factors.utils import mad_clip, safe_zscore


def overnight_gap(open_price: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """Daily overnight gap = open(t) / close(t-1) - 1.

    Positive = gapped up, negative = gapped down.
    """
    prev_close = close.shift(1)
    return open_price / prev_close - 1


def gap_reversal(gap: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Gap reversal signal: fade the average overnight gap over N days.

    Buy stocks that have been gapping down (negative gap → positive signal).
    Sell/underweight stocks that have been gapping up (positive gap → negative signal).

    This is a mean-reversion bet on retail overreaction.
    """
    avg_gap = gap.rolling(window, min_periods=3).mean()
    return -avg_gap  # fade the gap: buy gap-down, sell gap-up


def gap_reversal_zscore(open_price: pd.DataFrame, close: pd.DataFrame,
                         window: int = 5) -> pd.DataFrame:
    """Convenience: overnight gap → reversal signal → cross-sectional zscore."""
    gap = overnight_gap(open_price, close)
    signal = gap_reversal(gap, window=window)
    return safe_zscore(mad_clip(signal))


def gap_magnitude_filter(gap: pd.DataFrame, threshold: float = 0.02) -> pd.DataFrame:
    """Filter: only trade stocks with meaningful gaps (>threshold absolute)."""
    return gap.abs() > threshold


def gap_volume_confirmation(gap: pd.DataFrame, volume: pd.DataFrame,
                             vol_window: int = 20) -> pd.DataFrame:
    """Volume confirmation: gap is more meaningful when volume > recent avg.

    Returns a multiplier in [0.5, 1.5] — higher for high-vol gaps.
    """
    avg_vol = volume.rolling(vol_window).mean()
    vol_ratio = volume / avg_vol.replace(0, np.nan)
    # Clamp to reasonable range
    return vol_ratio.clip(0.5, 1.5)
