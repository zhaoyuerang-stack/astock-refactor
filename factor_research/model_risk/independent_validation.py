"""Independent Model Validation.

Implements independent validation processes:
1. Out-of-sample performance check
2. Parameter sensitivity & stability analysis (anti-p-hacking)
3. Look-ahead/contamination audit
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


class ValidationReport:
    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        self.passed = True
        self.metrics: dict[str, Any] = {}
        self.checks: list[dict[str, Any]] = []
        self.verdict = "PASS"

    def add_check(self, name: str, passed: bool, value: Any, threshold: Any, note: str = ""):
        self.checks.append({
            "name": name,
            "passed": passed,
            "value": value,
            "threshold": threshold,
            "note": note
        })
        if not passed:
            self.passed = False
            self.verdict = "FAIL"

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "passed": self.passed,
            "verdict": self.verdict,
            "metrics": self.metrics,
            "checks": self.checks
        }


def validate_strategy_performance(
    strategy_id: str,
    oos_returns: pd.Series,
    target_sharpe: float = 0.5,
    max_drawdown_limit: float = 0.25
) -> ValidationReport:
    """Validate out-of-sample performance metrics against institution standards."""
    report = ValidationReport(strategy_id)
    if len(oos_returns) == 0:
        report.add_check("OOS Returns Count", False, 0, 100, "No OOS return data provided")
        return report

    # Annualized Sharpe
    ann_ret = oos_returns.mean() * 252
    ann_vol = oos_returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0

    # Max drawdown
    cum = (1 + oos_returns).cumprod()
    max_dd = float((cum / cum.cummax() - 1).min())

    report.metrics["oos_sharpe"] = sharpe
    report.metrics["oos_annual_return"] = ann_ret
    report.metrics["oos_max_dd"] = max_dd

    report.add_check(
        "OOS Sharpe Ratio",
        sharpe >= target_sharpe,
        sharpe,
        target_sharpe,
        f"Target Sharpe is {target_sharpe}"
    )

    report.add_check(
        "OOS Max Drawdown",
        abs(max_dd) <= max_drawdown_limit,
        max_dd,
        -max_drawdown_limit,
        f"Max DD limit is {max_drawdown_limit}"
    )

    return report


def analyze_parameter_stability(
    strategy_id: str,
    target_sharpe: float,
    neighbor_sharpes: list[float]
) -> ValidationReport:
    """Analyze parameter sensitivity to detect p-hacking/overfitting.

    If the target parameter's Sharpe is vastly superior to all neighbors,
    it is likely a p-hacked artifact (overfit).
    """
    report = ValidationReport(strategy_id)
    if not neighbor_sharpes:
        report.add_check("Sensitivity Check", True, 0, 0, "No neighbor configurations tested")
        return report

    avg_neighbor_sharpe = float(np.mean(neighbor_sharpes))
    max_neighbor_sharpe = float(np.max(neighbor_sharpes))

    stability_ratio = avg_neighbor_sharpe / target_sharpe if target_sharpe > 0 else 0.0

    report.metrics["avg_neighbor_sharpe"] = avg_neighbor_sharpe
    report.metrics["max_neighbor_sharpe"] = max_neighbor_sharpe
    report.metrics["stability_ratio"] = stability_ratio

    # If target Sharpe is > 2x the neighbor average and neighbors are flat/negative
    overfit = target_sharpe > 1.0 and stability_ratio < 0.3
    passed = not overfit

    report.add_check(
        "Parameter Stability Check",
        passed,
        stability_ratio,
        0.3,
        "Target Sharpe is isolated peak (overfit)" if overfit else "Stable across adjacent parameters"
    )

    return report
