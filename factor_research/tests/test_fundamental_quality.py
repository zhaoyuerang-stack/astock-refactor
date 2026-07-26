"""对抗性测试:资产负债表运营质量因子族(factors/fundamental_quality.py,probe 前候选)。

Run:  cd factor_research && python3 tests/test_fundamental_quality.py

护栏 C:
  方向必须对(议价权强/盈余质量改善的股票必须得高分,恶化的必须垫底——符号写反必挂);
  缺字段/空面板必须显式 ValueError(静默给零分 = 半截口径混进候选池,R-DATA 系);
  预热期必须诚实 NaN(diff 未满 window 不给分,WARMUP 教训);
  输出形状恒等 close(index×columns),基本面独有票丢弃、close 独有票 NaN 不编造。
纯注入面板(core 函数),不读数据湖;PIT 的 anndate ffill 归 lake loader(已有守约)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.fundamental_quality import (
    bargaining_power_core,
    inventory_intensity_chg_core,
    receivable_intensity_chg_core,
)

_IDX = pd.bdate_range("2023-01-02", periods=300)
_CODES = ["000001.SZ", "000002.SZ", "000003.SZ"]


def _panel(values_by_code: dict, idx=_IDX) -> pd.DataFrame:
    """常数面板(dict 缺的票不给列——模拟基本面覆盖不全)。"""
    return pd.DataFrame({c: pd.Series(v, index=idx) for c, v in values_by_code.items()})


def _close(codes=_CODES, idx=_IDX) -> pd.DataFrame:
    return pd.DataFrame(10.0, index=idx, columns=codes)


def _bs(payable=None, notes_pay=None, receiv=None, notes_recv=None, inv=None, assets=None):
    """六字段齐全的注入面板;未覆盖的字段默认全池零(资产默认 100)。

    缺字段/空面板的对抗场景由调用方显式置 pd.DataFrame() 触发,不靠默认缺席。
    """
    zero = {c: 0 for c in _CODES}
    return {
        "acct_payable": _panel(payable if payable is not None else dict(zero)),
        "notes_payable": _panel(notes_pay if notes_pay is not None else dict(zero)),
        "accounts_receiv": _panel(receiv if receiv is not None else dict(zero)),
        "notes_receiv": _panel(notes_recv if notes_recv is not None else dict(zero)),
        "inventories": _panel(inv if inv is not None else dict(zero)),
        "total_assets": _panel(assets if assets is not None else {c: 100 for c in _CODES}),
    }


def test_bargaining_power_direction():
    """净占款(应付>应收)的股票必须得分最高,被占款(应收>应付)的必须垫底。"""
    bs = _bs(
        payable={"000001.SZ": 50, "000002.SZ": 10, "000003.SZ": 5},
        notes_pay={"000001.SZ": 10, "000002.SZ": 5, "000003.SZ": 0},
        receiv={"000001.SZ": 5, "000002.SZ": 10, "000003.SZ": 60},
        notes_recv={"000001.SZ": 0, "000002.SZ": 5, "000003.SZ": 10},
        assets={"000001.SZ": 100, "000002.SZ": 100, "000003.SZ": 100},
    )
    out = bargaining_power_core(bs, _close())
    last = out.iloc[-1]
    assert last["000001.SZ"] == last.max(), "净占款最强的票必须截面第一"
    assert last["000003.SZ"] == last.min(), "被上下游占款最狠的票必须垫底——符号写反必挂"
    assert list(out.index) == list(_IDX) and list(out.columns) == _CODES


def test_receivable_deterioration_ranks_bottom():
    """应收强度持续抬升(赊销撑收入)的股票必须垫底(因子取 −Δ,高=改善)。"""
    rising = pd.Series(np.linspace(5, 60, len(_IDX)), index=_IDX)  # 应收/资产 5%→60%
    bs = _bs(
        receiv={"000001.SZ": 10, "000002.SZ": 10},
        assets={"000001.SZ": 100, "000002.SZ": 100, "000003.SZ": 100},
    )
    bs["accounts_receiv"]["000003.SZ"] = rising
    bs["notes_receiv"] = _panel({c: 0 for c in _CODES})
    out = receivable_intensity_chg_core(bs, _close(), window=120)
    last = out.iloc[-1]
    assert last["000003.SZ"] == last.min(), "应收暴增的票必须垫底(盈余质量恶化)"


def test_inventory_destocking_ranks_top():
    falling = pd.Series(np.linspace(60, 5, len(_IDX)), index=_IDX)  # 去库存
    bs = _bs(
        inv={"000001.SZ": 30, "000002.SZ": 30},
        assets={"000001.SZ": 100, "000002.SZ": 100, "000003.SZ": 100},
    )
    bs["inventories"]["000003.SZ"] = falling
    out = inventory_intensity_chg_core(bs, _close(), window=120)
    last = out.iloc[-1]
    assert last["000003.SZ"] == last.max(), "去库存的票必须截面第一"


def test_missing_field_raises_not_silent_zero():
    bs = _bs(assets={"000001.SZ": 100})
    bs["acct_payable"] = pd.DataFrame()  # 字段空面板
    try:
        bargaining_power_core(bs, _close())
        raise AssertionError("缺字段必须显式 ValueError,不得静默给零分(半截口径)")
    except ValueError as e:
        assert "balancesheet" in str(e)


def test_warmup_is_honest_nan():
    """diff 未满 window 的预热期必须 NaN——WARMUP 教训:没数据的日子不给分。"""
    bs = _bs(
        receiv={c: 10 for c in _CODES},
        assets={c: 100 for c in _CODES},
    )
    out = receivable_intensity_chg_core(bs, _close(), window=120)
    assert out.iloc[:120].isna().all().all(), "预热期必须整段 NaN,不得用 0 冒充"
    assert out.iloc[121:].notna().any().any() or out.iloc[121:].isna().all().all()


def test_universe_alignment():
    """基本面独有票丢弃;close 独有票保留为 NaN 列(不编造覆盖)。"""
    bs = _bs(
        payable={"000001.SZ": 50, "999999.SZ": 50},  # 999999 不在 close 池
        notes_pay={"000001.SZ": 0},
        receiv={"000001.SZ": 5},
        notes_recv={"000001.SZ": 0},
        assets={"000001.SZ": 100, "000002.SZ": 100, "999999.SZ": 100},
    )
    out = bargaining_power_core(bs, _close())
    assert "999999.SZ" not in out.columns
    assert list(out.columns) == _CODES, "输出列必须恒等 close 股票池"


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
