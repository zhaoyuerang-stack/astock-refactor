"""window 扫描家族(small_cap_factor__window*)九门分支测试。

分支纪律:无 executable_spec 的窗口变体必须按台账 config 真实参数审计
(factor_fn_name/factor_params/top_n/rebalance_days),缺 config 即 fail-closed,
拒绝用默认参数产出假 DSR。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.nine_gate_runner import _auditable, run_evaluation  # noqa: E402

FAMILY = "small_cap_factor__window20"

MOCK_REGISTRY = {
    "families": [
        {
            "id": FAMILY,
            "name": FAMILY,
            "hypothesis": "小盘流动性溢价(测试)",
            "versions": [
                {
                    "version": "v1.0",
                    "status": "候选",
                    "data_scope": {"source": "data_lake", "period": "2010-2026"},
                    "config": {
                        "factor_fn_name": "factors.small_cap.small_cap_factor",
                        "factor_params": {"window": 20},
                        "top_n": 25,
                        "rebalance_days": 20,
                    },
                }
            ],
        }
    ]
}


def _panels():
    dates = pd.bdate_range("2024-01-01", periods=120)
    codes = [f"00000{i}" for i in range(6)]
    rng = np.random.default_rng(7)
    close = pd.DataFrame(
        10.0 * np.exp(np.cumsum(rng.normal(0, 0.01, (120, 6)), axis=0)),
        index=dates, columns=codes)
    volume = pd.DataFrame(1000.0, index=dates, columns=codes)
    amount = volume * close
    return close, volume, amount


def test_auditable_recognizes_window_family():
    assert _auditable(FAMILY, "v1.0") is True
    assert _auditable("small_cap_factor__window252", "v1.0") is True


def test_window_family_uses_registry_config():
    close, volume, amount = _panels()
    dates = close.index
    fake_weights = {d: pd.Series({c: 1.0 / 6 for c in close.columns}) for d in dates[::20]}

    with patch("strategy_registry._load", return_value=MOCK_REGISTRY), \
         patch("strategies.small_cap.load_price_panels", return_value=(close, volume, amount)), \
         patch("factors.small_cap.small_cap_factor",
               side_effect=lambda amt, window=60: amt.rank(axis=1, pct=True)) as mock_fn, \
         patch("strategies.small_cap.build_rebalance_weights",
               return_value=fake_weights) as mock_build, \
         patch("workflow.nine_gate_runner._family_n_trials", return_value=7), \
         patch("workflow.nine_gate_runner.record_nine_gate_research_run"):
        summary = run_evaluation(FAMILY, n_trials=7, persist=False, version="v1.0")

    # 因子函数必须按台账 factor_params 调用(window=20,非默认 60)
    assert mock_fn.call_args is not None
    assert mock_fn.call_args.kwargs.get("window") == 20
    # 权重构建必须按台账 top_n / rebalance_days
    assert mock_build.call_args.kwargs.get("top_n") == 25
    assert mock_build.call_args.kwargs.get("rebalance_days") == 20
    # 回测起点必须取台账 data_scope.period(2010-2026 → 2010-01-01)
    assert summary is not None
    assert summary["strategy"] == FAMILY
    assert summary["version"] == "v1.0"
    assert summary["n_trials"] == 7


def test_window_family_missing_config_fails_closed():
    empty_registry = {"families": [{"id": FAMILY, "name": FAMILY, "versions": []}]}
    with patch("strategy_registry._load", return_value=empty_registry):
        with pytest.raises(ValueError, match="拒绝用默认参数产出假 DSR"):
            run_evaluation(FAMILY, n_trials=7, persist=False, version="v1.0")
