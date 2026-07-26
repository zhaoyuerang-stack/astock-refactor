"""Small process-local job runner for minute-level UI actions."""
from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from contracts.views import ActionJobView

_MAX_WORKERS = max(1, int(os.environ.get("ASTCOK_ACTION_WORKERS", "2")))
_MAX_JOBS = max(20, int(os.environ.get("ASTCOK_ACTION_JOB_LIMIT", "200")))
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="astcok-action")
_LOCK = Lock()
_JOBS: dict[str, ActionJobView] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _to_result(value: Any) -> dict | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return {"value": value}


def _snapshot(job: ActionJobView) -> ActionJobView:
    data = job.model_dump() if hasattr(job, "model_dump") else job.dict()
    return ActionJobView(**data)


def _prune_locked() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    for job_id in sorted(_JOBS, key=lambda k: _JOBS[k].created_at)[: len(_JOBS) - _MAX_JOBS]:
        if _JOBS[job_id].status in {"succeeded", "failed"}:
            _JOBS.pop(job_id, None)


def submit_action_job(
    kind: str,
    fn: Callable[..., Any],
    *args: Any,
    job_context: dict | None = None,
    **kwargs: Any,
) -> ActionJobView:
    job_id = f"{kind.replace('.', '-')}-{uuid.uuid4().hex[:12]}"
    job = ActionJobView(
        job_id=job_id,
        kind=kind,
        status="queued",
        created_at=_now(),
        context=dict(job_context or {}),
    )
    with _LOCK:
        _JOBS[job_id] = job
        _prune_locked()
    _EXECUTOR.submit(_run_job, job_id, fn, args, kwargs)
    return get_action_job(job_id)


def get_action_job(job_id: str) -> ActionJobView:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return _snapshot(job)


def list_action_jobs() -> list[ActionJobView]:
    with _LOCK:
        return [
            _snapshot(job)
            for job in sorted(_JOBS.values(), key=lambda item: item.created_at, reverse=True)
        ]


def _run_job(job_id: str, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    with _LOCK:
        job = _JOBS[job_id]
        job.status = "running"
        job.started_at = _now()
    try:
        result = _to_result(fn(*args, **kwargs))
    except Exception as e:  # noqa: BLE001 - surfaced through job status for polling UI.
        with _LOCK:
            job = _JOBS[job_id]
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.finished_at = _now()
        return

    with _LOCK:
        job = _JOBS[job_id]
        job.status = "succeeded"
        job.result = result
        job.finished_at = _now()
