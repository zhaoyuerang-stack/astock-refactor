"""pledge_stat 风险/状态型因子语义测试。"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.pledge import build_pledge_risk_signals  # noqa: E402


def _panels():
    idx = pd.bdate_range("2026-01-01", periods=140)
    ratio = pd.DataFrame(index=idx, columns=["HIGH", "WORSE", "IMPROVE", "STALE", "NEVER"], dtype=float)
    ratio["HIGH"] = 35.0
    ratio["WORSE"] = 10.0
    ratio.loc[idx[-1], "WORSE"] = 16.0
    ratio["IMPROVE"] = 38.0
    ratio.loc[idx[-1], "IMPROVE"] = 28.0
    ratio["STALE"] = pd.NA
    ratio["NEVER"] = pd.NA

    state = pd.DataFrame("current", index=idx, columns=ratio.columns, dtype=object)
    state["STALE"] = "stale"
    state["NEVER"] = "never_seen"

    stale_days = pd.DataFrame(3.0, index=idx, columns=ratio.columns)
    stale_days["STALE"] = 90.0
    stale_days["NEVER"] = pd.NA
    return {
        "pledge_ratio": ratio,
        "pledge_coverage_state": state,
        "pledge_stale_days": stale_days,
    }


def test_pledge_risk_signals_are_state_based_not_low_ratio_rewards():
    out = build_pledge_risk_signals(_panels(), high_ratio_threshold=30.0)
    last = out["pledge_high_risk"].iloc[-1]

    assert last["HIGH"] == 1.0
    assert last["WORSE"] == 0.0
    assert pd.isna(last["STALE"])
    assert pd.isna(last["NEVER"])
    assert "pledge_low_risk" not in out
    print("✅ pledge factor:高质押是风险标记,缺失不奖励")


def test_pledge_worsening_and_improvement_use_week_windows():
    out = build_pledge_risk_signals(_panels(), high_ratio_threshold=30.0, improvement_drop_pp=5.0)

    assert out["pledge_worsening_4w"].iloc[-1]["WORSE"] == 1.0
    assert out["pledge_worsening_4w"].iloc[-1]["IMPROVE"] == 0.0
    assert out["pledge_improvement_4w"].iloc[-1]["IMPROVE"] == 1.0
    assert out["pledge_improvement_4w"].iloc[-1]["WORSE"] == 0.0
    print("✅ pledge factor:恶化/改善按周窗口识别")


def test_pledge_stale_and_never_seen_only_emit_coverage_flags():
    out = build_pledge_risk_signals(_panels(), max_stale_days=30)
    last = out["pledge_coverage_flag"].iloc[-1]

    assert last["HIGH"] == "current"
    assert last["STALE"] == "stale"
    assert last["NEVER"] == "never_seen"
    assert pd.isna(out["pledge_worsening_4w"].iloc[-1]["STALE"])
    assert pd.isna(out["pledge_improvement_4w"].iloc[-1]["NEVER"])
    print("✅ pledge factor:陈旧/未覆盖只作为 coverage flag")


if __name__ == "__main__":
    test_pledge_risk_signals_are_state_based_not_low_ratio_rewards()
    test_pledge_worsening_and_improvement_use_week_windows()
    test_pledge_stale_and_never_seen_only_emit_coverage_flags()
    print("\n🎉 pledge factor tests passed!")
