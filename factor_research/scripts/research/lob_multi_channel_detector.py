"""Multi-channel rising-edge detector based on LOB paper core method.

Key ideas from the paper (Section 4.2-4.4):
  1. Four signal channels, each capturing a different dimension of stress
  2. MAX aggregation: score = max(ch1, ch2, ch3, ch4)
     (not SUM — MAX lets a single strong channel trigger detection)
  3. Rising-edge condition: detect the ONSET of accumulation, not the plateau
  4. Adaptive threshold from rolling percentile

Strictly look-ahead-free: all signals use rolling history BEFORE the detection day.
T日信号 → T+1日仓位决策。

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/lob_multi_channel_detector.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)


# ======================================================================
# 1. Four signal channels
# ======================================================================
def make_channels(close, amount):
    """Build 4 raw LOB-style signal channels for A-share macro indicators.

    Each channel is z-scored relative to its own rolling history (252d window).
    Values > 2.0 indicate significant deviation from normal regime.
    """
    ret = close.pct_change(fill_method=None)
    has_trade = amount > 0

    # Channel 1: Breadth stress (inverted risk_appetite)
    up_ratio = ((ret > 0) & has_trade).sum(axis=1) / has_trade.sum(axis=1)
    # Stress = high proportion of stocks declining
    ch1 = (1.0 - up_ratio)  # 0=all up, 1=all down

    # Channel 2: Volatility surge
    mkt_ret = ret.mean(axis=1).fillna(0.0)
    ch2 = mkt_ret.rolling(20).std()

    # Channel 3: Liquidity drain (inverted)
    mkt_amount = amount.sum(axis=1)
    liq = mkt_amount / mkt_amount.rolling(20).mean()
    ch3 = 1.0 / liq.clip(0.3, 5.0)  # low liquidity = high stress

    # Channel 4: Breadth deterioration (inverted ma_diffusion)
    ma20 = close.rolling(20).mean()
    valid = ma20.notna() & close.notna()
    above_ma = (close > ma20) & valid
    ma_diff = above_ma.sum(axis=1) / valid.sum(axis=1)
    ch4 = 1.0 - ma_diff  # low diffusion = high stress

    df = pd.DataFrame({
        "breadth_stress": ch1,
        "vol_surge": ch2,
        "liq_drain": ch3,
        "breadth_deterioration": ch4,
    }, index=close.index)

    # Z-score each channel vs its own 252d rolling history
    for col in df.columns:
        mu = df[col].rolling(252, min_periods=60).mean()
        sigma = df[col].rolling(252, min_periods=60).std().replace(0, 1.0)
        df[f"{col}_z"] = ((df[col] - mu) / sigma).clip(-5, 10)

    return df


# ======================================================================
# 2. MAX aggregation + rising-edge trigger
# ======================================================================
def detect_rising_edge(df, threshold=2.0, suppress_days=10, min_delta=0.0):
    """
    MAX aggregation: score_t = max(ch1_z, ch2_z, ch3_z, ch4_z)

    Rising-edge detection (Definition from LOB paper Section 4.3):
      1. score_t >= threshold
      2. score_t - score_{t-1} > 0 (score is rising)
      3. score_{t-1} - score_{t-2} <= 0 (previous step was not rising = onset)
      4. t - t_last >= suppress_days (minimum interval between triggers)

    Returns: trigger series (True = stress onset detected)
    """
    z_cols = [c for c in df.columns if c.endswith("_z")]
    score = df[z_cols].max(axis=1)  # MAX aggregation

    diff1 = score.diff()
    diff2 = diff1.diff()

    raw = (
        (score >= threshold)
        & (diff1 > min_delta)
        & (diff2 <= 0)  # previous not rising → onset
    )

    # Suppression: minimum interval between triggers
    trigger = pd.Series(False, index=df.index)
    last_pos = -10**9
    for pos, flag in enumerate(raw.fillna(False).values):
        if flag and pos - last_pos >= suppress_days:
            trigger.iloc[pos] = True
            last_pos = pos

    # Shift(1): T日检测 → T+1日仓位
    return trigger.shift(1, fill_value=False), score


# ======================================================================
# 3. Exposure from triggers
# ======================================================================
def exposure_from_trigger(trigger, cut_days=10, floor=0.0):
    """Binary exit: cut to floor for cut_days after trigger, then resume."""
    exp = pd.Series(1.0, index=trigger.index, dtype="float64")
    for pos in np.flatnonzero(trigger.values):
        end = min(len(exp), pos + cut_days + 1)
        exp.iloc[pos:end] = floor
    return exp


# ======================================================================
# 4. Main
# ======================================================================
def main():
    print("Loading data...", flush=True)
    close, vol, amount = load_price_panels("2010-01-01")
    factor = small_cap_factor(amount, 60)
    timing, _, _ = small_cap_timing(close, amount, 16)
    scheduled = build_rebalance_weights(factor, close, 25, 20)
    cfg = StrategyConfig(start="2010-01-01")

    # v2.0 baseline
    ret_v20, _ = backtest_weights(close, scheduled, timing.astype(float), cfg)

    print("Building 4 channels...", flush=True)
    df = make_channels(close, amount)
    print(f"  Channels: {[c for c in df.columns]}", flush=True)

    def cagr(ret):
        r = ret.fillna(0); n = max(len(r) / 252, 1)
        return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1

    print("\nGrid search (threshold × cut_days × floor)...", flush=True)
    results = []
    for threshold in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        for cut_days in [5, 10, 20, 40]:
            for floor in [0.0, 0.3, 0.5]:
                trigger, score = detect_rising_edge(df, threshold, suppress_days=max(10, cut_days))
                exp = exposure_from_trigger(trigger, cut_days, floor)
                t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
                ret, _ = backtest_weights(close, scheduled, t, cfg)

                a = cagr(ret[ret.index.year >= 2018])
                s = ret[ret.index.year >= 2018].mean() / ret[ret.index.year >= 2018].std() * np.sqrt(252)
                d = float(((1 + ret.fillna(0)).cumprod() / (1 + ret.fillna(0)).cumprod().cummax() - 1).min())
                n_triggers = int(trigger.sum())
                results.append({
                    "th": threshold, "cut": cut_days, "fl": floor,
                    "annual": a, "sharpe": s, "maxdd": d, "n_triggers": n_triggers,
                })

    df_r = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    df_r.to_csv(OUT / "lob_detector_results.csv", index=False)

    print("\n=== LOB Multi-Channel Detector Results ===")
    print(f"v2.0 baseline: annual={cagr(ret_v20[ret_v20.index.year >= 2018]):+.1%} "
          f"sharpe={ret_v20[ret_v20.index.year >= 2018].mean() / ret_v20[ret_v20.index.year >= 2018].std() * np.sqrt(252):.2f}")
    print("\nTop 10:")
    for _, r in df_r.head(10).iterrows():
        print(f"  th={r['th']:.1f} cut={r['cut']:>2d} fl={r['fl']:.1f} "
              f"annual={r['annual']:+.1%} sharpe={r['sharpe']:.2f} "
              f"maxdd={r['maxdd']:+.1%} triggers={r['n_triggers']}", flush=True)

    best = df_r.iloc[0]
    print(f"\nBest: th={best['th']:.1f} cut={best['cut']}d fl={best['fl']:.1f} "
          f"annual={best['annual']:+.1%} sharpe={best['sharpe']:.2f}")


if __name__ == "__main__":
    main()
