"""Pure quality + growth factor (no overlay, fundamental data only).

Strict avail_date alignment: T日只能看到 avail_date <= T 的财务数据.
ffill from most recently disclosed financials — never look ahead.

Factor construction:
  Quality  = rank(roe) + rank(gross_margin)
  Growth   = rank(revenue_yoy) + rank(net_profit_yoy)
  Composite = (Quality + Growth) / 4 → cross-sectional z-score

Universe: top 500 by market cap proxy (amount × raw_close)

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/build_quality_growth.py
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

from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)


# ======================================================================
# 1. Build quality + growth panels (strict avail_date alignment)
# ======================================================================
def build_fundamental_panels():
    """Build daily date×code panels for ROE, gross_margin, revenue_yoy, net_profit_yoy.
    Align via avail_date + ffill forward only (no backward leak).
    """
    fund = pd.read_parquet("data_lake/fundamental_batch.parquet",
                           columns=["code","report_date","avail_date",
                                    "roe","gross_margin","revenue_yoy","net_profit_yoy"])
    fund["avail_date"] = pd.to_datetime(fund["avail_date"])

    # Clip extreme values
    fund["roe"] = fund["roe"].clip(-50, 50)
    fund["gross_margin"] = fund["gross_margin"].clip(-100, 100)
    fund["revenue_yoy"] = fund["revenue_yoy"].clip(-100, 500)
    fund["net_profit_yoy"] = fund["net_profit_yoy"].clip(-500, 1000)

    cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")
    trade_dates = pd.to_datetime(cal["date"]).sort_values()

    # Build panels: for each field, pivot by avail_date, reindex to trade_dates, ffill forward
    panels = {}
    for field in ["roe", "gross_margin", "revenue_yoy", "net_profit_yoy"]:
        sub = fund[["code","avail_date",field]].dropna(subset=[field])
        sub = sub.sort_values(["code","avail_date"]).drop_duplicates(["code","avail_date"], keep="last")
        pivot = sub.pivot(index="avail_date", columns="code", values=field)
        pivot = pivot.reindex(trade_dates).ffill()
        panels[field] = pivot

    return panels, trade_dates


def build_amount_close_panels(trade_dates):
    """Market cap proxy and adjusted close panels."""
    daily = pd.read_parquet("data_lake/price/daily_all.parquet",
                            columns=["date","code","amount","close"])
    daily["date"] = pd.to_datetime(daily["date"])
    amount = daily.pivot(index="date", columns="code", values="amount").reindex(trade_dates)
    close = daily.pivot(index="date", columns="code", values="close").reindex(trade_dates)
    raw = pd.read_parquet("data_lake/price/daily_raw_all.parquet")
    raw["date"] = pd.to_datetime(raw["date"])
    raw_close = raw.pivot(index="date", columns="code", values="raw_close").reindex(trade_dates)
    return amount, close, raw_close


def build_factor(panels, amount, raw_close, n_univ=300):
    """Build quality+growth composite factor. Returns (factor_df, universe_mask)."""
    # Universe: top N by market cap
    cap = amount.rolling(20).mean() * raw_close
    univ = cap.rank(axis=1, ascending=False, pct=False) <= n_univ

    # Cross-sectional rank (pct) for each sub-factor
    roe_r  = panels["roe"].rank(axis=1, pct=True, na_option="bottom")
    gm_r   = panels["gross_margin"].rank(axis=1, pct=True, na_option="bottom")
    rev_r  = panels["revenue_yoy"].rank(axis=1, pct=True, na_option="bottom")
    np_r   = panels["net_profit_yoy"].rank(axis=1, pct=True, na_option="bottom")

    quality = (roe_r + gm_r) / 2
    growth  = (rev_r + np_r) / 2
    composite = (quality + growth) / 2
    composite = composite.where(univ)  # restrict to universe

    # Cross-sectional z-score
    composite = composite.subtract(composite.mean(axis=1), axis=0)
    composite = composite.divide(composite.std(axis=1).replace(0,1.0), axis=0)

    return composite, univ


# ======================================================================
# 2. IC Analysis
# ======================================================================
def ic_analysis(factor, close, horizons=(5, 20, 40, 60)):
    results = {}
    for h in horizons:
        fwd = close.pct_change(h).shift(-h)
        ics = []
        for dt in factor.index[::20]:
            if dt not in fwd.index: continue
            f = factor.loc[dt].dropna()
            r = fwd.loc[dt].dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30: continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic): ics.append(ic)
        results[h] = {"mean": np.mean(ics), "icir": np.mean(ics)/np.std(ics) if np.std(ics)>0 else 0}
    return results


# ======================================================================
# 3. Backtest
# ======================================================================
def backtest_factor(factor, close, top_n=30, rebalance_bdays=63):
    """Quarterly rebalance, equal weight, long top N."""
    daily_ret = close.pct_change(fill_method=None).replace([np.inf,-np.inf],np.nan).fillna(0.0)
    dates = sorted(factor.dropna(how="all").index)
    rebal_dates = dates[::rebalance_bdays]

    current_w = pd.Series(dtype=float)
    rets = {}
    for i, dt in enumerate(dates):
        if i == 0 or dt not in daily_ret.index: continue

        is_rebal = any(abs((dt - rd).days) <= 2 for rd in rebal_dates)
        if is_rebal or len(current_w) == 0:
            f = factor.loc[dt].dropna()
            if len(f) >= top_n:
                sel = f.nlargest(top_n).index
                current_w = pd.Series(1.0/top_n, index=sel)

        if len(current_w) == 0:
            rets[dt] = 0.0; continue

        dr = daily_ret.loc[dt]
        common = current_w.index.intersection(dr.index)
        rets[dt] = float(np.dot(current_w[common].values, dr[common].values)) if len(common) else 0.0

    return pd.Series(rets).sort_index()


# ======================================================================
# 4. Main
# ======================================================================
def main():
    print("=" * 60)
    print("  纯财务质量+成长因子 (strictly look-ahead-free)")
    print("=" * 60)

    print("\n[1/4] Loading fundamental panels...", flush=True)
    panels, trade_dates = build_fundamental_panels()
    amount, close, raw_close = build_amount_close_panels(trade_dates)
    print(f"  ROE: {panels['roe'].notna().sum().sum():,} cells")
    print(f"  rev_yoy: {panels['revenue_yoy'].notna().sum().sum():,} cells")

    print("[2/4] IC analysis across universes...", flush=True)
    print(f"\n  {'Univ':>8} {'IC20d':>8} {'ICIR':>6} {'IC40d':>8} {'ICIR':>6}")
    best_icir, best_n, best_factor = -999, 0, None
    for n in [100, 200, 300, 500]:
        factor, univ = build_factor(panels, amount, raw_close, n)
        ic = ic_analysis(factor, close)
        print(f"  Top{n:>4} {ic[20]['mean']:>+7.3f} {ic[20]['icir']:>5.2f} {ic[40]['mean']:>+7.3f} {ic[40]['icir']:>5.2f}")
        if ic[20]["icir"] > best_icir:
            best_icir = ic[20]["icir"]; best_n = n; best_factor = factor

    print(f"\n  Best universe: Top {best_n} (IC20d ICIR={best_icir:.2f})")

    # IC by year
    print(f"\n[3/4] IC stability (Top {best_n})...")
    fwd = close.pct_change(20).shift(-20)
    print(f"  {'Year':>6} {'IC20d':>8} {'PosRatio':>8}")
    for year in range(2012, 2027):
        mask = best_factor.index.year == year
        if mask.sum() < 50: continue
        ics = []
        for dt in best_factor.loc[mask].index[::5]:
            f = best_factor.loc[dt].dropna()
            r = fwd.loc[dt].dropna() if dt in fwd.index else pd.Series()
            common = f.index.intersection(r.index) if len(r)>0 else pd.Index([])
            if len(common) < 30: continue
            ic, _ = spearmanr(f[common].values, r[common].values)
            if not np.isnan(ic): ics.append(ic)
        if ics:
            print(f"  {year:>6} {np.mean(ics):>+7.3f} {(np.array(ics)>0).mean():>7.1%}")

    # Backtest
    print(f"\n[4/4] Backtest (Top {best_n}, quarterly, top 30)...")
    ret = backtest_factor(best_factor, close, top_n=min(30, best_n//3), rebalance_bdays=63)

    def m(ret):
        r = ret.fillna(0); n_yr = max(len(r)/252, 1)
        a = (1+r).cumprod().iloc[-1]**(1/n_yr)-1
        s = r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
        d = float(((1+r).cumprod()/(1+r).cumprod().cummax()-1).min())
        return {"annual":a, "sharpe":s, "maxdd":d}

    for label, yr in [("IS 2018-2026",2018), ("OOS 2023-2026",2023), ("Full 2010-2026",2010)]:
        r = ret[ret.index.year>=yr]
        mm = m(r)
        print(f"  {label:<15} ann={mm['annual']:>+7.1%}  Sharpe={mm['sharpe']:>5.2f}  maxDD={mm['maxdd']:>+6.1%}")

    # Correlation vs v2.0
    print("\n[Bonus] Orthogonality check...")
    cfg = StrategyConfig(start="2010-01-01")
    base = run_small_cap_strategy(cfg)
    v20 = base["returns"]
    common = ret.index.intersection(v20.index)
    corr = ret.loc[common].corr(v20.loc[common])
    print(f"  vs v2.0 corr: {corr:.3f}  (target <0.5 = orthogonal)")

    # Yearly
    print(f"\n  {'Year':>6} {'Return':>9}")
    for year in sorted(set(ret.index.year)):
        r = ret[ret.index.year == year]
        ann = (1+r.fillna(0)).prod()**(252/len(r))-1 if len(r)>50 else float('nan')
        if not np.isnan(ann):
            print(f"  {year:>6} {ann:>+8.1%}")

    ret.to_csv(OUT / "quality_growth_daily.csv")
    print(f"\nWrote: {OUT / 'quality_growth_daily.csv'}")


if __name__ == "__main__":
    main()
