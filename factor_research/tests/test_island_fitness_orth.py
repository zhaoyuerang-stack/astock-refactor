"""§四修法②:islands fitness 的「正交增量」(orth)+「in-sample 内部稳定性」(stability)项。

- orth_weight>0:罚对 size/流动性风格暴露 → 逼搜索保持正交。
- stability_weight>0:edge 乘 in-sample 内部二分稳定性折扣 ∈[0,1]——只靠一半的过拟合候选被压;
  严格防泄露(只用训练面板,不碰 held-out OOS)。
默认(orth_weight=0, stability_weight=0)完全向后兼容,既有 fitness 硬断言测试不变。
"""
import numpy as np
import pandas as pd
import pytest

from factory.autoresearch.islands import _split_stability, _style_exposure


def _panels(n=12, m=40, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    codes = [f"{i:06d}" for i in range(m)]
    return dates, codes, rng


# ---- _style_exposure ----

def test_style_exposure_proxy_vs_orthogonal():
    dates, codes, rng = _panels(6, 40, 1)
    style = pd.DataFrame(rng.normal(size=(6, 40)), index=dates, columns=codes)
    assert _style_exposure(style, [style]) == pytest.approx(1.0, abs=1e-9)  # 与自身=1
    orth = pd.DataFrame(rng.normal(size=(6, 40)), index=dates, columns=codes)
    assert _style_exposure(orth, [style]) < 0.5  # 独立随机 → 低暴露


# ---- _split_stability ----

def test_stability_high_when_both_halves_predict():
    dates, codes, rng = _panels(12, 40, 2)
    fac = pd.DataFrame(rng.normal(size=(12, 40)), index=dates, columns=codes)
    fwd = pd.DataFrame(fac.values * 0.5 + rng.normal(scale=0.05, size=(12, 40)),
                       index=dates, columns=codes)  # 两半都同向 → 稳
    assert _split_stability(fac, fwd) > 0.6


def test_stability_low_when_one_half_only():
    dates, codes, rng = _panels(12, 40, 3)
    fac = pd.DataFrame(rng.normal(size=(12, 40)), index=dates, columns=codes)
    fwd = pd.DataFrame(rng.normal(size=(12, 40)), index=dates, columns=codes)
    fwd.iloc[:6] = (fac.iloc[:6] * 0.6).values  # 前半强相关、后半纯噪声 → 只靠一半
    assert _split_stability(fac, fwd) < 0.4


def test_stability_zero_when_halves_flip():
    dates, codes, rng = _panels(12, 40, 4)
    fac = pd.DataFrame(rng.normal(size=(12, 40)), index=dates, columns=codes)
    fwd = pd.DataFrame(np.zeros((12, 40)), index=dates, columns=codes)
    fwd.iloc[:6] = (fac.iloc[:6] * 0.6).values     # 前半正相关
    fwd.iloc[6:] = (-fac.iloc[6:] * 0.6).values    # 后半反相关 → 翻号
    assert _split_stability(fac, fwd) == 0.0


def test_stability_insufficient_dates_not_penalized():
    dates, codes, rng = _panels(4, 40, 5)
    fac = pd.DataFrame(rng.normal(size=(4, 40)), index=dates, columns=codes)
    assert _split_stability(fac, fac) == 1.0  # 日期不足以二分 → 不罚


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
