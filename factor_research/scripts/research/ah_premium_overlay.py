"""A-H premium as dynamic position scaler for v2.1.

Core idea: when A-shares are very expensive vs H-shares (high premium),
reduce v2.1 position. When cheap, increase.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/ah_premium_overlay.py
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

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
HK_DIR = ROOT / "data_lake" / "price" / "hk_daily"

# ── v2.1 baseline ──
print("Running v2.1 baseline...", flush=True)
close, vol, amount = load_price_panels("2018-01-01")
factor = small_cap_factor(amount, 30)
sched_v21 = build_rebalance_weights(factor, close, 30, 15)
timing, _, _ = small_cap_timing(close, amount, 16)
cfg = StrategyConfig(start="2018-01-01")
ret21, _ = backtest_weights(close, sched_v21, timing.astype(float), cfg)

# ── A-H pairs with verified HK data ──
pairs = {}
for f in HK_DIR.glob("*.parquet"):
    h_code = f.stem
    # Find matching A-code
    # Simple mapping: any A-code where h_code matches
    # Use a predefined list
    pass

AH_PAIRS = {
    "600028":"00386","601398":"01398","601288":"01288","601939":"00939",
    "601988":"03988","601328":"03328","601318":"02318","600036":"03968",
    "600585":"00914","601857":"00857","601088":"01088","601628":"02628",
    "601390":"00390","601111":"00753","600027":"01071","600011":"00902",
    "600029":"01055","600016":"01988","601998":"00998","601600":"02600",
    "601601":"02601","601618":"01618","601633":"02333","601607":"02607",
    "601808":"02883","600332":"00874","600196":"02196","600688":"00338",
    "601991":"00991","601898":"01898",
}

# ── Build mean A-H premium ──
print("Building A-H premium panel...", flush=True)
premiums = {}
for a_code, h_code in AH_PAIRS.items():
    cache_f = HK_DIR / f"{h_code}.parquet"
    if not cache_f.exists(): continue
    if a_code not in close.columns: continue
    hk = pd.read_parquet(cache_f)
    # Handle both formats: date as column or date as index
    if "date" in hk.columns:
        hk["date"] = pd.to_datetime(hk["date"])
        hk = hk.set_index("date")
    # Find the close column
    if hk.index.name != "date":
        hk.index = pd.to_datetime(hk.index)
    cols = [c for c in hk.columns if c in ["close", f"hk_{h_code}"]]
    if not cols: continue
    h_col = cols[0]
    h_s = hk[h_col].dropna()
    a_s = close[a_code].dropna()
    common = a_s.index.intersection(h_s.index)
    if len(common) < 500: continue
    prem = a_s.reindex(common) / (h_s.reindex(common) * 0.91) - 1
    premiums[a_code] = prem.clip(-0.5, 3.0)

mean_prem = pd.DataFrame(premiums).mean(axis=1).dropna()
print(f"  {len(premiums)} pairs, premium range: {mean_prem.min():+.1%} ~ {mean_prem.max():+.1%}", flush=True)

# ── Dynamic exposure grid ──
def cagr(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1

def sharpe(ret):
    r = ret.fillna(0)
    return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0

def maxdd(ret):
    return float(((1 + ret.fillna(0)).cumprod() / (1 + ret.fillna(0)).cumprod().cummax() - 1).min())

print(f"\n{'='*65}")
print("  A-H Premium Dynamic Exposure Grid")
print(f"{'='*65}")
print(f"  v2.1 baseline: ann={cagr(ret21[ret21.index.year>=2020]):+.1%}  "
      f"sharpe={sharpe(ret21[ret21.index.year>=2020]):.2f}  "
      f"maxdd={maxdd(ret21[ret21.index.year>=2020]):+.1%}")
print(f"  {'th':>5} {'floor':>6} {'ann':>8} {'sharpe':>7} {'maxdd':>8} {'Δsharpe':>9} {'avg_exp':>8}")
print(f"  {'-'*55}")

best_sharpe, best_th, best_fl = -999, 0, 0
best_ret = None

for th in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
    for floor in [0.3, 0.5, 0.7]:
        exp = pd.Series(1.0, index=close.index, dtype="float64")
        sp = mean_prem.reindex(close.index).ffill()
        above = sp > th
        # Scale: 1.0 at th → floor at th+0.20
        ratio = ((sp[above] - th) / 0.20).clip(0, 1.0)
        exp[above] = 1.0 - ratio[above] * (1.0 - floor)
        # Also floor when premium is extremely high (>0.5)
        exp[sp > 0.50] = floor

        t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
        ret, _ = backtest_weights(close, sched_v21, t, cfg)

        r = ret[ret.index.year >= 2020].fillna(0)
        a = cagr(r); s = sharpe(r); d = maxdd(r)
        ds = s - sharpe(ret21[ret21.index.year >= 2020])
        avg_e = exp[exp.index.year >= 2020].mean()
        print(f"  {th:>4.0%} {floor:>5.0%} {a:>+7.1%} {s:>6.2f} {d:>+7.1%} {ds:>+8.2f} {avg_e:>7.1%}")

        if s > best_sharpe:
            best_sharpe = s; best_th = th; best_fl = floor
            best_ret = ret

# ── Show best combo year-by-year ──
print(f"\n=== Best: th={best_th:.0%} floor={best_fl:.0%} ===")
print(f"  {'Year':>6} {'v2.1':>9} {'v2.1+AH':>9} {'Δ':>8}")
for year in range(2019, 2027):
    r21 = ret21[ret21.index.year == year]
    rb = best_ret[best_ret.index.year == year]
    if len(r21) < 50: continue
    a21 = cagr(r21); ab = cagr(rb)
    print(f"  {year:>6} {a21:>+8.1%} {ab:>+8.1%} {ab - a21:>+7.1%}")

# ── Prem exposure over time ──
sp = mean_prem.reindex(close.index).ffill()
exp_best = pd.Series(1.0, index=close.index, dtype="float64")
above_b = sp > best_th
ratio_b = ((sp[above_b] - best_th) / 0.20).clip(0, 1.0)
exp_best[above_b] = 1.0 - ratio_b[above_b] * (1.0 - best_fl)
exp_best[sp > 0.50] = best_fl

print(f"\n  Avg premium: {sp.mean():+.1%}  Current: {sp.iloc[-1]:+.1%}")
print(f"  Avg exposure: {exp_best.mean():.1%}  Current exposure: {exp_best.iloc[-1]:.2f}")
print(f"  Days below 1.0: {(exp_best < 1.0).sum()} / {len(exp_best)} ({(exp_best < 1.0).mean():.0%})")
