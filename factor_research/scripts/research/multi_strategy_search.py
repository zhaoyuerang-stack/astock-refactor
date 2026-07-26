"""Multi-strategy search across different market cap pools.

Goal: find strategies that are:
  1. NOT competing with v2.1 (different market cap pool)
  2. MaxDD < -15%
  3. Annual > 15%
  4. Standalone (no overlay needed — keep it simple)

Pools:
  Pool A: top 50 by market cap (蓝筹/银行/茅台)
  Pool B: top 51-200 (中盘成长)
  Pool C: top 2%-6% (中大盘, ~117 stocks)
  Pool D: bottom 70% (v2.1's zone - skip, we already have it)

Factors tested per pool:
  - Momentum (20d, 60d)
  - Reversal (5d, 20d)
  - Value (PE/PB if available)
  - 52-week high proximity
  - MA crossover

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/multi_strategy_search.py
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

OUT = ROOT / "reports" / "research"; OUT.mkdir(parents=True, exist_ok=True)

close, _, amount = load_price_panels("2010-01-01")
ret = close.pct_change(fill_method=None).fillna(0)

cap = amount.rolling(20).mean() * close
pct = cap.rank(axis=1, pct=True)

POOLS = {
    "A_蓝筹50": pct >= 0.99,                    # top 1% ~ top 50
    "B_中盘成长200": (pct >= 0.96) & (pct < 0.99),  # 96-99% ~ 51-200
    "C_中大盘117": (pct >= 0.02) & (pct <= 0.06),    # 2-6% ~ 100-300
}

R = lambda x: x.rank(axis=1, pct=True)
U = lambda x, pool: x.where(pool)

def build_factors(pool):
    return {
        "Rev5d":   U(R(-ret.rolling(5).sum()), pool),
        "Rev20d":  U(R(-ret.rolling(20).sum()), pool),
        "Mom20d":  U(R(ret.rolling(20).sum()), pool),
        "Mom60d":  U(R(ret.rolling(60).sum()), pool),
        "MA_Cross": U(R(close.rolling(20).mean() - close.rolling(60).mean()), pool),
        "Value52w": U(R(-close / close.rolling(252).max()), pool),
    }

@dataclass
class Result:
    name: str; pool: str; n_daily: int; ic: float; icir: float
    annual: float; sharpe: float; maxdd: float
    corr_v21: float; meets_criteria: bool

def backtest_factor(factor, pool_name, top_n=20, reb=63):
    dates = sorted(factor.dropna(how="all").index)
    rebal = [d for i,d in enumerate(dates) if i%reb==0]
    sched = {}
    for rd in rebal:
        if rd not in close.index: continue
        pos = close.index.get_loc(rd); eff = close.index[min(pos+1, len(close.index)-1)]
        f = factor.loc[rd].dropna()
        if len(f) < top_n: continue
        sched[eff] = pd.Series(1.0/top_n, index=f.nlargest(top_n).index)
    cfg = StrategyConfig(start="2010-01-01")
    ret_s, _ = backtest_weights(close, sched, pd.Series(1.0,index=close.index,dtype="float64"), cfg)
    return ret_s

def m(ret):
    r = ret.fillna(0); n = max(len(r)/252, 1)
    a = (1+r).cumprod().iloc[-1]**(1/n)-1
    s = r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    d = float(((1+r).cumprod()/(1+r).cumprod().cummax()-1).min())
    return a, s, d

# ── v2.1 ref ──
v21 = small_cap_factor(amount, 30)
s21 = build_rebalance_weights(v21, close, 30, 15)
t21, _, _ = small_cap_timing(close, amount, 16)
cfg = StrategyConfig(start="2010-01-01")
ret21, _ = backtest_weights(close, s21, t21.astype(float), cfg)

results = []
for pool_name, pool_mask in POOLS.items():
    n_daily = int(pool_mask.sum(axis=1).mean())
    print(f"\n--- {pool_name} ({n_daily} stocks/day) ---")

    for factor_name, factor in build_factors(pool_mask).items():
        # IC
        fwd20 = close.pct_change(20).shift(-20)
        ics, cors = [], []
        for dt in factor.index[::30]:
            if dt not in fwd20.index: continue
            f = factor.loc[dt].dropna(); r = fwd20.loc[dt].reindex(f.index).dropna()
            cc = f.index.intersection(r.index)
            if len(cc)<15: continue
            ic,_ = spearmanr(f[cc].values, r[cc].values)
            if not np.isnan(ic): ics.append(ic)
            if dt in v21.index:
                vv = v21.loc[dt].dropna(); c2 = cc.intersection(vv.index)
                if len(c2)>=15:
                    co,_ = spearmanr(f[list(c2)].values, vv[list(c2)].values)
                    if not np.isnan(co): cors.append(co)

        if len(ics)<20: continue
        ic_m = np.mean(ics); icir = ic_m/np.std(ics)

        # Skip if ICIR < 0.1 (too weak to bother backtesting)
        if icir < 0.1: continue

        # Backtest
        for tn in [15, 20, 25]:
            for reb in [21, 63]:
                ret_f = backtest_factor(factor, pool_name, tn, reb)
                r = ret_f[ret_f.index.year>=2018].fillna(0)
                a, s, d = m(r)
                mc = np.mean(cors) if cors else 0
                meets = (d > -0.15) and (a > 0.15)

                name = f"{pool_name[:2]}_{factor_name}_t{tn}_r{reb}"
                results.append(Result(name, pool_name, n_daily, ic_m, icir, a, s, d, mc, meets))

                if meets or (len(results) % 50 == 0):
                    mark = " *** FOUND ***" if meets else ""
                    print(f"  {name:<25} ann={a:+.1%} sharpe={s:.2f} maxdd={d:+.1%} ICIR={icir:.2f} corr_v21={mc:+.2f}{mark}")

# ── Summary ──
print(f"\n{'='*70}")
print("  Strategies Meeting Criteria (MaxDD<-15%, Ann>15%)")
print(f"{'='*70}")
found = [r for r in results if r.meets_criteria]
v21r = ret21[ret21.index.year>=2018].fillna(0)
a21, s21v, d21 = m(v21r)
print(f"  v2.1 (ref): ann={a21:+.1%} sharpe={s21v:.2f} maxdd={d21:+.1%}")
if found:
    for r in found:
        print(f"  {r.name:<30} ann={r.annual:+.1%} sharpe={r.sharpe:.2f} maxdd={r.maxdd:+.1%} pool={r.pool} corr={r.corr_v21:+.2f}")
else:
    # Show closest
    closest = sorted(results, key=lambda r: (abs(r.maxdd+0.15) + abs(r.annual-0.15)))[:10]
    print("  None found. Closest 10:")
    for r in closest:
        print(f"  {r.name:<30} ann={r.annual:+.1%} sharpe={r.sharpe:.2f} maxdd={r.maxdd:+.1%} pool={r.pool}")

pd.DataFrame([{'name':r.name,'pool':r.pool,'ic':r.ic,'icir':r.icir,
                'annual':r.annual,'sharpe':r.sharpe,'maxdd':r.maxdd,
                'corr_v21':r.corr_v21} for r in results]).to_csv(
    OUT/"multi_strategy_search.csv", index=False)
print(f"\nWrote: {OUT/'multi_strategy_search.csv'}")
print(f"  Total tested: {len(results)}")
