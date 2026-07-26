from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import workflow.phase1_synthetic as phase1  # noqa: E402


def _checker() -> phase1.Phase1Checker:
    return phase1.Phase1Checker(
        factor_builder=lambda close, volume, amount, dates: close,
        timing_builder=lambda close, amount: pd.Series(1.0, index=close.index),
    )


def test_missing_delisted_reference_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(phase1, "ROOT", tmp_path)
    result = _checker()._check_delisted_coverage(phase1.make_synthetic_clean())
    assert result.verdict == "FAIL"
    assert result.evidence["coverage"] is None
    assert "missing" in result.detail


def test_corrupt_delisted_reference_fails_closed(tmp_path, monkeypatch):
    meta = tmp_path / "data_lake" / "meta" / "delisted_codes.parquet"
    meta.parent.mkdir(parents=True)
    meta.write_text("not parquet", encoding="utf-8")
    monkeypatch.setattr(phase1, "ROOT", tmp_path)
    result = _checker()._check_delisted_coverage(phase1.make_synthetic_clean())
    assert result.verdict == "FAIL"
    assert result.evidence["coverage"] is None
    assert "unreadable" in result.detail


def test_known_delisted_reference_can_pass(tmp_path, monkeypatch):
    meta = tmp_path / "data_lake" / "meta" / "delisted_codes.parquet"
    meta.parent.mkdir(parents=True)
    meta.touch()
    monkeypatch.setattr(phase1, "ROOT", tmp_path)
    monkeypatch.setattr(
        phase1.pd,
        "read_parquet",
        lambda path: pd.DataFrame({"code": ["000003"]}),
    )
    result = _checker()._check_delisted_coverage(phase1.make_synthetic_clean())
    assert result.verdict == "PASS"
    assert result.evidence["coverage"] == 1.0
