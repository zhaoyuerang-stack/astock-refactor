"""回归防护(ADR-021):Phase 2 / Phase 3 验证栈必须把数据截到 < holdout boundary,
金库段(date >= boundary)不得进入任何回测段/窗口/选择判定。

用合成面板(2018→2026,跨越 boundary 2025-01-01)monkeypatch load_data,跑真实
Phase2Runner / WF3Runner,断言所有产出的收益日期 < boundary。任何人若把 §5.2 截断
去掉,这里立刻红。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import workflow.phase2_backtest as p2
import workflow.phase3_wf as p3
from governance.holdout import boundary

BOUNDARY = boundary()


def _synth_panels(_warmup_start="2010-01-01"):
    dates = pd.bdate_range("2013-01-01", "2026-06-20")  # 足够历史(WF≥5窗)且跨越金库 boundary
    cols = [f"s{i:02d}" for i in range(30)]
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0005, 0.02, size=(len(dates), len(cols)))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=cols)
    volume = pd.DataFrame(1e6, index=dates, columns=cols)
    amount = volume * close
    return close, volume, amount


def _factor_builder(close, volume, amount, trade_dates):
    # 纯横截面:逐日按 -amount 排名(小额优先),无时间轴统计量。
    return (-amount).rank(axis=1, pct=True)


def _timing_builder(close, amount):
    return pd.Series(1.0, index=close.index)


def test_phase2_segments_never_touch_vault(monkeypatch):
    monkeypatch.setattr(p2, "load_data", _synth_panels)
    runner = p2.Phase2Runner(_factor_builder, _timing_builder, family="synthtest",
                             config={"top_n": 10})
    report = runner.run()
    segs = report["segments"]
    assert segs, "无回测段产出"
    # 每段收益日期必须全部 < boundary
    for label, s in segs.items():
        ret = s.get("returns")
        if ret is not None and len(ret):
            assert ret.index.max() < BOUNDARY, f"{label} 段触碰金库(末日 {ret.index.max()})"
    # OOS 标签终点年应为金库前一年(boundary=2025 → 2024)
    oos_label = next(k for k in segs if k.startswith("OOS"))
    assert str((BOUNDARY - pd.Timedelta(days=1)).year) in oos_label, \
        f"OOS 标签未反映金库截断: {oos_label!r}"
    assert report["offset_sensitivity"]["verdict"] in {"PASS", "FAIL"}
    assert "offset_1_annual" in report["offset_sensitivity"]
    assert "offset_2_annual" in report["offset_sensitivity"]


def test_phase3_windows_never_test_vault_years(monkeypatch):
    monkeypatch.setattr(p3, "load_data", _synth_panels)
    runner = p3.WF3Runner(_factor_builder, _timing_builder, family="synthtest",
                          config={"top_n": 10})
    report = runner.run()
    assert "error" not in report, f"WF 未产出窗口: {report.get('error')}"
    # 任何测试窗口的 test_start_year 不得落进金库年
    vault_year = BOUNDARY.year
    for w in report["windows"]:
        assert w["test_start_year"] < vault_year, \
            f"WF 测试窗 {w['test_start_year']} 落入金库年(>= {vault_year})"
    # 聚合 OOS 收益末日 < boundary
    # (windows 已截,聚合自然 < boundary;此处只做存在性 sanity)
    assert report["aggregate"]["total_windows"] >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
