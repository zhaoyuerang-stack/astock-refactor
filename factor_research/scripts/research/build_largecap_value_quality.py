"""v3.0 Large-cap Value + Quality Strategy.

Key fixes from v1:
  - ROE winsorized [-30%, +50%] (filter 4-sigma outliers)
  - PE clipped [3, 200], PB clipped [0.1, 30]
  - Negative EPS stocks excluded from PE (assigned NaN rank, not bought)
  - Cross-sectional rank (not raw z-score)
  - Test multiple universes (top 100/200/300/500)
  - IC by year to check time-series stability
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)


# ============================================================
# 1. CLEAN financial panel
# ============================================================
def load_clean_panels():
    """Load and clean financial + price data."""
    fund = pd.read_parquet("data_lake/fundamental_batch.parquet")
    fund["report_date"] = pd.to_datetime(fund["report_date"])
    fund["avail_date"] = pd.to_datetime(fund["avail_date"])
    cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")
    trade_dates = pd.to_datetime(cal["date"]).sort_values()

    # Clean ROE: winsorize to [-30, +50]
    roe = fund["roe"].copy()
    roe = roe.clip(lower=-30, upper=50)

    # Clean EPS_TTM: keep only positive
    eps = fund["eps_ttm"].copy()
    eps[eps <= 0] = np.nan

    # Clean BPS: keep only positive, clip
    bps = fund["bps"].copy()
    bps = bps.clip(lower=0.01, upper=500)

    # Clean gross margin
    gm = fund["gross_margin"].copy()
    gm = gm.clip(lower=-50, upper=100)

    # Pivot to avail_date × code (防未来函数: ffill 仅从 avail_date 向未来填充)
    fields = {"eps_ttm": eps, "bps": bps, "roe": roe, "gross_margin": gm}
    panels = {}
    for name, series in fields.items():
        sub = pd.DataFrame({
            "code": fund["code"], "avail_date": fund["avail_date"], name: series
        }).dropna(subset=[name])
        sub = sub.sort_values(["code", "avail_date"]).drop_duplicates(["code", "avail_date"], keep="last")
        pivot = sub.pivot(index="avail_date", columns="code", values=name)
        pivot = pivot.reindex(trade_dates).ffill()
        panels[name] = pivot

    # Raw close (must load BEFORE stale tracker — needs raw_close.columns)
    raw_all = pd.read_parquet("data_lake/price/daily_raw_all.parquet")
    raw_all["date"] = pd.to_datetime(raw_all["date"])
    raw_close = raw_all.pivot(index="date", columns="code", values="raw_close")
    raw_close = raw_close.reindex(trade_dates)

    # ── 数据时效性追踪 ──
    rpt_sub = fund[["code", "avail_date", "report_date"]].dropna()
    rpt_sub = rpt_sub.sort_values(["code", "avail_date"]).drop_duplicates(["code", "avail_date"], keep="last")
    report_tracker = rpt_sub.pivot(index="avail_date", columns="code", values="report_date")
    report_tracker = report_tracker.reindex(trade_dates).ffill()
    price_codes = raw_close.columns.intersection(report_tracker.columns)
    report_tracker = report_tracker[price_codes]
    td_epoch = (trade_dates.astype('int64') // 1_000_000_000 // 86400).values
    rt_epoch = (report_tracker.values.astype('int64') // 1_000_000_000 // 86400)
    data_age_arr = td_epoch[:, None] - rt_epoch
    data_age = pd.DataFrame(data_age_arr, index=trade_dates, columns=report_tracker.columns).clip(lower=0)
    STALE_MAX_DAYS = 365
    stale_mask = data_age > STALE_MAX_DAYS

    # Amount (market cap proxy)
    daily_all = pd.read_parquet("data_lake/price/daily_all.parquet",
                                columns=["date", "code", "amount", "close"])
    daily_all["date"] = pd.to_datetime(daily_all["date"])
    amount = daily_all.pivot(index="date", columns="code", values="amount").reindex(trade_dates)
    close_adj = daily_all.pivot(index="date", columns="code", values="close").reindex(trade_dates)

    # Apply stale mask to all financial panels
    for name in ["eps_ttm", "bps", "roe", "gross_margin"]:
        panels[name] = panels[name].where(~stale_mask)

    # PE = raw_close / eps_ttm（数据年龄 ≤ 365 天）
    eps_aligned = panels["eps_ttm"].reindex(index=raw_close.index, columns=raw_close.columns)
    raw_aligned = raw_close.reindex(index=eps_aligned.index, columns=eps_aligned.columns)
    pe = raw_aligned / eps_aligned
    pe = pe.clip(lower=3, upper=200)

    # PB = raw_close / bps
    bps_aligned = panels["bps"].reindex(index=raw_close.index, columns=raw_close.columns)
    pb = raw_aligned / bps_aligned
    pb = pb.clip(lower=0.1, upper=30)

    return {
        "pe": pe, "pb": pb,
        "roe": panels["roe"], "gm": panels["gross_margin"],
        "raw_close": raw_close, "amount": amount, "close": close_adj,
        "data_age": data_age, "stale_ratio": float(stale_mask.mean().mean()),
    }


# ============================================================
# 2. Factor building
# ============================================================
def build_universe(panels, top_n):
    """Market cap proxy from amount × raw_close."""
    cap = panels["amount"].rolling(20).mean() * panels["raw_close"]
    return cap.rank(axis=1, ascending=False, pct=False) <= top_n


def build_factors(panels, universe_size=300):
    """
    Build value + quality factor.
    All factors use cross-sectional rank (robust to outliers).
    """
    univ = build_universe(panels, universe_size)

    # Value = rank(-pe) + rank(-pb)
    pe_r = panels["pe"].rank(axis=1, pct=True, na_option="bottom")
    pb_r = panels["pb"].rank(axis=1, pct=True, na_option="bottom")
    value = (1 - pe_r) + (1 - pb_r)  # higher = cheaper

    # Quality = rank(roe) + rank(gm)
    roe_r = panels["roe"].rank(axis=1, pct=True, na_option="bottom")
    gm_r = panels["gm"].rank(axis=1, pct=True, na_option="bottom")
    quality = roe_r + gm_r

    # Composite
    comp = (value + quality) / 4.0  # scale to [0, 1]
    comp = comp.where(univ)  # only within universe
    # Don't z-score — use raw rank composite which is robust

    return comp, univ, value, quality


def ic_analysis(factor, forward_ret, horizons=(5, 20, 40, 60)):
    """IC by horizon. forward_ret must be pre-computed."""
    results = {}
    for h in horizons:
        fwd = forward_ret[h]  # pre-computed forward return
        ics = []
        for dt in factor.index.intersection(fwd.index):
            if dt not in factor.index: continue
            f = factor.loc[dt].dropna()
            r = fwd.loc[dt].dropna()
            common = f.index.intersection(r.index)
            if len(common) < 50: continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic): ics.append(ic)
        results[h] = {"mean": np.mean(ics), "std": np.std(ics),
                       "icir": np.mean(ics) / np.std(ics) if np.std(ics) > 0 else 0,
                       "pos_ratio": np.mean([1 if i > 0 else 0 for i in ics])}
    return results


# ============================================================
# 3. Simple backtest
# ============================================================
def backtest_long(factor, close, top_n=30, rebalance_months=3):
    """Long-only: top N stocks, equal weight, rebalance every N months."""
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    dates = sorted(factor.dropna(how="all").index)
    rebal_dates = dates[::63*rebalance_months]  # ~63 trading days per month

    current_weight = pd.Series(dtype=float)
    rets = {}
    for i, dt in enumerate(dates):
        if i == 0 or dt not in daily_ret.index: continue

        should_rebal = any(abs((dt - rd).days) <= 2 for rd in rebal_dates)
        if should_rebal or len(current_weight) == 0:
            f = factor.loc[dt].dropna()
            if len(f) >= top_n:
                selected = f.nlargest(top_n).index
                current_weight = pd.Series(1.0 / top_n, index=selected)

        if len(current_weight) == 0:
            rets[dt] = 0.0; continue

        day_r = daily_ret.loc[dt]
        common = current_weight.index.intersection(day_r.index)
        if len(common) == 0:
            rets[dt] = 0.0; continue

        rets[dt] = float(np.dot(current_weight[common].values, day_r[common].values))

    return pd.Series(rets).sort_index()


def main():
    print("=" * 70)
    print("  v3.0 Large-cap Value + Quality (FIXED)")
    print("=" * 70)

    # Load
    print("\n[1/4] Loading & cleaning data...", flush=True)
    panels = load_clean_panels()

    # Pre-compute forward returns
    print("[2/4] IC analysis across universes...", flush=True)
    fwd = {}
    for h in [5, 20, 40, 60]:
        fwd[h] = panels["close"].pct_change(h).shift(-h)

    # Test different universes
    print(f"\n{'Universe':>12} {'IC20d mean':>11} {'IC20d ICIR':>10} {'IC60d mean':>11} {'IC60d ICIR':>10}")
    print("-" * 55)
    best_factor = None
    best_univ = None
    best_ic = -999

    for univ_size in [100, 200, 300, 500]:
        comp, univ, val, qual = build_factors(panels, univ_size)
        ic = ic_analysis(comp, fwd)
        ic20 = ic[20]
        ic60 = ic[60]
        print(f"  Top {univ_size:>4}        {ic20['mean']:>+10.4f}  {ic20['icir']:>9.2f}  {ic60['mean']:>+10.4f}  {ic60['icir']:>9.2f}")

        if ic20["icir"] > best_ic:
            best_ic = ic20["icir"]
            best_factor = comp
            best_univ = univ_size

    print(f"\n  Best universe: Top {best_univ} (IC20d ICIR={best_ic:.2f})")

    # IC by year for best
    print(f"\n[3/4] IC stability (Top {best_univ})...")
    print(f"  {'Year':>6} {'IC20d':>8} {'IC60d':>8} {'PosRatio':>9}")
    for year in range(2012, 2027):
        mask = best_factor.index.year == year
        if mask.sum() < 100: continue
        ic = ic_analysis(best_factor.loc[mask], {k: v.loc[mask] for k, v in fwd.items()})
        ic20 = ic[20]
        print(f"  {year:>6} {ic20['mean']:>+7.3f} {ic[60]['mean']:>+7.3f} {ic20['pos_ratio']:>8.1%}")

    # Backtest
    print(f"\n[4/4] Backtest (Top {best_univ}, quarterly rebalance)...")
    bt = backtest_long(best_factor, panels["close"], top_n=min(30, best_univ // 3), rebalance_months=3)

    # Metrics
    def m(ret):
        if len(ret) == 0: return {}
        nav = (1 + ret.fillna(0)).cumprod()
        n_yr = len(ret) / 252
        ann = nav.iloc[-1] ** (1 / n_yr) - 1
        sharpe = ret.mean() / ret.std() * np.sqrt(252)
        maxdd = float((nav / nav.cummax() - 1).min())
        return {"annual": ann, "sharpe": sharpe, "maxdd": maxdd}

    for label, start_yr in [("2010-2026", 2010), ("2018-2026", 2018), ("2023-2026", 2023)]:
        r = bt[bt.index.year >= start_yr]
        mm = m(r)
        print(f"  {label:<15} annual={mm['annual']:>+7.1%}  Sharpe={mm['sharpe']:>5.2f}  maxDD={mm['maxdd']:>+6.1%}")

    # Yearly
    print("\n  Yearly returns:")
    for year in sorted(set(bt.index.year)):
        r = bt[bt.index.year == year]
        ann = (1 + r.fillna(0)).prod() ** (252 / len(r)) - 1 if len(r) > 0 else 0
        print(f"    {year}: {ann:+.1%}")

    # Orthogonality
    print("\n[Bonus] Orthogonality check...")
    from strategies.small_cap import StrategyConfig, run_small_cap_strategy
    cfg = StrategyConfig(start="2010-01-01")
    base = run_small_cap_strategy(cfg)
    v20_ret = base["returns"]
    common_idx = bt.index.intersection(v20_ret.index)
    corr = bt.loc[common_idx].corr(v20_ret.loc[common_idx])
    print(f"  v3.0 vs v2.0 correlation: {corr:.3f} (target: <0.5 for orthogonal)")

    # Save
    bt.to_csv(OUT / "v3_largecap_results_v2.csv")
    print(f"\nWrote: {OUT / 'v3_largecap_results_v2.csv'}")


if __name__ == "__main__":
    main()
