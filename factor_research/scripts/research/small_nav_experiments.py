"""Small-NAV Experiments — 自动 PoC 提示后的人工验证。

PoC 提示: small_cap_timing output[1] = small_nav 100% 被丢弃 → 可能是下一个 Band。

4 个 small_nav 派生信号实验,在 Band timing 基线之上加 overlay:

  V1 DD gate     : small_nav cumdrawdown < -20% 强制空仓 (深度风控)
  V2 Slope boost : 用 small_nav 5/20 日 slope 替代 dist 驱动 boost
  V3 RS gate     : small_nav / mkt_nav 相对强度,弱于市场时减仓
  V4 Adaptive cap: small_nav 滚动 vol 控制 exposure_cap (高 vol 时收紧)

判定标准 (vs Band 1.0x baseline):
  ✓ 任一段 Sharpe +0.05 且其他段不差 → SHADOW 候选
  ✓ Calmar +5% 任一段且其他不差 → SHADOW 候选
  ✗ 否则 → small_nav 这个输出无独立价值,关闭分支
"""
import warnings; warnings.filterwarnings("ignore")
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore
from lake.load_lake import load_prices, load_raw_close
from strategies.small_cap import build_rebalance_weights

print("Loading...", flush=True)
from lake.units import implied_amount

px = load_prices(start="2010-01-01", fields=("close", "volume"))
raw = load_raw_close(start="2010-01-01")
close, volume = px["close"], px["volume"]
amount = implied_amount(volume, raw)
prices = PricePanel(close=close, volume=volume, amount=amount)
ret_a = close.pct_change(fill_method=None).abs()
illiq = (ret_a / (amount.replace(0, np.nan) + 1)).rolling(20).mean()
factor = safe_zscore(mad_clip(illiq))
scheduled = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)


# small_cap_timing 解包 small_nav (PoC 提示的"被丢弃输出")
binary, small_nav, dist = small_cap_timing(close, amount, ma_window=16)
# 修正: rolling drawdown 不是 cummax (cum 长期累积导致一直触发)
nav_rolling_max = small_nav.rolling(252, min_periods=60).max()
nav_drawdown = small_nav / nav_rolling_max - 1


def band_base():
    """基线 Band timing."""
    dc = dist.clip(-0.5, 0.5)
    raw = (1.0 + dc * 8.0).clip(0.0, 1.5)
    above = (dc > 0).astype(float)
    return (raw * above).shift(1).fillna(0).astype(float)


def v1_dd_gate(threshold=-0.15):
    """Band × NOT(rolling 252d drawdown < -15%). 真正深跌时空仓."""
    base = band_base()
    dd_block = (nav_drawdown < threshold).shift(1).fillna(False)
    return base * (~dd_block).astype(float)


def v2_slope_boost(slope_window=5, scale=15.0, cap=1.5):
    """exposure = clip(1 + slope * scale, 0, cap) × I(slope > 0)."""
    slope = small_nav.pct_change(slope_window)
    raw = (1.0 + slope * scale).clip(0.0, cap)
    above = (slope > 0).astype(float)
    return (raw * above).shift(1).fillna(0).astype(float)


def v3_rs_gate(rs_threshold=-0.20):
    """Band × NOT(small severely underperform market). 用更宽阈值."""
    mkt_idx = close.pct_change(fill_method=None).mean(axis=1).fillna(0)
    mkt_nav = (1 + mkt_idx).cumprod()
    rs = small_nav / mkt_nav
    rs_signal = rs / rs.rolling(60, min_periods=20).mean() - 1
    weak_rs = (rs_signal < rs_threshold).shift(1).fillna(False)
    base = band_base()
    return base * (~weak_rs).astype(float)


def v5_nav_vol_target(target_vol=0.30, vol_window=60, min_exp=0.3, max_exp=1.5):
    """直接用 small_nav 滚动 vol 做 vol-target × binary. 不依赖 dist."""
    nav_vol = small_nav.pct_change(fill_method=None).rolling(vol_window, min_periods=20).std() * np.sqrt(252)
    target = (target_vol / nav_vol.replace(0, np.nan)).clip(min_exp, max_exp)
    return (binary.astype(float) * target).shift(1).fillna(0).astype(float)


def v4_adaptive_cap(target_vol=0.25, vol_window=20, cap_floor=0.5, cap_ceil=1.5):
    """exposure_cap = clip(target_vol / realized_vol, floor, ceil). dist 仍驱动."""
    nav_vol = small_nav.pct_change(fill_method=None).rolling(vol_window, min_periods=10).std() * np.sqrt(252)
    adaptive_cap = (target_vol / nav_vol.replace(0, np.nan)).clip(cap_floor, cap_ceil)
    dc = dist.clip(-0.5, 0.5)
    # 用 adaptive_cap 替代固定 1.5
    raw_unclipped = 1.0 + dc * 8.0
    raw = raw_unclipped.clip(0.0)
    raw = pd.Series(np.minimum(raw.values, adaptive_cap.values), index=raw.index)
    above = (dc > 0).astype(float)
    return (raw * above).shift(1).fillna(0).astype(float)


def run(timing, leverage=1.0, exposure_cap=1.5):
    engine = BacktestEngine(prices=prices, config=BacktestConfig(
        start="2010-01-01",
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=leverage))
    return engine.run(Signal(weights=scheduled, timing=timing, exposure_cap=exposure_cap)).returns.dropna()


periods = [
    ("IS 18-22", "2018-01-01", "2022-12-31"),
    ("OOS 23-26", "2023-01-01", "2026-06-05"),
    ("Stress 10-17", "2010-01-01", "2017-12-31"),
]


def m(ret, s, e):
    sub = ret.loc[s:e]
    annual = float(sub.mean() * 252)
    vol = float(sub.std() * np.sqrt(252))
    sh = annual / (vol + 1e-9)
    cum = (1 + sub).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    cal = annual / abs(mdd) if mdd < 0 else 0.0
    return annual, mdd, sh, cal


print(f"\n{'Variant':>35s}  {'period':>12s}     ann      dd      sh     cal")
print("-" * 90)


# Baselines
r_binary = run(binary.astype(float), leverage=1.25, exposure_cap=1.0)
r_band = run(band_base(), leverage=1.0, exposure_cap=1.5)

for nm, s, e in periods:
    m_ = m(r_binary, s, e)
    print(f"{'BINARY (baseline)':>35s}  {nm:>12s}  {m_[0]:+6.1%} {m_[1]:+6.1%} {m_[2]:+5.2f}  {m_[3]:+5.2f}")
print()
for nm, s, e in periods:
    m_ = m(r_band, s, e)
    print(f"{'BAND (current SHADOW)':>35s}  {nm:>12s}  {m_[0]:+6.1%} {m_[1]:+6.1%} {m_[2]:+5.2f}  {m_[3]:+5.2f}")
print()

# Experiments
for label, fn in [
    ("V1 Band × DD gate (rolling -15%)", v1_dd_gate),
    ("V2 Slope boost (5d, scale 15)", v2_slope_boost),
    ("V3 Band × RS gate (-20%)", v3_rs_gate),
    ("V4 Band + Adaptive cap (25% vol)", v4_adaptive_cap),
    ("V5 Binary × NAV vol-target 30%", v5_nav_vol_target),
]:
    try:
        timing = fn()
        r = run(timing, leverage=1.0, exposure_cap=1.5)
        for nm, s, e in periods:
            m_ = m(r, s, e)
            print(f"{label:>35s}  {nm:>12s}  {m_[0]:+6.1%} {m_[1]:+6.1%} {m_[2]:+5.2f}  {m_[3]:+5.2f}")
        print()
    except Exception as ex:
        print(f"{label}: ERROR {type(ex).__name__}: {str(ex)[:80]}")
        print()
