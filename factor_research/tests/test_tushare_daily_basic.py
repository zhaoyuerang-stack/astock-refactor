"""Tushare daily_basic:ts_code 归一 + 长表→面板 pivot(纯逻辑,不联网)。

Run:
    cd factor_research && python3 tests/test_tushare_daily_basic.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.load_lake import load_tushare_panel, pivot_daily_basic  # noqa: E402
from lake.schema import TUSHARE_DATASETS  # noqa: E402
from lake.sources.tushare import to_code  # noqa: E402


def test_to_code_normalizes_suffix():
    s = pd.Series(["600519.SH", "000001.SZ", "920547.BJ", "688256.SH"])
    assert list(to_code(s)) == ["600519", "000001", "920547", "688256"]
    print("✅ ts_code → code 去后缀(SH/SZ/BJ)")


def test_pivot_daily_basic_shape_and_align():
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "600519.SH", "000001.SZ"],
        "trade_date": ["20260610", "20260611", "20260610"],
        "total_mv": [1.6e8, 1.61e8, 3.0e7],
        "pb": [9.0, 9.1, 0.6],
    })
    idx = pd.to_datetime(["2026-06-10", "2026-06-11"])
    panels = pivot_daily_basic(df, idx, ["total_mv", "pb"])
    assert set(panels) == {"total_mv", "pb"}
    mv = panels["total_mv"]
    assert list(mv.index) == list(idx)               # 对齐到给定交易日
    assert mv.loc["2026-06-10", "600519"] == 1.6e8   # code 已去后缀
    assert pd.isna(mv.loc["2026-06-11", "000001"])   # 缺失日为 NaN(不 ffill)
    print("✅ pivot:对齐交易日 + code 去后缀 + 缺失不 ffill")


def test_pivot_filters_codes():
    df = pd.DataFrame({"ts_code": ["600519.SH", "000001.SZ"],
                       "trade_date": ["20260610", "20260610"], "pe": [25.0, 5.0]})
    idx = pd.to_datetime(["2026-06-10"])
    panels = pivot_daily_basic(df, idx, ["pe"], codes=["600519"])
    assert list(panels["pe"].columns) == ["600519"]  # 只保留请求的 code
    print("✅ pivot:codes 过滤")


def test_dispatcher_registry_and_modes():
    # 价格/资金/市场 = 当日对齐;财务/事件 = 公告日 ffill
    assert TUSHARE_DATASETS["daily_basic"][1] == "by_date"
    assert TUSHARE_DATASETS["moneyflow"][1] == "by_date"
    assert TUSHARE_DATASETS["fina_indicator"][1] == "anndate"
    assert TUSHARE_DATASETS["forecast"][1] == "anndate"
    try:
        load_tushare_panel("nonexistent", pd.to_datetime(["2026-06-12"]))
        assert False, "未知 dataset 应抛 KeyError"
    except KeyError:
        pass
    print("✅ dispatcher:口径路由正确 + 未知 dataset 抛错")


def test_dispatcher_missing_file_returns_empty():
    # 文件不存在 → 返回结构正确的空面板(不崩)
    panels = load_tushare_panel("suspend", pd.to_datetime(["2026-06-12"]), fields=["suspend_type"], codes=["600519"])
    assert "suspend_type" in panels and list(panels["suspend_type"].columns) == ["600519"]
    print("✅ dispatcher:缺文件返回空面板不崩")


if __name__ == "__main__":
    test_to_code_normalizes_suffix()
    test_pivot_daily_basic_shape_and_align()
    test_pivot_filters_codes()
    test_dispatcher_registry_and_modes()
    test_dispatcher_missing_file_returns_empty()
    print("\n🎉 tushare daily_basic tests passed!")
