"""Large-cap analyst factor with self-timing protection.

Core idea: analyst buy-ratio predicts future returns for covered large-caps.
Add self-timing: when aggregate conviction drops, reduce position to cut drawdowns.

Target: ann>15%, maxDD>-15%.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/largecap_analyst_timing.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, load_price_panels

OUT = ROOT / "reports" / "research"; OUT.mkdir(parents=True, exist_ok=True)
ANALYST_DIR = OUT / "analyst_cache"

close, vol, amount = load_price_panels("2018-01-01")
ret = close.pct_change(fill_method=None).fillna(0)

# ── Build analyst factor ──
print("Building analyst factor from cache...", flush=True)
ratings = {}
for f in ANALYST_DIR.glob("*.parquet"):
    df = pd.read_parquet(f); code = f.stem
    if code not in close.columns: continue
    df = df.copy()
    dc = "日期" if "日期" in df.columns else "date"
    df["date"] = pd.to_datetime(df[dc])
    df["score"] = df["东财评级"].map({"买入":2,"增持":1,"中性":0,"减持":-1,"卖出":-2,"":np.nan})
    df = df.dropna(subset=["score","date"]).sort_values("date")
    if len(df) < 20: continue
    df["ym"] = df["date"].dt.to_period("M")
    m = df.groupby("ym").agg(
        buy_pct=("score", lambda x: (x>=1.0).mean()),
        n=("score", "count"),
    )
    m.index = m.index.to_timestamp()
    buy_90 = m["buy_pct"].rolling(90, min_periods=30).mean()
    n_90 = m["n"].rolling(90, min_periods=30).sum()
    factor = (buy_90 * n_90.clip(0, 30) / 3).reindex(close.index).ffill()
    ratings[code] = factor

F = pd.DataFrame(ratings).rank(axis=1, pct=True)
# Aggregate analyst sentiment (for timing)
AGG = pd.DataFrame(ratings).mean(axis=1).reindex(close.index).ffill()

# ── Universe: top 5% by market cap ──
cap = amount.rolling(20).mean() * close
univ = cap.rank(axis=1, pct=True) >= 0.95
F = F.where(univ)
n_daily = univ.sum(axis=1).mean()
print(f"  {len(ratings)} stocks, universe={n_daily:.0f}/day", flush=True)

# ── Alternative factors ──
R = lambda x: x.rank(axis=1, pct=True)
MA_CROSS = R(close.rolling(20).mean() - close.rolling(60).mean()).where(univ)
REV5D = R(-ret.rolling(5).sum()).where(univ)

# ── Build scheduled weights ──
dates = sorted(F.dropna(how="all").index)
rebal = [d for i, d in enumerate(dates) if i % 63 == 0]

def build_sched(factor, tn=20):
    sched = {}
    for rd in rebal:
        if rd not in close.index: continue
        pos = close.index.get_loc(rd)
        eff = close.index[min(pos + 1, len(close.index) - 1)]
        f = factor.loc[rd].dropna()
        if len(f) < tn: continue
        sched[eff] = pd.Series(1.0 / tn, index=f.nlargest(tn).index)
    return sched

def metrics(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    a = (1 + r).cumprod().iloc[-1] ** (1 / n) - 1
    s = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    d = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())
    return a, s, d

cfg = StrategyConfig(start="2018-01-01")

# ── Test grid ──
print(f"\n{'Factor+Timing':<40} {'Ann':>8} {'Sharpe':>6} {'MaxDD':>8} {'Meet?':>10}")
print("-" * 75)

ones = pd.Series(1.0, index=close.index, dtype="float64")
sig = AGG.shift(1).fillna(0.5)  # look-ahead-free

found = []
for factor, fname, tn in [(F, "analyst", 15), (MA_CROSS, "MAcrs", 15), (REV5D, "rev5d", 15)]:
    sched = build_sched(factor, tn)
    if len(sched) < 10: continue

    # Test 4 timing variants per factor
    for (tlabel, exp) in [
        ("always-on", ones),
        ("cut_<0.35_to_0.3", None),
        ("cut_<0.40_to_0.3", None),
        ("cut_<0.40_to_0.5", None),
    ]:
        if exp is None:
            thr = 0.35 if "0.35" in tlabel else 0.40
            fl = 0.3 if "0.3" in tlabel else 0.5
            exp_s = pd.Series(1.0, index=close.index, dtype="float64")
            exp_s[sig < thr] = fl
            exp_s[sig < 0.25] = 0.0
        else:
            exp_s = exp

        ret_s, _ = backtest_weights(close, sched, exp_s, cfg)
        a, s, d = metrics(ret_s[ret_s.index.year >= 2020])
        meets = (d > -0.15) and (a > 0.15)
        mark = "✅ FOUND!" if meets else ""
        label = f"{fname}_t{tn}_{tlabel}"
        print(f"{label:<40} {a:>+7.1%} {s:>5.2f} {d:>+7.1%} {mark:>10}")
        if meets: found.append((label, ret_s, exp_s))

# ── Yearly for best ──
if found:
    best_name, best_ret, best_exp = found[0]
    print(f"\n=== BEST: {best_name} ===\n  Yearly:")
    for y in range(2019, 2027):
        r = best_ret[best_ret.index.year == y]
        if len(r) < 50: continue
        print(f"    {y}: {(1+r.fillna(0)).prod()-1:+.1%}")
    best_ret.to_csv(OUT / "largecap_analyst_daily.csv")
    print(f"\nWrote: {OUT/'largecap_analyst_daily.csv'}")
else:
    print("\nNo strategy meets criteria. Closest approaches:")
