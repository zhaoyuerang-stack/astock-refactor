"""Mid-large cap factor search (500-1000亿 market cap range).

Universe: top 2-6% by amount*close proxy → ~100-300 mid-large cap stocks.
Factors tested across 3 horizons: momentum, reversal, 52wk high, value, composite.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/midcap_factor.py
"""
import os
import sys
import time
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

# ── Universe ──
cap = amount.rolling(20).mean() * close
pct = cap.rank(axis=1, pct=True)
univ = (pct >= 0.02) & (pct <= 0.06)
print(f"Universe: {univ.sum(axis=1).mean():.0f} stocks/day", flush=True)

# ── Factors ──
R = lambda x: x.rank(axis=1, pct=True)
U = lambda x: x.where(univ)

factors = {
    "Momentum20d":  U(R(ret.rolling(20).sum())),
    "Momentum60d":  U(R(ret.rolling(60).sum())),
    "Reversal5d":   U(R(-ret.rolling(5).sum())),
    "Value_52wk":   U(R(-close / close.rolling(252).max())),  # closer to 52wk high = buy
    "MA_Crossover": U(R(close.rolling(20).mean() - close.rolling(60).mean())),
    "Composite":    None,  # computed below
}

# Composite = average rank of all 5
comps = []
for k in list(factors.keys()):
    if factors[k] is not None:
        comps.append(factors[k])
factors["Composite"] = U(R(sum(comps) / len(comps)))

# ── IC screening ──
fwd20 = close.pct_change(20).shift(-20)
v21 = small_cap_factor(amount, 30)

print(f"\n{'Factor':<18} {'IC20d':>9} {'ICIR':>7} {'Pos%':>7} {'Corr_v21':>9} {'N':>5}")
print("-" * 55)

all_ics = {}
for name, factor in factors.items():
    t0 = time.time()
    ics, cors = [], []
    for dt in factor.index[::30]:
        if dt not in fwd20.index: continue
        try:
            f = factor.loc[dt].dropna(); r = fwd20.loc[dt].reindex(f.index).dropna()
            cc = f.index.intersection(r.index)
            if len(cc) < 20: continue
            ic, _ = spearmanr(f[cc].values, r[cc].values)
            if not np.isnan(ic): ics.append(ic)
            if dt in v21.index:
                vv = v21.loc[dt].dropna(); c2 = cc.intersection(vv.index)
                if len(c2) >= 20:
                    co, _ = spearmanr(f[list(c2)].values, vv[list(c2)].values)
                    if not np.isnan(co): cors.append(co)
        except Exception: continue
    if ics:
        m = np.mean(ics); icir = m / np.std(ics); mc = np.mean(cors) if cors else 1.0
        orth = " ← ORTH" if abs(mc) < 0.3 else ""
        print(f"{name:<18} {m:>+8.4f} {icir:>6.2f} {(np.array(ics)>0).mean():>6.0%} {mc:>+8.3f}{orth}")
        all_ics[name] = {"mean": m, "icir": icir}

# ── Best factor backtest ──
best_name = max(all_ics, key=lambda k: all_ics[k]["icir"])
best_factor = factors[best_name]
print(f"\n=== Backtesting: {best_name} ===\n", flush=True)

dates = sorted(best_factor.dropna(how="all").index)
rebal = [d for i, d in enumerate(dates) if i % 63 == 0]
sched = {}
for rd in rebal:
    if rd not in close.index: continue
    pos = close.index.get_loc(rd); eff = close.index[min(pos+1, len(close.index)-1)]
    f = best_factor.loc[rd].dropna()
    if len(f) < 15: continue
    tn = min(15, len(f))
    sched[eff] = pd.Series(1.0/tn, index=f.nlargest(tn).index)

cfg = StrategyConfig(start="2010-01-01")
ret_mid, _ = backtest_weights(close, sched, pd.Series(1.0,index=close.index), cfg)

# v2.1 ref
f21 = small_cap_factor(amount, 30)
s21 = build_rebalance_weights(f21, close, 30, 15)
t21, _, _ = small_cap_timing(close, amount, 16)
ret21, _ = backtest_weights(close, s21, t21.astype(float), cfg)

def cagr(r): rr=r.fillna(0); n=max(len(rr)/252,1); return (1+rr).cumprod().iloc[-1]**(1/n)-1
def shrp(r): rr=r.fillna(0); return rr.mean()/rr.std()*np.sqrt(252) if rr.std()>0 else 0
def mdd(r): return float(((1+r.fillna(0)).cumprod()/(1+r.fillna(0)).cumprod().cummax()-1).min())

for label, ret_s in [("Mid-cap factor", ret_mid), ("v2.1 small-cap", ret21)]:
    r = ret_s[ret_s.index.year>=2018].fillna(0)
    print(f"{label:<20} ann={cagr(r):+.1%}  sharpe={shrp(r):.2f}  maxdd={mdd(r):+.1%}")

cc = ret_mid.loc[ret_mid.index.intersection(ret21.index)].corr(
    ret21.loc[ret_mid.index.intersection(ret21.index)])
print(f"\nReturn correlation: {cc:.3f}")

# 50/50
combo = ret_mid.reindex(ret21.index).fillna(0)*0.5 + ret21*0.5
r = combo[combo.index.year>=2018].fillna(0)
print(f"50/50 combo: ann={cagr(r):+.1%}  sharpe={shrp(r):.2f}  maxdd={mdd(r):+.1%}")

# Yearly
print(f"\n{'Year':>6} {'Mid-cap':>9} {'v2.1':>9} {'50/50':>9}")
for y in range(2019, 2027):
    rm = ret_mid[ret_mid.index.year==y]; rv = ret21[ret21.index.year==y]
    rc = combo[combo.index.year==y]
    if len(rm) < 50: continue
    print(f"{y:>6} {cagr(rm):>+8.1%} {cagr(rv):>+8.1%} {cagr(rc):>+8.1%}")

ret_mid.to_csv(OUT / "midcap_factor_daily.csv")
print(f"\nWrote: {OUT / 'midcap_factor_daily.csv'}")
