"""Unified research work-item read model and append-only repositories."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _tmp(name: str) -> Path:
    return Path(tempfile.mkdtemp()) / name


def test_status_mapping_separates_review_blocked_ready_and_archived():
    from services.read.research_work_items import canonical_state

    assert canonical_state("hypothesis", "l3_passed")[:2] == ("review", "review")
    assert canonical_state("hypothesis", "queued")[:2] == ("ready", "run_l0")
    assert canonical_state("autoresearch", "promote_failed")[:2] == ("blocked", "promote")
    assert canonical_state("autoresearch", "discarded")[:2] == ("archived", "")
    assert canonical_state("draft", "active", draft_complete=False)[:2] == ("blocked", "complete_draft")
    assert canonical_state("draft", "active", draft_complete=True)[:2] == ("ready", "queue")


def test_work_items_sort_by_human_action_then_blocked_ready_running():
    from contracts.views import ResearchWorkItemView
    from services.read.research_work_items import sort_work_items

    rows = [
        ResearchWorkItemView(work_id="autoresearch:a", kind="autoresearch", item_id="a", title="running", status="running", updated_at="2026-06-21T10:00:00"),
        ResearchWorkItemView(work_id="hypothesis:b", kind="hypothesis", item_id="b", title="ready", status="ready", updated_at="2026-06-21T09:00:00"),
        ResearchWorkItemView(work_id="hypothesis:c", kind="hypothesis", item_id="c", title="blocked", status="blocked", updated_at="2026-06-21T08:00:00"),
        ResearchWorkItemView(work_id="autoresearch:d", kind="autoresearch", item_id="d", title="review", status="review", updated_at="2026-06-21T07:00:00"),
    ]

    assert [row.status for row in sort_work_items(rows)] == ["review", "blocked", "ready", "running"]


def test_work_items_sort_newest_first_within_same_status():
    from contracts.views import ResearchWorkItemView
    from services.read.research_work_items import sort_work_items

    rows = [
        ResearchWorkItemView(work_id="hypothesis:old", kind="hypothesis", item_id="old", title="old", status="review", updated_at="2026-06-20T09:00:00"),
        ResearchWorkItemView(work_id="hypothesis:new", kind="hypothesis", item_id="new", title="new", status="review", updated_at="2026-06-21T09:00:00"),
    ]

    assert [row.item_id for row in sort_work_items(rows)] == ["new", "old"]


def test_draft_repository_is_append_only_and_latest_record_wins():
    from research_ledger.workspace import DraftRepository

    path = _tmp("drafts.jsonl")
    repo = DraftRepository(path)
    created = repo.create(
        title="铜价上涨传导",
        source="report",
        mechanism="上游铜价上涨压缩下游低议价权公司的利润率",
        citation="研报 A",
    )
    updated = repo.update(
        created.draft_id,
        factor_fn_name="factors.alpha.builtins.illiq.AmihudIlliq",
        factor_params={"window": 20},
    )

    assert updated.draft_id == created.draft_id
    assert updated.factor_params == {"window": 20}
    assert updated.revision == 2
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    reloaded = DraftRepository(path)
    assert reloaded.get(created.draft_id).factor_fn_name.endswith("AmihudIlliq")


def test_autoresearch_review_migration_is_idempotent():
    from research_ledger.workspace import ResearchReviewRepository

    path = _tmp("reviews.jsonl")
    repo = ResearchReviewRepository(path)
    legacy = [
        {
            "fingerprint": "abc123",
            "status": "approved",
            "review_action": "approve",
            "reviewer_notes": "机制可信",
            "reviewed_at": "2026-06-20",
        }
    ]

    assert repo.migrate_autoresearch(legacy) == 1
    assert repo.migrate_autoresearch(legacy) == 0
    review = repo.latest("autoresearch", "abc123")
    assert review.action == "approve"
    assert review.notes == "机制可信"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_autoresearch_review_migration_never_overwrites_native_review():
    from research_ledger.workspace import ResearchReviewRepository

    path = _tmp("reviews.jsonl")
    repo = ResearchReviewRepository(path)
    repo.record(kind="autoresearch", item_id="abc123", action="reject", notes="本地复核更新")

    migrated = repo.migrate_autoresearch([
        {
            "fingerprint": "abc123",
            "review_action": "approve",
            "reviewer_notes": "旧复核",
            "reviewed_at": "2026-06-20",
        }
    ])

    assert migrated == 0
    assert repo.latest("autoresearch", "abc123").action == "reject"


def test_work_item_read_does_not_write_legacy_review_migration():
    from factory.autoresearch import Candidate, CandidateRepository, CandidateStatus, ReviewQueue
    from factory.pool.pool_repo import HypothesisPool
    from research_ledger.workspace import DraftRepository, ResearchReviewRepository
    from services.read.research_work_items import list_work_items

    root = Path(tempfile.mkdtemp())
    candidate_repo = CandidateRepository(root / "candidates.jsonl")
    legacy_queue = ReviewQueue(root / "legacy_reviews.jsonl")
    review_path = root / "reviews.jsonl"
    review_repo = ResearchReviewRepository(review_path)
    candidate = Candidate(
        fingerprint="abc123",
        ast={"type": "linear_combo", "terms": [], "direction": "negative", "thesis": {"mechanism": "unit"}},
        status=CandidateStatus.APPROVED,
        source="autoresearch",
    )
    candidate_repo.record(candidate)
    legacy_queue._append_record({
        "fingerprint": "abc123",
        "status": "approved",
        "candidate": candidate.ast,
        "decision": "promote",
        "reason": "",
        "metrics": {},
        "review_action": "approve",
        "reviewer_notes": "legacy",
        "reviewed_at": "2026-06-20",
    })

    result = list_work_items(
        draft_repo=DraftRepository(root / "drafts.jsonl"),
        review_repo=review_repo,
        hypothesis_pool=HypothesisPool(root / "hypotheses.jsonl"),
        candidate_repo=candidate_repo,
        legacy_review_queue=legacy_queue,
    )

    item = next(row for row in result.items if row.item_id == "abc123")
    assert item.review is not None
    assert item.review.action == "approve"
    assert not review_path.exists(), "GET/read aggregation must not mutate the migration ledger"


def test_filter_work_items_applies_status_kind_and_limit_after_priority_sort():
    from contracts.views import ResearchWorkItemView
    from services.read.research_work_items import filter_work_items

    rows = [
        ResearchWorkItemView(work_id="draft:1", kind="draft", item_id="1", title="d", status="blocked"),
        ResearchWorkItemView(work_id="hypothesis:2", kind="hypothesis", item_id="2", title="h2", status="ready", updated_at="2026-06-20"),
        ResearchWorkItemView(work_id="hypothesis:3", kind="hypothesis", item_id="3", title="h3", status="ready", updated_at="2026-06-21"),
    ]

    filtered = filter_work_items(rows, status="ready", kind="hypothesis", limit=1)
    assert [row.item_id for row in filtered] == ["3"]


def test_filter_work_items_can_select_review_and_promote_actions():
    from contracts.views import ResearchWorkItemView
    from services.read.research_work_items import filter_work_items

    rows = [
        ResearchWorkItemView(work_id="hypothesis:1", kind="hypothesis", item_id="1", title="review", status="review", next_action="review"),
        ResearchWorkItemView(work_id="hypothesis:2", kind="hypothesis", item_id="2", title="promote", status="ready", next_action="promote"),
        ResearchWorkItemView(work_id="hypothesis:3", kind="hypothesis", item_id="3", title="run", status="ready", next_action="run_l1"),
    ]

    filtered = filter_work_items(rows, action="review,promote", limit=20)
    assert [row.item_id for row in filtered] == ["1", "2"]


def test_running_job_overrides_ready_work_item_state():
    from contracts.views import ActionJobView
    from factory.autoresearch import CandidateRepository, ReviewQueue
    from factory.pool.pool_repo import HypothesisPool
    from research_ledger.workspace import DraftRepository, ResearchReviewRepository
    from services.read.research_work_items import list_work_items

    root = Path(tempfile.mkdtemp())
    drafts = DraftRepository(root / "drafts.jsonl")
    draft = drafts.create(
        title="ready draft",
        mechanism="unit",
        factor_fn_name="factors.small_cap.small_cap_factor",
    )
    jobs = [
        ActionJobView(
            job_id="job-1",
            kind="research.queue_draft",
            status="running",
            created_at="2026-06-21",
            context={"work_id": f"draft:{draft.draft_id}", "action": "queue"},
        )
    ]
    result = list_work_items(
        draft_repo=drafts,
        review_repo=ResearchReviewRepository(root / "reviews.jsonl"),
        hypothesis_pool=HypothesisPool(root / "hypotheses.jsonl"),
        candidate_repo=CandidateRepository(root / "candidates.jsonl"),
        legacy_review_queue=ReviewQueue(root / "legacy_reviews.jsonl"),
        job_views=jobs,
    )

    item = next(row for row in result.items if row.item_id == draft.draft_id)
    assert item.status == "running"
    assert item.next_action == ""
    assert item.latest_result["job_id"] == "job-1"


if __name__ == "__main__":
    test_status_mapping_separates_review_blocked_ready_and_archived()
    test_work_items_sort_by_human_action_then_blocked_ready_running()
    test_work_items_sort_newest_first_within_same_status()
    test_draft_repository_is_append_only_and_latest_record_wins()
    test_autoresearch_review_migration_is_idempotent()
    test_autoresearch_review_migration_never_overwrites_native_review()
    test_work_item_read_does_not_write_legacy_review_migration()
    test_filter_work_items_applies_status_kind_and_limit_after_priority_sort()
    test_filter_work_items_can_select_review_and_promote_actions()
    test_running_job_overrides_ready_work_item_state()
    print("research work-item tests passed")
