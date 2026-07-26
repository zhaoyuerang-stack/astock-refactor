"""Size + NetProfit YoY: Walk-Forward with Vol Target.

Tests vol-target as drawdown control on top of the size+NPY factor:
  - Vol target scales exposure: exp = min(1.0, target_vol / trailing_realized_vol)
  - Test target vols: 12%, 15%, 18%, 20%, 25% (annual)
  - Test lookback windows: 20d, 40d, 60d
  - Compare: PureTrend only, Vol Target only, Both combined
  - Full WF on the best vol-target config

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/size_npy_walkforward.py
"""
import warnings; warnings.filterwarnings("ignore")
import os
import sys
from itertools import product
from pathlib import Path

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.utils import mad_clip, safe_zscore
from lake.load_lake import load_fundamental_panel
from strategies.small_cap import load_price_panels

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

TOP_N = 25
REBALANCE = 20


# ── Helpers ──

def build_rebalance_weights(factor, close, top_n=TOP_N, rebalance_days=REBALANCE):
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 50:
        return {}
    weights = {}
    for rd in list(fdates[::rebalance_days]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            continue
        weights[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return weights


def compute_vol_target(close, amount, target_vol=0.18, lookback=20,
                        min_exp=0.2, max_exp=1.5):
    """Build a continuous vol-target exposure multiplier.

    Uses the small-cap portfolio daily returns as the vol proxy (same
    methodology as small_cap_timing for consistency).
    """
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
    small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    # Trailing realized volatility (annualized)
    realized_vol = small_idx.rolling(lookback, min_periods=10).std() * np.sqrt(252)
    # Exposure = target / realized, capped
    exposure = target_vol / realized_vol.replace(0, np.nan)
    exposure = exposure.clip(min_exp, max_exp)
    # Shift to T+1 (today's vol determines tomorrow's exposure)
    return exposure.shift(1)


def backtest_period(close, volume, amount, weights, timing=None, leverage=1.25):
    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start=str(close.index[0].date()), leverage=leverage),
    )
    signal = Signal(weights=weights, timing=timing, family="wf", version="test")
    result = engine.run(signal)
    m = result.metrics
    return {
        "annual": m["annual"],
        "sharpe": m["sharpe"],
        "maxdd": m["maxdd"],
        "calmar": m["calmar"],
        "turnover": result.detail["turnover"].mean() * 252,
        "n_days": len(result.returns),
        "returns": result.returns,
    }


# ── Main ──

def main():
    print("=" * 70)
    print("  Size + NPY: Vol Target Drawdown Control")
    print("=" * 70, flush=True)

    # Load data
    print("\nLoading data...", flush=True)
    close, volume, amount = load_price_panels("2018-01-01")
    trade_dates = close.index

    fund = load_fundamental_panel(trade_dates, codes=None, fields=["net_profit_yoy"])
    npy = fund["net_profit_yoy"].reindex(trade_dates).ffill()

    sc_factor = small_cap_factor(amount, window=60)
    npy_z = safe_zscore(mad_clip(npy))
    factor = safe_zscore(mad_clip(0.5 * sc_factor + 0.5 * npy_z))  # λ=0.5 fixed

    pt_timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    print(f"  Data ready: {close.shape[1]} stocks × {close.shape[0]} days", flush=True)

    # ═══════════════════════════════════════════════════════════
    # Phase 1: Vol Target Sweep (full period 2018-2026)
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  PHASE 1: Vol Target Parameter Sweep (full period)")
    print(f"{'='*70}", flush=True)

    weights = build_rebalance_weights(factor, close)

    target_vols = [0.12, 0.15, 0.18, 0.20, 0.25]
    lookbacks = [20, 40, 60]
    min_exp = 0.3
    max_exp = 1.5

    # Baselines
    print("\n  --- Baselines ---", flush=True)
    res_no_timing = backtest_period(close, volume, amount, weights, timing=None)
    print(f"  No timing:         Annual={res_no_timing['annual']:+.1%} "
          f"DD={res_no_timing['maxdd']:+.1%} Sharpe={res_no_timing['sharpe']:.2f}", flush=True)

    res_pt = backtest_period(close, volume, amount, weights, timing=pt_timing)
    print(f"  PureTrend only:    Annual={res_pt['annual']:+.1%} "
          f"DD={res_pt['maxdd']:+.1%} Sharpe={res_pt['sharpe']:.2f}", flush=True)

    # Vol Target only (no PureTrend)
    print("\n  --- Vol Target ONLY (no PureTrend) ---", flush=True)
    print(f"  {'Target Vol':<10} {'LB':<5} {'Annual':>8} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}", flush=True)
    print(f"  {'-'*50}", flush=True)
    vt_results = []
    for tv, lb in product(target_vols, lookbacks):
        vt = compute_vol_target(close, amount, target_vol=tv, lookback=lb,
                                min_exp=min_exp, max_exp=max_exp)
        res = backtest_period(close, volume, amount, weights, timing=vt)
        vt_results.append({"target_vol": tv, "lookback": lb, **res})
        print(f"  {tv:.0%}        {lb:<5} {res['annual']:+8.1%} {res['maxdd']:+8.1%} "
              f"{res['sharpe']:+7.2f} {res['calmar']:+7.2f}", flush=True)

    # Vol Target + PureTrend combined
    print("\n  --- Vol Target + PureTrend ---", flush=True)
    print(f"  {'Target Vol':<10} {'LB':<5} {'Annual':>8} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}", flush=True)
    print(f"  {'-'*50}", flush=True)
    vt_pt_results = []
    for tv, lb in product(target_vols, lookbacks):
        vt = compute_vol_target(close, amount, target_vol=tv, lookback=lb,
                                min_exp=min_exp, max_exp=max_exp)
        # Combined: PT decides direction (in/out), VT scales size
        combined = pt_timing.astype(float) * vt
        res = backtest_period(close, volume, amount, weights, timing=combined)
        vt_pt_results.append({"target_vol": tv, "lookback": lb, **res})
        print(f"  {tv:.0%}        {lb:<5} {res['annual']:+8.1%} {res['maxdd']:+8.1%} "
              f"{res['sharpe']:+7.2f} {res['calmar']:+7.2f}", flush=True)

    # Find Pareto-optimal: high Calmar, high Sharpe, low |DD|
    all_vt = vt_results + vt_pt_results
    # Best by Calmar
    best_calmar = max(all_vt, key=lambda r: r["calmar"])
    print(f"\n  Best by Calmar: VolTarget={best_calmar.get('target_vol', 'N/A')} "
          f"LB={best_calmar.get('lookback', 'N/A')} "
          f"Annual={best_calmar['annual']:+.1%} DD={best_calmar['maxdd']:+.1%} "
          f"Sharpe={best_calmar['sharpe']:.2f} Calmar={best_calmar['calmar']:.2f}", flush=True)

    # Best by DD
    best_dd = min(all_vt, key=lambda r: r["maxdd"])  # most negative = worst, so find least negative
    # Actually find the smallest absolute DD
    best_dd = min(all_vt, key=lambda r: abs(r["maxdd"]))
    print(f"  Best by |DD|:   VolTarget={best_dd.get('target_vol', 'N/A')} "
          f"LB={best_dd.get('lookback', 'N/A')} "
          f"Annual={best_dd['annual']:+.1%} DD={best_dd['maxdd']:+.1%} "
          f"Sharpe={best_dd['sharpe']:.2f} Calmar={best_dd['calmar']:.2f}", flush=True)

    # ═══════════════════════════════════════════════════════════
    # Phase 2: Deep-dive on best configs with leverage sweep
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  PHASE 2: Best Configs — Leverage Sweep")
    print(f"{'='*70}", flush=True)

    # Pick top 3 configs from Phase 1
    top_configs = sorted(all_vt, key=lambda r: r["calmar"], reverse=True)[:3]

    for i, cfg in enumerate(top_configs):
        tv = cfg.get("target_vol")
        lb = cfg.get("lookback")
        has_pt = cfg not in vt_results  # if not in vt_only, it's vt+pt

        print(f"\n  Config {i+1}: VolTarget={tv:.0%} LB={lb}d "
              f"{'+ PureTrend' if has_pt else 'vol-only'}", flush=True)

        for lev in [1.0, 1.1, 1.25, 1.5]:
            vt = compute_vol_target(close, amount, target_vol=tv, lookback=lb,
                                    min_exp=min_exp, max_exp=max_exp)
            if has_pt:
                timing = pt_timing.astype(float) * vt
            else:
                timing = vt

            res = backtest_period(close, volume, amount, weights, timing=timing, leverage=lev)
            hit = res["annual"] >= 0.15 and abs(res["maxdd"]) <= 0.20
            print(f"    Lev={lev:.2f}: Annual={res['annual']:+.1%} DD={res['maxdd']:+.1%} "
                  f"Sharpe={res['sharpe']:.2f} Calmar={res['calmar']:.2f} "
                  f"{'✅ HIT' if hit else ''}", flush=True)

    # ═══════════════════════════════════════════════════════════
    # Phase 3: Walk-Forward on best vol-target config
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print("  PHASE 3: Walk-Forward with Vol Target")
    print(f"{'='*70}", flush=True)

    # Use the best config from Phase 1: highest Calmar
    best_tv = best_calmar.get("target_vol", 0.18)
    best_lb = best_calmar.get("lookback", 40)
    best_has_pt = best_calmar not in vt_results

    print(f"  Config: VolTarget={best_tv:.0%} LB={best_lb}d "
          f"{'+ PureTrend' if best_has_pt else 'vol-only'}", flush=True)

    # WF: rolling 3yr train → 1yr test
    blend_weights = [0.3, 0.5, 0.7, 1.0]
    pt_options = [False, True]

    windows = []
    for test_year in range(2020, 2027):
        train_start = pd.Timestamp(f"{test_year - 3}-01-01")
        train_end = pd.Timestamp(f"{test_year - 1}-12-31")
        test_start = pd.Timestamp(f"{test_year}-01-01")
        test_end = pd.Timestamp(f"{test_year}-12-31")
        if train_start < trade_dates[0] or test_end > trade_dates[-1]:
            continue
        if test_year == 2026:
            test_end = trade_dates[-1]
        windows.append({"test_year": test_year, "train_start": train_start,
                        "train_end": train_end, "test_start": test_start,
                        "test_end": test_end})

    print(f"  WF Windows: {len(windows)}", flush=True)

    all_results = []
    oos_returns = []

    for i, win in enumerate(windows):
        # Slice train
        mask = (trade_dates >= win["train_start"]) & (trade_dates <= win["train_end"])
        c_tr = close.loc[mask]; v_tr = volume.loc[mask]; a_tr = amount.loc[mask]
        sc_tr = sc_factor.loc[mask]; np_tr = npy_z.loc[mask]

        best_sharpe = -np.inf
        best_cfg = None

        for bw in blend_weights:
            f_tr = safe_zscore(mad_clip(bw * sc_tr + (1 - bw) * np_tr))
            w_tr = build_rebalance_weights(f_tr, c_tr)

            for use_pt in pt_options:
                timing_tr = None
                if use_pt:
                    pt_tr, _, _ = small_cap_timing(c_tr, a_tr, ma_window=16)
                    vt_tr = compute_vol_target(c_tr, a_tr, target_vol=best_tv,
                                               lookback=best_lb, min_exp=min_exp, max_exp=max_exp)
                    timing_tr = pt_tr.astype(float) * vt_tr
                else:
                    timing_tr = compute_vol_target(c_tr, a_tr, target_vol=best_tv,
                                                   lookback=best_lb, min_exp=min_exp, max_exp=max_exp)

                if len(w_tr) < 3:
                    continue
                res = backtest_period(c_tr, v_tr, a_tr, w_tr, timing=timing_tr)
                if np.isfinite(res["sharpe"]) and res["sharpe"] > best_sharpe:
                    best_sharpe = res["sharpe"]
                    best_cfg = {"blend_weight": bw, "use_pt": use_pt,
                                "train_sharpe": res["sharpe"],
                                "train_annual": res["annual"],
                                "train_maxdd": res["maxdd"]}

        if best_cfg is None:
            print(f"    Win {i+1} ({win['test_year']}): ⚠️ no valid config", flush=True)
            continue

        # OOS test
        mask_test = (trade_dates >= win["test_start"]) & (trade_dates <= win["test_end"])
        c_te = close.loc[mask_test]; v_te = volume.loc[mask_test]; a_te = amount.loc[mask_test]
        sc_te = sc_factor.loc[mask_test]; np_te = npy_z.loc[mask_test]

        bw = best_cfg["blend_weight"]
        f_te = safe_zscore(mad_clip(bw * sc_te + (1 - bw) * np_te))
        w_te = build_rebalance_weights(f_te, c_te)

        if best_cfg["use_pt"]:
            pt_te, _, _ = small_cap_timing(c_te, a_te, ma_window=16)
            vt_te = compute_vol_target(c_te, a_te, target_vol=best_tv,
                                       lookback=best_lb, min_exp=min_exp, max_exp=max_exp)
            timing_te = pt_te.astype(float) * vt_te
        else:
            timing_te = compute_vol_target(c_te, a_te, target_vol=best_tv,
                                           lookback=best_lb, min_exp=min_exp, max_exp=max_exp)

        res = backtest_period(c_te, v_te, a_te, w_te, timing=timing_te)

        print(f"    {win['test_year']}: λ={bw:.1f} PT={best_cfg['use_pt']} → "
              f"OOS Annual={res['annual']:+.1%} DD={res['maxdd']:+.1%} "
              f"Sharpe={res['sharpe']:.2f}", flush=True)

        all_results.append({**win, **best_cfg,
                            "oos_annual": res["annual"], "oos_sharpe": res["sharpe"],
                            "oos_maxdd": res["maxdd"], "oos_calmar": res["calmar"],
                            "oos_turnover": res["turnover"], "oos_days": res["n_days"]})
        oos_returns.append(res["returns"])

    # ── WF Aggregate ──
    if oos_returns:
        wf_ret = pd.concat(oos_returns).sort_index()
        wf_ret = wf_ret[~wf_ret.index.duplicated(keep="first")]
        wf_annual = float(wf_ret.mean() * 252)
        wf_vol = float(wf_ret.std() * np.sqrt(252))
        wf_sharpe = wf_annual / wf_vol if wf_vol > 0 else 0.0
        wf_maxdd = float(((1 + wf_ret).cumprod() / (1 + wf_ret).cumprod().cummax() - 1).min())

        print(f"\n{'='*70}")
        print("  WF AGGREGATE OOS (Vol Target)")
        print(f"{'='*70}", flush=True)
        print(f"  Period: {wf_ret.index[0].date()} ~ {wf_ret.index[-1].date()} ({len(wf_ret)}d)")
        print(f"  Annual: {wf_annual:+.1%}")
        print(f"  Sharpe: {wf_sharpe:.2f}")
        print(f"  MaxDD:  {wf_maxdd:+.1%}")
        print(f"  Calmar: {wf_annual / abs(wf_maxdd) if wf_maxdd < 0 else 0:.2f}")

        oos_annuals = [r["oos_annual"] for r in all_results]
        for r in all_results:
            print(f"    {r['test_year']}: Annual={r['oos_annual']:+.1%} DD={r['oos_maxdd']:+.1%}", flush=True)
        print(f"  Positive: {sum(1 for a in oos_annuals if a > 0)}/{len(oos_annuals)}")

    # ── Final comparison table ──
    print(f"\n{'='*70}")
    print("  FINAL COMPARISON")
    print(f"{'='*70}", flush=True)
    print(f"  {'Strategy':<35} {'Annual':>8} {'MaxDD':>8} {'Sharpe':>7} {'Calmar':>7}")
    print(f"  {'-'*65}")

    # Baseline v2.0
    res_bl = backtest_period(close, volume, amount,
                             build_rebalance_weights(sc_factor, close), timing=pt_timing)
    print(f"  {'Baseline v2.0 (size only + PT)':<35} {res_bl['annual']:+8.1%} "
          f"{res_bl['maxdd']:+8.1%} {res_bl['sharpe']:+7.2f} {res_bl['calmar']:+7.2f}")

    # Fixed λ=0.5 + PT
    print(f"  {'Size+NPY λ=0.5 + PT':<35} {res_pt['annual']:+8.1%} "
          f"{res_pt['maxdd']:+8.1%} {res_pt['sharpe']:+7.2f} {res_pt['calmar']:+7.2f}")

    # Best vol target (full period)
    print(f"  {'Best Vol Target (full period)':<35} {best_calmar['annual']:+8.1%} "
          f"{best_calmar['maxdd']:+8.1%} {best_calmar['sharpe']:+7.2f} {best_calmar['calmar']:+7.2f}")

    # WF aggregate
    if oos_returns:
        print(f"  {'WF OOS with Vol Target':<35} {wf_annual:+8.1%} "
              f"{wf_maxdd:+8.1%} {wf_sharpe:+7.2f} {wf_annual/abs(wf_maxdd) if wf_maxdd < 0 else 0:+7.2f}")

    print("\nDone.", flush=True)


if __name__ == "__main__":
    main()
