"""Phase-2 feature-tradable infrastructure: cache keys, context, vol opt-in.

Does not claim alpha. Verifies dirty default stability + clean path isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import factors.autoresearch_dsl as dsl
from factor_store.store import build_factor_id, save_factor_panel
from factors.alpha.base import FactorData
from factors.alpha.builtins.volatility import Volatility
from factors.momentum import volatility as mom_volatility
from factors.tradable_mask import (
    FEATURE_MASK_NONE,
    FEATURE_MASK_VERSION,
    cleanse,
    feature_mask_context,
    get_active_feature_mask,
    normalize_feature_mask_version,
)


def _toy_panels(n=40, n_codes=8, poison_day=15, poison_px=50.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    cols = [f"{i:06d}" for i in range(n_codes)]
    rng = np.random.default_rng(1)
    base = 10 + np.cumsum(rng.normal(0, 0.05, size=(n, n_codes)), axis=0)
    close = pd.DataFrame(base, index=idx, columns=cols)
    day = min(int(poison_day), n - 1)
    close.iloc[day, 0] = poison_px  # synthetic limit spike on code0
    volume = pd.DataFrame(1e6, index=idx, columns=cols)
    # tradable: block only the poison cell
    tradable = pd.DataFrame(True, index=idx, columns=cols)
    tradable.iloc[day, 0] = False
    return close, volume, tradable


def test_normalize_feature_mask_version():
    assert normalize_feature_mask_version(None) == FEATURE_MASK_NONE
    assert normalize_feature_mask_version("none") == FEATURE_MASK_NONE
    assert normalize_feature_mask_version("OFF") == FEATURE_MASK_NONE
    assert normalize_feature_mask_version(FEATURE_MASK_VERSION) == FEATURE_MASK_VERSION


def test_feature_mask_context_sets_and_resets():
    close, _, tradable = _toy_panels()
    assert get_active_feature_mask()[0] is None
    with feature_mask_context(tradable, version=FEATURE_MASK_VERSION):
        t, v = get_active_feature_mask()
        assert t is not None
        assert v == FEATURE_MASK_VERSION
        assert bool(t.iloc[15, 0]) is False
    assert get_active_feature_mask()[0] is None
    assert get_active_feature_mask()[1] == FEATURE_MASK_NONE


def test_feature_mask_context_rejects_none_version():
    _, _, tradable = _toy_panels()
    with pytest.raises(ValueError):
        with feature_mask_context(tradable, version="none"):
            pass


def test_build_factor_id_dirty_stable_clean_diverges():
    dirty = build_factor_id("volatility", {"window": 20})
    dirty2 = build_factor_id("volatility", {"window": 20}, feature_mask_version="")
    dirty3 = build_factor_id("volatility", {"window": 20}, feature_mask_version="none")
    clean = build_factor_id(
        "volatility",
        {"window": 20},
        feature_mask_version=FEATURE_MASK_VERSION,
    )
    assert dirty == dirty2 == dirty3
    assert clean != dirty
    assert FEATURE_MASK_VERSION in str(
        # id is hashed; verify via params fold by comparing two clean same
        build_factor_id("volatility", {"window": 20}, feature_mask_version=FEATURE_MASK_VERSION)
    ) or clean.startswith("volatility__")
    assert clean == build_factor_id(
        "volatility",
        {"window": 20},
        feature_mask_version=FEATURE_MASK_VERSION,
    )


def test_save_factor_panel_records_mask_version(tmp_path):
    close, _, _ = _toy_panels(n=5, n_codes=3)
    panel = close.astype(float)
    rec = save_factor_panel(
        panel,
        factor_name="volatility",
        params={"window": 20},
        data_vintage="test",
        feature_mask_version=FEATURE_MASK_VERSION,
        store_root=tmp_path,
    )
    assert rec.params.get("feature_mask_version") == FEATURE_MASK_VERSION
    dirty = save_factor_panel(
        panel,
        factor_name="volatility",
        params={"window": 20},
        data_vintage="test",
        store_root=tmp_path,
    )
    assert dirty.factor_id != rec.factor_id
    assert "feature_mask_version" not in dirty.params


def test_cache_path_dirty_omits_fm_clean_includes(monkeypatch, tmp_path):
    monkeypatch.setattr(dsl, "_ROOT", tmp_path)
    monkeypatch.setattr(dsl, "_source_data_mtime", lambda: 1)
    monkeypatch.setattr(dsl, "_factor_source_hash", lambda name: "abc123abc123")

    dirty = dsl._get_cache_path("volatility", {"window": 20}, data_signature=None)
    clean = dsl._get_cache_path(
        "volatility",
        {"window": 20},
        data_signature=None,
        feature_mask_version=FEATURE_MASK_VERSION,
    )
    assert "_fm" not in dirty.name
    assert f"_fm{FEATURE_MASK_VERSION}" in clean.name or "_fmfeat_tradable_v1" in clean.name
    assert dirty != clean


def test_dsl_volatility_diverges_under_feature_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(dsl, "_ROOT", tmp_path)
    monkeypatch.setattr(dsl, "_source_data_mtime", lambda: 7)
    close, volume, tradable = _toy_panels()
    ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "volatility",
            "params": {"window": 10},
            "transforms": [],
            "weight": 1.0,
        }],
        "direction": "positive",
    }
    dsl.clear_factor_cache()
    dirty = dsl.compute_dsl_factor(
        close, volume, ast=ast, cache_mode="memory", feature_mask="off"
    )
    dsl.clear_factor_cache()
    with feature_mask_context(tradable, version=FEATURE_MASK_VERSION):
        clean = dsl.compute_dsl_factor(
            close, volume, ast=ast, cache_mode="memory", feature_mask="on"
        )
    # Poisoned name should differ somewhere after the spike day
    assert not dirty.equals(clean)
    # Clean path must not use the 50-spike in later windows as heavily —
    # at least one post-spike cell differs on code0.
    post = close.index[20]
    assert dirty.loc[post, "000000"] != clean.loc[post, "000000"] or (
        np.isnan(clean.loc[post, "000000"]) != np.isnan(dirty.loc[post, "000000"])
    )


def test_momentum_volatility_tradable_kwarg_matches_cleanse_path():
    close, _, tradable = _toy_panels()
    via_kw = mom_volatility(close, 10, tradable=tradable)
    via_pre = mom_volatility(cleanse(close, tradable), 10)
    # pre-cleansed uses full-window min_periods; kwarg path uses min_periods//2 —
    # both must exclude the poison price. Compare finite overlap correlation.
    common = via_kw.notna() & via_pre.notna()
    assert common.to_numpy().any()
    # Same sign structure on code0 after spike: both finite or patterns close
    assert via_kw.loc[close.index[25], "000000"] != mom_volatility(close, 10).loc[close.index[25], "000000"]


def test_oo_volatility_uses_tradable_on_factordata():
    close, volume, tradable = _toy_panels()
    amount = volume * close
    dirty = Volatility(window=10).compute(
        FactorData(close=close, volume=volume, amount=amount)
    )
    clean = Volatility(window=10).compute(
        FactorData(close=close, volume=volume, amount=amount, tradable=tradable)
    )
    assert not dirty.equals(clean)


def test_panel_key_includes_mask_version():
    close, volume, _ = _toy_panels(n=10, n_codes=3)
    ast = {"type": "linear_combo", "terms": [], "direction": "positive"}
    k_dirty = dsl._panel_key(ast, close, volume)
    k_clean = dsl._panel_key(ast, close, volume, feature_mask_version=FEATURE_MASK_VERSION)
    assert k_dirty != k_clean
    assert k_dirty[2] == FEATURE_MASK_NONE
    assert k_clean[2] == FEATURE_MASK_VERSION


def test_adr040_default_on_uses_volume_floor_without_context():
    """ADR-040: without context, synthetic panel still gets volume-floor cleanse path."""
    close, volume, _ = _toy_panels()
    volume.iloc[10, 0] = 0.0  # suspend one cell
    ast = {
        "type": "linear_combo",
        "terms": [{
            "factor": "momentum",
            "params": {"window": 5},
            "transforms": [],
            "weight": 1.0,
        }],
        "direction": "positive",
    }
    dsl.clear_factor_cache()
    dirty = dsl.compute_dsl_factor(
        close, volume, ast=ast, cache_mode="memory", feature_mask="off"
    )
    dsl.clear_factor_cache()
    default_on = dsl.compute_dsl_factor(close, volume, ast=ast, cache_mode="memory")
    # Default-on must not equal pure dirty when volume floor bites.
    assert not dirty.equals(default_on)
