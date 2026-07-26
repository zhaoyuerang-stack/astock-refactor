"""Walk-forward for the pure trend filter (no HMM).

Hypothesis: pure trend tw=3 (no HMM) had Sharpe 3.40 on full data.
Is this real signal or overfitting?

Test: same as HMM walk-forward:
  - Wide window grid (tw=2,3,5,7,10,15,20)
  - Pure trend (no HMM condition)
  - Pick best by IS Sharpe each year, run OOS
  - Compare to baseline and HMM tw=3

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/pure_trend_walkforward.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

cfg = StrategyConfig(start="2010-01-01")
print("[setup] loading baseline...", flush=True)
base = run_small_cap_strategy(cfg)
close, amount = base["close"], base["amount"]
baseline_ret = base["returns"]
scheduled = base["scheduled_weights"]
smallcap_timing = base["timing"].astype(float)
mkt_ret = close.pct_change(fill_method=None).mean(axis=1)


def run_pure_trend(close, scheduled, smallcap_timing, cfg, mkt_ret, tw, floor=0.0):
    """Pure trend filter (no HMM): exit when mkt_ret rolling(tw) < 0."""
    trend = mkt_ret.reindex(close.index).fillna(0.0).rolling(tw).sum()
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    exp[trend < 0] = floor
    timing = smallcap_timing * exp
    ret, _ = backtest_weights(close, scheduled, timing, cfg)
    return ret


def sharpe_of(ret):
    r = ret.fillna(0)
    if r.std() == 0: return 0.0
    return r.mean() / r.std() * np.sqrt(252)


def annual_return(ret):
    r = ret.fillna(0)
    n_years = max(1, len(r) / 252)
    return (1 + r).prod() ** (1.0 / n_years) - 1


# Grid: pure trend windows only
windows = [2, 3, 5, 7, 10, 15, 20]
floors = [0.0, 0.2]
combos = [(w, f) for w in windows for f in floors]
print(f"Combo grid: {len(combos)}", flush=True)

print("Pre-computing combo returns...", flush=True)
combo_rets = {}
for _, (w, f) in enumerate(combos):
    combo_rets[(w, f)] = run_pure_trend(close, scheduled, smallcap_timing, cfg, mkt_ret, w, f)

# Walk-forward
years = sorted(set(baseline_ret.index.year))
wf_years = [y for y in years if y >= 2015]

print(f"\nWalk-forward: {len(wf_years)} years (PURE TREND, no HMM)...", flush=True)
wf_returns = []
fixed_tw3_returns = []
baseline_annual = []
param_history = []

fixed_tw3_ret = combo_rets[(3, 0.0)]

for y in wf_years:
    is_years = [year for year in years if year < y]
    if len(is_years) < 3: continue

    best_sharpe = -999
    best_params = None
    for params, ret in combo_rets.items():
        is_r = ret[ret.index.year.isin(is_years)]
        if len(is_r) < 100: continue
        s = sharpe_of(is_r)
        if s > best_sharpe:
            best_sharpe = s
            best_params = params

    oos_r = combo_rets[best_params][combo_rets[best_params].index.year == y]
    fixed_oos = fixed_tw3_ret[fixed_tw3_ret.index.year == y]
    base_oos = baseline_ret[baseline_ret.index.year == y]

    wf_ann = annual_return(oos_r)
    fixed_ann = annual_return(fixed_oos)
    base_ann = annual_return(base_oos)

    wf_returns.append(wf_ann)
    fixed_tw3_returns.append(fixed_ann)
    baseline_annual.append(base_ann)
    param_history.append({
        "year": y, "params": str(best_params),
        "tw": best_params[0], "floor": best_params[1],
        "is_sharpe": best_sharpe, "oos_ret": wf_ann,
        "fixed_tw3_ret": fixed_ann, "base_ret": base_ann,
    })
    w, f = best_params
    print(f"  {y}: IS best=(tw={w:>2d}, fl={f:.1f}) IS_Sharpe={best_sharpe:.2f} | "
          f"WF={wf_ann:+.1%} Fixed-tw3={fixed_ann:+.1%} Base={base_ann:+.1%}", flush=True)

# Summary
wf_series = pd.Series(wf_returns, index=wf_years)
fixed_series = pd.Series(fixed_tw3_returns, index=wf_years)
base_series = pd.Series(baseline_annual, index=wf_years)

print("\n=== Walk-Forward Summary (PURE TREND, 2015-2026) ===", flush=True)
print(f"{'Metric':<20} {'WF':>14} {'Fixed-tw3':>14} {'Baseline':>14}", flush=True)
print(f"{'Annualized':<20} {wf_series.mean():>+13.1%} {fixed_series.mean():>+13.1%} {base_series.mean():>+13.1%}", flush=True)
print(f"{'Sharpe':<20} {sharpe_of(wf_series):>13.2f} {sharpe_of(fixed_series):>13.2f} {sharpe_of(base_series):>13.2f}", flush=True)
print(f"{'Min Yearly':<20} {wf_series.min():>+13.1%} {fixed_series.min():>+13.1%} {base_series.min():>+13.1%}", flush=True)
print(f"{'Win vs Base':<20} {(wf_series > base_series).mean():>13.0%} {(fixed_series > base_series).mean():>13.0%}", flush=True)

print("\n=== Parameter Selection History ===", flush=True)
for p in param_history:
    print(f"  {p['year']}: tw={p['tw']:>2d} fl={p['floor']:.1f}  (IS Sharpe={p['is_sharpe']:.2f})", flush=True)

unique = set(p["params"] for p in param_history)
print(f"\n  Unique combos selected: {len(unique)} / {len(param_history)} years", flush=True)

window_counts = {}
for p in param_history:
    window_counts[p["tw"]] = window_counts.get(p["tw"], 0) + 1
print(f"  Window frequency: {sorted(window_counts.items(), key=lambda x: -x[1])}", flush=True)

# Year-by-year comparison vs HMM tw=3 reference
print("\n=== Comparison: Pure Trend (tw=3) vs HMM tw=3 ===", flush=True)
print(f"{'Year':<6} {'Base':>9} {'PureT-tw3':>10} {'HMM-tw3':>10} {'PT-Base':>9} {'HMM-Base':>9}", flush=True)

# Load HMM tw=3 from previous result
hmm_path = OUT / "hmm_macro_trend_filter.csv"
if hmm_path.exists():
    hmm_df = pd.read_csv(hmm_path)
    # Find th=0.05, fl=0.0, tw=3
    hmm_row = hmm_df[(hmm_df["threshold"] == 0.05) & (hmm_df["floor"] == 0.0) & (hmm_df["trend_window"] == 3)]
    if not hmm_row.empty:
        hmm_full_ret = pd.Series(
            [(1 + hmm_row.iloc[0][f"annual_{y}"]) ** (1/(y - 2010 + 1)) - 1 for y in wf_years],
            index=wf_years,
        )
    else:
        hmm_full_ret = None
else:
    hmm_full_ret = None

for y in wf_years:
    b = annual_return(baseline_ret[baseline_ret.index.year == y])
    p_t = annual_return(fixed_tw3_ret[fixed_tw3_ret.index.year == y])
    line = f"{y:<6} {b:>+8.1%} {p_t:>+9.1%}"
    if hmm_full_ret is not None and y in hmm_full_ret.index:
        h = hmm_full_ret.loc[y]
        line += f" {h:>+9.1%} {p_t - b:>+8.1%} {h - b:>+8.1%}"
    print(line, flush=True)

df = pd.DataFrame(param_history)
df.to_csv(OUT / "pure_trend_walkforward.csv", index=False)
print(f"\nWrote: {OUT / 'pure_trend_walkforward.csv'}", flush=True)
