"""Complexity budget for autonomous candidates."""
from __future__ import annotations

from .models import Candidate, ComplexityReport


def compute_complexity(candidate: Candidate) -> ComplexityReport:
    ast = candidate.ast
    terms = ast.get("terms", [])
    factors = [t.get("factor") for t in terms]
    windows = {
        t.get("params", {}).get("window")
        for t in terms
        if t.get("params", {}).get("window") is not None
    }
    transforms = [op for t in terms for op in t.get("transforms", [])]

    score = 0.0
    reasons: list[str] = []

    factor_score = float(len(set(factors)))
    score += factor_score
    reasons.append(f"base_factors={factor_score:g}")

    window_score = float(max(0, len(windows) - 1))
    score += window_score
    if window_score:
        reasons.append(f"extra_windows={window_score:g}")

    transform_score = len(set(transforms)) / 2.0
    score += transform_score
    if transform_score:
        reasons.append(f"transforms={transform_score:g}")

    neutralize_score = len(ast.get("neutralize", [])) * 0.25
    score += neutralize_score
    if neutralize_score:
        reasons.append(f"neutralize={neutralize_score:g}")

    if ast.get("regime_filter"):
        score += 2.0
        reasons.append("regime_filter=2")

    if ast.get("industry_scope"):
        score += 2.0
        reasons.append("industry_scope=2")

    if ast.get("conditions"):
        score += 2.0
        reasons.append("conditions=2")

    if score <= 5:
        max_stage = "l2"
    elif score <= 8:
        max_stage = "l2_requires_l1_pass"
    else:
        max_stage = "review_only"

    return ComplexityReport(score=round(score, 2), max_auto_stage=max_stage, reasons=reasons)
