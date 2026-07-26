"""Portfolio composition CLI.

Usage:
  python3 apps/portfolio_cli.py --compose equal_weight
  python3 apps/portfolio_cli.py --compose risk_parity
  python3 apps/portfolio_cli.py --compose regime_adaptive
  python3 apps/portfolio_cli.py --analyze
  python3 apps/portfolio_cli.py --marginal   # regime-aware eval
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal
from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore
from lake.load_lake import load_fundamental_panel
from portfolio.analysis import (
    contribution_decompose,
    correlation_matrix,
    regime_breakdown,
    rolling_correlation,
)
from portfolio.composer import compose
from portfolio.composer import metrics as port_metrics
from portfolio.marginal import evaluate as marginal_evaluate
from portfolio.regime import classify, regime_stats
from strategies.size_earnings import build_vol_target
from strategies.small_cap import load_price_panels

# ═══════════════════════════════════════════════════
# Strategy builders
# ═══════════════════════════════════════════════════

class Illiq:
    def __init__(self, w=20): self.w = w
    def __call__(self, c, v, a, d):
        r = c.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip((r/(a+1)).rolling(self.w).mean()))

class SizeLoVol:
    def __init__(self, vw=20): self.vw = vw
    def __call__(self, c, v, a, d):
        sz = -np.log(a.rolling(60).mean()+1)
        r = c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip(sz - r.rolling(self.vw).std()))


def run_strategy(name, factor, timing, leverage=1.25):
    """Run a single strategy and return daily returns."""
    close, volume, amount = load_price_panels("2010-01-01")
    td = close.index

    if callable(factor):
        factor = factor(close, volume, amount, td)
    if callable(timing):
        timing = timing(close, amount)

    def wts(f, c, n=25, reb=20):
        fd = f.dropna(how="all").index.intersection(c.index)
        if len(fd) < 50: return {}
        w = {}
        for rd in list(fd[::reb]):
            pos = c.index.get_loc(rd)
            if pos+1 >= len(c.index): continue
            eff = c.index[pos+1]
            fv = f.loc[rd].dropna()
            act = c.loc[rd].dropna().index
            fv = fv.reindex(act).dropna()
            if len(fv) < n: continue
            w[eff] = pd.Series(1.0/n, index=fv.nlargest(n).index)
        return w

    w = wts(factor, close)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    cfg = BacktestConfig(start="2018-01-01", leverage=leverage)
    r = BacktestEngine(prices=prices, config=cfg).run(
        Signal(weights=w, timing=timing, family=name, version="v1.0"))
    return r.returns, close.index


def load_all_strategies():
    """Run all 4 strategies, return dict of daily returns."""
    print("Loading strategies (this takes ~2 min)...", flush=True)

    # Shared timing
    close, volume, amount = load_price_panels("2010-01-01")
    td = close.index
    pt, _, _ = small_cap_timing(close, amount, 16)
    pt_f = pt.astype(float)

    # illiquidity
    print("  [1/4] illiquidity...", flush=True)
    il_f = Illiq(20)(close, volume, amount, td)
    ret_il, _ = run_strategy("illiquidity", il_f, pt_f, 1.25)

    # size-low-vol
    print("  [2/4] size-low-vol...", flush=True)
    slv_f = SizeLoVol(20)(close, volume, amount, td)
    ret_slv, _ = run_strategy("size-low-vol", slv_f, pt_f, 1.25)

    # size-earnings
    print("  [3/4] size-earnings...", flush=True)
    fund = load_fundamental_panel(td, fields=['net_profit_yoy'])
    npy = fund.get('net_profit_yoy', pd.DataFrame()).reindex(td).ffill()
    se_f = safe_zscore(mad_clip(0.5*safe_zscore(mad_clip(-np.log(amount.rolling(60).mean()+1))) + 0.5*safe_zscore(mad_clip(npy))))
    vt = build_vol_target(close, amount, target_vol=0.25, lookback=60)
    ret_se, _ = run_strategy("size-earnings", se_f, pt_f * vt, 1.10)

    # illiq+size blend
    print("  [4/4] illiq+size...", flush=True)
    isz_f = safe_zscore(mad_clip(il_f + safe_zscore(mad_clip(-np.log(amount.rolling(60).mean()+1)))))
    ret_isz, _ = run_strategy("illiq+size", isz_f, pt_f, 1.25)

    print("  Done.", flush=True)

    return {
        "illiquidity": ret_il,
        "size-low-vol": ret_slv,
        "size-earnings": ret_se,
        "illiq+size": ret_isz,
    }, pt_f


# ═══════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════

def cmd_compose(returns, regime, method):
    """Compose portfolio and print results."""
    kw = {}
    if method == "capped":
        from portfolio.strategy_runners import defensive_strategies
        kw = {"defensive": defensive_strategies(), "cap": 0.30}  # LIVE 配比:防御腿合计封顶 30%
    port_ret, weights = compose(returns, method=method, regime_signal=regime, **kw)
    m = port_metrics(port_ret)

    print(f"\n{'='*60}")
    print(f"  Portfolio: {method.upper()}")
    print(f"{'='*60}")
    print(f"  Annual: {m['annual']:+.1%}  MaxDD: {m['maxdd']:+.1%}  "
          f"Sharpe: {m['sharpe']:.2f}  Calmar: {m['calmar']:.2f}  "
          f"Days: {m['n_days']}")

    # Compare to individual strategies
    print("\n  vs Individual Strategies:")
    print(f"  {'Strategy':<20} {'Annual':>8} {'MaxDD':>8} {'Sharpe':>7}")
    print(f"  {'-'*45}")
    for name in returns:
        m_s = port_metrics(returns[name])
        print(f"  {name:<20} {m_s['annual']:>+7.1%} {m_s['maxdd']:>+7.1%} {m_s['sharpe']:>+6.2f}")
    print(f"  {'-'*45}")
    print(f"  {'PORTFOLIO':<20} {m['annual']:>+7.1%} {m['maxdd']:>+7.1%} {m['sharpe']:>+6.2f}")


def cmd_analyze(returns, regime):
    """Run full analysis suite."""
    print(f"\n{'='*60}")
    print("  PORTFOLIO ANALYSIS")
    print(f"{'='*60}")

    # Correlation matrix
    corr = correlation_matrix(returns)
    print("\n  Correlation Matrix:")
    print(corr.round(3).to_string())

    # Contribution decomposition (equal weight baseline)
    contrib = contribution_decompose(returns)
    print("\n  Contribution Decomposition (equal weight):")
    print(f"  {'Strategy':<20} {'Weight':>7} {'Ann':>7} {'Contrib':>8} {'Risk%':>7} {'MargS':>7}")
    for idx, row in contrib.iterrows():
        print(f"  {idx:<20} {row['weight']:>6.1%} {row['annual']:>+6.1%} "
              f"{row['ann_contrib_pct']:>7.1%} {row['risk_contrib_pct']:>6.1%} "
              f"{row['marginal_sharpe']:>+6.2f}")

    # Regime breakdown
    if regime is not None:
        rb = regime_breakdown(returns, regime)
        print("\n  Regime Breakdown (PureTrend ON vs OFF):")
        print(rb.round(3).to_string())

    # Rolling correlation
    names = list(returns.keys())
    if len(names) >= 2:
        a, b = names[0], names[1]
        rc = rolling_correlation(returns, 252)
        print(f"\n  Rolling 252d Correlation ({a} vs {b}):")
        print(f"    Mean: {rc.mean():.3f}  Min: {rc.min():.3f}  Max: {rc.max():.3f}  "
              f"<0.5: {(rc<0.5).mean():.0%}")


def cmd_marginal(returns, regime_signal):
    """Regime-aware marginal contribution evaluation."""
    # Use small-cap index as market proxy for regime classification
    close, volume, amount = load_price_panels("2010-01-01")
    mkt_ret = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)

    # Regime distribution
    regimes = classify(mkt_ret)
    stats = regime_stats(mkt_ret, regimes)

    print(f"\n{'='*60}")
    print("  REGIME-AWARE MARGINAL EVALUATION")
    print(f"{'='*60}")

    print("\n  Market Regime Distribution (2010-2026):")
    for idx, row in stats.iterrows():
        print(f"    {idx:<18} {row['pct_days']:>5.0%}  ann={row['annual']:>+6.1%}  "
              f"vol={row['vol']:.0%}  sharpe={row['sharpe']:>+5.2f}  n={row['n_days']}d")

    # Build existing LIVE pool (all 4 strategies)
    live_pool = returns.copy()

    print("\n  Evaluating each strategy against LIVE pool (4 strategies):")

    # Test: add defensive candidates (low-vol, amplitude, quality)
    from factors.utils import mad_clip, safe_zscore
    ret_daily = close.pct_change(fill_method=None).replace([np.inf,-np.inf],np.nan)

    # Low vol variants
    lo_vol_20 = safe_zscore(mad_clip(-ret_daily.rolling(20).std()))
    lo_vol_60 = safe_zscore(mad_clip(-ret_daily.rolling(60).std()))
    # Amplitude mean (low intraday range = defensive)
    raw_all = pd.read_parquet("data_lake/price/daily_raw_all.parquet",
                               columns=["date","code","raw_high","raw_low"])
    raw_all["date"] = pd.to_datetime(raw_all["date"])
    raw_all = raw_all[raw_all["date"] >= pd.Timestamp("2010-01-01")]
    raw_all["code"] = raw_all["code"].astype(str).str.zfill(6)
    raw_high = raw_all.pivot(index="date", columns="code", values="raw_high").reindex(index=close.index)
    raw_low = raw_all.pivot(index="date", columns="code", values="raw_low").reindex(index=close.index)
    amplitude = (raw_high - raw_low) / raw_low.replace(0, np.nan)
    amp_mean = safe_zscore(mad_clip(-amplitude.rolling(60).mean()))

    # Large-cap bias (top 50% by amount)
    amt_rank = amount.rolling(60).mean().rank(axis=1, pct=True)
    lc_lo_vol = safe_zscore(mad_clip(-ret_daily.rolling(60).std() * (amt_rank > 0.50)))

    test_factors = {
        "low_vol_n20": lo_vol_20,
        "low_vol_n60": lo_vol_60,
        "amplitude_n60": amp_mean,
        "largecap_lowvol": lc_lo_vol,
    }

    def wts(f, c, n=25, reb=20):
        fd = f.dropna(how="all").index.intersection(c.index)
        if len(fd)<50: return {}
        w = {}
        for rd in list(fd[::reb]):
            pos=c.index.get_loc(rd)
            if pos+1>=len(c.index): continue
            eff=c.index[pos+1]; fv=f.loc[rd].dropna(); act=c.loc[rd].dropna().index
            fv=fv.reindex(act).dropna()
            if len(fv)<n: continue
            w[eff]=pd.Series(1.0/n,index=fv.nlargest(n).index)
        return w

    prices = PricePanel(close=close, volume=volume, amount=amount)
    cfg = BacktestConfig(start="2018-01-01", leverage=1.25)

    # Add existing strategies + new defensive test candidates
    candidates = {}
    for name in returns:
        candidates[name] = returns[name]

    for t_name, t_factor in test_factors.items():
        t_w = wts(t_factor, close)
        t_r = BacktestEngine(prices=prices, config=cfg).run(
            Signal(weights=t_w, timing=None)).returns
        candidates[t_name] = t_r

    results = []
    for c_name, c_ret in candidates.items():
        report = marginal_evaluate(c_ret, c_name, live_pool, mkt_ret)
        results.append(report)

    results.sort(key=lambda r: (
        0 if r["grade"] == "LIVE_D" else
        1 if r["grade"] == "LIVE_P" else
        2 if r["grade"] == "LIVE_K" else
        3 if r["grade"] == "LIVE_C" else 4
    ))

    print(f"\n  {'Candidate':<20} {'Grade':<12} {'ΔSharpe':>8} {'RegimeSc':>8} "
          f"{'BearImpr':>8} {'Corr':>6} {'ΔMaxDD':>8}")
    print(f"  {'-'*80}")
    for r in results:
        fs = r["full_sample"]
        df = r["defensive"]
        impr = df.get("improvement", 0)
        print(f"  {r['candidate']:<20} {r['grade']:<12} {fs['delta_sharpe']:>+7.2f} "
              f"{r['regime_weighted_score']:>+7.2f} {impr:>+7.1%} "
              f"{df['avg_corr_to_live']:>+5.2f} {fs['delta_maxdd']:>+7.1%}")

    # Highlight defensive findings
    defensive = [r for r in results if r["grade"] == "LIVE_D"]
    if defensive:
        print("\n  ⭐ DEFENSIVE ASSETS FOUND:")
        for r in defensive:
            print(f"    {r['candidate']}: {r['recommendation']}")
    else:
        # Show near-misses
        near = [r for r in results if "NEAR MISS" in r.get("recommendation", "")]
        if near:
            print("\n  ⚡ NEAR-MISS DEFENSIVE:")
            for r in near:
                print(f"    {r['candidate']}: {r['recommendation']}")
        else:
            print("\n  ℹ️  No defensive candidates with sufficient bear protection yet.")
            print("    Current LIVE all highly correlated (0.8+) — cross-asset data needed.")

    # Regime detail for top candidates
    print("\n  Regime detail (top 3 + any LIVE_D):")
    shown = set()
    count = 0
    for r in results:
        if r["grade"] == "LIVE_D" or count < 3:
            if r["candidate"] in shown: continue
            shown.add(r["candidate"])
            count += 1
            rd = r["regime_summary"]
            print(f"  {r['candidate']:<20} ", end="")
            parts = [f"{reg}:{rd[reg]['annual']:>+6.1%}" for reg in ["bull","bear","chop","panic","upside_crisis"]]
            print("  ".join(parts))


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def cmd_discover_legs(start="2018-01-01"):
    """跨资产防御腿发现:按对在册组合的边际 Δsharpe 排序,标 SHADOW 推荐。"""
    from portfolio.cross_asset import search_cross_asset_legs

    res = search_cross_asset_legs(start=start)
    mb = res["baseline"]
    print(f"\n{'='*72}\n  跨资产防御腿发现(边际透镜,非单独 Sharpe)\n{'='*72}")
    print(f"  在册 ACTIVE 基线: sh={mb['sharpe']:+.2f} cal={mb['calmar']:+.2f} mdd={mb['maxdd']:+.1%}\n")
    print(f"  {'腿':<20s}{'单Sh':>6s}{'相关':>7s}{'2018':>8s}{'Δsh':>7s}{'Δcal':>7s}  SHADOW?")
    print("  " + "-" * 66)
    for l in res["legs"]:
        flag = "★推荐" if l["shadow_recommend"] else ""
        print(f"  {l['leg']:<20s}{l['standalone_sharpe']:+6.2f}{l['corr_to_book']:+7.2f}"
              f"{l['ret_2018']:+8.1%}{l['d_sharpe']:+7.2f}{l['d_calmar']:+7.2f}  {flag}")
    rec = res["recommended"]
    print(f"\n  SHADOW 推荐({len(rec)}): " + (", ".join(l["leg"] for l in rec) if rec else "无"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--compose", choices=["equal_weight", "risk_parity", "regime_adaptive", "capped"],
                    help="Run portfolio composition(capped=防御腿封顶30%,LIVE 配比)")
    ap.add_argument("--analyze", action="store_true", help="Run full analysis")
    ap.add_argument("--marginal", action="store_true", help="Regime-aware marginal evaluation")
    ap.add_argument("--discover-legs", action="store_true", help="跨资产防御腿发现(边际排序)")
    args = ap.parse_args()

    if not any([args.compose, args.analyze, args.marginal, args.discover_legs]):
        ap.print_help()
        return

    if args.discover_legs:
        cmd_discover_legs()
        if not any([args.compose, args.analyze, args.marginal]):
            return

    returns, regime = load_all_strategies()

    if args.compose:
        cmd_compose(returns, regime, args.compose)

    if args.analyze:
        cmd_analyze(returns, regime)

    if args.marginal:
        cmd_marginal(returns, regime)


if __name__ == "__main__":
    main()
