"""Portfolio Optimizer.

Solves the multi-objective portfolio construction problem:
    max w^T * alpha - l1 * factor_risk - l2 * specific_risk - l3 * trans_cost - l4 * market_impact - l5 * turnover_penalty
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from portfolio.constraints import PortfolioConstraints


class PortfolioOptimizer:
    def __init__(
        self,
        l1_factor_risk: float = 1.0,
        l2_specific_risk: float = 1.5,
        l3_trans_cost: float = 0.5,
        l4_market_impact: float = 0.5,
        l5_turnover_penalty: float = 0.2,
        constraints: PortfolioConstraints | None = None
    ):
        self.l1 = l1_factor_risk
        self.l2 = l2_specific_risk
        self.l3 = l3_trans_cost
        self.l4 = l4_market_impact
        self.l5 = l5_turnover_penalty
        self.constraints = constraints or PortfolioConstraints()

    def optimize(
        self,
        alpha: np.ndarray,                     # expected return vector
        initial_weights: np.ndarray,           # previous portfolio weights
        exposures: np.ndarray,                 # factor loading matrix (assets x factors)
        factor_cov: np.ndarray,                # factor covariance matrix (factors x factors)
        specific_var: np.ndarray,              # specific variance vector (assets)
        tradable_flags: np.ndarray | None = None,
        buy_sell_costs: np.ndarray | None = None, # cost per asset (e.g. commission + spread)
        market_impact_coefs: np.ndarray | None = None, # coefficient for square-root market impact
        target_sum: float = 1.0                # target leverage
    ) -> np.ndarray:
        """Solve the optimization problem to find the target weights.

        Uses SLSQP solver for convex optimization.
        """
        n_assets = len(alpha)
        
        # Initial guess: equal-weight or initial weights
        if np.sum(initial_weights) > 0:
            x0 = initial_weights.copy()
        else:
            x0 = np.ones(n_assets) / n_assets
            if tradable_flags is not None:
                x0[~tradable_flags] = 0.0
                if np.sum(x0) > 0:
                    x0 = x0 / np.sum(x0)

        # Objective function to minimize (negative of utility)
        def objective(w: np.ndarray) -> float:
            # 1. Expected return (Alpha)
            utility_alpha = np.dot(w, alpha)

            # 2. Factor Risk: (B^T w)^T * F * (B^T w)
            factor_exp = np.dot(exposures.T, w)
            factor_risk = np.dot(factor_exp, np.dot(factor_cov, factor_exp))

            # 3. Specific Risk: \sum w_i^2 * specific_var_i
            specific_risk = np.sum((w ** 2) * specific_var)

            # 4. Transaction Cost: \sum buy_sell_cost_i * |w_i - w_0|
            trades = w - initial_weights
            if buy_sell_costs is not None:
                trans_cost = np.sum(buy_sell_costs * np.abs(trades))
            else:
                trans_cost = 0.0025 * np.sum(np.abs(trades))

            # 5. Market Impact (non-linear power model, usually w^1.5 or w^2)
            if market_impact_coefs is not None:
                market_impact = np.sum(market_impact_coefs * (np.abs(trades) ** 1.5))
            else:
                market_impact = 0.0

            # 6. Turnover Penalty
            turnover_pen = np.sum(np.abs(trades))

            # Net Utility
            net_utility = (
                utility_alpha
                - self.l1 * factor_risk
                - self.l2 * specific_risk
                - self.l3 * trans_cost
                - self.l4 * market_impact
                - self.l5 * turnover_pen
            )
            return -net_utility

        # Equality constraint: sum of weights = target_sum
        def weight_sum_constraint(w: np.ndarray) -> float:
            return np.sum(w) - target_sum

        cons = [
            {"type": "eq", "fun": weight_sum_constraint}
        ]

        # Style constraint boundaries: e.g. Size and Beta exposure limitations
        # We can implement them as linear inequality constraints: -limit <= w^T B_c <= limit
        # For simplicity in optimization, we can add them to cons:
        # B^T w - limit_low >= 0, limit_high - B^T w >= 0
        if exposures is not None:
            # Enforce constraints on the first few columns if named Beta/Size
            # For simplicity, we just allow the objective function penalties or explicit boundary limits
            pass

        # Bounds (box constraints for each asset)
        bounds = self.constraints.build_bounds(n_assets, tradable_flags)

        # Solve
        res = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": 100, "ftol": 1e-6}
        )

        if not res.success:
            # Fallback to initial weights or normalized alpha if optimize fails
            return initial_weights if np.sum(initial_weights) > 0 else x0

        # Post-process weights to force exact constraints or zeroing out very small values
        w_opt = res.x
        w_opt[w_opt < 1e-5] = 0.0
        
        # Re-scale to target_sum if active
        sum_opt = np.sum(w_opt)
        if sum_opt > 0:
            w_opt = w_opt * (target_sum / sum_opt)

        return w_opt
