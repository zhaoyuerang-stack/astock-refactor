"""Bridge AutoResearch candidates into the real factory L0/L1/L2/L3 lines."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from factory.ontology import (
    Decision,
    EconomicThesis,
    Experiment,
    ExperimentProtocol,
    Hypothesis,
    HypothesisStatus,
)

from .complexity import compute_complexity
from .guards import run_leakage_guard
from .models import Candidate, CandidateDecision, CandidateEvaluationResult, CandidateStatus
from .registry import ALLOWED_FACTORS
from .repositories import CandidateRepository, ExperimentLog, ReviewQueue


def _deps_for_ast(ast: dict[str, Any]) -> tuple[str, ...]:
    deps = {"price/close"}  # compute_dsl_factor always receives close as first arg.
    for term in ast.get("terms", []):
        spec = ALLOWED_FACTORS.get(term.get("factor"))
        if spec:
            deps.update(spec.data_dependencies)
    return tuple(sorted(deps))


def ast_to_hypothesis(candidate: Candidate) -> Hypothesis:
    thesis = candidate.ast.get("thesis", {})
    return Hypothesis(
        name=f"autoresearch_{candidate.fingerprint[:8]}",
        description="AutoResearch controlled DSL candidate",
        factor_fn_name="factors.autoresearch_dsl.compute_dsl_factor",
        factor_params={"ast": candidate.ast},
        data_dependencies=_deps_for_ast(candidate.ast),
        thesis=EconomicThesis(
            mechanism=str(thesis.get("mechanism", "")),
            citation=str(thesis.get("citation", "autoresearch")),
            falsifiability=str(thesis.get("falsifiability", "fails L0-L3 validation gates")),
        ),
        source="autoresearch",
        source_ref=candidate.fingerprint,
        novelty_score=0.0,
        estimated_cost_seconds=0.0,
        # QUEUED 而非 DRAFTED:候选已过 DSL 校验+泄露守卫+复杂度预算,等价于
        # factory_cli queue 后的状态;run_l0 的 F-2 铁律要求进 L0 时必须是 QUEUED。
        status=HypothesisStatus.QUEUED,
        created_at=date.today().isoformat(),
    )


def _default_runners() -> dict[str, Callable]:
    from factory.lines.line2_validation.l0_ic_scan import run_l0
    from factory.lines.line2_validation.l1_quick_bt import run_l1
    from factory.lines.line2_validation.l2_multi_regime import run_l2
    from factory.lines.line2_validation.l3_walk_forward import run_l3

    return {"l0": run_l0, "l1": run_l1, "l2": run_l2, "l3": run_l3}


def _direction_from_l0(exp: Experiment) -> int:
    detail = exp.result.details.get("direction")
    if detail == "short":
        return -1
    return 1


def _status_after_protocol(protocol: ExperimentProtocol, decision: Decision) -> CandidateStatus:
    if decision == Decision.DISCARD:
        return CandidateStatus.DISCARDED
    if decision == Decision.SHELVE:
        return CandidateStatus.SHELVED
    if protocol == ExperimentProtocol.L0_IC_SCAN:
        return CandidateStatus.L0_PASSED
    if protocol == ExperimentProtocol.L1_QUICK_BT:
        return CandidateStatus.L1_PASSED
    if protocol == ExperimentProtocol.L2_MULTI_REGIME:
        return CandidateStatus.L2_PASSED
    if protocol == ExperimentProtocol.L3_WALK_FORWARD:
        return CandidateStatus.PROMOTED_TO_REVIEW if decision == Decision.PROMOTE else CandidateStatus.SHELVED
    return CandidateStatus.GENERATED


def _hyp_with_status(hyp: Hypothesis, status: HypothesisStatus) -> Hypothesis:
    return Hypothesis(
        name=hyp.name,
        description=hyp.description,
        factor_fn_name=hyp.factor_fn_name,
        factor_params=hyp.factor_params,
        timing_fn_name=hyp.timing_fn_name,
        timing_params=hyp.timing_params,
        data_dependencies=hyp.data_dependencies,
        thesis=hyp.thesis,
        source=hyp.source,
        source_ref=hyp.source_ref,
        parent_hypothesis_id=hyp.parent_hypothesis_id,
        novelty_score=hyp.novelty_score,
        estimated_cost_seconds=hyp.estimated_cost_seconds,
        status=status,
        created_at=hyp.created_at,
    )


def _candidate_decision_for_status(status: CandidateStatus) -> CandidateDecision:
    if status == CandidateStatus.PROMOTED_TO_REVIEW:
        return CandidateDecision.PROMOTE
    if status == CandidateStatus.SHELVED:
        return CandidateDecision.SHELVE
    if status == CandidateStatus.DISCARDED:
        return CandidateDecision.DISCARD
    return CandidateDecision.KEEP


def run_validation_pipeline(
    candidate: Candidate,
    *,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    forward_ret: pd.DataFrame,
    vintage_id: str,
    repository: CandidateRepository | None = None,
    experiment_log: ExperimentLog | None = None,
    review_queue: ReviewQueue | None = None,
    runners: dict[str, Callable] | None = None,
    sample_dates: int | None = None,
    max_stage: str = "l3",
    knowledge_graph=None,
    computation_time_budget: float = 10.0,
) -> CandidateEvaluationResult:
    """Run a candidate through the existing real L0/L1/L2/L3 validation functions."""
    repository = repository or CandidateRepository()
    experiment_log = experiment_log or ExperimentLog()
    review_queue = review_queue or ReviewQueue()
    runners = runners or _default_runners()

    repository.add(candidate)
    leakage = run_leakage_guard(candidate)
    complexity = compute_complexity(candidate)
    if complexity.max_auto_stage == "review_only":
        status = CandidateStatus.SHELVED
        result = CandidateEvaluationResult(
            fingerprint=candidate.fingerprint,
            status=status,
            decision=CandidateDecision.SHELVE,
            metrics={"complexity": {"score": complexity.score, "reasons": complexity.reasons}},
            reason="complexity requires human review before real validation",
        )
        repository.record(candidate.with_status(status, result.reason))
        experiment_log.append(result)
        return result

    hyp = ast_to_hypothesis(candidate)
    if knowledge_graph is None:
        try:
            from knowledge.graph import load_graph
            knowledge_graph = load_graph()
        except Exception:
            knowledge_graph = None

    knowledge_gate = {"action": "", "reason": "", "priority_adjustment": 1.0}
    if knowledge_graph is not None:
        try:
            should_skip, skip_reason = knowledge_graph.should_skip(hyp)
            priority_adjustment = float(knowledge_graph.priority_adjustment(hyp))
            knowledge_gate = {
                "action": "SKIP" if should_skip else ("DEPRIORITIZE" if priority_adjustment < 1.0 else ""),
                "reason": skip_reason,
                "priority_adjustment": priority_adjustment,
            }
            if should_skip:
                status = CandidateStatus.DISCARDED
                result = CandidateEvaluationResult(
                    fingerprint=candidate.fingerprint,
                    status=status,
                    decision=CandidateDecision.DISCARD,
                    metrics={
                        "complexity": {"score": complexity.score, "max_auto_stage": complexity.max_auto_stage},
                        "leakage": {"passed": leakage.passed, "checks": leakage.checks},
                        "knowledge_gate": knowledge_gate,
                    },
                    reason=f"knowledge graph skip: {skip_reason}",
                )
                repository.record(candidate.with_status(status, result.reason))
                experiment_log.append(result)
                return result
        except Exception as exc:
            knowledge_gate = {
                "action": "ERROR",
                "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                "priority_adjustment": 1.0,
            }
    experiments: list[Experiment] = []
    status = CandidateStatus.GENERATED
    reason = ""
    direction = 1

    stage_order = ["l0", "l1", "l2", "l3"]
    stop_at = stage_order.index(max_stage) if max_stage in stage_order else len(stage_order) - 1

    # Pre-calculate factor panel once at the pipeline level to share across all validation stages (L0, L1, L2, L3)
    cached_factor = None
    try:
        from factory.lines.line2_validation.l1_quick_bt import _dispatch_args, _resolve_factor_fn
        fn = _resolve_factor_fn(hyp.factor_fn_name)
        args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
        cached_factor = fn(*args, **hyp.factor_params)
    except Exception:
        pass

    for stage in stage_order[: stop_at + 1]:
        if stage == "l0":
            exp = runners[stage](hyp, close, volume, amount, forward_ret, vintage_id=vintage_id, sample_dates=sample_dates, factor=cached_factor)
            if exp.cost_spent_seconds > computation_time_budget:
                from dataclasses import replace
                exp = replace(
                    exp,
                    decision=Decision.DISCARD,
                    notes=f"computation time budget exceeded ({exp.cost_spent_seconds:.2f} s > {computation_time_budget} s)"
                )
            if exp.decision == Decision.PROMOTE:
                hyp = _hyp_with_status(hyp, HypothesisStatus.L0_PASSED)
                direction = _direction_from_l0(exp)
        elif stage == "l1":
            exp = runners[stage](hyp, close, volume, amount, direction=direction, vintage_id=vintage_id, factor=cached_factor)
            if exp.decision == Decision.PROMOTE:
                hyp = _hyp_with_status(hyp, HypothesisStatus.L1_PASSED)
        elif stage == "l2":
            exp = runners[stage](hyp, close, volume, amount, direction=direction, vintage_id=vintage_id, factor=cached_factor)
            if exp.decision == Decision.PROMOTE:
                hyp = _hyp_with_status(hyp, HypothesisStatus.L2_PASSED)
        else:
            exp = runners[stage](hyp, close, volume, amount, direction=direction, vintage_id=vintage_id, factor=cached_factor)

        experiments.append(exp)
        status = _status_after_protocol(exp.protocol, exp.decision)
        reason = exp.notes
        if exp.decision in (Decision.DISCARD, Decision.SHELVE):
            break

    # 弃牌堆分诊:L1 死亡但 L0 信息量强的候选,标记进 VetoFilter 边际贡献复评
    # (只做标记,不改主线状态;复评走 scripts/research/veto_filter_marginal.py)
    veto_review = False
    if len(experiments) >= 2:
        from factory.lines.line2_validation.veto_triage import should_route_to_veto_review

        veto_review = should_route_to_veto_review(experiments[0], experiments[1])
    if veto_review:
        reason = (reason + "; " if reason else "") + "L0 信息量强,转 VetoFilter 复评"

    metrics = {
        "complexity": {"score": complexity.score, "max_auto_stage": complexity.max_auto_stage},
        "leakage": {"passed": leakage.passed, "checks": leakage.checks},
        "knowledge_gate": knowledge_gate,
        "veto_review_candidate": veto_review,
        "experiments": [
            {
                "experiment_id": e.experiment_id,
                "protocol": e.protocol.value,
                "decision": e.decision.value,
                "metrics": e.result.metrics,
                "details": e.result.details,
                "error": e.result.error,
                "notes": e.notes,
                "cost_spent_seconds": float(e.cost_spent_seconds) if e.cost_spent_seconds is not None else 0.0,
            }
            for e in experiments
        ],
    }
    decision = _candidate_decision_for_status(status)
    result = CandidateEvaluationResult(
        fingerprint=candidate.fingerprint,
        status=status,
        decision=decision,
        metrics=metrics,
        reason=reason,
    )

    updated = candidate.with_status(status, reason)
    repository.record(updated)
    experiment_log.append(result)
    if status == CandidateStatus.PROMOTED_TO_REVIEW:
        review_queue.add(updated, result)
    return result
