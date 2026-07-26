"""Pure trend with ADV-based liquidity cap — walk-forward validation.

Implements risk 6 fix: cap single-stock weight at 5% of daily turnover.
Uses the existing engine's backtest_weights but caps target weights BEFORE
passing to engine. This is simpler and avoids reimplementing the full engine.

Key logic:
  adv_cap_weight_for_stock = 0.05 * daily_amount / portfolio_nav_total
  effective_weight = min(strategy_weight, adv_cap_weight)

Walk-forward verifies whether pure-trend tw=2 still wins after capping.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/pure_trend_liquidity_cap_wf.py
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


# ========================================================================
# Strategy 1: Pure trend (no cap) — reference
# ========================================================================
def pure_trend_exposure(mkt_ret, tw=2, floor=0.0):
    trend = mkt_ret.reindex(close.index).fillna(0.0).rolling(tw).sum()
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    exp[trend < 0] = floor
    return exp


def pure_trend_ret(mkt_ret, tw=2, floor=0.0):
    exp = pure_trend_exposure(mkt_ret, tw, floor)
    timing = smallcap_timing * exp
    ret, _ = backtest_weights(close, scheduled, timing, cfg)
    return ret


# ========================================================================
# Strategy 2: Pure trend WITH position-level ADV cap
# ========================================================================
def cap_weights_by_adv(scheduled_weights_at_date, amount_at_date, portfolio_nav):
    """
    Cap each stock's target weight by 5% of its daily turnover.

    Args:
        scheduled_weights_at_date: float Series indexed by stock code
        amount_at_date: float Series, daily turnover in CNY per stock
        portfolio_nav: float, total portfolio value in CNY

    Returns capped weights (float Series, same index as input).
    """
    orig_idx = scheduled_weights_at_date.index
    orig_vals = scheduled_weights_at_date.values.astype(float)
    adv_limit = 0.05 * amount_at_date.reindex(orig_idx).fillna(0.0).values / max(portfolio_nav, 1e-6)
    capped_vals = np.minimum(orig_vals, adv_limit)
    capped_vals = np.maximum(capped_vals, 0.0)  # no negative weights
    result = pd.Series(capped_vals, index=orig_idx)
    return result.dropna()


def pure_trend_ret_with_adv_cap(mkt_ret, amount, tw=2, floor=0.0, adv_cap_pct=0.05, portfolio_nav=5_000_000):
    """
    Like pure_trend_ret but applies ADV cap to scheduled weights at each rebalance.

    portfolio_nav: assumed constant portfolio size (CNY)
    5M is ~500万 which we know from diagnostics is within executable range.
    """
    exp = pure_trend_exposure(mkt_ret, tw, floor)

    # Build exposure series to filter rebalance dates
    scheduled_dates = sorted(scheduled.keys())

    # Create a rebalance weight schedule with ADV caps applied
    capped_scheduled = {}
    for dt in scheduled_dates:
        w = scheduled[dt].dropna()
        # Cap each stock's weight by 5% of prevailing daily amount
        if dt in amount.index:
            capped_scheduled[dt] = cap_weights_by_adv(w, amount.loc[dt], portfolio_nav)
        else:
            capped_scheduled[dt] = w

    timing = smallcap_timing * exp
    ret, _ = backtest_weights(close, capped_scheduled, timing, cfg)
    return ret


# ========================================================================
# Strategy 3: Walk-forward on both versions
# ========================================================================
def sharpe_of(ret):
    r = ret.fillna(0)
    if r.std() == 0: return 0.0
    return r.mean() / r.std() * np.sqrt(252)


def annual_return(ret):
    r = ret.fillna(0)
    n_years = max(1, len(r) / 252)
    return (1 + r).prod() ** (1.0 / n_years) - 1


# Pre-compute all combos for both no-cap and capped
windows = [2, 3, 5, 7, 10]
floors = [0.0, 0.2]
combos = [(w, f) for w in windows for f in floors]

print("Pre-computing NO-CAP combo returns...", flush=True)
no_cap_rets = {}
for w, f in combos:
    no_cap_rets[(w, f)] = pure_trend_ret(mkt_ret, w, f)

print("Pre-computing ADV-capped combo returns (portfolio=5M CNY)...", flush=True)
cap_rets = {}
for w, f in combos:
    cap_rets[(w, f)] = pure_trend_ret_with_adv_cap(mkt_ret, amount, w, f, 0.05, 5_000_000)

print("Pre-computing ADV-capped combo returns (portfolio=10M CNY)...", flush=True)
cap_rets_10m = {}
for w, f in combos:
    cap_rets_10m[(w, f)] = pure_trend_ret_with_adv_cap(mkt_ret, amount, w, f, 0.05, 10_000_000)

# === Walk-Forward for ADV cap at 5M ===
years = sorted(set(baseline_ret.index.year))
wf_years = [y for y in years if y >= 2015]

print(f"\nWalk-forward with ADV cap (5M CNY portfolio): {len(wf_years)} years...", flush=True)
param_history = {}
for label, combo_dict in [("noCap", no_cap_rets), ("cap5M", cap_rets), ("cap10M", cap_rets_10m)]:
    wf_returns = []
    history = []
    fixed_tw2_returns = []
    fixed_tw2_dict = combo_dict

    for y in wf_years:
        is_years = [year for year in years if year < y]
        if len(is_years) < 3: continue

        best_sharpe = -999
        best_params = None
        for params, ret in combo_dict.items():
            is_r = ret[ret.index.year.isin(is_years)]
            if len(is_r) < 100: continue
            s = sharpe_of(is_r)
            if s > best_sharpe:
                best_sharpe = s
                best_params = params

        oos_r = combo_dict[best_params][combo_dict[best_params].index.year == y]
        fixed_tw2_oos = fixed_tw2_dict[(2, 0.0)][fixed_tw2_dict[(2, 0.0)].index.year == y]
        wf_returns.append(annual_return(oos_r))
        fixed_tw2_returns.append(annual_return(fixed_tw2_oos))
        history.append({
            "year": y, "params": str(best_params),
            "tw": best_params[0], "floor": best_params[1],
            "is_sharpe": best_sharpe, "oos_ret": annual_return(oos_r),
            "fixed_tw2_ret": annual_return(fixed_tw2_oos),
        })

    param_history[label] = {
        "returns": pd.Series(wf_returns, index=wf_years),
        "fixed_tw2": pd.Series(fixed_tw2_returns, index=wf_years),
        "history": history,
    }

# Baseline on same axis
base_series = pd.Series([annual_return(baseline_ret[baseline_ret.index.year == y]) for y in wf_years], index=wf_years)

# ========================================================================
# Print summary
# ========================================================================
print("\n=== Walk-Forward Summary (2015-2026) ===", flush=True)
print(f"{'Metric':<20} {'noCap_tw2':>14} {'cap5M_tw2':>14} {'cap10M_tw2':>14} {'Baseline':>14}", flush=True)
print("-" * 80, flush=True)

for label in ["noCap", "cap5M", "cap10M"]:
    d = param_history[label]
    print(f"{label:<20} {d['fixed_tw2'].mean():>+13.1%} {'':>14} {'':>14} {'':>14}", flush=True)
    # Param stability
    hist = d["history"]
    unique = set(p["params"] for p in hist)
    wins = [(p["tw"], p["floor"]) for p in hist]
    tw_counts = {}
    for w, _ in wins:
        tw_counts[w] = tw_counts.get(w, 0) + 1
    print(f"  Params: {len(unique)} unique / {len(hist)} years", flush=True)
    print(f"  Window freq: {sorted(tw_counts.items(), key=lambda x: -x[1])}", flush=True)
    print(f"  IS Sharpe range: {min(p['is_sharpe'] for p in hist):.2f} - {max(p['is_sharpe'] for p in hist):.2f}", flush=True)

# Year-by-year comparison
print("\n=== Year-by-Year: noCap vs cap5M (both fixed tw=2, fl=0.0) ===", flush=True)
noCap_tw2 = no_cap_rets[(2, 0.0)]
cap5M_tw2 = cap_rets[(2, 0.0)]
print(f"{'Year':<6} {'Baseline':>9} {'noCap':>9} {'cap5M':>9} {'cap5M-Base':>10}", flush=True)
for y in wf_years:
    b = annual_return(baseline_ret[baseline_ret.index.year == y])
    nc = annual_return(noCap_tw2[noCap_tw2.index.year == y])
    c5 = annual_return(cap5M_tw2[cap5M_tw2.index.year == y])
    print(f"{y:<6} {b:>+8.1%} {nc:>+8.1%} {c5:>+8.1%} {c5-b:>+9.1%}", flush=True)

# Cap impact by combo
print("\n=== Cap Impact (5M CNY, annual 2015-2026) ===", flush=True)
print(f"{'tw':>3} {'fl':>3} {'NoCap':>10} {'cap5M':>10} {'Diff':>10}", flush=True)
for w, f in combos:
    nc = annual_return(no_cap_rets[(w, f)])
    c5 = annual_return(cap_rets[(w, f)])
    print(f"{w:>3} {f:>3.1f} {nc:>+9.1%} {c5:>+9.1%} {c5-nc:>+9.1%}", flush=True)

# Save
df_rows = []
for label, d in param_history.items():
    for p in d["history"]:
        p["label"] = label
        df_rows.append(p)
pd.DataFrame(df_rows).to_csv(OUT / "pure_trend_liquidity_cap_walkforward.csv", index=False)
print(f"\nWrote: {OUT / 'pure_trend_liquidity_cap_walkforward.csv'}", flush=True)
