"""Composite Return aggregation.

Aggregates individual account or strategy return streams into GIPS-compliant composites.
"""
from __future__ import annotations

import pandas as pd


def compute_composite_return(
    strategy_returns: dict[str, pd.Series],
    strategy_aums: dict[str, pd.Series]
) -> pd.Series:
    """Compute AUM-weighted composite returns across multiple strategies."""
    # Find all dates
    all_dates = pd.DatetimeIndex([])
    for ret in strategy_returns.values():
        all_dates = all_dates.union(ret.index)
    
    all_dates = sorted(all_dates)
    composite = pd.Series(0.0, index=all_dates)
    total_weight = pd.Series(0.0, index=all_dates)

    for name, rets in strategy_returns.items():
        aums = strategy_aums.get(name, pd.Series(1.0, index=rets.index))
        # Align
        aligned_rets = rets.reindex(all_dates, fill_value=0.0)
        aligned_aums = aums.reindex(all_dates, fill_value=0.0)

        composite += aligned_rets * aligned_aums
        total_weight += aligned_aums

    # Avoid division by zero
    total_weight = total_weight.replace(0.0, 1.0)
    return composite / total_weight
