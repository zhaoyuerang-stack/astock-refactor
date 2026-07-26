"""Alpha101 IC screening — parallel-accelerated (multiprocessing).

Target: <3 minutes for 30+ factors on 4000×4900 data.

Parallelization:
  - Factor building: each factor computed in a separate process
  - IC screening: each factor screened in parallel (CPU-bound, independent)

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/alpha101_screening.py
"""
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
from strategies.small_cap import load_price_panels

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

t0 = time.time()

# ── Load shared data (once, in parent process) ──
print("Loading data...", flush=True)
close, vol, amount = load_price_panels("2010-01-01")
ret1d = close.pct_change(fill_method=None).fillna(0)
print(f"  Loaded in {time.time()-t0:.0f}s", flush=True)

# ── Shared operators (used in factor building) ──
def R(x): return x.rank(axis=1, pct=True)
def M(x, d): return x.rolling(d).mean()
def S(x, d): return x.rolling(d).std()
def T(x, d): return x.rolling(d).sum()
def O(x, d): return x.shift(d)
def Delta(x, d): return x - O(x, d)
def C(x, y, d): return x.rolling(d).corr(y)
def Sgn(x): return np.sign(x)
def Abs(x): return abs(x)
def Z(x): return x.sub(x.mean(1),0).div(x.std(1).replace(0,1),0)

# ── Factor builders (each returns (name, DataFrame)) ──
def build_factors_parallel(batch_names):
    """Build a batch of factors in a subprocess. Returns dict of name->DataFrame."""
    c = close; r = ret1d; v = vol
    results = {}
    for name in batch_names:
        try:
            if name == "a001": results[name] = R(S(r.where(r<0,c),20))-0.5
            elif name == "a002": results[name] = -C(R(Delta(np.log(v+1),2)),R(r),6)
            elif name == "a003": results[name] = -C(R(c),R(v),10)
            elif name == "a005": results[name] = R(c-M(c,10))*(-Abs(R(c-c)))
            elif name == "a006": results[name] = -C(c,v,10)
            elif name == "a008": results[name] = -R(Delta(T(c,5)*T(r,5),10))
            elif name == "a009": results[name] = Delta(c,1).where(Delta(c,1)>0, -Delta(c,1))
            elif name == "a012": results[name] = Sgn(Delta(v,1))*(-Delta(c,1))
            elif name == "a013": results[name] = -R(C(R(c),R(v),5))
            elif name == "a014": results[name] = -R(Delta(r,3))*C(c,v,10)
            elif name == "a015": results[name] = -T(R(C(R(c),R(v),3)),3)
            elif name == "a017": results[name] = -R(S(r,10))*Delta(c,1)
            elif name == "a018": results[name] = -R(S(Abs(r),20))*Sgn(r)
            elif name == "a019": results[name] = -Sgn(Delta(c,7))*(1+R(1+T(r,250)))
            elif name == "a020": results[name] = -R(c-O(c,1))*R(c-O(c,1))*R(c-O(c,1))
            elif name == "a021": results[name] = M(c,5)-M(c,20)
            elif name == "a022": results[name] = (C(c,O(c,1),5)-0.5)*R(Delta(c,1))
            elif name == "a023": results[name] = M(c,5).where(M(c,5)>=M(c,20),-1)
            elif name == "a024": results[name] = -Delta(c,1)
            elif name == "a025": results[name] = -S(r,20)*R(c)
            elif name == "a028": results[name] = Z(C(M(v,20),c,5)+(R(c)-c/c.rolling(20).max()))
            elif name == "a030": results[name] = R(r)+R(v/(M(v,20)+1))
            elif name == "a032": results[name] = Z(M(c,7)-c)+20*Z(C(c,O(c,5),230))
            elif name == "a033": results[name] = R(-(1-c/(O(c,1)+1e-8)))
            elif name == "a034": results[name] = R(1-R(S(r,2)/(S(r,5)+1e-6)))+R(1-R(Delta(c,1)))
            elif name == "a037": results[name] = R(C(O(-r*c,1),c,200))+R(-r*c)
            elif name == "a038": results[name] = -R(c-O(c,9))*R(c/(O(c,1)+1e-8))
            elif name == "a040": results[name] = -R(S(c,10))*C(c,v,10)
            elif name == "a044": results[name] = -C(c,R(v),5)
            elif name == "a049": results[name] = -Delta(c,1)
            elif name == "a050": results[name] = -R(C(R(v),R(c),5)).rolling(5).max()
            elif name == "a055": results[name] = -C(R((c-c.rolling(12).min())/(c.rolling(12).max()-c.rolling(12).min()+1e-6)),R(v),6)
        except Exception:
            pass  # skip problematic factors
    return results


def screen_factor_parallel(args):
    """Screen one factor's IC in a subprocess. Returns list of result dicts."""
    name, factor_values, close, fwd20, v21_vals = args
    dates = close.index[::30]
    ics, cors = [], []
    for dt in dates:
        if dt not in fwd20.index: continue
        try:
            vals = factor_values.loc[dt].dropna()
            if len(vals) < 30: continue
            rv = fwd20.loc[dt].dropna()
            cmn = vals.index.intersection(rv.index)
            if len(cmn) < 30: continue
            ic, _ = spearmanr(vals[cmn].values, rv[cmn].values)
            if not np.isnan(ic): ics.append(ic)
            # corr with v2.1
            if dt in v21_vals.index:
                vv = v21_vals.loc[dt].dropna()
                c2 = vals.index.intersection(vv.index)
                if len(c2) >= 30:
                    co, _ = spearmanr(vals[c2].values, vv[c2].values)
                    if not np.isnan(co): cors.append(co)
        except Exception:
            continue
    if len(ics) < 20: return []
    m = np.mean(ics)
    return [{"name": name, "ic_mean": m,
             "icir": m / np.std(ics) if np.std(ics) > 0 else 0,
             "pos_ratio": (np.array(ics) > 0).mean(),
             "corr_v21": np.mean(cors) if cors else 1.0}]


def main():
    t0 = time.time()

    # ── 1. Build factors in parallel ──
    all_names = ["a001","a002","a003","a005","a006","a008","a009","a012",
                 "a013","a014","a015","a017","a018","a019","a020","a021",
                 "a022","a023","a024","a025","a028","a030","a032","a033",
                 "a034","a037","a038","a040","a044","a049","a050","a055"]

    n_workers = min(8, os.cpu_count() or 4)
    batches = [all_names[i::n_workers] for i in range(n_workers)]

    print(f"\nBuilding {len(all_names)} factors with {n_workers} workers...", flush=True)
    built = {}
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(build_factors_parallel, batch) for batch in batches if batch]
        for f in as_completed(futures):
            built.update(f.result())
    print(f"  Built {len(built)} factors in {time.time()-t0:.0f}s", flush=True)

    if not built:
        print("  No factors built. Aborting."); return

    # ── 2. IC screening in parallel ──
    fwd20 = close.pct_change(20).shift(-20)
    v21 = R(S(amount.rolling(30).mean(), 1))  # v2.1 proxy: zscore of 30d amount

    screen_args = [(name, factor, close, fwd20, v21) for name, factor in built.items()]
    print(f"Screening IC for {len(screen_args)} factors with {n_workers} workers...", flush=True)

    all_results = []
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(screen_factor_parallel, args) for args in screen_args]
        for f in as_completed(futures):
            all_results.extend(f.result())
    print(f"  Screening done in {time.time()-t0:.0f}s", flush=True)

    # ── 3. Report ──
    df = pd.DataFrame(all_results).sort_values("icir", ascending=False)
    df.to_csv(OUT / "alpha101_screening.csv", index=False)

    print(f"\n{'='*60}")
    print("  Alpha101 IC Screening Results (Top 20)")
    print(f"{'='*60}")
    print(f"  {'Name':<8} {'IC20d':>8} {'ICIR':>7} {'Pos%':>7} {'Corr_v21':>9}")
    orth = []
    for _, r in df.head(25).iterrows():
        m = " ← ORTH" if abs(r["corr_v21"]) < 0.3 else ""
        if abs(r["corr_v21"]) < 0.3 and r["icir"] > 0.1: orth.append(r)
        print(f"  {r['name']:<8} {r['ic_mean']:>+8.4f} {r['icir']:>6.2f} {r['pos_ratio']:>6.0%} {r['corr_v21']:>+8.3f}{m}")

    print(f"\n  Orthogonal to v2.1 (|corr|<0.3, ICIR>0.1): {len(orth)}")
    for r in orth:
        print(f"    {r['name']}  ICIR={r['icir']:.2f}  IC={r['ic_mean']:+.4f}")
    print(f"\n  Total time: {time.time()-t0:.0f}s")
    print(f"  Wrote: {OUT/'alpha101_screening.csv'}")


if __name__ == "__main__":
    main()
