"""Model Limitations & Boundary Conditions.

Defines rules and limitations for models, including style drift boundaries,
AUM constraints, and market regime restrictions.
"""
from __future__ import annotations

from typing import Any


class LimitationCheck:
    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self.breached = False
        self.details: list[dict[str, Any]] = []

    def check_style_drift(self, exposures: dict[str, float], limits: dict[str, float]):
        """Ensure exposures to style factor components (e.g. Size, Beta, Value) do not exceed budget."""
        for factor, value in exposures.items():
            limit = limits.get(factor, 0.5)
            if abs(value) > limit:
                self.breached = True
                self.details.append({
                    "type": "style_drift",
                    "factor": factor,
                    "value": value,
                    "limit": limit,
                    "status": "BREACHED"
                })
            else:
                self.details.append({
                    "type": "style_drift",
                    "factor": factor,
                    "value": value,
                    "limit": limit,
                    "status": "OK"
                })

    def check_capacity_limit(self, current_aum: float, capacity_limit: float):
        """Verify if the strategy AUM exceeds its theoretical capacity."""
        if current_aum > capacity_limit:
            self.breached = True
            self.details.append({
                "type": "capacity_limit",
                "current_aum": current_aum,
                "limit": capacity_limit,
                "status": "BREACHED"
            })
        else:
            self.details.append({
                "type": "capacity_limit",
                "current_aum": current_aum,
                "limit": capacity_limit,
                "status": "OK"
            })

    def check_regime_applicability(self, current_regime: str, applicable_regimes: list[str]):
        """Ensure the strategy is only running in approved market regimes."""
        if current_regime not in applicable_regimes:
            self.breached = True
            self.details.append({
                "type": "regime_applicability",
                "current_regime": current_regime,
                "applicable_regimes": applicable_regimes,
                "status": "BREACHED"
            })
        else:
            self.details.append({
                "type": "regime_applicability",
                "current_regime": current_regime,
                "applicable_regimes": applicable_regimes,
                "status": "OK"
            })

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "breached": self.breached,
            "details": self.details
        }
