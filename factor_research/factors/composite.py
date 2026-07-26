"""Canonical composite factor formulas."""
from __future__ import annotations

import pandas as pd

from factors.small_cap import small_cap_factor
from factors.utils import mad_clip, safe_zscore


def size_earnings_factor(
    amount: pd.DataFrame,
    net_profit_yoy: pd.DataFrame,
    *,
    size_window: int = 60,
    blend_weight: float = 0.5,
) -> pd.DataFrame:
    """Blend small-cap size and PIT net-profit growth into one factor panel."""
    size = small_cap_factor(amount, window=size_window)
    if net_profit_yoy.empty:
        return size
    npy = net_profit_yoy.reindex(index=amount.index, columns=amount.columns).ffill()
    npy_z = safe_zscore(mad_clip(npy))
    return safe_zscore(mad_clip(blend_weight * size + (1.0 - blend_weight) * npy_z))
