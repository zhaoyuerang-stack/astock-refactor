"""Synthetic tests for feature-tradable mask / cleanse (upstream contamination hygiene).

Does not claim alpha validity. Verifies:
  · limit-day prices are excluded from rolling means
  · post-hoc NaN on factor cannot undo window pollution (A vs B diverge)
  · zero is never used as a stand-in for non-tradable
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from factors.tradable_mask import (
    at_limit_flags,
    build_feature_tradable_mask,
    cleanse,
    factor_momentum_clean,
    factor_price_to_ma_clean,
    factor_volatility_clean,
    mask_summary,
    masked_rolling_mean,
)


def _panel(values, dates=None, codes=None) -> pd.DataFrame:
    if dates is None:
        dates = pd.bdate_range("2020-01-01", periods=len(values))
    if codes is None:
        codes = ["AAA"]
    if np.ndim(values) == 1:
        values = np.asarray(values, dtype=float).reshape(-1, 1)
    return pd.DataFrame(values, index=dates, columns=codes)


def test_at_limit_flags_within_tol():
    raw = _panel([10.0, 11.0, 9.0])
    up = _panel([11.0, 11.0, 11.0])
    dn = _panel([9.0, 9.0, 9.0])
    at_up, at_dn = at_limit_flags(raw, up, dn, price_tol=0.01)
    assert not bool(at_up.iloc[0, 0])
    assert bool(at_up.iloc[1, 0])
    assert bool(at_dn.iloc[2, 0])


def test_at_limit_flags_missing_limit_not_flagged():
    raw = _panel([10.0, 11.0])
    up = _panel([np.nan, np.nan])
    dn = _panel([np.nan, np.nan])
    at_up, at_dn = at_limit_flags(raw, up, dn)
    assert not bool(at_up.any(axis=None))
    assert not bool(at_dn.any(axis=None))


def test_build_feature_tradable_mask_blocks_limit_and_zero_volume():
    dates = pd.bdate_range("2020-01-01", periods=4)
    raw = _panel([10.0, 11.0, 10.5, 10.0], dates=dates)
    up = _panel([11.0, 11.0, 11.0, 11.0], dates=dates)
    dn = _panel([9.0, 9.0, 9.0, 9.0], dates=dates)
    vol = _panel([1e6, 1e6, 0.0, 1e6], dates=dates)
    m = build_feature_tradable_mask(raw, up, dn, vol, price_tol=0.01)
    assert list(m["AAA"].astype(bool)) == [True, False, False, True]


def test_cleanse_uses_nan_not_zero():
    panel = _panel([10.0, 11.0])
    tradable = _panel([True, False]).astype(bool)
    out = cleanse(panel, tradable)
    assert out.iloc[0, 0] == 10.0
    assert np.isnan(out.iloc[1, 0])


def test_masked_rolling_mean_excludes_limit_day_from_window():
    """Classic upstream bug: limit close=20 poisons MA if not masked first."""
    dates = pd.bdate_range("2020-01-01", periods=5)
    close = _panel([10.0, 10.0, 20.0, 10.0, 10.0], dates=dates)  # day2 = limit
    up = _panel([11.0, 11.0, 20.0, 11.0, 11.0], dates=dates)
    dn = _panel([9.0, 9.0, 9.0, 9.0, 9.0], dates=dates)
    vol = _panel([1e6] * 5, dates=dates)
    tradable = build_feature_tradable_mask(close, up, dn, vol)

    dirty = close.rolling(3, min_periods=2).mean()
    clean = masked_rolling_mean(close, tradable, 3, min_periods=2)

    # On last day, dirty MA includes the 20; clean MA only sees 10s after NaN limit day.
    assert dirty.iloc[-1, 0] == pytest.approx((20 + 10 + 10) / 3)
    assert clean.iloc[-1, 0] == pytest.approx(10.0)


def test_post_hoc_mask_cannot_undo_window_pollution():
    """apply_universe-style post filter leaves polluted MA history intact."""
    dates = pd.bdate_range("2020-01-01", periods=5)
    close = _panel([10.0, 10.0, 20.0, 10.0, 10.0], dates=dates)
    up = _panel([11.0, 11.0, 20.0, 11.0, 11.0], dates=dates)
    dn = _panel([9.0] * 5, dates=dates)
    vol = _panel([1e6] * 5, dates=dates)
    tradable = build_feature_tradable_mask(close, up, dn, vol)

    dirty_ma = close.rolling(3, min_periods=2).mean()
    # Post-hoc: blank factor only on limit day (universe filter style)
    post_hoc = dirty_ma.where(tradable)
    clean_ma = masked_rolling_mean(close, tradable, 3, min_periods=2)

    # Limit day: post-hoc blanks the already-polluted MA; clean path may still
    # publish a MA from prior clean points — either way history remains polluted.
    assert np.isnan(post_hoc.iloc[2, 0])
    assert dirty_ma.iloc[2, 0] == pytest.approx((10 + 10 + 20) / 3)
    # Days *after* the limit: post-hoc still carries the 20 inside the window.
    assert post_hoc.iloc[-1, 0] != clean_ma.iloc[-1, 0]
    assert post_hoc.iloc[-1, 0] == pytest.approx((20 + 10 + 10) / 3)
    assert clean_ma.iloc[-1, 0] == pytest.approx(10.0)


def test_factor_momentum_clean_nan_when_endpoint_limit():
    dates = pd.bdate_range("2020-01-01", periods=4)
    close = _panel([10.0, 10.5, 11.0, 12.0], dates=dates)
    up = _panel([15.0, 15.0, 11.0, 15.0], dates=dates)  # day2 at limit
    dn = _panel([5.0] * 4, dates=dates)
    vol = _panel([1e6] * 4, dates=dates)
    tradable = build_feature_tradable_mask(close, up, dn, vol)
    mom = factor_momentum_clean(close, tradable, n=2)
    # endpoint day2 is limit → mom[2] NaN; day3 uses clean 12 / clean 10.5
    assert np.isnan(mom.iloc[2, 0])
    assert mom.iloc[3, 0] == pytest.approx(12.0 / 10.5 - 1.0)


def test_factor_price_to_ma_and_vol_run_on_clean_path():
    dates = pd.bdate_range("2020-01-01", periods=30)
    rng = np.random.default_rng(0)
    px = 10 + np.cumsum(rng.normal(0, 0.1, size=30))
    close = _panel(px, dates=dates)
    up = _panel(px * 1.1, dates=dates)
    dn = _panel(px * 0.9, dates=dates)
    # Force one limit day
    close.iloc[10, 0] = float(up.iloc[10, 0])
    vol = _panel([1e6] * 30, dates=dates)
    tradable = build_feature_tradable_mask(close, up, dn, vol)
    pma = factor_price_to_ma_clean(close, tradable, 5)
    v = factor_volatility_clean(close, tradable, 10)
    assert pma.notna().any(axis=None)
    assert v.notna().any(axis=None)
    assert np.isnan(pma.iloc[10, 0])


def test_mask_summary_rates():
    tradable = _panel([[True], [False], [True]]).astype(bool)
    s = mask_summary(tradable)
    assert s["n_cells"] == 3
    assert s["n_tradable"] == 2
    assert s["tradable_rate"] == pytest.approx(2 / 3)


def test_price_tol_negative_raises():
    raw = _panel([10.0])
    with pytest.raises(ValueError):
        at_limit_flags(raw, raw, raw, price_tol=-0.01)
