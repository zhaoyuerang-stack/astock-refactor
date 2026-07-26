"""Cost-Aware Portfolio Rebalancing.

Manages rebalancing events, calculating the target trades while considering
transaction costs, market impact, and constraints.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio.optimizer import PortfolioOptimizer


class CostAwareRebalancer:
    def __init__(self, optimizer: PortfolioOptimizer | None = None):
        self.optimizer = optimizer or PortfolioOptimizer()

    def rebalance(
        self,
        current_weights: pd.Series,
        alpha_forecast: pd.Series,
        risk_exposures: pd.DataFrame,          # stocks x factors
        factor_cov: pd.DataFrame,              # factors x factors
        specific_var: pd.Series,              # stocks
        tradable_flags: pd.Series | None = None,
        buy_sell_costs: pd.Series | None = None,
        market_impact_coefs: pd.Series | None = None,
        target_sum: float = 1.0
    ) -> pd.Series:
        """Run rebalance optimization to find the new portfolio weights.

        Aligns all input series and DataFrames to ensure asset index consistency.
        """
        # Align all assets
        assets = alpha_forecast.index
        
        # Align current weights
        w0 = current_weights.reindex(assets, fill_value=0.0).values

        # Align alphas
        alpha_val = alpha_forecast.values

        # Align exposures: assets x factors
        B = risk_exposures.reindex(assets, fill_value=0.0).values
        F = factor_cov.values
        d = specific_var.reindex(assets, fill_value=0.0002).values

        # Tradability
        if tradable_flags is not None:
            flags = tradable_flags.reindex(assets, fill_value=True).values
        else:
            flags = np.ones(len(assets), dtype=bool)

        # Costs
        if buy_sell_costs is not None:
            costs = buy_sell_costs.reindex(assets, fill_value=0.0025).values
        else:
            costs = np.ones(len(assets)) * 0.0025

        if market_impact_coefs is not None:
            impacts = market_impact_coefs.reindex(assets, fill_value=0.0).values
        else:
            impacts = np.zeros(len(assets))

        # Solve
        w_opt = self.optimizer.optimize(
            alpha=alpha_val,
            initial_weights=w0,
            exposures=B,
            factor_cov=F,
            specific_var=d,
            tradable_flags=flags,
            buy_sell_costs=costs,
            market_impact_coefs=impacts,
            target_sum=target_sum
        )

        return pd.Series(w_opt, index=assets)
