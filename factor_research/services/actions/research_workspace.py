"""State-safe actions for the unified research workspace."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, replace
from threading import Lock
from typing import Any

from contracts.views import ActionJobView, ResearchDraftView, ResearchReviewView
from research_ledger.workspace import DraftRepository, ResearchReviewRepository


class WorkItemConflict(RuntimeError):
    """The requested action conflicts with current or in-flight state."""


class InvalidTransition(ValueError):
    """The requested state transition is not valid for this work item."""


def _review_autoresearch_with_generic_ledger(
    *,
    fingerprint: str,
    action: str,
    notes: str = "",
    reviewer: str = "human",
    review_repo: ResearchReviewRepository,
    candidate_repo=None,
    legacy_review_queue=None,
):
    from factory.autoresearch import CandidateRepository, ReviewQueue
    from services.actions.autoresearch import review_autoresearch_candidate

    candidate_repo = candidate_repo if candidate_repo is not None else CandidateRepository()
    legacy_review_queue = legacy_review_queue if legacy_review_queue is not None else ReviewQueue()
    legacy = review_autoresearch_candidate(
        fingerprint=fingerprint,
        action=action,
        notes=notes,
        repository=candidate_repo,
        review_queue=legacy_review_queue,
    )
    record = review_repo.record(
        kind="autoresearch",
        item_id=fingerprint,
        action=action,
        notes=notes,
        reviewer=reviewer,
    )
    return legacy, record


def review_legacy_autoresearch_candidate(
    *,
    fingerprint: str,
    action: str,
    notes: str = "",
    reviewer: str = "human",
    review_repo: ResearchReviewRepository | None = None,
    candidate_repo=None,
    legacy_review_queue=None,
):
    """Keep the legacy response while synchronizing the canonical review ledger."""
    review_repo = review_repo if review_repo is not None else ResearchReviewRepository()
    legacy, _ = _review_autoresearch_with_generic_ledger(
        fingerprint=fingerprint,
        action=action,
        notes=notes,
        reviewer=reviewer,
        review_repo=review_repo,
        candidate_repo=candidate_repo,
        legacy_review_queue=legacy_review_queue,
    )
    return legacy


def queue_draft(
    draft_id: str,
    *,
    draft_repo: DraftRepository | None = None,
    hypothesis_pool=None,
) -> dict:
    from factory.ontology import EconomicThesis, Hypothesis, HypothesisStatus
    from factory.pool.pool_repo import HypothesisPool

    draft_repo = draft_repo if draft_repo is not None else DraftRepository()
    hypothesis_pool = hypothesis_pool if hypothesis_pool is not None else HypothesisPool()
    draft = draft_repo.get(draft_id)
    if draft.status != "active":
        raise WorkItemConflict(f"draft is not active: {draft.status}")
    if not draft.factor_fn_name:
        raise InvalidTransition("draft requires factor_fn_name before queue")
    if not draft.mechanism:
        raise InvalidTransition("draft requires mechanism before queue")

    hypothesis = Hypothesis(
        name=draft.title,
        description=draft.mechanism,
        factor_fn_name=draft.factor_fn_name,
        factor_params=dict(draft.factor_params),
        timing_fn_name=draft.timing_fn_name,
        timing_params=dict(draft.timing_params),
        data_dependencies=tuple(draft.data_dependencies),
        thesis=EconomicThesis(mechanism=draft.mechanism, citation=draft.citation),
        source=draft.source,
        source_ref=draft.draft_id,
        status=HypothesisStatus.QUEUED,
        created_at=draft.created_at,
    )
    hypothesis_pool.add(hypothesis)
    draft_repo.update(
        draft_id,
        status="converted",
        linked_work_id=f"hypothesis:{hypothesis.id}",
    )
    return {"kind": "hypothesis", "item_id": hypothesis.id, "work_id": f"hypothesis:{hypothesis.id}"}


def review_work_item(
    kind: str,
    item_id: str,
    *,
    action: str,
    notes: str = "",
    reviewer: str = "human",
    review_repo: ResearchReviewRepository | None = None,
    hypothesis_pool=None,
    candidate_repo=None,
    legacy_review_queue=None,
) -> ResearchReviewView:
    from factory.ontology import HypothesisStatus
    from factory.pool.pool_repo import HypothesisPool

    if action not in {"approve", "reject"}:
        raise InvalidTransition("review action must be approve or reject")
    review_repo = review_repo if review_repo is not None else ResearchReviewRepository()

    if kind == "hypothesis":
        hypothesis_pool = hypothesis_pool if hypothesis_pool is not None else HypothesisPool()
        item = hypothesis_pool.get(item_id)
        if item is None:
            raise KeyError(item_id)
        if item.status != HypothesisStatus.L3_PASSED:
            raise WorkItemConflict(f"hypothesis is not awaiting review: {item.status.value}")
        if action == "reject":
            hypothesis_pool.update_status(item_id, HypothesisStatus.DISCARDED)
    elif kind == "autoresearch":
        try:
            _, record = _review_autoresearch_with_generic_ledger(
                fingerprint=item_id,
                action=action,
                notes=notes,
                reviewer=reviewer,
                review_repo=review_repo,
                candidate_repo=candidate_repo,
                legacy_review_queue=legacy_review_queue,
            )
        except ValueError as exc:
            raise WorkItemConflict(str(exc)) from exc
        return ResearchReviewView(**asdict(record))
    else:
        raise InvalidTransition(f"review unsupported for kind: {kind}")

    record = review_repo.record(
        kind=kind,
        item_id=item_id,
        action=action,
        notes=notes,
        reviewer=reviewer,
    )
    return ResearchReviewView(**asdict(record))


def promote_work_item(
    kind: str,
    item_id: str,
    *,
    version: str = "v1.0",
    target_status: str = "",
    review_repo: ResearchReviewRepository | None = None,
    hypothesis_pool=None,
    candidate_repo=None,
    legacy_review_queue=None,
    promote_fn: Callable | None = None,
) -> dict:
    from factory.autoresearch import CandidateRepository, ReviewQueue
    from factory.ontology import HypothesisStatus
    from factory.pool.pool_repo import HypothesisPool

    review_repo = review_repo if review_repo is not None else ResearchReviewRepository()
    review = review_repo.latest(kind, item_id)
    if review is None or review.action != "approve":
        raise WorkItemConflict("human approval is required before promotion")

    if kind == "hypothesis":
        hypothesis_pool = hypothesis_pool if hypothesis_pool is not None else HypothesisPool()
        item = hypothesis_pool.get(item_id)
        if item is None:
            raise KeyError(item_id)
        if item.status != HypothesisStatus.L3_PASSED:
            raise WorkItemConflict(f"hypothesis status changed: {item.status.value}")
        if promote_fn is None:
            from workflow.promote import promote_hypothesis
            promote_fn = promote_hypothesis
        report = promote_fn(item, version=version, target_status=target_status)
        registered = bool(report is not None and getattr(report, "registered", False))
        if registered:
            hypothesis_pool.update_status(item_id, HypothesisStatus.PROMOTED)
        return {
            "kind": kind,
            "item_id": item_id,
            "version": version,
            "registered": registered,
            "detail": getattr(report, "detail", "") if report is not None else "promotion skipped",
            "registry_status": getattr(report, "status", "") if report is not None else "",
        }

    if kind == "autoresearch":
        from services.actions.autoresearch import promote_approved_candidate

        candidate_repo = candidate_repo if candidate_repo is not None else CandidateRepository()
        legacy_review_queue = legacy_review_queue if legacy_review_queue is not None else ReviewQueue()
        response = promote_approved_candidate(
            fingerprint=item_id,
            version=version,
            target_status=target_status,
            repository=candidate_repo,
            review_queue=legacy_review_queue,
            promote_fn=promote_fn,
        )
        return response.model_dump()
    raise InvalidTransition(f"promotion unsupported for kind: {kind}")


def _load_stage_data(start: str):
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    from lake.load_lake import load_prices, load_raw_close
    from lake.units import implied_amount

    px = load_prices(start=start, fields=("close", "volume"))
    close, volume = px["close"], px["volume"]
    raw = load_raw_close(start=start).reindex(index=volume.index, columns=volume.columns)
    amount = implied_amount(volume, raw)
    forward = precompute_forward_returns(close)
    vintage = f"data_lake@{close.index[-1].strftime('%Y-%m-%d')}"
    return close, volume, amount, forward, vintage


def _default_stage_runners() -> dict[str, Callable]:
    from factory.lines.line2_validation import run_l0, run_l1, run_l2, run_l3
    return {"l0": run_l0, "l1": run_l1, "l2": run_l2, "l3": run_l3}


def _direction_from_experiments(experiments) -> int:
    for exp in reversed(list(experiments)):
        if getattr(exp.protocol, "value", "") == "l0_ic_scan":
            return -1 if exp.result.details.get("direction") == "short" else 1
    raise WorkItemConflict("L0 direction evidence is missing")


def run_hypothesis_stage(
    item_id: str,
    stage: str,
    *,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    hypothesis_pool=None,
    experiment_log=None,
    data_loader: Callable = _load_stage_data,
    runners: dict[str, Callable] | None = None,
) -> dict:
    from factory.ontology import Decision, HypothesisStatus
    from factory.pool.pool_repo import HypothesisPool
    from factory.repositories.experiment_log import ExperimentLog

    expected = {
        "l0": HypothesisStatus.QUEUED,
        "l1": HypothesisStatus.L0_PASSED,
        "l2": HypothesisStatus.L1_PASSED,
        "l3": HypothesisStatus.L2_PASSED,
    }
    advanced = {
        "l0": HypothesisStatus.L0_PASSED,
        "l1": HypothesisStatus.L1_PASSED,
        "l2": HypothesisStatus.L2_PASSED,
        "l3": HypothesisStatus.L3_PASSED,
    }
    if stage not in expected:
        raise InvalidTransition(f"unknown stage: {stage}")
    hypothesis_pool = hypothesis_pool if hypothesis_pool is not None else HypothesisPool()
    experiment_log = experiment_log if experiment_log is not None else ExperimentLog()
    item = hypothesis_pool.get(item_id)
    if item is None:
        raise KeyError(item_id)
    if item.status != expected[stage]:
        raise WorkItemConflict(
            f"stale stage request: {stage} requires {expected[stage].value}, got {item.status.value}"
        )

    close, volume, amount, forward, vintage = data_loader(start)
    runner = (runners or _default_stage_runners())[stage]
    if stage == "l0":
        exp = runner(item, close, volume, amount, forward, vintage_id=vintage, sample_dates=sample_dates)
    else:
        direction = _direction_from_experiments(experiment_log.list_by_hypothesis(item_id))
        exp = runner(
            item, close, volume, amount, direction=direction,
            vintage_id=vintage, start=start,
        )
    experiment_log.append(exp)
    if exp.decision == Decision.PROMOTE:
        hypothesis_pool.update_status(item_id, advanced[stage])
    elif exp.decision == Decision.SHELVE:
        hypothesis_pool.update_status(item_id, HypothesisStatus.SHELVED)
    else:
        hypothesis_pool.update_status(item_id, HypothesisStatus.DISCARDED)
    return {
        "kind": "hypothesis",
        "item_id": item_id,
        "stage": stage,
        "experiment_id": exp.experiment_id,
        "decision": exp.decision.value,
        "reason": exp.notes,
        "metrics": exp.result.metrics,
        "error": exp.result.error,
    }


def run_autoresearch_stage(
    item_id: str,
    stage: str,
    *,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    candidate_repo=None,
    experiment_log=None,
    legacy_review_queue=None,
    data_loader: Callable = _load_stage_data,
    runners: dict[str, Callable] | None = None,
) -> dict:
    from factory.autoresearch import (
        CandidateRepository,
        ExperimentLog,
        ReviewQueue,
        ast_to_hypothesis,
    )
    from factory.autoresearch.models import (
        CandidateDecision,
        CandidateEvaluationResult,
        CandidateStatus,
    )
    from factory.ontology import Decision, HypothesisStatus

    expected = {
        "l0": CandidateStatus.GENERATED,
        "l1": CandidateStatus.L0_PASSED,
        "l2": CandidateStatus.L1_PASSED,
        "l3": CandidateStatus.L2_PASSED,
    }
    hyp_status = {
        "l0": HypothesisStatus.QUEUED,
        "l1": HypothesisStatus.L0_PASSED,
        "l2": HypothesisStatus.L1_PASSED,
        "l3": HypothesisStatus.L2_PASSED,
    }
    passed_status = {
        "l0": CandidateStatus.L0_PASSED,
        "l1": CandidateStatus.L1_PASSED,
        "l2": CandidateStatus.L2_PASSED,
        "l3": CandidateStatus.PROMOTED_TO_REVIEW,
    }
    if stage not in expected:
        raise InvalidTransition(f"unknown stage: {stage}")
    candidate_repo = candidate_repo if candidate_repo is not None else CandidateRepository()
    experiment_log = experiment_log if experiment_log is not None else ExperimentLog()
    legacy_review_queue = legacy_review_queue if legacy_review_queue is not None else ReviewQueue()
    candidate = candidate_repo.get(item_id)
    if candidate is None:
        raise KeyError(item_id)
    if candidate.status != expected[stage]:
        raise WorkItemConflict(
            f"stale stage request: {stage} requires {expected[stage].value}, got {candidate.status.value}"
        )

    close, volume, amount, forward, vintage = data_loader(start)
    runner = (runners or _default_stage_runners())[stage]
    hyp = replace(ast_to_hypothesis(candidate), status=hyp_status[stage])
    prior = [row for row in experiment_log.iter_all() if row.fingerprint == item_id]
    if stage == "l0":
        exp = runner(hyp, close, volume, amount, forward, vintage_id=vintage, sample_dates=sample_dates)
    else:
        embedded = []
        for result in prior:
            embedded.extend(result.metrics.get("experiments", []))
        direction = -1 if any(
            row.get("protocol") == "l0_ic_scan"
            and row.get("details", {}).get("direction") == "short"
            for row in embedded
        ) else 1
        exp = runner(hyp, close, volume, amount, direction=direction, vintage_id=vintage, start=start)

    if exp.decision == Decision.PROMOTE:
        status = passed_status[stage]
        decision = CandidateDecision.PROMOTE if stage == "l3" else CandidateDecision.KEEP
    elif exp.decision == Decision.SHELVE:
        status, decision = CandidateStatus.SHELVED, CandidateDecision.SHELVE
    else:
        status, decision = CandidateStatus.DISCARDED, CandidateDecision.DISCARD
    metrics = {"experiments": [{
        "experiment_id": exp.experiment_id,
        "protocol": exp.protocol.value,
        "decision": exp.decision.value,
        "metrics": exp.result.metrics,
        "details": exp.result.details,
        "error": exp.result.error,
        "notes": exp.notes,
    }]}
    result = CandidateEvaluationResult(
        fingerprint=item_id, status=status, decision=decision, metrics=metrics, reason=exp.notes,
    )
    updated = candidate.with_status(status, exp.notes)
    candidate_repo.record(updated)
    experiment_log.append(result)
    if status == CandidateStatus.PROMOTED_TO_REVIEW:
        legacy_review_queue.add(updated, result)
    return {
        "kind": "autoresearch",
        "item_id": item_id,
        "stage": stage,
        "experiment_id": exp.experiment_id,
        "decision": exp.decision.value,
        "reason": exp.notes,
        "metrics": exp.result.metrics,
        "error": exp.result.error,
    }


class ActionCoordinator:
    def __init__(self, *, submitter=None, getter=None):
        if submitter is None or getter is None:
            from services.actions.jobs import get_action_job, submit_action_job
            submitter = submitter or submit_action_job
            getter = getter or get_action_job
        self.submitter = submitter
        self.getter = getter
        self._active: dict[str, str] = {}
        self._lock = Lock()

    def submit(self, key: str, kind: str, fn: Callable, *args: Any, **kwargs: Any) -> ActionJobView:
        with self._lock:
            existing_id = self._active.get(key)
            if existing_id:
                existing = self.getter(existing_id)
                if existing.status in {"queued", "running"}:
                    raise WorkItemConflict(f"action already running: {existing_id}")
            work_id, action = key.rsplit(":", 1)
            job = self.submitter(
                kind,
                fn,
                *args,
                job_context={"work_id": work_id, "action": action},
                **kwargs,
            )
            self._active[key] = job.job_id
            return job


ACTION_COORDINATOR = ActionCoordinator()


def submit_work_item_action(
    kind: str,
    item_id: str,
    action: str,
    *,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    version: str = "v1.0",
    target_status: str = "",
    coordinator: ActionCoordinator = ACTION_COORDINATOR,
) -> ActionJobView:
    from services.read.research_work_items import get_work_item

    detail = get_work_item(kind, item_id)
    expected = detail.item.next_action
    if detail.item.status == "running":
        raise WorkItemConflict("work item already has a running action")
    if action != expected:
        if action in {"queue", "run_l0", "run_l1", "run_l2", "run_l3", "promote"}:
            raise WorkItemConflict(f"stale action: expected {expected or 'none'}, got {action}")
        raise InvalidTransition(f"unsupported action: {action}")

    key = f"{kind}:{item_id}:{action}"
    if action == "queue" and kind == "draft":
        return coordinator.submit(key, "research.queue_draft", queue_draft, item_id)
    if action.startswith("run_l"):
        stage = action.removeprefix("run_")
        runner = run_hypothesis_stage if kind == "hypothesis" else run_autoresearch_stage
        if kind not in {"hypothesis", "autoresearch"}:
            raise InvalidTransition(f"{action} unsupported for kind: {kind}")
        return coordinator.submit(
            key, f"research.{action}", runner, item_id, stage,
            start=start, sample_dates=sample_dates,
        )
    if action == "promote":
        return coordinator.submit(
            key, "research.promote", promote_work_item, kind, item_id,
            version=version, target_status=target_status,
        )
    raise InvalidTransition(f"unsupported action: {action}")


def create_draft(**fields: Any) -> ResearchDraftView:
    return ResearchDraftView(**asdict(DraftRepository().create(**fields)))


def update_draft(draft_id: str, **fields: Any) -> ResearchDraftView:
    return ResearchDraftView(**asdict(DraftRepository().update(draft_id, **fields)))
