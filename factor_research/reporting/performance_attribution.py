"""Performance Attribution.

Attributes strategy returns to standard factor exposures (Beta, Size),
industry sectors, and stock-specific selection (alpha).
"""
from __future__ import annotations

import pandas as pd


def attribute_returns(
    weights: pd.DataFrame,                 # date x asset
    returns: pd.DataFrame,                 # date x asset
    exposures: dict[str, pd.DataFrame],    # factor_name -> date x asset
    benchmark_returns: pd.Series | None = None
) -> pd.DataFrame:
    """Attribute daily portfolio returns to systematic style factors and alpha."""
    common_idx = weights.index.intersection(returns.index)
    attribution = pd.DataFrame(index=common_idx)
    
    portfolio_ret = (weights * returns).sum(axis=1)
    attribution["portfolio_return"] = portfolio_ret
    
    # Benchmark return
    if benchmark_returns is not None:
        attribution["benchmark_return"] = benchmark_returns.reindex(common_idx, fill_value=0.0)
        attribution["active_return"] = attribution["portfolio_return"] - attribution["benchmark_return"]
    else:
        attribution["active_return"] = attribution["portfolio_return"]

    # Attribute to each style factor
    systematic_sum = pd.Series(0.0, index=common_idx)
    for factor_name, f_df in exposures.items():
        # Align factor exposures
        f_aligned = f_df.reindex(index=common_idx, columns=weights.columns, fill_value=0.0)
        
        # Portfolio exposure: sum(w_i * exposure_i)
        port_exp = (weights * f_aligned).sum(axis=1)
        
        # Approximate factor returns (simple cross-sectional regression projection or returns correlation)
        # Using correlation of factor exposure with stock returns as proxy of factor returns
        f_returns = (f_aligned * returns).mean(axis=1)
        
        attribution[f"factor_exp_{factor_name}"] = port_exp
        attribution[f"factor_ret_{factor_name}"] = port_exp * f_returns
        systematic_sum += attribution[f"factor_ret_{factor_name}"]

    attribution["systematic_return"] = systematic_sum
    attribution["selection_return"] = attribution["portfolio_return"] - systematic_sum
    
    return attribution
