"""Unified read model for pre-registration research work."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict

from contracts.views import (
    ResearchReviewView,
    ResearchWorkItemDetailView,
    ResearchWorkItemListView,
    ResearchWorkItemView,
)
from research_ledger.workspace import DraftRepository, ResearchReviewRepository

_STATUS_PRIORITY = {"review": 0, "blocked": 1, "ready": 2, "running": 3, "completed": 4, "archived": 5}


def canonical_state(
    kind: str,
    raw_status: str,
    *,
    has_approval: bool = False,
    draft_complete: bool = False,
) -> tuple[str, str, str, str]:
    raw = (raw_status or "").lower()
    if kind == "draft":
        if raw in {"converted", "archived"}:
            return "archived", "", "draft", ""
        if not draft_complete:
            return "blocked", "complete_draft", "draft", "缺少可执行因子定义"
        return "ready", "queue", "draft", ""

    if raw in {"promoted", "promoted_shadow"}:
        return "completed", "", "registered", ""
    if raw in {"discarded", "shelved", "rejected_by_human", "retired"}:
        return "archived", "", raw, ""
    if raw in {"promoting"}:
        return "running", "", "promotion", ""
    if raw in {"promote_failed"}:
        return "blocked", "promote", "promotion", "最近一次晋级失败"
    if raw in {"l3_passed", "promoted_to_review"}:
        if has_approval:
            return "ready", "promote", "review", ""
        return "review", "review", "review", ""
    next_by_status = {
        "drafted": ("queue", "draft"),
        "queued": ("run_l0", "l0"),
        "generated": ("run_l0", "l0"),
        "l0_passed": ("run_l1", "l1"),
        "l1_passed": ("run_l2", "l2"),
        "l2_passed": ("run_l3", "l3"),
        "approved": ("promote", "review"),
    }
    if raw in next_by_status:
        action, stage = next_by_status[raw]
        return "ready", action, stage, ""
    return "blocked", "", raw or "unknown", f"未知或不可执行状态: {raw_status}"


def sort_work_items(items: Iterable[ResearchWorkItemView]) -> list[ResearchWorkItemView]:
    newest_first = sorted(items, key=lambda row: (row.updated_at, row.work_id), reverse=True)
    return sorted(newest_first, key=lambda row: _STATUS_PRIORITY.get(row.status, 99))


def filter_work_items(
    items: Iterable[ResearchWorkItemView],
    *,
    status: str = "",
    kind: str = "",
    action: str = "",
    limit: int = 200,
) -> list[ResearchWorkItemView]:
    rows = sort_work_items(items)
    if status:
        allowed = {value.strip() for value in status.split(",") if value.strip()}
        rows = [row for row in rows if row.status in allowed]
    if kind:
        allowed_kind = {value.strip() for value in kind.split(",") if value.strip()}
        rows = [row for row in rows if row.kind in allowed_kind]
    if action:
        allowed_actions = {value.strip() for value in action.split(",") if value.strip()}
        rows = [row for row in rows if row.next_action in allowed_actions]
    return rows[:limit] if limit > 0 else rows


def _review_view(record) -> ResearchReviewView | None:
    return ResearchReviewView(**asdict(record)) if record else None


def list_work_items(
    *,
    draft_repo: DraftRepository | None = None,
    review_repo: ResearchReviewRepository | None = None,
    hypothesis_pool=None,
    candidate_repo=None,
    legacy_review_queue=None,
    job_views=None,
    status: str = "",
    kind: str = "",
    action: str = "",
    limit: int = 200,
) -> ResearchWorkItemListView:
    from factory.autoresearch import CandidateRepository, ReviewQueue, compute_complexity
    from factory.pool.pool_repo import HypothesisPool

    draft_repo = draft_repo or DraftRepository()
    review_repo = review_repo or ResearchReviewRepository()
    hypothesis_pool = hypothesis_pool if hypothesis_pool is not None else HypothesisPool()
    candidate_repo = candidate_repo if candidate_repo is not None else CandidateRepository()
    legacy_review_queue = legacy_review_queue if legacy_review_queue is not None else ReviewQueue()
    legacy_reviews = {
        str(row.get("fingerprint") or ""): row
        for row in legacy_review_queue.all()
        if row.get("review_action") in {"approve", "reject"}
    }
    rows: list[ResearchWorkItemView] = []

    for draft in draft_repo.all():
        complete = bool(draft.factor_fn_name and draft.mechanism)
        canonical_status, next_action, stage, blocked = canonical_state("draft", draft.status, draft_complete=complete)
        rows.append(ResearchWorkItemView(
            work_id=f"draft:{draft.draft_id}", kind="draft", item_id=draft.draft_id,
            title=draft.title, source=draft.source, raw_status=draft.status, status=canonical_status,
            stage=stage, mechanism=draft.mechanism, citation=draft.citation,
            updated_at=draft.updated_at, next_action=next_action, blocked_reason=blocked,
        ))

    for hyp in hypothesis_pool.all():
        review = review_repo.latest("hypothesis", hyp.id)
        canonical_status, next_action, stage, blocked = canonical_state(
            "hypothesis", hyp.status.value, has_approval=bool(review and review.action == "approve")
        )
        rows.append(ResearchWorkItemView(
            work_id=f"hypothesis:{hyp.id}", kind="hypothesis", item_id=hyp.id,
            title=hyp.name, source=hyp.source, raw_status=hyp.status.value, status=canonical_status,
            stage=stage, mechanism=hyp.thesis.mechanism if hyp.thesis else "",
            citation=hyp.thesis.citation if hyp.thesis else "", updated_at=hyp.created_at,
            next_action=next_action, blocked_reason=blocked, review=_review_view(review),
        ))

    for candidate in candidate_repo.all():
        review = review_repo.latest("autoresearch", candidate.fingerprint)
        legacy = legacy_reviews.get(candidate.fingerprint)
        if review is None and legacy is not None:
            review_view = ResearchReviewView(
                review_id=f"legacy:{candidate.fingerprint}",
                kind="autoresearch",
                item_id=candidate.fingerprint,
                action=legacy.get("review_action", ""),
                notes=legacy.get("reviewer_notes", ""),
                reviewer="human",
                reviewed_at=legacy.get("reviewed_at", ""),
                migrated_from="autoresearch_review_queue",
            )
        else:
            review_view = _review_view(review)
        canonical_status, next_action, stage, blocked = canonical_state(
            "autoresearch", candidate.status.value,
            has_approval=bool(review_view and review_view.action == "approve"),
        )
        try:
            complexity = compute_complexity(candidate)
            complexity_result = {
                "complexity_score": complexity.score,
                "max_auto_stage": complexity.max_auto_stage,
            }
        except Exception as exc:
            complexity_result = {"complexity_error": f"{type(exc).__name__}: {exc}"}
        thesis = candidate.ast.get("thesis") or {}
        rows.append(ResearchWorkItemView(
            work_id=f"autoresearch:{candidate.fingerprint}", kind="autoresearch",
            item_id=candidate.fingerprint, title=f"AutoResearch {candidate.fingerprint[:8]}",
            source=candidate.source, raw_status=candidate.status.value, status=canonical_status,
            stage=stage, mechanism=str(thesis.get("mechanism") or ""),
            citation=str(thesis.get("citation") or ""), updated_at=candidate.created_at,
            next_action=next_action, blocked_reason=blocked,
            latest_result=complexity_result,
            review=review_view,
        ))

    active_jobs = {
        str(job.context.get("work_id") or ""): job
        for job in (job_views or [])
        if job.status in {"queued", "running"} and job.context.get("work_id")
    }
    for index, row in enumerate(rows):
        job = active_jobs.get(row.work_id)
        if job is None:
            continue
        rows[index] = row.model_copy(update={
            "status": "running",
            "next_action": "",
            "latest_result": {
                **row.latest_result,
                "job_id": job.job_id,
                "job_kind": job.kind,
                "job_status": job.status,
            },
        })
    counts = dict(Counter(row.status for row in rows))
    return ResearchWorkItemListView(
        items=filter_work_items(rows, status=status, kind=kind, action=action, limit=limit),
        counts=counts,
    )


def get_work_item(kind: str, item_id: str) -> ResearchWorkItemDetailView:
    listing = list_work_items(limit=0)
    item = next((row for row in listing.items if row.kind == kind and row.item_id == item_id), None)
    if item is None:
        raise KeyError(f"{kind}:{item_id}")

    raw: dict = {}
    runs: list[dict] = []
    evidence = {"mechanism": item.mechanism, "citation": item.citation}
    if kind == "draft":
        raw = asdict(DraftRepository().get(item_id))
    elif kind == "hypothesis":
        from factory.pool.pool_repo import HypothesisPool
        from factory.repositories.experiment_log import ExperimentLog
        hyp = HypothesisPool().get(item_id)
        raw = asdict(hyp) if hyp else {}
        runs = [asdict(run) for run in ExperimentLog().list_by_hypothesis(item_id)]
    elif kind == "autoresearch":
        from factory.autoresearch import CandidateRepository, ExperimentLog
        candidate = CandidateRepository().get(item_id)
        raw = asdict(candidate) if candidate else {}
        runs = [asdict(run) for run in ExperimentLog().iter_all() if run.fingerprint == item_id]
    return ResearchWorkItemDetailView(item=item, evidence=evidence, runs=runs, raw=raw)
