"""signal_source_probe 纯逻辑单测(数据无关):中性化残差正交性、留存率、风格相关。"""
import importlib

import numpy as np
import pandas as pd
import pytest

probe = importlib.import_module("scripts.research.signal_source_probe")


def _frame(seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-31", periods=6, freq="ME")
    codes = [f"{i:06d}" for i in range(40)]
    return dates, codes, rng


def test_neutralize_kills_pure_control_proxy():
    dates, codes, rng = _frame(1)
    size = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    # 因子 = 纯 size 代理 → 去 size 后残差应≈0
    fac = size * 3.0 + 1.0
    resid = probe._neutralize(fac, [size])
    assert np.nanmean(np.abs(resid.values)) < 1e-6


def test_neutralize_preserves_orthogonal_factor():
    dates, codes, rng = _frame(2)
    size = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    fac = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    resid = probe._neutralize(fac, [size])
    # 残差与原因子高度相关(正交因子去 size 几乎不变)
    cs = [resid.loc[t].corr(fac.loc[t]) for t in dates]
    assert np.nanmean(cs) > 0.9


def test_xcorr_detects_proxy():
    dates, codes, rng = _frame(3)
    a = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    assert probe._xcorr(a, a) == pytest.approx(1.0, abs=1e-6)         # 自相关=1
    assert abs(probe._xcorr(a, -a)) == pytest.approx(1.0, abs=1e-6)   # 反相关=±1


def test_retention():
    assert probe._retention({"ic": 0.02}, {"ic": 0.013}) == "65%"
    assert probe._retention({"ic": 0.0}, {"ic": 0.01}) == "—"
    assert probe._retention(None, {"ic": 0.01}) == "—"


def test_seg_ic_shape():
    dates, codes, rng = _frame(4)
    fac = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    fwd = pd.DataFrame(rng.normal(size=(len(dates), len(codes))), index=dates, columns=codes)
    out = probe._seg_ic(fac, fwd, "2020-01-01", "2021-01-01")
    assert set(out) == {"ic", "icir", "months"} and out["months"] == len(dates)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
