"""对抗性测试:隐含预期差因子族(factors/expectation_gap.py,probe 前候选)。

Run:  cd factor_research && python3 tests/test_expectation_gap.py

护栏 C:
  族的信号必须在"差"上——退化成纯成长(忽略估值)或纯价值(忽略兑现)的实现必挂;
  亏损股(pe_ttm≤0)必须诚实 NaN,不得编造分数(覆盖诚实);
  指引口径必须快报实际优先于预告中点(口径同 earnings.py,写反必挂);
  缺字段/空面板必须显式 ValueError(静默给零分 = 半截口径,R-DATA 系);
  输出形状恒等 close(index×columns),基本面独有票丢弃、close 独有票 NaN 不编造。
纯注入面板(core 函数),不读数据湖;PIT 的 anndate ffill / by_date 对齐归 lake loader。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.expectation_gap import (
    guidance_gap_core,
    implied_growth,
    implied_growth_gap_core,
    peg_inverse_core,
)

_IDX = pd.bdate_range("2023-01-02", periods=60)
_CODES = ["000001", "000002", "000003"]


def _panel(values_by_code: dict, idx=_IDX) -> pd.DataFrame:
    """常数面板(dict 缺的票不给列——模拟覆盖不全)。"""
    return pd.DataFrame({c: pd.Series(v, index=idx) for c, v in values_by_code.items()})


def _close(codes=_CODES, idx=_IDX) -> pd.DataFrame:
    return pd.DataFrame(10.0, index=idx, columns=codes)


def test_gap_not_pure_value():
    """同估值不同兑现:高兑现必须截面第一——退化成纯价值(只看 PE)的实现必挂。"""
    db = {"pe_ttm": _panel({c: 30.0 for c in _CODES})}
    fina = {"netprofit_yoy": _panel({"000001": 50.0, "000002": 10.0, "000003": -30.0})}
    out = implied_growth_gap_core(db, fina, _close())
    last = out.iloc[-1]
    assert last["000001"] == last.max(), "同估值下兑现最高的票必须第一"
    assert last["000003"] == last.min(), "同估值下增速塌陷的票必须垫底"
    assert list(out.index) == list(_IDX) and list(out.columns) == _CODES


def test_gap_not_pure_growth():
    """同兑现不同估值:价格要求最高(高 PE)必须垫底——退化成纯成长(忽略估值)的实现必挂。"""
    db = {"pe_ttm": _panel({"000001": 10.0, "000002": 30.0, "000003": 90.0})}
    fina = {"netprofit_yoy": _panel({c: 20.0 for c in _CODES})}
    out = implied_growth_gap_core(db, fina, _close())
    last = out.iloc[-1]
    assert last["000003"] == last.min(), "同兑现下价格要求最高(PE=90)的票必须垫底"
    assert last["000001"] == last.max(), "同兑现下价格要求最低(PE=10)的票必须第一"


def test_loss_maker_honest_nan():
    """亏损股(pe_ttm≤0)必须整列 NaN——本族只对盈利宇宙有定义,不给亏损股编分。"""
    db = {"pe_ttm": _panel({"000001": -15.0, "000002": 30.0, "000003": 60.0})}
    fina = {"netprofit_yoy": _panel({c: 20.0 for c in _CODES})}
    out = implied_growth_gap_core(db, fina, _close())
    assert out["000001"].isna().all(), "亏损股必须诚实 NaN,不得编造分数"
    assert out[["000002", "000003"]].iloc[-1].notna().all()


def test_implied_growth_rank_invariant_to_r():
    """g_implied 是 PE 的单调变换:discount_rate 只改水平不改截面排序(族的信号在差上)。"""
    pe = _panel({"000001": 10.0, "000002": 30.0, "000003": 90.0})
    r1 = implied_growth(pe, 0.06).iloc[-1].rank()
    r2 = implied_growth(pe, 0.12).iloc[-1].rank()
    assert (r1 == r2).all(), "隐含增速的截面排序必须与 r 无关"


def test_guidance_express_priority():
    """快报实际必须优先于预告中点——同 earnings.py 口径,写反(预告优先)必挂。"""
    db = {"pe_ttm": _panel({c: 30.0 for c in _CODES})}
    fc = {
        "p_change_min": _panel({"000001": -60.0, "000002": 5.0, "000003": 25.0}),
        "p_change_max": _panel({"000001": -40.0, "000002": 15.0, "000003": 35.0}),
    }
    ex = {"yoy_net_profit": _panel({"000001": 50.0})}  # 仅 000001 有快报实际
    out = guidance_gap_core(db, fc, ex, _close())
    last = out.iloc[-1]
    # 快报(+50)必须覆盖预告中点(−50):若实现错用预告,000001 垫底而非第一
    assert last["000001"] == last.max(), "有快报实际的票必须按快报计(+50),预告中点(−50)必须被覆盖"


def test_guidance_no_coverage_is_nan():
    """无预告无快报的票必须 NaN(条件披露覆盖有偏,不硬造指引)。"""
    db = {"pe_ttm": _panel({c: 30.0 for c in _CODES})}
    fc = {
        "p_change_min": _panel({"000001": 10.0, "000002": 20.0}),
        "p_change_max": _panel({"000001": 20.0, "000002": 30.0}),
    }
    ex = {"yoy_net_profit": _panel({"000001": 15.0})}
    out = guidance_gap_core(db, fc, ex, _close())
    assert out["000003"].isna().all(), "无任何指引的票必须 NaN"


def test_peg_direction():
    """高增速低估值必须第一;负增速高估值必须垫底(乘性参数化同一机制)。"""
    db = {"pe_ttm": _panel({"000001": 10.0, "000002": 40.0, "000003": 80.0})}
    fina = {"netprofit_yoy": _panel({"000001": 40.0, "000002": 20.0, "000003": -10.0})}
    out = peg_inverse_core(db, fina, _close())
    last = out.iloc[-1]
    assert last["000001"] == last.max(), "高增速低估值(PEG 最优)必须第一"
    assert last["000003"] == last.min(), "负增速高估值必须垫底"


def test_missing_field_raises_not_silent_zero():
    db = {"pe_ttm": pd.DataFrame()}  # 空面板
    fina = {"netprofit_yoy": _panel({c: 20.0 for c in _CODES})}
    try:
        implied_growth_gap_core(db, fina, _close())
        raise AssertionError("缺字段必须显式 ValueError,不得静默给零分(半截口径)")
    except ValueError as e:
        assert "daily_basic" in str(e)


def test_universe_alignment():
    """基本面独有票丢弃;close 独有票保留为 NaN 列(不编造覆盖)。"""
    db = {"pe_ttm": _panel({"000001": 30.0, "999999": 30.0})}  # 999999 不在 close 池
    fina = {"netprofit_yoy": _panel({"000001": 20.0, "999999": 20.0})}
    out = implied_growth_gap_core(db, fina, _close())
    assert "999999" not in out.columns
    assert list(out.columns) == _CODES, "输出列必须恒等 close 股票池"
    assert out["000002"].isna().all(), "无基本面覆盖的票必须 NaN,不编造"


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
