"""v3.0 Large-cap Value+Quality — comprehensive validation.

1. Walk-Forward: same protocol as v2.2 (12y OOS, 10 combos)
2. Portfolio: 50% v2.2 + 50% v3.0 combination
3. Rebalance sensitivity: monthly / quarterly / semi-annual / annual

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/validate_v30.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from scripts.research.build_largecap_value_quality import (
    build_factors,
    load_clean_panels,
)
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

WARMUP = "2010-01-01"
SEGMENTS = [
    ("IS  2018-2026", 2018, 2026),
    ("OOS 2023-2026", 2023, 2026),
    ("压力 2010-2026", 2010, 2026),
]


def build_scheduled_weights(factor, close_panel, top_n=20, rebalance_days=63):
    """Build scheduled weights from factor panel. Quarterly = 63 trading days."""
    dates = sorted(factor.dropna(how="all").index)
    rebal_dates = dates[::rebalance_days]
    scheduled = {}
    for rd in rebal_dates:
        if rd not in close_panel.index:
            continue
        pos = close_panel.index.get_loc(rd)
        effective = close_panel.index[min(pos + 1, len(close_panel.index) - 1)]
        f = factor.loc[rd].dropna()
        active = close_panel.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            continue
        scheduled[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return scheduled


def pt2_exposure(close_panel):
    """Pure trend tw=2 exposure series. T日趋势决定T+1日仓位(shift(1)防前视)."""
    mkt = close_panel.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    trend = mkt.rolling(2).sum()
    return (trend >= 0).astype(float).shift(1, fill_value=1.0)


# ── CAGR metrics (same formula as validate_v22.py) ──
def _annual(ret):
    r = ret.fillna(0); n = max(len(r)/252, 1)
    return (1+r).cumprod().iloc[-1]**(1/n) - 1
def _sharpe(ret):
    r = ret.fillna(0); return r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0.0
def _maxdd(ret):
    return float(((1+ret.fillna(0)).cumprod()/(1+ret.fillna(0)).cumprod().cummax()-1).min())
def _metrics(ret):
    a = _annual(ret); d = _maxdd(ret)
    return {"annual":a, "sharpe":_sharpe(ret), "maxdd":d, "calmar":a/abs(d) if d<0 else 0}


def run_one_config(close, factor, cfg, top_n=20, rebalance_days=63):
    """Run v3.0 with given params. Returns (ret_pure, ret_pt2)."""
    sched = build_scheduled_weights(factor, close, top_n, rebalance_days)
    if not sched:
        return None, None
    exp = pt2_exposure(close)

    # w/ PT2
    ret_pt2, _ = backtest_weights(close, sched, exp, cfg)
    # pure
    ones = pd.Series(1.0, index=close.index, dtype="float64")
    ret_pure, _ = backtest_weights(close, sched, ones, cfg)

    return ret_pure, ret_pt2


# ================================================================
# 1. Walk-Forward
# ================================================================
def walk_forward_v30():
    print("=" * 65)
    print("  1. Walk-Forward: v3.0 Large-cap Value + Quality")
    print("=" * 65)

    panels = load_clean_panels()
    close = panels["close"]
    cfg = StrategyConfig(start=WARMUP)

    # Combo grid
    univ_sizes = [200, 300, 500]
    top_ns = [15, 20, 30]
    rebal_days = [63]  # quarterly
    combos = [(u, t, r) for u in univ_sizes for t in top_ns for r in rebal_days]

    # Pre-compute all factors
    print(f"\nPre-computing {len(combos)} factor × rebalance combos...", flush=True)
    combo_rets = {}
    for univ_size, top_n, rd in combos:
        factor, _, _, _ = build_factors(panels, univ_size)
        _, ret_pt2 = run_one_config(close, factor, cfg, top_n, rd)
        if ret_pt2 is not None:
            combo_rets[(univ_size, top_n, rd)] = ret_pt2

    print(f"  Valid combos: {len(combo_rets)}", flush=True)

    # WF
    years = sorted(set(close.dropna(how="all").index.year))
    wf_years = [y for y in years if y >= 2015]
    print(f"\nWalk-Forward: {len(wf_years)} years...", flush=True)

    wf_returns = []
    fixed_returns = []  # default: 500/20/q
    base_returns = []
    param_history = []

    # Fixed combo for reference
    factor_500, _, _, _ = build_factors(panels, 500)
    _, fixed_ret = run_one_config(close, factor_500, cfg, 20, 63)
    v20_base = run_small_cap_strategy(cfg)
    v20_ret = v20_base["returns"]

    for y in wf_years:
        is_years = [yr for yr in years if yr < y]
        if len(is_years) < 3:
            continue

        # Best IS combo
        best_sharpe = -999
        best_params = None
        for params, ret in combo_rets.items():
            is_r = ret[ret.index.year.isin(is_years)]
            if len(is_r) < 100: continue
            s = _sharpe(is_r)
            if s > best_sharpe:
                best_sharpe = s
                best_params = params

        oos_r = combo_rets[best_params][combo_rets[best_params].index.year == y]
        fixed_oos = fixed_ret[fixed_ret.index.year == y]
        base_oos = v20_ret[v20_ret.index.year == y]

        wf_returns.append(_annual(oos_r))
        fixed_returns.append(_annual(fixed_oos))
        base_returns.append(_annual(base_oos))
        param_history.append({
            "year": y, "params": str(best_params),
            "univ": best_params[0], "top_n": best_params[1],
            "is_sharpe": best_sharpe,
            "oos_ret": _annual(oos_r),
            "fixed_ret": _annual(fixed_oos),
            "base_ret": _annual(base_oos),
        })
        print(f"  {y}: IS best=(univ={best_params[0]}, top{best_params[1]}) "
              f"IS_Sharpe={best_sharpe:.2f} | "
              f"WF={_annual(oos_r):+.1%} Fixed={_annual(fixed_oos):+.1%} Base={_annual(base_oos):+.1%}", flush=True)

    wf_s = pd.Series(wf_returns, index=wf_years)
    fix_s = pd.Series(fixed_returns, index=wf_years)
    base_s = pd.Series(base_returns, index=wf_years)

    print("\n  WF Summary (2015-2026):")
    print(f"    Ann: WF={wf_s.mean():+.1%}  Fixed={fix_s.mean():+.1%}  Base={base_s.mean():+.1%}")
    print(f"    WF Sharpe: {_sharpe(wf_s):.2f}  WinRate vsBase: {(wf_s>base_s).mean():.0%}")

    # Param stability
    unique = set(p["params"] for p in param_history)
    univ_counts = {}
    for p in param_history:
        univ_counts[p["univ"]] = univ_counts.get(p["univ"], 0) + 1
    print(f"    Unique combos: {len(unique)}/{len(param_history)}  Univ freq: {univ_counts}", flush=True)

    return v20_ret, fixed_ret, wf_s, fix_s, base_s, param_history


# ================================================================
# 2. Portfolio combination
# ================================================================
def portfolio_combo(v20_ret, v30_ret):
    print(f"\n{'='*65}")
    print("  2. Portfolio: 50% v2.2 + 50% v3.0")
    print("=" * 65)

    common = v20_ret.index.intersection(v30_ret.index)
    v20 = v20_ret.loc[common]
    v30 = v30_ret.loc[common]
    combo = v20 * 0.5 + v30 * 0.5

    # Segments
    print(f"\n  {'Segment':<22} {'v2.2':>10} {'v3.0':>10} {'50/50':>10} {'Corr':>7}")
    print("  " + "-" * 62)
    for label, start, end in SEGMENTS:
        v22 = _metrics(v20[(v20.index.year >= start) & (v20.index.year <= end)])
        v3 = _metrics(v30[(v30.index.year >= start) & (v30.index.year <= end)])
        c = _metrics(combo[(combo.index.year >= start) & (combo.index.year <= end)])
        corr = v20.loc[common[(common.year >= start) & (common.year <= end)]
                       ].corr(v30.loc[common[(common.year >= start) & (common.year <= end)]])
        print(f"  {label:<22} {v22['annual']:>+9.1%} {v3['annual']:>+9.1%} {c['annual']:>+9.1%} {corr:>6.3f}")

    # Yearly combo
    print(f"\n  {'Year':>6} {'v2.2':>9} {'v3.0':>9} {'50/50':>9} {'v2.0':>9}")
    for year in sorted(set(v20.index.year)):
        r22 = v20[v20.index.year == year]
        r3 = v30[v30.index.year == year]
        rc = combo[combo.index.year == year]
        r0 = v20[v20.index.year == year]
        if len(r22) < 50: continue
        print(f"  {year:>6} {_annual(r22):>+8.1%} {_annual(r3):>+8.1%} {_annual(rc):>+8.1%} {_annual(r0):>+8.1%}")

    return combo


# ================================================================
# 3. Rebalance sensitivity
# ================================================================
def rebalance_sensitivity():
    print(f"\n{'='*65}")
    print("  3. Rebalance Sensitivity (univ=500, top=20)")
    print("=" * 65)

    panels = load_clean_panels()
    close = panels["close"]
    factor, _, _, _ = build_factors(panels, 500)
    cfg = StrategyConfig(start=WARMUP)

    # Pure trend for this
    print(f"\n  {'RebalFreq':>18} {'Days':>5} {'v3.0 Pure':>11} {'v3.0+PT2':>11}")
    print("  " + "-" * 55)

    for freq_label, freq_days, top_n in [
        ("Monthly", 21, 20),
        ("Quarterly", 63, 20),
        ("Semi-annual", 126, 20),
        ("Annual", 252, 20),
    ]:
        sched = build_scheduled_weights(factor, close, top_n, freq_days)
        exp = pt2_exposure(close)
        ones = pd.Series(1.0, index=close.index, dtype="float64")

        rp, _ = backtest_weights(close, sched, exp, cfg)
        rn, _ = backtest_weights(close, sched, ones, cfg)
        mp = _metrics(rp)
        mn = _metrics(rn)
        print(f"  {freq_label:>18} {freq_days:>5} {mn['annual']:>+10.1%} {mp['annual']:>+10.1%}")

    # Also test with PT2 overlay acting only on v3.0
    # Compare 50/50 combo of v2.2 and v3.0+PT2
    print("\n  Combined v2.2(50%) + v3.0+PT2(50%) quarterly:")
    v20_base = run_small_cap_strategy(cfg)
    v20 = v20_base["returns"]
    sched = build_scheduled_weights(factor, close, 20, 63)
    exp = pt2_exposure(close)
    v30_pt2, _ = backtest_weights(close, sched, exp, cfg)
    common = v20.index.intersection(v30_pt2.index)
    combo = v20.loc[common] * 0.5 + v30_pt2.loc[common] * 0.5
    mc = _metrics(combo[combo.index.year >= 2018])
    print(f"    ann={mc['annual']:+.1%}  Sharpe={mc['sharpe']:.2f}  maxDD={mc['maxdd']:+.1%}")
    corr_c = combo.loc[common].corr(v20.loc[common])
    print(f"    vs v2.2 corr: {corr_c:.3f}")


# ================================================================
# Main
# ================================================================
def main():
    print("=" * 65)
    print("  v3.0 Large-cap Value+Quality — Full Validation")
    print("=" * 65)

    # 1. WF
    v20_ret, v30_fixed, wf_s, fix_s, base_s, ph = walk_forward_v30()

    # 2. Portfolio
    combo_ret = portfolio_combo(v20_ret, v30_fixed)

    # 3. Rebalance
    rebalance_sensitivity()

    # Save
    pd.DataFrame(ph).to_csv(OUT / "validate_v3_walkforward.csv")
    combo_ret.to_csv(OUT / "validate_v3_combo.csv")
    print(f"\n{'='*65}")
    print(f"  Results saved to {OUT}")
    print("=" * 65)


if __name__ == "__main__":
    main()
