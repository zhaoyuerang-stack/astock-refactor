"""Research workspace actions: draft conversion, review gate and job conflicts."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _tmp(name: str) -> Path:
    return Path(tempfile.mkdtemp()) / name


def test_complete_draft_queues_canonical_hypothesis():
    from factory.pool.pool_repo import HypothesisPool
    from research_ledger.workspace import DraftRepository
    from services.actions.research_workspace import queue_draft

    drafts = DraftRepository(_tmp("drafts.jsonl"))
    pool = HypothesisPool(_tmp("hypothesis_pool.jsonl"))
    draft = drafts.create(
        title="成交额反转",
        source="report",
        mechanism="成交额异常扩张后风险补偿回归",
        citation="研报 A",
        factor_fn_name="factors.small_cap.small_cap_factor",
        factor_params={"window": 20},
        data_dependencies=["price/amount"],
    )

    result = queue_draft(draft.draft_id, draft_repo=drafts, hypothesis_pool=pool)

    hypothesis = pool.get(result["item_id"])
    assert hypothesis is not None
    assert hypothesis.status.value == "queued"
    assert hypothesis.thesis.mechanism == "成交额异常扩张后风险补偿回归"
    assert drafts.get(draft.draft_id).status == "converted"
    assert drafts.get(draft.draft_id).linked_work_id == f"hypothesis:{hypothesis.id}"


def test_incomplete_draft_cannot_enter_l0_queue():
    from research_ledger.workspace import DraftRepository
    from services.actions.research_workspace import InvalidTransition, queue_draft

    drafts = DraftRepository(_tmp("drafts.jsonl"))
    draft = drafts.create(title="只有叙事", mechanism="尚未定义公式")

    try:
        queue_draft(draft.draft_id, draft_repo=drafts)
        raise AssertionError("incomplete draft must be rejected")
    except InvalidTransition as exc:
        assert "factor_fn_name" in str(exc)


def test_hypothesis_promotion_requires_generic_human_approval():
    from factory.ontology import EconomicThesis, Hypothesis, HypothesisStatus
    from factory.pool.pool_repo import HypothesisPool
    from research_ledger.workspace import ResearchReviewRepository
    from services.actions.research_workspace import (
        WorkItemConflict,
        promote_work_item,
        review_work_item,
    )

    pool = HypothesisPool(_tmp("hypothesis_pool.jsonl"))
    reviews = ResearchReviewRepository(_tmp("reviews.jsonl"))
    hyp = Hypothesis(
        name="review-gated",
        description="unit",
        factor_fn_name="factors.small_cap.small_cap_factor",
        factor_params={"window": 20},
        data_dependencies=("price/amount",),
        thesis=EconomicThesis(mechanism="风险补偿", citation="unit"),
        status=HypothesisStatus.L3_PASSED,
    )
    pool.add(hyp)
    calls: list[str] = []

    def fake_promote(item, version="v1.0", **kwargs):
        calls.append(item.id)
        return SimpleNamespace(registered=True, detail="registered", status="候选")

    try:
        promote_work_item(
            "hypothesis", hyp.id, version="v1.0",
            review_repo=reviews, hypothesis_pool=pool, promote_fn=fake_promote,
        )
        raise AssertionError("unapproved L3 item must not promote")
    except WorkItemConflict as exc:
        assert "approval" in str(exc)

    review_work_item(
        "hypothesis", hyp.id, action="approve", notes="机制与证据一致",
        review_repo=reviews, hypothesis_pool=pool,
    )
    result = promote_work_item(
        "hypothesis", hyp.id, version="v1.0",
        review_repo=reviews, hypothesis_pool=pool, promote_fn=fake_promote,
    )
    assert result["registered"] is True
    assert calls == [hyp.id]
    assert pool.get(hyp.id).status.value == "promoted"


def test_legacy_autoresearch_review_also_records_generic_review():
    from factory.autoresearch import (
        Candidate,
        CandidateDecision,
        CandidateEvaluationResult,
        CandidateRepository,
        CandidateStatus,
        ReviewQueue,
    )
    from research_ledger.workspace import ResearchReviewRepository
    from services.actions.research_workspace import (
        WorkItemConflict,
        review_legacy_autoresearch_candidate,
        review_work_item,
    )

    root = Path(tempfile.mkdtemp())
    candidates = CandidateRepository(root / "candidates.jsonl")
    legacy_reviews = ReviewQueue(root / "legacy_reviews.jsonl")
    reviews = ResearchReviewRepository(root / "reviews.jsonl")
    candidate = Candidate(
        fingerprint="legacy-review",
        ast={"type": "linear_combo", "terms": [], "direction": "negative"},
        status=CandidateStatus.PROMOTED_TO_REVIEW,
    )
    candidates.record(candidate)
    legacy_reviews.add(
        candidate,
        CandidateEvaluationResult(
            fingerprint=candidate.fingerprint,
            status=CandidateStatus.PROMOTED_TO_REVIEW,
            decision=CandidateDecision.PROMOTE,
        ),
    )

    result = review_legacy_autoresearch_candidate(
        fingerprint=candidate.fingerprint,
        action="approve",
        notes="legacy endpoint",
        review_repo=reviews,
        candidate_repo=candidates,
        legacy_review_queue=legacy_reviews,
    )

    assert result.review_action == "approve"
    assert reviews.latest("autoresearch", candidate.fingerprint).action == "approve"
    try:
        review_work_item(
            "autoresearch",
            candidate.fingerprint,
            action="reject",
            review_repo=reviews,
            candidate_repo=candidates,
            legacy_review_queue=legacy_reviews,
        )
        raise AssertionError("stale review must return a work-item conflict")
    except WorkItemConflict:
        pass


def test_duplicate_running_action_returns_conflict():
    from contracts.views import ActionJobView
    from services.actions.research_workspace import ActionCoordinator, WorkItemConflict

    jobs: dict[str, ActionJobView] = {}

    def submitter(kind, fn, *args, **kwargs):
        job = ActionJobView(job_id="job-1", kind=kind, status="running", created_at="2026-06-21")
        jobs[job.job_id] = job
        return job

    def getter(job_id):
        return jobs[job_id]

    coordinator = ActionCoordinator(submitter=submitter, getter=getter)
    coordinator.submit("hypothesis:abc:run_l0", "research.run_l0", lambda: None)
    try:
        coordinator.submit("hypothesis:abc:run_l0", "research.run_l0", lambda: None)
        raise AssertionError("duplicate running action must conflict")
    except WorkItemConflict as exc:
        assert "already running" in str(exc)


def test_single_hypothesis_stage_reuses_canonical_runner_and_advances_status():
    import pandas as pd

    from factory.ontology import (
        Decision,
        EconomicThesis,
        Experiment,
        ExperimentProtocol,
        ExperimentResult,
        Hypothesis,
        HypothesisStatus,
    )
    from factory.pool.pool_repo import HypothesisPool
    from factory.repositories.experiment_log import ExperimentLog
    from services.actions.research_workspace import run_hypothesis_stage

    pool = HypothesisPool(_tmp("hypothesis_pool.jsonl"))
    log = ExperimentLog(_tmp("experiment_log.jsonl"))
    hyp = Hypothesis(
        name="single-stage",
        description="unit",
        factor_fn_name="factors.small_cap.small_cap_factor",
        factor_params={"window": 20},
        data_dependencies=("price/amount",),
        thesis=EconomicThesis(mechanism="风险补偿", citation="unit"),
        status=HypothesisStatus.QUEUED,
    )
    pool.add(hyp)

    def fake_l0(item, close, volume, amount, forward_ret, vintage_id, sample_dates=None):
        assert item.id == hyp.id
        return Experiment(
            experiment_id="exp-l0",
            hypothesis_id=item.id,
            protocol=ExperimentProtocol.L0_IC_SCAN,
            vintage_id=vintage_id,
            result=ExperimentResult(metrics={"ICIR": 0.5}, details={"direction": "long"}),
            decision=Decision.PROMOTE,
            notes="pass",
        )

    empty = pd.DataFrame()
    result = run_hypothesis_stage(
        hyp.id,
        "l0",
        start="2018-01-01",
        sample_dates=5,
        hypothesis_pool=pool,
        experiment_log=log,
        data_loader=lambda start: (empty, empty, empty, empty, "unit-vintage"),
        runners={"l0": fake_l0},
    )

    assert result["decision"] == "promote"
    assert pool.get(hyp.id).status == HypothesisStatus.L0_PASSED
    assert log.list_by_hypothesis(hyp.id)[0].experiment_id == "exp-l0"


if __name__ == "__main__":
    test_complete_draft_queues_canonical_hypothesis()
    test_incomplete_draft_cannot_enter_l0_queue()
    test_hypothesis_promotion_requires_generic_human_approval()
    test_legacy_autoresearch_review_also_records_generic_review()
    test_duplicate_running_action_returns_conflict()
    test_single_hypothesis_stage_reuses_canonical_runner_and_advances_status()
    print("research workflow action tests passed")
