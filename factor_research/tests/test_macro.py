"""宏观时序层:防未来 lag 对齐(纯逻辑,不联网)。

Run:
    cd factor_research && python3 tests/test_macro.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.load_lake import align_macro  # noqa: E402


def test_monthly_no_lookahead():
    # 2024-05 参考月 CPI=3.0,应 2024-07-01 才可见(M+2)
    df = pd.DataFrame({"month": ["202404", "202405"], "nt_yoy": [2.0, 3.0]})
    # 4月值 6-01 可见,5月值 7-01 可见(M+2)
    idx = pd.to_datetime(["2024-05-20", "2024-06-15", "2024-06-30", "2024-07-01", "2024-08-10"])
    out = align_macro(df, idx)
    assert pd.isna(out.loc["2024-05-20", "nt_yoy"])    # 任何值都未到可见日 → NaN
    assert out.loc["2024-06-15", "nt_yoy"] == 2.0      # 4月值已可见(4月→6-01),5月值未到
    assert out.loc["2024-06-30", "nt_yoy"] == 2.0      # 仍只 4月值
    assert out.loc["2024-07-01", "nt_yoy"] == 3.0      # 5月值 7-01 可见
    assert out.loc["2024-08-10", "nt_yoy"] == 3.0      # ffill
    print("✅ 月度防未来:参考月 M 的值 M+2 月初才可见(M+1 月内仍只见 M-1)")


def test_daily_same_day_align():
    df = pd.DataFrame({"date": ["20260610", "20260611"], "on": [1.8, 1.9]})
    idx = pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"])
    out = align_macro(df, idx)
    assert out.loc["2026-06-10", "on"] == 1.8          # 当日对齐
    assert out.loc["2026-06-12", "on"] == 1.9          # ffill 到无数据日
    print("✅ 日度:当日对齐 + ffill")


if __name__ == "__main__":
    test_monthly_no_lookahead()
    test_daily_same_day_align()
    print("\n🎉 macro tests passed!")
