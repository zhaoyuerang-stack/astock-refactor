import json
import multiprocessing
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from governance.holdout import (
    HoldoutAlreadyConsumed,
    HoldoutIdentityMismatch,
    validate_on_holdout,
)


def _returns():
    idx = pd.date_range("2024-01-01", "2026-01-30", freq="B")
    return pd.Series(np.random.default_rng(7).normal(0.001, 0.01, len(idx)), index=idx)


def _validate(path, **kwargs):
    return validate_on_holdout(
        "candidate-a",
        _returns(),
        spec_hash="spec-a",
        data_fingerprint="data-a",
        path=path,
        **kwargs,
    )


def _concurrent_validate(path: str, candidate_id: str, start_event) -> None:
    if not start_event.wait(timeout=10):
        raise RuntimeError("timed out waiting for concurrent holdout start")
    validate_on_holdout(
        candidate_id,
        _returns(),
        spec_hash=f"spec-{candidate_id}",
        data_fingerprint="data-a",
        path=Path(path),
    )


def _run_concurrent(path, candidate_ids):
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(target=_concurrent_validate, args=(str(path), candidate_id, start_event))
        for candidate_id in candidate_ids
    ]
    try:
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=30)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert [process.exitcode for process in processes] == [0] * len(processes)


def test_same_identity_retry_is_idempotent(tmp_path):
    path = tmp_path / "holdout.jsonl"

    first = _validate(path)
    second = _validate(path)

    assert first["peek_count"] == 1
    assert second["peek_count"] == 1
    assert second["idempotent_retry"] is True
    assert len(path.read_text().splitlines()) == 1


def test_active_second_evaluation_is_rejected(tmp_path):
    path = tmp_path / "holdout.jsonl"
    _validate(path)

    with pytest.raises(HoldoutAlreadyConsumed):
        _validate(path, idempotent_retry=False)


def test_changed_spec_requires_new_candidate_identity(tmp_path):
    path = tmp_path / "holdout.jsonl"
    _validate(path)

    with pytest.raises(HoldoutIdentityMismatch):
        validate_on_holdout(
            "candidate-a",
            _returns(),
            spec_hash="spec-b",
            data_fingerprint="data-a",
            path=path,
        )


def test_record_contains_full_identity_and_return_hash(tmp_path):
    path = tmp_path / "holdout.jsonl"
    _validate(path)

    record = json.loads(path.read_text().strip())
    assert record["candidate_id"] == "candidate-a"
    assert record["spec_hash"] == "spec-a"
    assert record["data_fingerprint"] == "data-a"
    assert record["holdout_boundary"] == "2025-01-01"
    assert len(record["return_hash"]) == 64
    assert record["consumed_at"]


def test_corrupt_validation_ledger_fails_closed(tmp_path):
    path = tmp_path / "holdout.jsonl"
    path.write_text('{"candidate_id":"ok"}\n{broken json\n')

    with pytest.raises(RuntimeError, match="line 2"):
        _validate(path)


def test_concurrent_same_identity_consumes_holdout_once(tmp_path):
    path = tmp_path / "holdout.jsonl"
    _run_concurrent(path, ["same-candidate"] * 8)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["holdout_trials"] == 1


def test_concurrent_distinct_candidates_get_unique_monotonic_trial_counts(tmp_path):
    path = tmp_path / "holdout.jsonl"
    _run_concurrent(path, [f"candidate-{index}" for index in range(8)])
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 8
    assert sorted(record["holdout_trials"] for record in records) == list(range(1, 9))
