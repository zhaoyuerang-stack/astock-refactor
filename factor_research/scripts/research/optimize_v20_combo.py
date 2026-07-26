"""v2.0 combined optimal params: SizeWin30 + Rebal15 + Top30 — WF validation.

Test 3 combos against baseline:
  v2.0_base: (60, 20, 25) — original
  v2.1_A:    (30, 20, 25) — only size window optimized
  v2.1_C:    (60, 15, 25) — only rebalance optimized
  v2.1_full: (30, 15, 30) — all 3 best values combined

WF protocol: 2010-2014 warmup, 2015-2026 OOS. Same as all prior WF.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/optimize_v20_combo.py
"""
import os
import sys
from pathlib import Path

import numpy as np

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

close, vol, amount = load_price_panels("2010-01-01")


def build_v20(sw=60, reb=20, tn=25, ma=16):
    """Build v2.0 variant with given params."""
    f = small_cap_factor(amount, sw)
    sched = build_rebalance_weights(f, close, tn, reb)
    t, _, _ = small_cap_timing(close, amount, ma)
    return sched, t


def run_variant(name, sw, reb, tn, ma=16):
    sched, t = build_v20(sw, reb, tn, ma)
    cfg = StrategyConfig(start="2010-01-01")
    ret, _ = backtest_weights(close, sched, t.astype(float), cfg)
    return ret


def cagr(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1


def sharpe(ret):
    r = ret.fillna(0)
    return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0


def maxdd(ret):
    return float(((1 + ret.fillna(0)).cumprod() / (1 + ret.fillna(0)).cumprod().cummax() - 1).min())


print("=== v2.0 Combined Parameter Optimization ===")

variants = {
    "v2.0_base":  (60, 20, 25),
    "v2.1_A_size30": (30, 20, 25),
    "v2.1_C_reb15": (60, 15, 25),
    "v2.1_D_top30": (60, 20, 30),
    "v2.1_full":  (30, 15, 30),
}

rets = {}
for name, (sw, reb, tn) in variants.items():
    print(f"  {name}: sw={sw} reb={reb} top={tn}...", flush=True)
    rets[name] = run_variant(name, sw, reb, tn)

# ── Full-sample metrics ──
print(f"\n{'Name':<20} {'Ann':>9} {'Sharpe':>7} {'MaxDD':>8}")
for name in variants:
    r = rets[name][rets[name].index.year >= 2018]
    print(f"{name:<20} {cagr(r):>+8.1%}  {sharpe(r):>5.2f}  {maxdd(r):>+7.1%}")

# ── WF: test each pair vs baseline ──
print("\n=== Walk-Forward: v2.1_full(30,15,30) vs Base(60,20,25) ===")
years = sorted(set(close.dropna(how="all").index.year))
wf_years = [y for y in years if y >= 2015]

for label, (a_name, b_name) in [("full vs base", ("v2.1_full", "v2.0_base")),
                                   ("size30 vs base", ("v2.1_A_size30", "v2.0_base")),
                                   ("reb15 vs base", ("v2.1_C_reb15", "v2.0_base"))]:
    wins = 0; total = 0; deltas = []
    print(f"\n  {label}:")
    print(f"  {'Year':>6} {a_name:>10} {b_name:>10} {'Δ':>9}")
    for y in wf_years:
        ra = rets[a_name][rets[a_name].index.year == y]
        rb = rets[b_name][rets[b_name].index.year == y]
        if len(ra) < 50: continue
        aa = cagr(ra); ab = cagr(rb); d = aa - ab
        deltas.append(d); total += 1
        if d > 0: wins += 1
        print(f"  {y:>6} {aa:>+9.1%} {ab:>+9.1%} {d:>+8.1%}")
    print(f"  WinRate: {wins}/{total} ({wins/total:.0%})  AvgΔ={np.mean(deltas):+.1%}  MedΔ={np.median(deltas):+.1%}", flush=True)

# ── Correlation between best variant and base ──
common = rets["v2.1_full"].index.intersection(rets["v2.0_base"].index)
corr = rets["v2.1_full"].loc[common].corr(rets["v2.0_base"].loc[common])
print(f"\nCorr(v2.1_full, v2.0_base): {corr:.3f}")
