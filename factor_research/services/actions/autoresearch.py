"""Actions for running Auto Factor Research candidates through real validation lines."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pandas as pd

from contracts.views import (
    AutoResearchPromoteResponse,
    AutoResearchReviewItemView,
    AutoResearchRunResponse,
    AutoResearchRunResultView,
)
from factory.autoresearch import (
    CandidateRepository,
    CandidateStatus,
    ExperimentLog,
    ReviewQueue,
    ast_to_hypothesis,
    generate_seed_candidates,
    run_validation_pipeline,
)


def _load_validation_data(start: str):
    """Load prices + feature-tradable context (ADR-040: search uses clean rolling).

    Returns ``(close, volume, amount, forward_ret, tradable, mask_version)``.
    """
    from factors.tradable_mask import load_feature_tradable_context
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns
    from lake.units import implied_amount

    ctx = load_feature_tradable_context(start)
    close, volume = ctx.close, ctx.volume
    amount = ctx.amount
    if amount is None:
        # canonical lake volume unit = share; amount CNY = shares × raw CNY/share
        amount = implied_amount(volume, ctx.raw_close)
    forward_ret = precompute_forward_returns(close)
    return close, volume, amount, forward_ret, ctx.tradable, ctx.mask_version


def _protocols(result) -> list[str]:
    return [e.get("protocol", "") for e in result.metrics.get("experiments", []) if e.get("protocol")]


def _run_candidates(
    candidates,
    *,
    max_stage: str = "l0",
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    close: pd.DataFrame | None = None,
    volume: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    forward_ret: pd.DataFrame | None = None,
    vintage_id: str | None = None,
    repository: CandidateRepository | None = None,
    experiment_log: ExperimentLog | None = None,
    review_queue: ReviewQueue | None = None,
    runners: dict[str, Callable] | None = None,
    computation_time_budget: float = 10.0,
) -> AutoResearchRunResponse:
    """共用驱动:一批已校验候选走真实 L0/L1/L2/L3 验证线。"""
    if max_stage not in {"l0", "l1", "l2", "l3"}:
        raise ValueError("max_stage must be one of: l0, l1, l2, l3")
    tradable = None
    mask_version = None
    if close is None or volume is None or amount is None or forward_ret is None:
        close, volume, amount, forward_ret, tradable, mask_version = _load_validation_data(start)

    if vintage_id is None:
        from lake.fingerprint import stamp_vintage

        # vintage 带数据内容指纹:数据湖日内漂移时,不同面板绝不共享同一凭证
        vintage_id = stamp_vintage(f"data_lake:{start}:{date.today().isoformat()}", close)
    vintage = vintage_id
    results: list[AutoResearchRunResultView] = []

    def _loop() -> None:
        for candidate in candidates:
            result = run_validation_pipeline(
                candidate,
                close=close,
                volume=volume,
                amount=amount,
                forward_ret=forward_ret,
                vintage_id=vintage,
                repository=repository,
                experiment_log=experiment_log,
                review_queue=review_queue,
                runners=runners,
                sample_dates=sample_dates,
                max_stage=max_stage,
                computation_time_budget=computation_time_budget,
            )
            results.append(
                AutoResearchRunResultView(
                    fingerprint=result.fingerprint,
                    status=result.status.value,
                    decision=result.decision.value,
                    reason=result.reason,
                    protocols=_protocols(result),
                )
            )

    if tradable is not None and mask_version is not None:
        from factors.tradable_mask import feature_mask_context

        with feature_mask_context(tradable, version=mask_version):
            _loop()
    else:
        _loop()
    return AutoResearchRunResponse(vintage_id=vintage, max_stage=max_stage, results=results)


def run_autoresearch_seeds(*, limit: int = 5, **kw) -> AutoResearchRunResponse:
    """Run deterministic seed candidates through L0/L1/L2/L3.

    Default max_stage is L0 because L1-L3 are real backtests and can be slow.
    Passing max_stage='l3' uses the existing canonical factory validation lines.
    """
    return _run_candidates(list(generate_seed_candidates(limit=limit)), **kw)


def review_autoresearch_candidate(
    *,
    fingerprint: str,
    action: str,
    notes: str = "",
    repository: CandidateRepository | None = None,
    review_queue: ReviewQueue | None = None,
    auto_promote_after_approve: bool | None = None,
    promote_version: str = "v1.0",
    promote_fn: Callable | None = None,
    job_submitter: Callable | None = None,
) -> AutoResearchReviewItemView:
    """Human approve/reject for a promoted candidate.

    Approve 只把状态推进到 APPROVED——不写 LIVE 台账;
    入册仍走唯一通道 workflow/promote.py(后续独立步骤)。
    """
    if action not in {"approve", "reject"}:
        raise ValueError("action must be 'approve' or 'reject'")
    repository = repository or CandidateRepository()
    review_queue = review_queue or ReviewQueue()

    item = review_queue.get(fingerprint)
    if item is None:
        raise ValueError(f"fingerprint not in review queue: {fingerprint}")
    if item.get("status") != CandidateStatus.PROMOTED_TO_REVIEW.value:
        raise ValueError(f"candidate is not pending review (status={item.get('status')})")

    status = CandidateStatus.APPROVED if action == "approve" else CandidateStatus.REJECTED_BY_HUMAN
    candidate = repository.get(fingerprint)
    if candidate is not None:
        repository.record(candidate.with_status(status, notes or f"human {action}"))
    rec = review_queue.record_decision(
        fingerprint,
        status,
        action=action,
        notes=notes,
        reviewed_at=date.today().isoformat(),
    )
    if action == "approve" and _auto_promote_enabled(auto_promote_after_approve):
        if candidate is not None:
            repository.record(candidate.with_status(
                CandidateStatus.PROMOTING,
                notes or "approved; auto shadow promote queued",
            ))
        rec = review_queue.record_status(
            fingerprint,
            CandidateStatus.PROMOTING,
            target_status="SHADOW",
            promote_error="",
        )
        submitter = job_submitter
        if submitter is None:
            from services.actions.jobs import submit_action_job
            submitter = submit_action_job
        job = submitter(
            "autoresearch.promote_after_approve",
            promote_approved_candidate_job,
            fingerprint=fingerprint,
            version=promote_version,
            repository=repository,
            review_queue=review_queue,
            promote_fn=promote_fn,
            target_status="SHADOW",
        )
        rec = review_queue.record_fields(
            fingerprint,
            promote_job_id=getattr(job, "job_id", ""),
            target_status="SHADOW",
        )
    return AutoResearchReviewItemView(**rec)


def promote_approved_candidate(
    *,
    fingerprint: str,
    version: str = "v1.0",
    run_marginal: bool = False,
    repository: CandidateRepository | None = None,
    review_queue: ReviewQueue | None = None,
    promote_fn: Callable | None = None,
    target_status: str = "",
    auto_job: bool = False,
) -> AutoResearchPromoteResponse:
    """APPROVED 候选 → workflow phase1~4 正式入册的"最后一公里"。

    入册唯一通道不变:这里只是把候选翻译成 factory Hypothesis 后交给
    workflow.promote.promote_hypothesis(phase1 合成防未来审计 → phase2/3 → phase4 唯一登记)。
    本函数自身绝不写台账。
    """
    repository = repository or CandidateRepository()
    review_queue = review_queue or ReviewQueue()

    item = review_queue.get(fingerprint)
    if item is None:
        raise ValueError(f"fingerprint not in review queue: {fingerprint}")
    if item.get("review_action") != "approve":
        raise ValueError(f"candidate is not approved (review_action={item.get('review_action') or 'pending'})")
    candidate = repository.get(fingerprint)
    if candidate is None:
        raise ValueError(f"fingerprint not in candidate repository: {fingerprint}")

    if promote_fn is None:
        from workflow.promote import promote_hypothesis

        promote_fn = promote_hypothesis

    hyp = ast_to_hypothesis(candidate)
    promote_kwargs = {"run_marginal": run_marginal}
    # ADR-022:把候选种子溯源透传给 phase4,落进 registry evidence(LLM 起源触发 semantic_seed_review)。
    if candidate.provenance:
        promote_kwargs["seed_provenance"] = dict(candidate.provenance)
    if target_status:
        promote_kwargs["target_status"] = target_status
    # §5.2:把候选的 holdout 校验 id 透传给 phase4 金库闸(scheduled_factor_search 已写记录)
    promote_kwargs["holdout_id"] = item.get("holdout_id") or f"autoresearch_{fingerprint[:8]}"
    report = promote_fn(hyp, version=version, **promote_kwargs)

    registered = bool(report is not None and getattr(report, "registered", False))
    detail = getattr(report, "detail", "") if report is not None else "知识图谱 gate 跳过"
    registry_status = getattr(report, "status", "") if report is not None else ""
    phase_summary = getattr(report, "phase_summary", {}) if report is not None else {}
    if str(target_status or "").upper() == "SHADOW" and registry_status in {"在册", "ACTIVE", "active"}:
        raise RuntimeError(f"SHADOW auto-promote attempted ACTIVE registry status: {registry_status}")
    note = f"registered {hyp.name}/{version}" if registered else f"promotion not registered: {detail or 'gates not met'}"
    if auto_job and str(target_status or "").upper() == "SHADOW":
        shadow_note = f"shadow promoted {hyp.name}/{version}: {detail}"
        repository.record(candidate.with_status(CandidateStatus.PROMOTED_SHADOW, shadow_note))
        review_queue.record_status(
            fingerprint,
            CandidateStatus.PROMOTED_SHADOW,
            target_status="SHADOW",
            registry_status=registry_status,
            promote_detail=detail,
            promote_error="",
        )
    else:
        repository.record(candidate.with_status(candidate.status, note))
    return AutoResearchPromoteResponse(
        fingerprint=fingerprint,
        hypothesis_name=hyp.name,
        version=version,
        registered=registered,
        detail=detail,
        target_status=target_status,
        registry_status=registry_status,
        phase_summary=phase_summary,
    )


def promote_approved_candidate_job(
    *,
    fingerprint: str,
    version: str = "v1.0",
    repository: CandidateRepository | None = None,
    review_queue: ReviewQueue | None = None,
    promote_fn: Callable | None = None,
    target_status: str = "SHADOW",
) -> AutoResearchPromoteResponse:
    """Async job wrapper: auto approve path may only promote into SHADOW/候选."""
    repository = repository or CandidateRepository()
    review_queue = review_queue or ReviewQueue()
    candidate = repository.get(fingerprint)
    if candidate is not None:
        repository.record(candidate.with_status(CandidateStatus.PROMOTING, "auto shadow promote running"))
    review_queue.record_status(
        fingerprint,
        CandidateStatus.PROMOTING,
        target_status=target_status,
        promote_error="",
    )
    try:
        return promote_approved_candidate(
            fingerprint=fingerprint,
            version=version,
            repository=repository,
            review_queue=review_queue,
            promote_fn=promote_fn,
            target_status=target_status,
            auto_job=True,
        )
    except Exception as e:
        msg = f"{type(e).__name__}: {str(e)[:500]}"
        candidate = repository.get(fingerprint)
        if candidate is not None:
            repository.record(candidate.with_status(CandidateStatus.PROMOTE_FAILED, msg))
        review_queue.record_status(
            fingerprint,
            CandidateStatus.PROMOTE_FAILED,
            target_status=target_status,
            promote_error=msg,
        )
        raise


def _auto_promote_enabled(value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    try:
        from app_config.settings import get_settings
        return bool(get_settings().factory.auto_promote_after_approve)
    except Exception:
        return False
