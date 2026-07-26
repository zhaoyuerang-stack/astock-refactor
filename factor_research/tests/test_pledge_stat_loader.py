"""pledge_stat 专用对齐口径测试。"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.load_lake import align_pledge_stat  # noqa: E402


def _sample():
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
        "end_date": ["20260703", "20260710", "20260703"],
        "pledge_count": [9, 10, 7],
        "unrest_pledge": [100.0, 110.0, 200.0],
        "rest_pledge": [5.0, 6.0, 0.0],
        "total_share": [1000.0, 1000.0, 2000.0],
        "pledge_ratio": [10.5, 11.6, 10.0],
    })


def test_align_pledge_stat_uses_strict_prior_end_date():
    panels = align_pledge_stat(
        _sample(),
        pd.to_datetime(["2026-07-03", "2026-07-06", "2026-07-10", "2026-07-13"]),
        codes=["000001"],
        max_stale_days=7,
    )

    ratio = panels["pledge_ratio"]["000001"]
    observed = panels["pledge_observed"]["000001"]

    assert pd.isna(ratio.loc["2026-07-03"])      # end_date == trade_date 不可当天用
    assert ratio.loc["2026-07-06"] == 10.5       # 只从下一交易日起可见
    assert ratio.loc["2026-07-10"] == 10.5       # 20260710 本日记录仍不可当天用
    assert ratio.loc["2026-07-13"] == 11.6
    assert observed.loc["2026-07-06"] is True
    assert observed.loc["2026-07-10"] is False   # ffill 日不是源端新观测日
    print("✅ pledge_stat:严格使用 end_date < trade_date")


def test_align_pledge_stat_expires_values_but_keeps_stale_state():
    panels = align_pledge_stat(
        _sample(),
        pd.to_datetime(["2026-07-06", "2026-07-15"]),
        codes=["000002"],
        max_stale_days=7,
    )

    assert panels["pledge_ratio"].loc["2026-07-06", "000002"] == 10.0
    assert pd.isna(panels["pledge_ratio"].loc["2026-07-15", "000002"])
    assert panels["pledge_count"].loc["2026-07-15", "000002"] != 7
    assert panels["pledge_stale_days"].loc["2026-07-15", "000002"] == 12
    assert panels["pledge_coverage_state"].loc["2026-07-15", "000002"] == "stale"
    print("✅ pledge_stat:超过有效期置空数值但保留 stale 状态")


def test_align_pledge_stat_marks_never_seen_without_zero_fill():
    panels = align_pledge_stat(
        _sample(),
        pd.to_datetime(["2026-07-06"]),
        codes=["000003"],
        max_stale_days=30,
    )

    assert pd.isna(panels["pledge_ratio"].loc["2026-07-06", "000003"])
    assert panels["pledge_observed"].loc["2026-07-06", "000003"] is False
    assert pd.isna(panels["pledge_stale_days"].loc["2026-07-06", "000003"])
    assert panels["pledge_coverage_state"].loc["2026-07-06", "000003"] == "never_seen"
    print("✅ pledge_stat:never_seen 不填 0")


def test_align_pledge_stat_accepts_microsecond_datetime_precision():
    df = _sample()
    df["end_date"] = pd.to_datetime(df["end_date"], format="%Y%m%d").astype("datetime64[us]")
    panels = align_pledge_stat(
        df,
        pd.to_datetime(["2026-07-06"]),
        codes=["000001"],
        max_stale_days=7,
    )

    assert panels["pledge_ratio"].loc["2026-07-06", "000001"] == 10.5
    print("✅ pledge_stat:兼容 parquet datetime64[us] 精度")


if __name__ == "__main__":
    test_align_pledge_stat_uses_strict_prior_end_date()
    test_align_pledge_stat_expires_values_but_keeps_stale_state()
    test_align_pledge_stat_marks_never_seen_without_zero_fill()
    test_align_pledge_stat_accepts_microsecond_datetime_precision()
    print("\n🎉 pledge_stat loader tests passed!")
