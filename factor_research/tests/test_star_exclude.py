"""科创板(688)处理:小盘 universe 显式排除。

Run:
    cd factor_research && python3 tests/test_star_exclude.py
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, _drop_star  # noqa: E402


def test_drop_star_removes_688_columns():
    idx = pd.bdate_range("2024-01-01", periods=2)
    df = pd.DataFrame({"600519": [1.0, 2], "688256": [3.0, 4], "300750": [5.0, 6]}, index=idx)
    (out,) = _drop_star(df)
    assert list(out.columns) == ["600519", "300750"]  # 688 被剔除
    print("✅ _drop_star 剔除 688 列")


def test_exclude_star_default_on():
    assert StrategyConfig().exclude_star is True  # 默认排除科创板(保留验证过的口径)
    print("✅ exclude_star 默认 True")


if __name__ == "__main__":
    test_drop_star_removes_688_columns()
    test_exclude_star_default_on()
    print("\n🎉 科创板 universe tests passed!")
