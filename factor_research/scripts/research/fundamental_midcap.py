"""Fundamental factors on mid/large-cap pools — properly aligned.

Previously failed because:
  1. Applied to ALL stocks (small-cap dilutes signal)
  2. ROE outliers not clipped
  3. Not tested across different pool ranges

Now: test fundamental combos on top% pools with proper cleaning.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, load_price_panels

OUT = ROOT / "reports" / "research"; OUT.mkdir(parents=True, exist_ok=True)

# ═══════════ 1. Load & clean ═══════════
print("Loading data...", flush=True)
fund = pd.read_parquet("data_lake/fundamental_batch.parquet",
    columns=["code","report_date","avail_date",
             "eps_ttm","bps","roe","gross_margin","revenue_yoy","net_profit_yoy"])
fund["avail_date"] = pd.to_datetime(fund["avail_date"])

# Clean
fund["roe"] = fund["roe"].clip(-50, 50)
fund["gross_margin"] = fund["gross_margin"].clip(-50, 100)
fund["revenue_yoy"] = fund["revenue_yoy"].clip(-100, 500)
fund["net_profit_yoy"] = fund["net_profit_yoy"].clip(-500, 1000)
fund["eps_ttm"] = fund["eps_ttm"].clip(-50, 100).where(fund["eps_ttm"] > 0, np.nan)
fund["bps"] = fund["bps"].clip(0.01, 500)

close, _, amount = load_price_panels("2010-01-01")
ret = close.pct_change(fill_method=None).fillna(0)

# Pivot financials to daily panels (avail_date aligned, ffill forward)
cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")
trade_dates = pd.to_datetime(cal["date"]).sort_values()

panels = {}
for field in ["eps_ttm","bps","roe","gross_margin","revenue_yoy","net_profit_yoy"]:
    sub = fund[["code","avail_date",field]].dropna(subset=[field])
    sub = sub.sort_values(["code","avail_date"]).drop_duplicates(["code","avail_date"], keep="last")
    p = sub.pivot(index="avail_date", columns="code", values=field)
    p = p.reindex(trade_dates).ffill()
    panels[field] = p

# Raw close for PE/PB
raw = pd.read_parquet("data_lake/price/daily_raw_all.parquet")
raw["date"] = pd.to_datetime(raw["date"])
raw_close = raw.pivot(index="date", columns="code", values="raw_close").reindex(trade_dates)

# PE = raw_close / eps_ttm, restricted to positive eps only
pe = raw_close / panels["eps_ttm"].clip(0.01, None)
pe = pe.clip(3, 200)
pb = raw_close / panels["bps"]
pb = pb.clip(0.1, 30)

# ── Build factors per pool ──
cap = amount.rolling(20).mean() * raw_close
pct = cap.rank(axis=1, pct=True)

POOLS = {
    "Top_5%":     pct >= 0.95,           # ~250 stocks
    "Top_5to15%": (pct >= 0.85) & (pct < 0.95),  # ~500 stocks, mid-cap
    "Top_15to30%": (pct >= 0.70) & (pct < 0.85), # ~700 stocks
}

R = lambda x: x.rank(axis=1, pct=True, na_option="bottom")

def build_combo(pool_mask):
    """Build composite values = (1-pe_r) + (1-pb_r) + roe_r + gm_r + rev_r + np_r"""
    # Value
    pe_r = (1 - R(pe.where(pool_mask)))
    pb_r = (1 - R(pb.where(pool_mask)))
    value = (pe_r + pb_r) / 2
    # Quality
    qual = (R(panels["roe"].where(pool_mask)) +
            R(panels["gross_margin"].where(pool_mask))) / 2
    # Growth
    growth = (R(panels["revenue_yoy"].where(pool_mask)) +
              R(panels["net_profit_yoy"].where(pool_mask))) / 2
    # Combos
    return {
        "Value": value,
        "Quality": qual,
        "Growth": growth,
        "V+Q": (value + qual) / 2,
        "V+Q+G": (value + qual + growth) / 3,
    }

# ═══════════ 2. IC + Backtest ═══════════
fwd20 = close.pct_change(20).shift(-20)

print(f"\n{'Pool':<16} {'Factor':<10} {'n':>5} {'IC20d':>9} {'ICIR':>7} {'Ann':>9} {'Sharpe':>7} {'MaxDD':>8} {'✅':>5}")
print("-" * 75)

all_results = []
for pool_name, pool_mask in POOLS.items():
    n_daily = int(pool_mask.sum(axis=1).mean())
    for factor_name, factor in build_combo(pool_mask).items():
        # IC
        ics = []
        for dt in factor.index[::30]:
            if dt not in fwd20.index: continue
            f = factor.loc[dt].dropna(); r = fwd20.loc[dt].reindex(f.index).dropna()
            cc = f.index.intersection(r.index)
            if len(cc) < 20: continue
            ic, _ = spearmanr(f[cc].values, r[cc].values)
            if not np.isnan(ic): ics.append(ic)
        if len(ics) < 20: continue
        ic_m = np.mean(ics); icir = ic_m / np.std(ics)
        if icir < 0.05: continue  # skip noise

        # Backtest: quarterly rebalance, top 20
        dates = sorted(factor.dropna(how="all").index)
        rebal = [d for i, d in enumerate(dates) if i % 63 == 0]
        sched = {}
        for rd in rebal:
            if rd not in close.index: continue
            pos = close.index.get_loc(rd)
            eff = close.index[min(pos + 1, len(close.index) - 1)]
            f = factor.loc[rd].dropna()
            if len(f) < 20: continue
            sched[eff] = pd.Series(1.0 / 20, index=f.nlargest(20).index)

        if len(sched) < 10: continue
        cfg = StrategyConfig(start="2010-01-01")
        ret_s, _ = backtest_weights(close, sched, pd.Series(1.0, index=close.index, dtype="float64"), cfg)

        r = ret_s[ret_s.index.year >= 2018].fillna(0)
        n_yr = max(len(r) / 252, 1)
        a = (1 + r).cumprod().iloc[-1] ** (1 / n_yr) - 1
        s = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
        d = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())

        meets = d > -0.15 and a > 0.15
        mark = "✅✅✅" if meets else ""
        print(f"{pool_name:<16} {factor_name:<10} {n_daily:>5.0f} {ic_m:>+8.3f} {icir:>6.2f} {a:>+8.1%} {s:>6.2f} {d:>+7.1%} {mark:>5}")

        if meets:
            all_results.append({
                "pool": pool_name, "factor": factor_name,
                "ic": ic_m, "icir": icir, "annual": a, "sharpe": s, "maxdd": d,
            })

if all_results:
    print("\n=== STRATEGIES MEETING CRITERIA (ann>15%, maxdd>-15%) ===")
    for r in all_results:
        print(f"  {r['pool']:<16} {r['factor']:<10} ann={r['annual']:+.1%} sharpe={r['sharpe']:.2f} maxdd={r['maxdd']:+.1%}")
else:
    print("\nNo strategy meets criteria. V2.1 remains the only option.")

pd.DataFrame(all_results).to_csv(OUT / "fundamental_midcap_results.csv", index=False)
