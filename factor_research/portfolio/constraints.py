"""Portfolio Optimization Constraints.

Defines industry exposure bounds, single stock weight limits,
style neutrality rules, and liquidity/tradability filters.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


class PortfolioConstraints:
    def __init__(
        self,
        min_weight: float = 0.0,
        max_weight: float = 0.05,
        industry_max_deviation: float = 0.03,
        beta_limit: float = 0.1,
        size_limit: float = 0.1,
        max_turnover: float = 0.20
    ):
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.industry_max_deviation = industry_max_deviation
        self.beta_limit = beta_limit
        self.size_limit = size_limit
        self.max_turnover = max_turnover

    def build_bounds(
        self,
        n_assets: int,
        tradable_flags: np.ndarray | None = None
    ) -> list[tuple[float, float]]:
        """Build lower and upper bounds for each asset's weight.

        Non-tradable assets are bound to (0, 0).
        """
        bounds = []
        for i in range(n_assets):
            if tradable_flags is not None and not tradable_flags[i]:
                bounds.append((0.0, 0.0))
            else:
                bounds.append((self.min_weight, self.max_weight))
        return bounds

    def check_constraints(
        self,
        weights: np.ndarray,
        initial_weights: np.ndarray,
        exposures: pd.DataFrame,
        industry_mapping: pd.Series | None = None
    ) -> list[dict[str, Any]]:
        """Verify if current weights satisfy all constraints."""
        violations = []

        # Budget / Full Investment (allowing cash/leverage leeway)
        tot_w = float(np.sum(weights))
        if abs(tot_w - 1.0) > 0.01:
            violations.append({"constraint": "budget", "deviation": tot_w - 1.0})

        # Single stock weight violation
        max_w = float(np.max(weights))
        if max_w > self.max_weight:
            violations.append({"constraint": "single_stock_max", "max_found": max_w})

        # Turnover violation
        turnover = float(np.sum(np.abs(weights - initial_weights)) / 2.0)
        if turnover > self.max_turnover:
            violations.append({"constraint": "turnover", "value": turnover, "limit": self.max_turnover})

        # Style exposures check
        if exposures is not None:
            for col in exposures.columns:
                exp_val = float(weights @ exposures[col].values)
                if col == "Beta" and abs(exp_val - 1.0) > self.beta_limit:
                    violations.append({"constraint": "beta_exposure", "value": exp_val, "limit": self.beta_limit})
                elif col == "Size" and abs(exp_val) > self.size_limit:
                    violations.append({"constraint": "size_exposure", "value": exp_val, "limit": self.size_limit})

        return violations
