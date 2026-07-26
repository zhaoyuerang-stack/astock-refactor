"""北向资金因子族:对齐 close、截面 z-score、累积方向正确。逻辑用合成面板,不依赖数据湖。"""
import numpy as np
import pandas as pd
import pytest

import factors.northbound as nb


def _fixture(monkeypatch):
    dates = pd.bdate_range("2020-01-01", periods=40)
    codes = ["000001", "000002", "000003", "000004"]
    close = pd.DataFrame(100.0, index=dates, columns=codes)
    # 合成北向持股比例:000001 单调增持(累积为正),000002 单调减持,其余持平
    hold = pd.DataFrame(1.0, index=dates, columns=codes)
    hold["000001"] = np.linspace(1.0, 3.0, len(dates))
    hold["000002"] = np.linspace(3.0, 1.0, len(dates))
    panel = {
        "northbound_hold_pct": hold,
        "northbound_hold_shares_chg_1d": hold.diff().fillna(0.0),
        "northbound_buy_value_1d": hold.diff().fillna(0.0) * 1e6,
    }
    monkeypatch.setattr(nb, "_load_nb_cache", lambda: panel)
    return close, codes


def test_factors_aligned_to_close(monkeypatch):
    close, codes = _fixture(monkeypatch)
    for fn in (nb.northbound_accumulation, nb.northbound_hold_level, nb.northbound_flow_strength):
        out = fn(close)
        assert list(out.index) == list(close.index)
        assert list(out.columns) == codes


def test_accumulation_direction(monkeypatch):
    close, codes = _fixture(monkeypatch)
    acc = nb.northbound_accumulation(close, window=20)
    last = acc.iloc[-1]
    # 增持股截面 z 最高,减持股最低
    assert last.idxmax() == "000001"
    assert last.idxmin() == "000002"
    # 截面 z-score:近似零均值
    assert abs(last.mean()) < 1e-6


def test_hold_level_ranks_overweight_top(monkeypatch):
    close, codes = _fixture(monkeypatch)
    lvl = nb.northbound_hold_level(close)
    # 末日 000001 持仓最高 → z 最高
    assert lvl.iloc[-1].idxmax() == "000001"


def test_family_registry_callables(monkeypatch):
    close, _ = _fixture(monkeypatch)
    assert set(nb.NORTHBOUND_FACTORS) >= {
        "northbound_accumulation_20d", "northbound_hold_level", "northbound_flow_strength_5d"
    }
    for name, fn in nb.NORTHBOUND_FACTORS.items():
        out = fn(close)
        assert out.shape == close.shape, name


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
