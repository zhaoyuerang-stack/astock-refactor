"""Read views for Auto Factor Research Lite."""
from __future__ import annotations

from pathlib import Path

from contracts.views import (
    AutoResearchCandidateView,
    AutoResearchFunnelView,
    AutoResearchReviewItemView,
)
from factory.autoresearch import CandidateRepository, ReviewQueue, compute_complexity
from factory.autoresearch.models import CandidateStatus

_FUNNEL_ORDER = [
    CandidateStatus.GENERATED.value,
    CandidateStatus.L0_PASSED.value,
    CandidateStatus.L1_PASSED.value,
    CandidateStatus.L2_PASSED.value,
    CandidateStatus.L3_PASSED.value,
    CandidateStatus.SHELVED.value,
    CandidateStatus.DISCARDED.value,
    CandidateStatus.PROMOTED_TO_REVIEW.value,
    CandidateStatus.APPROVED.value,
    CandidateStatus.PROMOTING.value,
    CandidateStatus.PROMOTED_SHADOW.value,
    CandidateStatus.PROMOTE_FAILED.value,
    CandidateStatus.REJECTED_BY_HUMAN.value,
    CandidateStatus.RETIRED.value,
]


def autoresearch_candidates(
    limit: int = 60,
    path: Path | None = None,
) -> list[AutoResearchCandidateView]:
    repo = CandidateRepository(path) if path else CandidateRepository()
    out: list[AutoResearchCandidateView] = []
    for candidate in repo.all()[:limit]:
        complexity = compute_complexity(candidate)
        out.append(
            AutoResearchCandidateView(
                fingerprint=candidate.fingerprint,
                status=candidate.status.value,
                source=candidate.source,
                ast=candidate.ast,
                complexity_score=complexity.score,
                max_auto_stage=complexity.max_auto_stage,
                notes=candidate.notes,
                created_at=candidate.created_at,
            )
        )
    return out


def autoresearch_review_queue(
    limit: int = 60,
    path: Path | None = None,
) -> list[AutoResearchReviewItemView]:
    queue = ReviewQueue(path) if path else ReviewQueue()
    return [AutoResearchReviewItemView(**item) for item in queue.all()[:limit]]


def autoresearch_funnel(
    candidate_path: Path | None = None,
    review_path: Path | None = None,
) -> AutoResearchFunnelView:
    repo = CandidateRepository(candidate_path) if candidate_path else CandidateRepository()
    counts = {status: 0 for status in _FUNNEL_ORDER}
    for candidate in repo.all():
        counts[candidate.status.value] = counts.get(candidate.status.value, 0) + 1
    stages = [{"stage": status, "count": counts.get(status, 0)} for status in _FUNNEL_ORDER]
    queue = ReviewQueue(review_path) if review_path else ReviewQueue()
    # review_queue = 待复核数(已 approve/reject 的不再计入)
    return AutoResearchFunnelView(total=len(repo.all()), stages=stages, review_queue=len(queue.pending()))
