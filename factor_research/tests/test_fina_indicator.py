"""fina_indicator loader:公告日 ffill 防未来(纯逻辑,不联网)。

Run:
    cd factor_research && python3 tests/test_fina_indicator.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.load_lake import ffill_by_anndate  # noqa: E402


def test_anndate_ffill_no_lookahead():
    # 600519 在 2026-04-29 公告 Q1(roe=10),000001 在 2026-04-20 公告(roe=8)
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "000001.SZ"],
        "ann_date": ["20260429", "20260420"],
        "end_date": ["20260331", "20260331"],
        "roe": [10.0, 8.0],
    })
    idx = pd.to_datetime(["2026-04-19", "2026-04-25", "2026-05-06"])
    panels = ffill_by_anndate(df, ["roe"], idx)
    roe = panels["roe"]
    assert pd.isna(roe.loc["2026-04-19", "600519"])  # 公告(4-29)前不可见 → 防未来
    assert pd.isna(roe.loc["2026-04-25", "600519"])  # 仍在公告前
    assert roe.loc["2026-05-06", "600519"] == 10.0   # 公告后可见 + ffill
    assert roe.loc["2026-04-25", "000001"] == 8.0    # 000001 已于 4-20 公告 → 4-25 可见
    print("✅ 公告日 ffill:公告前不可见(防未来),公告后 ffill")


def test_code_normalize_and_dedup():
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "600519.SH"],
        "ann_date": ["20260429", "20260429"],  # 同公告日重复 → 取 last
        "end_date": ["20260331", "20260331"],
        "roe": [9.0, 10.0],
    })
    idx = pd.to_datetime(["2026-05-06"])
    roe = ffill_by_anndate(df, ["roe"], idx)["roe"]
    assert list(roe.columns) == ["600519"]      # ts_code 去后缀
    assert roe.loc["2026-05-06", "600519"] == 10.0  # 同 key 取 last
    print("✅ code 去后缀 + 同公告日去重取 last")


if __name__ == "__main__":
    test_anndate_ffill_no_lookahead()
    test_code_normalize_and_dedup()
    print("\n🎉 fina_indicator tests passed!")
