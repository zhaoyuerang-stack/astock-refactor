"""CNE6 风格中性化审计:风格面板构建 + loadings 纯逻辑(轻量,不全量加载)。

Run:
    cd factor_research && python3 tests/test_style_neutralization.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.research.style_neutralization import build_cne6_styles, style_loadings  # noqa: E402

EXPECTED = {"Size", "Liquidity", "Beta", "Momentum", "ResidVol", "Value_BP", "Value_EP", "Growth", "Quality"}


def _synthetic():
    idx = pd.bdate_range("2020-01-01", periods=320)
    rng = np.random.default_rng(1)
    codes = [f"{600000+i}" for i in range(150)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (320, 150)), axis=0)),
                         index=idx, columns=codes)
    amount = pd.DataFrame(rng.lognormal(18, 1.0, (320, 150)), index=idx, columns=codes)
    return close, amount


def test_build_styles_has_size_and_market_styles():
    close, amount = _synthetic()
    styles = build_cne6_styles(close, amount)
    # 不依赖基本面缓存的风格(SizeLiq/Beta/Momentum/ResidVol)必须建出且非全空
    for k in ("Size", "Liquidity", "Beta", "Momentum", "ResidVol"):
        assert k in styles, f"缺风格 {k}"
        assert styles[k].shape == close.shape
        assert styles[k].iloc[-1].notna().sum() > 50, f"{k} 末日覆盖过低"
    assert set(styles) == EXPECTED  # 全风格键齐全(基本面风格可能因无缓存全 NaN,但键在)
    print("✅ CNE6 风格子集键齐全 + 量价风格有覆盖")


def test_style_loadings_self_correlation_is_one():
    close, amount = _synthetic()
    styles = build_cne6_styles(close, amount)
    size = styles["Size"]
    dates = close.index[::20]
    load = dict(style_loadings(size, styles, dates))
    assert abs(load["Size"] - 1.0) < 1e-6  # 因子对自身风格相关=1(健全性)
    for v in load.values():
        assert -1.0001 <= v <= 1.0001
    print("✅ loadings: 自相关=1,取值合法")


if __name__ == "__main__":
    test_build_styles_has_size_and_market_styles()
    test_style_loadings_self_correlation_is_one()
    print("\n🎉 style neutralization tests passed!")
