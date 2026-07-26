"""Factory discard-pile triage helpers."""
from __future__ import annotations

from enum import Enum


class TriageDecision(Enum):
    IGNORE = "ignore"
    VETO_REVIEW = "veto_review"


def route_failed_candidate(
    *,
    l0_icir: float | None,
    l1_decision: str,
    l1_reason: str = "",
    threshold: float = 0.5,
) -> TriageDecision:
    """Route strong-information L1 failures into control-rule review."""
    del l1_reason  # kept for future ledger enrichment without changing API
    if l1_decision.lower() != "discard":
        return TriageDecision.IGNORE
    try:
        if abs(float(l0_icir)) >= threshold:
            return TriageDecision.VETO_REVIEW
    except (TypeError, ValueError):
        return TriageDecision.IGNORE
    return TriageDecision.IGNORE
