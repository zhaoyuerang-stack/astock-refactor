"""Deterministic decision rules for the Lite autonomous loop."""
from __future__ import annotations

from .complexity import compute_complexity
from .models import Candidate, CandidateDecision
from .redundancy import factor_redundancy_score

L0_GATES = {
    "rank_ic_mean": 0.02,
    "icir": 0.3,
    "coverage": 0.80,
    "nan_ratio": 0.05,
    "extreme_ratio": 0.05,
}

L1_GATES = {
    "top_bottom_return": 0.0,
    "cost_after_return": 0.0,
    "turnover": 1.5,
}


def l0_passed(metrics: dict) -> tuple[bool, str]:
    if abs(float(metrics.get("rank_ic_mean", 0.0))) < L0_GATES["rank_ic_mean"]:
        return False, "rank IC below L0 gate"
    if abs(float(metrics.get("icir", 0.0))) < L0_GATES["icir"]:
        return False, "ICIR below L0 gate"
    if float(metrics.get("coverage", 0.0)) < L0_GATES["coverage"]:
        return False, "coverage below L0 gate"
    if float(metrics.get("nan_ratio", 1.0)) > L0_GATES["nan_ratio"]:
        return False, "NaN ratio above L0 gate"
    if float(metrics.get("extreme_ratio", 1.0)) > L0_GATES["extreme_ratio"]:
        return False, "extreme ratio above L0 gate"
    return True, "L0 passed"


def l1_passed(metrics: dict) -> tuple[bool, str]:
    if not metrics.get("monotonic_groups", False):
        return False, "group returns are not monotonic"
    if float(metrics.get("top_bottom_return", 0.0)) <= L1_GATES["top_bottom_return"]:
        return False, "top-bottom return is not positive"
    if float(metrics.get("cost_after_return", 0.0)) <= L1_GATES["cost_after_return"]:
        return False, "cost-after return is not positive"
    if float(metrics.get("turnover", 99.0)) > L1_GATES["turnover"]:
        return False, "turnover above L1 gate"
    return True, "L1 passed"


def decide_candidate(
    candidate: Candidate,
    *,
    l0_metrics: dict,
    l1_metrics: dict,
    redundancy_inputs: dict,
) -> tuple[CandidateDecision, str]:
    complexity = compute_complexity(candidate)
    if complexity.max_auto_stage == "review_only":
        return CandidateDecision.SHELVE, "complexity requires human review"

    ok_l0, reason = l0_passed(l0_metrics)
    if not ok_l0:
        return CandidateDecision.DISCARD, reason

    ok_l1, reason = l1_passed(l1_metrics)
    if not ok_l1:
        return CandidateDecision.DISCARD, reason

    redundancy = factor_redundancy_score(**redundancy_inputs)
    if redundancy.score >= 0.65:
        return CandidateDecision.SHELVE, f"redundancy score {redundancy.score:.2f} too high"

    return CandidateDecision.PROMOTE, "candidate passed Lite gates and is ready for human review"
