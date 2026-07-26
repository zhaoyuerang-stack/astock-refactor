"""Cheap branch for routing dead L1 candidates to VetoFilter review."""
from __future__ import annotations

from factory.ontology import Experiment, ExperimentProtocol
from research_toolkit import TriageDecision, route_failed_candidate


def should_route_to_veto_review(
    l0_experiment: Experiment,
    l1_experiment: Experiment,
    *,
    icir_threshold: float = 0.5,
) -> bool:
    """Return True when a failed L1 candidate still has strong L0 information.

    This does not promote the candidate and does not touch L0-L3 mainline state.
    It only marks the discard pile as worth a host-scoped VetoFilter marginal
    contribution review.
    """
    if l0_experiment.protocol != ExperimentProtocol.L0_IC_SCAN:
        return False
    if l1_experiment.protocol != ExperimentProtocol.L1_QUICK_BT:
        return False
    raw = l0_experiment.result.details.get("ic_ir")
    if raw is None:
        raw = l0_experiment.result.metrics.get("ICIR")
    routed = route_failed_candidate(
        l0_icir=raw,
        l1_decision=l1_experiment.decision.value,
        l1_reason=l1_experiment.notes or str(l1_experiment.result.details.get("decision_reason", "")),
        threshold=icir_threshold,
    )
    return routed == TriageDecision.VETO_REVIEW
