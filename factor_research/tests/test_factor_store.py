"""Factor Store persistence tests.

Run:
    cd factor_research && python3 -m pytest tests/test_factor_store.py -q
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factor_store import (  # noqa: E402
    build_factor_id,
    load_factor_manifest,
    load_factor_panel,
    save_factor_panel,
)


def _panel() -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-05", periods=5)
    codes = ["000001", "000002", "600000"]
    values = np.arange(len(dates) * len(codes), dtype=float).reshape(len(dates), len(codes))
    return pd.DataFrame(values, index=dates, columns=codes)


def test_build_factor_id_is_stable_for_param_order():
    first = build_factor_id("amihud_illiquidity", {"window": 20, "direction": "long"})
    second = build_factor_id("amihud_illiquidity", {"direction": "long", "window": 20})
    changed = build_factor_id("amihud_illiquidity", {"window": 60, "direction": "long"})

    assert first == second
    assert first != changed
    assert first.startswith("amihud_illiquidity__")


def test_build_factor_id_feature_mask_version_is_opt_in():
    """Phase-2: dirty id unchanged; clean mask version forks the digest."""
    dirty = build_factor_id("volatility", {"window": 20})
    assert dirty == build_factor_id("volatility", {"window": 20}, feature_mask_version="")
    assert dirty == build_factor_id("volatility", {"window": 20}, feature_mask_version="none")
    clean = build_factor_id("volatility", {"window": 20}, feature_mask_version="feat_tradable_v1")
    assert clean != dirty



def test_save_factor_panel_persists_panel_and_manifest(tmp_path):
    panel = _panel()

    record = save_factor_panel(
        panel,
        factor_name="amihud_illiquidity",
        version="v1.0",
        params={"window": 20},
        data_vintage="unit-test-vintage#abc123",
        dependencies=["price/close", "price/volume"],
        description="Unit test factor panel",
        store_root=tmp_path,
    )

    assert (tmp_path / "panels" / f"{record.factor_id}.parquet").exists()
    manifest = load_factor_manifest(record.factor_id, store_root=tmp_path)
    assert manifest.factor_id == record.factor_id
    assert manifest.factor_name == "amihud_illiquidity"
    assert manifest.version == "v1.0"
    assert manifest.params == {"window": 20}
    assert manifest.data_vintage == "unit-test-vintage#abc123"
    assert manifest.dependencies == ["price/close", "price/volume"]
    assert manifest.start == "2026-01-05"
    assert manifest.end == "2026-01-09"
    assert manifest.shape == [5, 3]
    assert manifest.fingerprint

    loaded = load_factor_panel(record.factor_id, store_root=tmp_path)
    pd.testing.assert_frame_equal(loaded, panel, check_freq=False)


def test_load_factor_panel_can_slice_dates(tmp_path):
    panel = _panel()
    record = save_factor_panel(
        panel,
        factor_name="toy_rank_factor",
        params={"window": 3},
        data_vintage="unit-test-vintage#def456",
        store_root=tmp_path,
    )

    loaded = load_factor_panel(
        record.factor_id,
        start="2026-01-06",
        end="2026-01-08",
        store_root=tmp_path,
    )

    pd.testing.assert_frame_equal(loaded, panel.loc["2026-01-06":"2026-01-08"], check_freq=False)


def test_save_factor_panel_rejects_invalid_panel(tmp_path):
    bad = _panel()
    bad.iloc[0, 0] = np.inf

    try:
        save_factor_panel(
            bad,
            factor_name="bad_factor",
            params={},
            data_vintage="unit-test-vintage#bad",
            store_root=tmp_path,
        )
    except ValueError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("Factor Store must reject non-finite factor panels")


if __name__ == "__main__":
    test_build_factor_id_is_stable_for_param_order()
    with tempfile.TemporaryDirectory() as td:
        test_save_factor_panel_persists_panel_and_manifest(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_load_factor_panel_can_slice_dates(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_save_factor_panel_rejects_invalid_panel(Path(td))
    print("✅ Factor Store tests passed")
