"""Unit test for the 9-Gate Evaluation Framework.

Verifies that all 9 gates execute and return correct report formats using synthetic data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.analysis.nine_gates import NineGatesEvaluator, NineGatesReport
from core.engine import PricePanel, Signal


def _synthetic_data(T=150, N=30, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=T)
    codes = [f"{600000+i:06d}" for i in range(N)]
    
    close = pd.DataFrame(10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, (T, N)), axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(rng.integers(1000, 5000, (T, N)), index=dates, columns=codes)
    amount = volume * close
    
    # Generate a dummy factor
    factor = pd.DataFrame(rng.normal(0, 1, (T, N)), index=dates, columns=codes)
    
    return PricePanel(close=close, volume=volume, amount=amount), factor


def test_nine_gates_framework_executes_successfully():
    prices, factor = _synthetic_data()
    
    thesis = {
        "mechanism": "This is a synthetic test factor that checks lookback characteristics of liquidity and volatility premium.",
        "citation": "Test Thesis (2026)"
    }
    
    # Setup simple signal
    weights = pd.DataFrame(0.0, index=prices.close.index, columns=prices.close.columns)
    # Give some dummy holdings
    for dt in weights.index[::20]:
        weights.loc[dt, weights.columns[:5]] = 0.20
    weights = weights.ffill()
    
    signal = Signal(
        weights=weights,
        family="test_family",
        version="v1.0"
    )
    
    # Instantiate evaluator
    evaluator = NineGatesEvaluator(
        prices=prices,
        factor_df=factor,
        factor_builder=lambda p: factor, # stub builder
        thesis=thesis,
        n_trials=5,
        forward_days=20
    )
    
    reports = evaluator.evaluate_all(signal, start=str(prices.close.index[20].date()))
    
    # Verify the complete required gate set is present (含 7A 子门 → 共 10 份报告)。
    # 断言 gate ID 集合而非固定计数/连续整数,避免新增子门时这里陈旧失败(Task 9/16)。
    assert {r.gate_id for r in reports} == {0, 1, 2, 3, 4, 5, 6, 7, "7A", 8}
    for r in reports:
        assert isinstance(r.passed, bool)
        assert r.verdict in ("PASS", "WARN", "FAIL")
        assert isinstance(r.metrics, dict)
        assert isinstance(r.details, str)
        assert isinstance(r.reasons, list)
        
    # Build report
    report = NineGatesReport(
        factor_name="TestFactor_v1.0",
        run_date="2026-06-16",
        passed_all=all(r.passed for r in reports),
        reports=reports
    )
    
    markdown_out = report.to_markdown()
    assert "# Research-to-Production Risk Report: TestFactor_v1.0" in markdown_out
    assert "## Executive Summary of Gates" in markdown_out
    assert "Gate 0: Data Audit" in markdown_out
    
    print("✅ Nine-Gate Framework runs and generates clean reports on synthetic data.")


if __name__ == "__main__":
    test_nine_gates_framework_executes_successfully()
    print("\n🎉 Nine-Gate unit tests passed successfully!")
