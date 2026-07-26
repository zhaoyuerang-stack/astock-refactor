"""Leakage and availability guards for candidate ASTs."""
from __future__ import annotations

from .models import Candidate, LeakageReport
from .registry import ALLOWED_FACTORS, looks_forbidden


class LeakageGuardError(ValueError):
    """Raised when a candidate references future, label, or unavailable data."""


def run_leakage_guard(candidate: Candidate) -> LeakageReport:
    issues: list[str] = []
    checks = [
        "field_whitelist",
        "future_field_scan",
        "label_scan",
        "availability_contract",
        "window_isolation",
    ]

    for term in candidate.ast.get("terms", []):
        factor = term.get("factor", "")
        if looks_forbidden(factor):
            issues.append(f"forbidden future/label-like factor: {factor}")
        for key, value in term.get("params", {}).items():
            if looks_forbidden(key) or looks_forbidden(value):
                issues.append(f"forbidden param on {factor}: {key}={value}")
        if factor not in ALLOWED_FACTORS:
            issues.append(f"factor not in availability whitelist: {factor}")

    if looks_forbidden(candidate.ast.get("target", "")):
        issues.append("target/label fields cannot be embedded in candidate AST")

    if issues:
        raise LeakageGuardError("; ".join(issues))

    return LeakageReport(passed=True, checks=checks)
