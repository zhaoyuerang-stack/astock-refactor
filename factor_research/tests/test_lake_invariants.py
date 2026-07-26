"""数据湖写路径不变量测试(2026-06-12 假崩盘事故防复发)。

Run:
    cd factor_research && python3 tests/test_lake_invariants.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.invariants import (  # noqa: E402
    LakeInvariantError,
    assert_price_panel_sane,
    check_cross_section_sanity,
)


def _long_panel(n_days=10, n_stocks=600, poison_last_day=False, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_days)
    codes = [f"{600000+i:06d}" for i in range(n_stocks)]
    rets = rng.normal(0.0, 0.01, size=(n_days, n_stocks))
    px = 100.0 * np.exp(np.cumsum(rets, axis=0))
    if poison_last_day:
        px[-1, : int(n_stocks * 0.7)] /= 190.0  # 70% 个股混入不复权口径(事故形态)
    return pd.DataFrame({
        "date": np.repeat(dates, n_stocks),
        "code": codes * n_days,
        "close": px.ravel(),
    })


def test_normal_panel_passes():
    report = assert_price_panel_sane(_long_panel())
    assert report["ok"] and report["days"]
    assert all(v["jump_frac"] < 0.01 for v in report["days"].values())


def test_poisoned_final_day_rejected():
    poisoned = _long_panel(poison_last_day=True)
    report = check_cross_section_sanity(poisoned)
    assert not report["ok"] and report["breaches"]
    try:
        assert_price_panel_sane(poisoned)
    except LakeInvariantError as e:
        assert "拒绝落盘" in str(e)
    else:
        raise AssertionError("毒面板必须被拒绝")


def test_small_cross_section_skipped_not_false_alarm():
    # 截面 < min_stocks 的日期跳过(新湖/小样本不误报)
    small = _long_panel(n_stocks=50, poison_last_day=True)
    report = check_cross_section_sanity(small)
    assert report["ok"] and not report["days"]


def test_compact_refuses_to_overwrite_on_poison():
    from lake.compact import _compact_dir

    with tempfile.TemporaryDirectory() as td:
        daily = Path(td) / "daily"
        daily.mkdir()
        out = Path(td) / "all.parquet"
        long = _long_panel(poison_last_day=True)
        for code, g in long.groupby("code"):
            g[["date", "close"]].to_parquet(daily / f"{code}.parquet", index=False)

        try:
            _compact_dir(daily, out, columns=["date", "close"], close_col="close")
        except LakeInvariantError:
            pass
        else:
            raise AssertionError("compact 必须拒绝毒数据")
        assert not out.exists(), "被拒绝时绝不能落盘"


def test_panel_fingerprint_detects_data_drift():
    from lake.fingerprint import panel_fingerprint, stamp_vintage

    long = _long_panel()
    panel = long.pivot(index="date", columns="code", values="close")
    fp = panel_fingerprint(panel)
    assert fp == panel_fingerprint(panel.copy())  # 同数据同指纹

    drifted = panel.copy()
    drifted.iloc[-1, 0] *= 1.001  # 末日单格漂移(抽样列 0 必中)
    assert panel_fingerprint(drifted) != fp

    v = stamp_vintage("data_lake:2018-01-01:2026-06-12", panel)
    assert v.endswith(f"#{fp}") and panel_fingerprint(pd.DataFrame()) == "empty"


def test_backtest_result_sentinel_flags_impossible_results():
    from core.engine import BacktestResult

    idx = pd.bdate_range("2026-01-05", periods=60)
    z = pd.Series(0.0, index=idx)

    normal = pd.Series(np.random.default_rng(7).normal(0.001, 0.015, 60), index=idx)
    assert BacktestResult(returns=normal, turnover=z, cost=z).anomalies == []

    crash = normal.copy()
    crash.iloc[-2] = -0.60  # 假崩盘形态:分散组合单日 -60% 超物理边界
    flags = BacktestResult(returns=crash, turnover=z, cost=z).anomalies
    assert flags and "疑数据问题" in flags[0]


if __name__ == "__main__":
    test_normal_panel_passes()
    test_poisoned_final_day_rejected()
    test_small_cross_section_skipped_not_false_alarm()
    test_compact_refuses_to_overwrite_on_poison()
    test_panel_fingerprint_detects_data_drift()
    test_backtest_result_sentinel_flags_impossible_results()
    print("✅ Lake invariant tests passed")
