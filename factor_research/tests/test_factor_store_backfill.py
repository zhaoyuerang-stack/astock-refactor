"""Core factor backfill tests.

Run:
    cd factor_research && python3 -m pytest tests/test_factor_store_backfill.py -q
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factor_store import load_factor_panel, load_factor_score  # noqa: E402
from factor_store.core_backfill import (  # noqa: E402
    backfill_core_factors,
    build_core_factor_panels,
)
from factors.composite import size_earnings_factor  # noqa: E402
from factors.small_cap import small_cap_factor  # noqa: E402


def _synthetic(seed: int = 11):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=100)
    codes = [f"{600000 + i:06d}" for i in range(45)]
    base_ret = rng.normal(0.0002, 0.015, (len(dates), len(codes)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(base_ret, axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(
        rng.lognormal(mean=12.0, sigma=0.5, size=close.shape),
        index=dates,
        columns=codes,
    )
    amount = volume * close
    net_profit_yoy = pd.DataFrame(
        rng.normal(0.15, 0.25, close.shape),
        index=dates,
        columns=codes,
    )
    total_mv = pd.DataFrame(
        rng.lognormal(mean=13.0, sigma=0.8, size=close.shape),
        index=dates,
        columns=codes,
    )
    return close, volume, amount, net_profit_yoy, total_mv


def test_build_core_factor_panels_uses_canonical_formulas():
    close, volume, amount, net_profit_yoy, _ = _synthetic()

    panels = build_core_factor_panels(
        close=close,
        volume=volume,
        amount=amount,
        net_profit_yoy=net_profit_yoy,
    )

    assert set(panels) == {"illiquidity", "small_cap_size", "size_earnings"}
    pd.testing.assert_frame_equal(
        panels["small_cap_size"],
        small_cap_factor(amount, window=60),
    )
    pd.testing.assert_frame_equal(
        panels["size_earnings"],
        size_earnings_factor(amount, net_profit_yoy, size_window=60, blend_weight=0.5),
    )


def test_backfill_core_factors_persists_panels_scores_and_correlation(tmp_path):
    close, volume, amount, net_profit_yoy, total_mv = _synthetic()

    result = backfill_core_factors(
        close=close,
        volume=volume,
        amount=amount,
        net_profit_yoy=net_profit_yoy,
        total_mv=total_mv,
        data_vintage="unit-vintage#backfill",
        store_root=tmp_path,
        horizons=(1, 5),
        primary_horizon=1,
    )

    assert set(result.factor_ids) == {"illiquidity", "small_cap_size", "size_earnings"}
    for factor_id in result.factor_ids.values():
        assert not load_factor_panel(factor_id, store_root=tmp_path).empty
        assert load_factor_score(factor_id, store_root=tmp_path).ic_count >= 30

    assert result.correlation_path.exists()
    payload = json.loads(result.correlation_path.read_text(encoding="utf-8"))
    assert payload["data_vintage"] == "unit-vintage#backfill"
    assert set(payload["factor_ids"]) == set(result.factor_ids)
    assert payload["matrix"]["illiquidity"]["illiquidity"] == 1.0
