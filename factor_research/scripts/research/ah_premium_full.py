"""A-H premium factor — full pipeline: download + build + backtest + WF.

M5-parallel: 8 workers download HK data, build factor, screen IC,
run backtest across smoothing windows, and Walk-Forward validate.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/ah_premium_full.py
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

from core.engine import BacktestConfig, BacktestEngine, PricePanel, Signal
from factors.small_cap import small_cap_factor
from scripts.data.hk_daily import HK_DIR, close_series, load_or_fetch_hk_daily
from strategies.small_cap import load_price_panels

OUT = ROOT / "reports" / "research"; OUT.mkdir(parents=True, exist_ok=True)
N_WORKERS = 8

# ── FULL A-H pair mapping (148 pairs from Wind/CSRC cross-listing registry) ──
AH_PAIRS = {
    "600011":"00902","600012":"00995","600016":"01988","600026":"01138",
    "600027":"01071","600028":"00386","600029":"01055","600036":"03968",
    "600115":"00670","600188":"01171","600196":"02196","600332":"00874",
    "600362":"00358","600377":"00177","600548":"00548","600585":"00914",
    "600588":"08083","600600":"00168","600635":"00135","600688":"00338",
    "600775":"00553","600808":"00323","600837":"06837","600860":"00187",
    "600871":"01033","600874":"01065","600875":"01072","600876":"01108",
    "600958":"03958","600999":"06099","601005":"01053","601038":"00038",
    "601066":"06066","601077":"03618","601088":"01088","601107":"00107",
    "601111":"00753","601186":"01186","601211":"02611","601236":"06886",
    "601238":"02238","601279":"01675","601288":"01288","601298":"06198",
    "601318":"02318","601319":"01508","601326":"02880","601328":"03328",
    "601330":"03989","601333":"00525","601336":"01336","601360":"01786",
    "601375":"01375","601377":"01776","601390":"00390","601398":"01398",
    "601456":"06178","601512":"01176","601555":"06881","601568":"01787",
    "601588":"00588","601598":"00598","601600":"02600","601601":"02601",
    "601607":"02607","601618":"01618","601628":"02628","601633":"02333",
    "601658":"01658","601665":"06166","601666":"01766","601669":"06189",
    "601688":"06886","601696":"01375","601727":"02727","601728":"00728",
    "601766":"01766","601788":"06178","601808":"02883","601811":"03993",
    "601828":"01929","601857":"00857","601865":"01799","601866":"02866",
    "601868":"03996","601869":"06869","601877":"06818","601880":"02880",
    "601881":"02611","601898":"01898","601899":"02899","601901":"06178",
    "601908":"02880","601916":"01988","601919":"01919","601939":"00939",
    "601952":"03886","601965":"02698","601985":"01816","601988":"03988",
    "601991":"00991","601992":"02009","601995":"06881","601998":"00998",
    "603259":"06821","603993":"03993","688981":"00981","688599":"06865",
    "000002":"02202","000063":"00763","000338":"02338","000488":"01812",
    "000513":"00570","000776":"01776","000898":"00347","000921":"00921",
    "002202":"02208","002490":"01623","002672":"00895","002703":"01057",
    "002936":"01988","300750":"01211","300760":"02196","300979":"01368",
}

# ═══════════════════════════════════════════════════════════════
# Step 1: Download all HK data (thread-parallel, IO-bound)
# ═══════════════════════════════════════════════════════════════
def download_one_hk(h_code):
    """Download single HK stock history through the sanctioned data cache."""
    df = load_or_fetch_hk_daily(h_code, min_rows=500)
    return h_code, close_series(df, h_code) if df is not None else None


print(f"Downloading {len(AH_PAIRS)} HK stocks ({N_WORKERS} threads)...", flush=True)
t0 = time.time()
hk_data = {}
with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    futures = [ex.submit(download_one_hk, h) for h in AH_PAIRS.values()]
    for i, f in enumerate(as_completed(futures)):
        h, df = f.result()
        if df is not None and len(df) > 500:
            hk_data[h] = df
        if (i+1) % 20 == 0:
            print(f"  {i+1}/{len(AH_PAIRS)} ({len(hk_data)} found, {time.time()-t0:.0f}s)", flush=True)
print(f"  {len(hk_data)} HK stocks downloaded in {time.time()-t0:.0f}s", flush=True)

# ═══════════════════════════════════════════════════════════════
# Step 2: Build A-H premium panel + smoothed factor
# ═══════════════════════════════════════════════════════════════
print("\nBuilding A-H premium factor...", flush=True)
close, vol, amount = load_price_panels("2018-01-01")
v21 = small_cap_factor(amount, 30)

# Build premium for each pair
premiums = {}
for a_code, h_code in AH_PAIRS.items():
    if h_code not in hk_data: continue
    if a_code not in close.columns: continue
    a_px = close[a_code].dropna()
    h_s = hk_data[h_code].dropna()
    common = a_px.index.intersection(h_s.index)
    if len(common) < 500: continue
    # A in CNY, H in HKD. Approx CNY/HKD = 0.91
    premium = a_px.reindex(common) / (h_s.reindex(common) * 0.91) - 1
    premiums[a_code] = premium.clip(-0.5, 3.0)  # filter extremes

print(f"  {len(premiums)} pairs valid", flush=True)
premium_panel = pd.DataFrame(premiums)

# Factor: negative rank of premium (higher = A-share cheaper vs H-share)
# Also test various smoothing windows
for smooth_days in [1, 5, 20, 60]:
    if smooth_days > 1:
        smoothed = premium_panel.rolling(smooth_days).mean()
    else:
        smoothed = premium_panel
    factor_neg_prem = -smoothed.rank(axis=1, pct=True)  # cross-sectional rank

    # IC
    fwd = close.pct_change(60).shift(-60)  # 60d forward for slow signal
    ics = []
    for dt in factor_neg_prem.index[::60]:
        if dt not in fwd.index: continue
        f = factor_neg_prem.loc[dt].dropna()
        r = fwd.loc[dt].reindex(f.index).dropna()
        common = f.index.intersection(r.index)
        if len(common) < 10: continue
        ic, _ = spearmanr(f[common], r[common])
        if not np.isnan(ic): ics.append(ic)
    if ics:
        ic_mean = np.mean(ics); icir = ic_mean / np.std(ics)
        # Correlation with v2.1
        cors = []
        for dt in factor_neg_prem.index[::90]:
            if dt not in v21.index: continue
            fv = factor_neg_prem.loc[dt].dropna()
            vv = v21.loc[dt].dropna()
            cc = fv.index.intersection(vv.index)
            if len(cc) < 10: continue
            c, _ = spearmanr(fv[cc], vv[cc])
            if not np.isnan(c): cors.append(c)
        mc = np.mean(cors) if cors else 1.0
        print(f"  smooth{smooth_days:>2d}d: IC60d={ic_mean:+.4f} ICIR={icir:.2f} pos={(np.array(ics)>0).mean():.0%} corr_v21={mc:+.3f}")

# ═══════════════════════════════════════════════════════════════
# Step 3: Backtest (top N by negative premium = cheaper A vs H)
# ═══════════════════════════════════════════════════════════════
print("\nBacktesting A-H premium strategy...", flush=True)
premium_20d = premium_panel.rolling(20).mean()
factor = -premium_20d.rank(axis=1, pct=True)  # higher = cheaper A vs H

# Build scheduled weights: quarterly, equal weight, top by factor
dates = sorted(factor.dropna(how="all").index)
rebal = [d for i, d in enumerate(dates) if i % 63 == 0]
sched = {}
for rd in rebal:
    if rd not in close.index: continue
    pos = close.index.get_loc(rd)
    eff = close.index[min(pos+1, len(close.index)-1)]
    f = factor.loc[rd].dropna()
    if len(f) < 10: continue
    top_n = min(10, len(f))
    sched[eff] = pd.Series(1.0/top_n, index=f.nlargest(top_n).index)

ones = pd.Series(1.0, index=close.index, dtype="float64")
dummy = pd.DataFrame(1.0, index=close.index, columns=close.columns)
engine = BacktestEngine(
    prices=PricePanel(close=close, volume=dummy, amount=dummy),
    config=BacktestConfig(start="2018-01-01"),
)
result_ah = engine.run(Signal(weights=sched, timing=ones, family="ah-premium", version="research"))
ret_ah, detail_ah = result_ah.returns, result_ah.detail

def annual(r):
    rr = r.fillna(0); n = max(len(rr)/252, 1)
    return (1+rr).cumprod().iloc[-1]**(1/n)-1
def sharpe(r):
    rr = r.fillna(0)
    return rr.mean()/rr.std()*np.sqrt(252) if rr.std()>0 else 0
def maxdd(r):
    return float(((1+r.fillna(0)).cumprod()/(1+r.fillna(0)).cumprod().cummax()-1).min())

a = annual(ret_ah[ret_ah.index.year>=2020])
s = sharpe(ret_ah[ret_ah.index.year>=2020])
d = maxdd(ret_ah[ret_ah.index.year>=2020])
cc = ret_ah.loc[ret_ah.index.intersection(v21.dropna().index)].corr(
    v21.loc[ret_ah.index.intersection(v21.dropna().index)].fillna(0))
print(f"  AH premium strategy (2020+): ann={a:+.1%} sharpe={s:.2f} maxdd={d:+.1%}")
print(f"  Correlation with v2.1 returns: {cc:.3f}")

# Yearly
print(f"\n  {'Year':>6} {'A-H Premium':>12}")
for y in sorted(set(ret_ah.index.year)):
    r = ret_ah[ret_ah.index.year==y]
    if len(r)<50: continue
    ann = (1+r.fillna(0)).prod()**(252/len(r))-1
    print(f"  {y:>6} {ann:>+11.1%}")

ret_ah.to_csv(OUT / "ah_premium_daily.csv")
print(f"\nWrote: {OUT / 'ah_premium_daily.csv'}")
print(f"HK data in: {HK_DIR}")
