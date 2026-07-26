from __future__ import annotations

import math

import numpy as np
import pandas as pd

from scripts.research.defensive_overlay_audit import audit_defensive_overlay


def _synthetic_panels() -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    dates = pd.bdate_range("2021-01-01", periods=90)
    bull = np.linspace(10.0, 14.0, 30)
    bear = np.linspace(14.0, 9.0, 30)
    recovery = np.linspace(9.0, 12.5, 30)
    small_path = np.concatenate([bull, bear, recovery])

    close = pd.DataFrame(
        {
            "S1": small_path,
            "S2": small_path * 1.01,
            "L1": np.linspace(20.0, 21.0, len(dates)),
            "L2": np.linspace(30.0, 30.5, len(dates)),
        },
        index=dates,
    )
    amount = pd.DataFrame(
        {
            "S1": 1_000_000.0,
            "S2": 1_200_000.0,
            "L1": 9_000_000.0,
            "L2": 10_000_000.0,
        },
        index=dates,
    )
    bond_close = pd.Series(1.0 + np.arange(len(dates)) * 0.0002, index=dates, name="511010")
    return close, amount, bond_close


def test_defensive_overlay_audit_is_research_only_and_non_deploying():
    close, amount, bond_close = _synthetic_panels()

    report = audit_defensive_overlay(
        close=close,
        amount=amount,
        bond_close=bond_close,
        ma_window=5,
        trial_windows=[3, 5, 8],
    )

    assert report["candidate"]["family"] == "defensive-ma-bond"
    assert report["candidate"]["role"] == "defensive"
    assert report["candidate"]["status"] == "draft_research_only"
    assert report["deployment_verdict"] in {"needs_human_review", "blocked"}

    assert report["decision_boundary"] == {
        "registry_write": False,
        "deployment_write": False,
        "production_allowed": False,
    }

    evidence = report["evidence"]
    assert evidence["ma_window"] == 5
    assert evidence["selected_window"] == 5
    assert evidence["n_trials"] == 3
    assert evidence["bear_days"] > 0
    assert evidence["bull_days"] > 0

    metrics = report["metrics"]
    assert set(metrics) == {
        "small_cap_ma_cash",
        "small_cap_ma_bond",
        "bond_when_bear",
        "incremental_bond_vs_cash",
    }
    for block in metrics.values():
        assert block["n_days"] > 0
        assert math.isfinite(block["annual_return"])
        assert math.isfinite(block["sharpe"])
        assert block["max_drawdown"] <= 0.0


def test_start_filters_evidence_after_signal_warmup():
    close, amount, bond_close = _synthetic_panels()
    start = str(close.index[-8].date())

    report = audit_defensive_overlay(
        close=close,
        amount=amount,
        bond_close=bond_close,
        ma_window=5,
        trial_windows=[5],
        start=start,
    )

    assert report["metrics"]["small_cap_ma_bond"]["n_days"] == 8
    assert report["evidence"]["bull_days"] > 0


def test_too_short_review_window_remains_blocked_and_non_deploying():
    close, amount, bond_close = _synthetic_panels()
    start = str(close.index[-2].date())

    report = audit_defensive_overlay(
        close=close,
        amount=amount,
        bond_close=bond_close,
        ma_window=5,
        trial_windows=[5],
        start=start,
    )

    assert report["deployment_verdict"] == "blocked"
    assert report["candidate"]["status"] == "draft_research_only"
    assert report["metrics"]["small_cap_ma_bond"]["n_days"] <= 2
    assert report["decision_boundary"] == {
        "registry_write": False,
        "deployment_write": False,
        "production_allowed": False,
    }
