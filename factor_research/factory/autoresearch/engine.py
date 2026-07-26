"""Lite autonomous evaluation loop.

The Lite loop evaluates already-computed L0/L1 summaries. Heavy IC/backtest
execution remains delegated to the existing factory lines so this module never
changes the canonical evaluation口径.
"""
from __future__ import annotations

from .complexity import compute_complexity
from .decision import decide_candidate
from .guards import run_leakage_guard
from .models import Candidate, CandidateDecision, CandidateEvaluationResult, CandidateStatus
from .redundancy import factor_redundancy_score
from .repositories import CandidateRepository, ExperimentLog, ReviewQueue


def _status_for_decision(decision: CandidateDecision) -> CandidateStatus:
    if decision == CandidateDecision.PROMOTE:
        return CandidateStatus.PROMOTED_TO_REVIEW
    if decision == CandidateDecision.SHELVE:
        return CandidateStatus.SHELVED
    if decision == CandidateDecision.DISCARD:
        return CandidateStatus.DISCARDED
    return CandidateStatus.L1_PASSED


def evaluate_lite(
    candidate: Candidate,
    *,
    l0_metrics: dict,
    l1_metrics: dict,
    redundancy_inputs: dict,
    repository: CandidateRepository | None = None,
    experiment_log: ExperimentLog | None = None,
    review_queue: ReviewQueue | None = None,
) -> CandidateEvaluationResult:
    """Evaluate one candidate through the P1 Lite gates."""
    repository = repository or CandidateRepository()
    experiment_log = experiment_log or ExperimentLog()
    review_queue = review_queue or ReviewQueue()

    repository.add(candidate)
    leakage = run_leakage_guard(candidate)
    complexity = compute_complexity(candidate)
    redundancy = factor_redundancy_score(**redundancy_inputs)

    decision, reason = decide_candidate(
        candidate,
        l0_metrics=l0_metrics,
        l1_metrics=l1_metrics,
        redundancy_inputs=redundancy_inputs,
    )
    status = _status_for_decision(decision)
    updated = candidate.with_status(status, notes=reason)

    metrics = {
        "l0": l0_metrics,
        "l1": l1_metrics,
        "complexity": {
            "score": complexity.score,
            "max_auto_stage": complexity.max_auto_stage,
            "reasons": complexity.reasons,
        },
        "leakage": {"passed": leakage.passed, "checks": leakage.checks},
        "redundancy": {
            "score": redundancy.score,
            "components": redundancy.components,
        },
    }
    result = CandidateEvaluationResult(
        fingerprint=candidate.fingerprint,
        status=status,
        decision=decision,
        metrics=metrics,
        reason=reason,
    )

    repository.record(updated)
    experiment_log.append(result)
    if decision == CandidateDecision.PROMOTE:
        review_queue.add(updated, result)

    return result
