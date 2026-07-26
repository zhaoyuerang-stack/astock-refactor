"""Dollar Capacity Modeling.

Estimates the dollar-based assets under management (AUM) capacity limits
for individual factors and portfolio allocations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def estimate_dollar_capacity(
    weights: pd.DataFrame,
    adv: pd.DataFrame,                     # Index (date) x columns (asset) Average Daily Volume (CNY)
    max_participation_rate: float = 0.05,  # Max 5% of ADV
    impact_budget_bps: float = 50.0,       # Max slippage budget in bps
    execution_days: int = 2
) -> float:
    """Estimate the strategy's AUM capacity limit in CNY.

    Capacity = min over time of (Portfolio ADV Limit / Portfolio Turnover)
    """
    common_idx = weights.index.intersection(adv.index)
    if len(common_idx) == 0:
        return 0.0

    w_aligned = weights.loc[common_idx]
    adv_aligned = adv.loc[common_idx]

    daily_capacities = []
    
    for date in common_idx:
        w = w_aligned.loc[date]
        v = adv_aligned.loc[date]
        
        # Filter non-zero weights
        active_assets = w[w > 0.0001].index
        if len(active_assets) == 0:
            continue
            
        w_active = w.loc[active_assets]
        v_active = v.reindex(active_assets, fill_value=1e6) # fallback to 1M ADV
        
        # Max trading size per asset based on ADV participation
        max_asset_trade = v_active * max_participation_rate * execution_days
        
        # Portfolio dollar limit: min over active assets of (max_asset_trade / weight)
        # i.e., what is the largest AUM where no asset trade exceeds its ADV budget
        asset_capacities = max_asset_trade / w_active
        portfolio_cap = float(asset_capacities.min())
        
        daily_capacities.append(portfolio_cap)

    return float(np.percentile(daily_capacities, 10)) if daily_capacities else 0.0
