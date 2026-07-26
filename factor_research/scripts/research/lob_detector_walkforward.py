"""Walk-Forward validation for LOB multi-channel rising-edge detector.

Tests whether the best params (mode=rising_edge, th=2.0, cut=3, supp=10, fl=0.0)
are robust across years or just an overfit on full-sample data.

Grid (36 combos around the best region):
  th: [1.75, 2.0, 2.5], cut: [3, 5, 8], floor: [0.0, 0.2], suppress: [5, 10]

All use rising_edge mode (simple_cross/adaptive/individual channels were worse).

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/lob_detector_walkforward.py
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

# ── Data ──
print("Loading data...", flush=True)
close, vol, amount = load_price_panels("2010-01-01")
factor = small_cap_factor(amount, 60)
timing, _, _ = small_cap_timing(close, amount, 16)
scheduled = build_rebalance_weights(factor, close, 25, 20)
cfg = StrategyConfig(start="2010-01-01")

# ── Channels (shared across all combos) ──
ret = close.pct_change(fill_method=None)
has_trade = amount > 0
up_ratio = ((ret > 0) & has_trade).sum(axis=1) / has_trade.sum(axis=1)
mkt_ret = ret.mean(axis=1).fillna(0.0)
mkt_amount = amount.sum(axis=1)
ma20 = close.rolling(20).mean()
valid = ma20.notna() & close.notna()

ch1 = 1.0 - up_ratio
ch2 = mkt_ret.rolling(20).std()
ch3 = 1.0 / (mkt_amount / mkt_amount.rolling(20).mean()).clip(0.3, 5.0)
ch4 = 1.0 - ((close > ma20) & valid).sum(axis=1) / valid.sum(axis=1)

def zscore(s):
    mu = s.rolling(252, min_periods=60).mean()
    sigma = s.rolling(252, min_periods=60).std().replace(0, 1.0)
    return ((s - mu) / sigma).clip(-5, 10)

score_max = pd.DataFrame({k: zscore(v) for k, v in
    {"b": ch1, "v": ch2, "l": ch3, "d": ch4}.items()}).max(axis=1)


def make_trigger(score, threshold, suppress_days):
    """Rising-edge trigger (the best mode from tuning)."""
    d1 = score.diff(); d2 = d1.diff()
    raw = (score >= threshold) & (d1 > 0) & (d2 <= 0)
    trigger = pd.Series(False, index=score.index)
    last_pos = -10**9
    for pos, flag in enumerate(raw.fillna(False).values):
        if flag and pos - last_pos >= suppress_days:
            trigger.iloc[pos] = True; last_pos = pos
    return trigger.shift(1, fill_value=False)


def make_exposure(trigger, cut_days, floor):
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    for pos in np.flatnonzero(trigger.reindex(close.index).fillna(False).values):
        end = min(len(exp), pos + cut_days + 1)
        exp.iloc[pos:end] = floor
    return exp


def run_combo(th, cut, fl, supp):
    trig = make_trigger(score_max, th, supp)
    exp = make_exposure(trig, cut, fl)
    t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
    ret_s, _ = backtest_weights(close, scheduled, t, cfg)
    return ret_s


def sharpe_of(ret):
    r = ret.fillna(0)
    return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0


def annual(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1


# ── Grid & pre-compute ──
thresholds = [1.75, 2.0, 2.5]
cuts = [3, 5, 8]
floors = [0.0, 0.2]
suppresses = [5, 10]
combos = [(t, c, f, s) for t in thresholds for c in cuts for f in floors for s in suppresses]
print(f"\nPre-computing {len(combos)} combos...", flush=True)

combo_rets = {}
for i, (th, cut, fl, supp) in enumerate(combos):
    combo_rets[(th, cut, fl, supp)] = run_combo(th, cut, fl, supp)
    if (i + 1) % 10 == 0:
        print(f"  {i+1}/{len(combos)} done", flush=True)

# ── Walk-Forward ──
years = sorted(set(close.dropna(how="all").index.year))
wf_years = [y for y in years if y >= 2015]
v20_ret = pd.Series(dtype=float)
for y in years:
    sub = timing.astype(float).reindex(close.index).fillna(0.0)
    ret_v20_y, _ = backtest_weights(close, scheduled, sub, cfg)
    v20_ret = pd.concat([v20_ret, ret_v20_y[ret_v20_y.index.year == y]])

# Fixed best combo from tuning: (2.0, 3, 0.0, 10)
fixed_ret = combo_rets[(2.0, 3, 0.0, 10)]

print(f"\nWalk-Forward: {len(wf_years)} years...\n", flush=True)
wf_returns = []
fixed_returns = []
base_returns = []
param_history = []

for y in wf_years:
    is_years = [yr for yr in years if yr < y]
    if len(is_years) < 3: continue

    best_sharpe = -999
    best_params = None
    for params, ret_s in combo_rets.items():
        is_r = ret_s[ret_s.index.year.isin(is_years)]
        if len(is_r) < 100: continue
        s = sharpe_of(is_r)
        if s > best_sharpe: best_sharpe = s; best_params = params

    oos_r = combo_rets[best_params][combo_rets[best_params].index.year == y]
    fixed_oos = fixed_ret[fixed_ret.index.year == y]
    base_oos = v20_ret[v20_ret.index.year == y]

    wf_returns.append(annual(oos_r))
    fixed_returns.append(annual(fixed_oos))
    base_returns.append(annual(base_oos))
    param_history.append({
        "year": y, "params": str(best_params),
        "th": best_params[0], "cut": best_params[1],
        "fl": best_params[2], "supp": best_params[3],
        "is_sharpe": best_sharpe,
        "oos_ret": annual(oos_r), "fixed_ret": annual(fixed_oos),
        "base_ret": annual(base_oos),
    })
    print(f"  {y}: best=(th={best_params[0]:.2f} cut={best_params[1]}d fl={best_params[2]:.1f} s={best_params[3]}) "
          f"IS_sh={best_sharpe:.2f} | WF={annual(oos_r):+.1%} Fixed={annual(fixed_oos):+.1%} Base={annual(base_oos):+.1%}", flush=True)

# ── Summary ──
wf_s = pd.Series(wf_returns, index=wf_years)
fix_s = pd.Series(fixed_returns, index=wf_years)
base_s = pd.Series(base_returns, index=wf_years)

print("\n=== Walk-Forward Summary (2015-2026) ===")
print(f"{'Metric':<20} {'WF':>14} {'Fixed(2.0,3d)':>14} {'Baseline':>14}")
print(f"{'Annualized':<20} {wf_s.mean():>+13.1%} {fix_s.mean():>+13.1%} {base_s.mean():>+13.1%}")
print(f"{'Sharpe':<20} {sharpe_of(wf_s):>13.2f} {sharpe_of(fix_s):>13.2f} {sharpe_of(base_s):>13.2f}")
print(f"{'Min Yearly':<20} {wf_s.min():>+13.1%} {fix_s.min():>+13.1%} {base_s.min():>+13.1%}")
print(f"{'Win vs Base':<20} {(wf_s > base_s).mean():>13.0%} {(fix_s > base_s).mean():>13.0%} {'':>14}")

print("\n=== Parameter Selection History ===")
for p in param_history:
    print(f"  {p['year']}: th={p['th']:.2f} cut={p['cut']}d fl={p['fl']:.1f} supp={p['supp']} (IS_sh={p['is_sharpe']:.2f})")

unique = set(p["params"] for p in param_history)
print(f"\n  Unique combos: {len(unique)}/{len(param_history)}")


df = pd.DataFrame(param_history)
df.to_csv(OUT / "lob_detector_walkforward.csv", index=False)
print(f"\nWrote: {OUT / 'lob_detector_walkforward.csv'}")
