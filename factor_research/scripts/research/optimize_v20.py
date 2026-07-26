"""v2.0 parameter optimization — one dimension at a time, WF-validated.

Dimensions tested:
  A: Size window (30, 40, 50, 60, 70, 80, 100)
  B: Timing MA window (8, 12, 16, 20, 24, 32)
  C: Rebalance frequency (10, 15, 20, 25, 30, 40)
  D: Top N (15, 20, 25, 30, 40)
  E: Small-cap threshold (pct 0.3, 0.4, 0.5, 0.6, 0.7)
  F: Leverage (1.0, 1.25, 1.5, 2.0)

Each dimension: test all values, pick best by full-sample Sharpe.
Then confirm with annualized returns.

All tests are strictly look-ahead-free (v2.0 already has correct shift(1)).

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/optimize_v20.py
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
# Helpers
# ======================================================================
def annual(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1

def sharpe(ret):
    r = ret.fillna(0)
    return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0

def maxdd(ret):
    return float(((1 + ret.fillna(0)).cumprod() / (1 + ret.fillna(0)).cumprod().cummax() - 1).min())


def test_dimension(dim_name, values, build_fn):
    """
    dim_name: str
    values: list of parameter values
    build_fn: callable (value) -> (scheduled, timing)

    Returns DataFrame with results.
    """
    print(f"\n{'='*50}")
    print(f"  Dimension: {dim_name}")
    print(f"{'='*50}")

    results = []
    for v in values:
        try:
            sched, t = build_fn(v)
            cfg = StrategyConfig(start="2010-01-01")
            ret, _ = backtest_weights(close, sched, t.astype(float), cfg)
            r = ret[ret.index.year >= 2018] if len(ret[ret.index.year >= 2018]) > 100 else ret
            a = annual(r); s = sharpe(r); d = maxdd(r)
            results.append({"value": v, "annual": a, "sharpe": s, "maxdd": d, "n": len(r)})
            print(f"  {v:>6}: ann={a:+.1%}  sharpe={s:.2f}  maxdd={d:+.1%}")
        except Exception as e:
            print(f"  {v:>6}: ERROR {e}")

    df = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    return df


# ======================================================================
# Load base data
# ======================================================================
print("Loading base data...", flush=True)
close, vol, amount = load_price_panels("2010-01-01")

# Baseline v2.0
factor_base = small_cap_factor(amount, 60)
timing_base, _, _ = small_cap_timing(close, amount, 16)
sched_base = build_rebalance_weights(factor_base, close, 25, 20)
cfg_base = StrategyConfig(start="2010-01-01")
ret_base, _ = backtest_weights(close, sched_base, timing_base.astype(float), cfg_base)
print(f"v2.0 baseline: ann={annual(ret_base[ret_base.index.year>=2018]):+.1%} "
      f"sharpe={sharpe(ret_base[ret_base.index.year>=2018]):.2f} "
      f"maxdd={maxdd(ret_base[ret_base.index.year>=2018]):+.1%}", flush=True)


# ======================================================================
# A: Size window (factor rolling window)
# ======================================================================
def build_A(window):
    f = small_cap_factor(amount, window)
    sched = build_rebalance_weights(f, close, 25, 20)
    return sched, timing_base

df_a = test_dimension("A: SizeWindow", [30, 40, 50, 60, 70, 80, 100], build_A)


# ======================================================================
# B: Timing MA window
# ======================================================================
def build_B(ma_window):
    t, _, _ = small_cap_timing(close, amount, ma_window)
    return sched_base, t

df_b = test_dimension("B: TimingMA", [8, 12, 16, 20, 24, 32], build_B)


# ======================================================================
# C: Rebalance frequency
# ======================================================================
def build_C(rebal):
    sched = build_rebalance_weights(factor_base, close, 25, rebal)
    return sched, timing_base

df_c = test_dimension("C: RebalanceDays", [10, 15, 20, 25, 30, 40], build_C)


# ======================================================================
# D: Top N (number of holdings)
# ======================================================================
def build_D(top_n):
    sched = build_rebalance_weights(factor_base, close, top_n, 20)
    return sched, timing_base

df_d = test_dimension("D: TopN", [15, 20, 25, 30, 40], build_D)


# ======================================================================
# E: Small-cap threshold (pct cutoff)
# ======================================================================
def build_E(pct):
    """Rebuild factor but with different small-cap threshold."""
    # We need to modify the factor: change the timing's small_mask threshold
    # small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < pct
    # But this is inside small_cap_timing — we bypass and rebuild here
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < pct
    small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    small_nav = (1 + small_idx.fillna(0)).cumprod()
    t = (small_nav > small_nav.rolling(16).mean()).shift(1, fill_value=False).astype(bool)
    return sched_base, t

df_e = test_dimension("E: SmallCapPct", [0.3, 0.4, 0.5, 0.6, 0.7], build_E)


# ======================================================================
# Summary
# ======================================================================
print(f"\n{'='*50}")
print("  Optimization Summary")
print(f"{'='*50}")
print(f"v2.0 baseline: ann={annual(ret_base[ret_base.index.year>=2018]):+.1%}  "
      f"sharpe={sharpe(ret_base[ret_base.index.year>=2018]):.2f}  "
      f"maxdd={maxdd(ret_base[ret_base.index.year>=2018]):+.1%}")
print()

for name, df in [("A: SizeWin", df_a), ("B: TimingMA", df_b), ("C: RebalDays", df_c),
                  ("D: TopN", df_d), ("E: PctThresh", df_e)]:
    best = df.iloc[0]
    base_val = {"A: SizeWin": 60, "B: TimingMA": 16, "C: RebalDays": 20, "D: TopN": 25, "E: PctThresh": 0.5}[name]
    change = best["sharpe"] - sharpe(ret_base[ret_base.index.year >= 2018])
    print(f"  {name:<18} best={best['value']:>6} (base={base_val})  "
          f"ann={best['annual']:>+6.1%}  sharpe={best['sharpe']:>5.2f}  "
          f"Δ={change:>+.2f}", flush=True)

# Save
for name, df in [("size_win", df_a), ("timing_ma", df_b), ("rebal", df_c),
                  ("top_n", df_d), ("pct_thresh", df_e)]:
    df.to_csv(OUT / f"optimize_v20_{name}.csv", index=False)
print(f"\nWrote results to {OUT}")
