"""业绩 SUE 因子族:对齐 close、express 实际优先于 forecast 预告、截面 z。合成面板,不依赖湖。"""
import numpy as np
import pandas as pd
import pytest

import factors.earnings as ef


def _fixture(monkeypatch):
    dates = pd.bdate_range("2020-01-01", periods=20)
    codes = ["000001", "000002", "000003", "000004"]
    close = pd.DataFrame(100.0, index=dates, columns=codes)
    # forecast 中点各异(避免 mad=0 退化):000001=10/000002=-20/000003=5/000004=50(最高)
    mids = {"000001": 10.0, "000002": -20.0, "000003": 5.0, "000004": 50.0}
    fmin = pd.DataFrame({c: [v - 5] * len(dates) for c, v in mids.items()}, index=dates)[codes]
    fmax = pd.DataFrame({c: [v + 5] * len(dates) for c, v in mids.items()}, index=dates)[codes]
    # express 实际:000001 高、000002 低,000003/4 缺(回退用 forecast)
    yoy = pd.DataFrame(np.nan, index=dates, columns=codes)
    yoy["000001"] = 80.0; yoy["000002"] = -50.0    # express 实际
    fc = {"p_change_min": fmin, "p_change_max": fmax}
    ex = {"yoy_net_profit": yoy}
    monkeypatch.setattr(ef, "_load_earnings_cache", lambda: (fc, ex))
    return close, codes


def test_sue_aligned_and_express_preferred(monkeypatch):
    close, codes = _fixture(monkeypatch)
    out = ef.sue(close)
    assert list(out.index) == list(close.index) and list(out.columns) == codes
    last = out.iloc[-1]
    # express 实际:000001(+80)最高、000002(-50)最低;000004 用 forecast 中点 50 居中
    assert last.idxmax() == "000001"
    assert last.idxmin() == "000002"
    assert abs(last.mean()) < 1e-6  # 截面 z 近似零均值


def test_forecast_surprise_uses_forecast_only(monkeypatch):
    close, codes = _fixture(monkeypatch)
    out = ef.earnings_forecast_surprise(close)
    # 只看预告 → 000004(中点50)最高,其余预告=0
    assert out.iloc[-1].idxmax() == "000004"


def test_family_registry(monkeypatch):
    close, _ = _fixture(monkeypatch)
    assert set(ef.EARNINGS_FACTORS) == {"sue", "earnings_forecast_surprise"}
    for name, fn in ef.EARNINGS_FACTORS.items():
        assert fn(close).shape == close.shape, name


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
