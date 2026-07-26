"""Download delisted stock data and rebuild price panel with correct format.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 scripts/data/fix_delisted_stocks.py

Output:
  - Individual parquet files in data_lake/price/daily/ with full columns
  - Rebuilt daily_all.parquet (LONG format: date×code×close×volume×amount)
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

PRICE_DIR = ROOT / "data_lake" / "price" / "daily"
META_DIR = ROOT / "data_lake" / "meta"
DAILY_ALL = PRICE_DIR / "daily_all.parquet"

proxy = 'http://127.0.0.1:7897'
handler = urllib.request.ProxyHandler({'http': proxy})
opener = urllib.request.build_opener(handler)

def to_tx(code):
    if code.startswith('6'): return 'sh' + code
    if code.startswith(('0','3')): return 'sz' + code
    return None

def fetch_one(code, start='2010-01-01', max_pages=20):
    sym = to_tx(code)
    if sym is None: return None
    seen, rows = set(), []
    end = '2026-12-31'
    for _ in range(max_pages):
        url = (f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
               f'?param={sym},day,{start},{end},640,hfq')
        try:
            resp = opener.open(url, timeout=15)
            data = json.loads(resp.read())
            node = data.get('data', {})
            if not isinstance(node, dict): break
            k = node.get(sym, {})
            arr = k.get('hfqday') or k.get('day') or []
            if not arr: break
            new = [r for r in arr if r[0] not in seen]
            if not new: break
            for r in new: seen.add(r[0])
            rows = new + rows
            earliest = arr[0][0]
            if earliest <= start: break
            end = earliest
        except Exception: break
    if not rows: return None
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=['date','open','close','high','low','volume'])
    df['date'] = pd.to_datetime(df['date'])
    for c in ['open','close','high','low','volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.drop_duplicates('date').sort_values('date').reset_index(drop=True)
    df = df[df['date'] >= pd.Timestamp(start)]
    df['amount'] = df['volume'] * df['close']  # approximate
    return df

# ── Step 1: Find missing ──
print("[1/3] Finding delisted stocks...", flush=True)
codes = pd.read_parquet(META_DIR / "codes.parquet")
all_codes = set(codes["code"].tolist())
existing = {f.stem for f in PRICE_DIR.glob("*.parquet")
            if f.stem.isdigit() and len(f.stem) == 6}
missing = sorted(all_codes - existing)
print(f"  Have: {len(existing)}  Missing: {len(missing)}")
if not missing:
    print("  No missing stocks."); sys.exit(0)

# ── Step 2: Download ──
print(f"\n[2/3] Downloading {len(missing)} stocks...", flush=True)
added = 0
for i, code in enumerate(missing):
    out_path = PRICE_DIR / f"{code}.parquet"
    if out_path.exists(): continue
    df = fetch_one(code)
    if df is not None and len(df) > 20:
        df.to_parquet(out_path, index=False)
        added += 1
    if (i + 1) % 50 == 0:
        print(f"  {i+1}/{len(missing)}  added={added}", flush=True)
    time.sleep(0.15)
print(f"  Done. Added {added} stocks")

# ── Step 3: Rebuild daily_all as LONG table ──
print("\n[3/3] Rebuilding daily_all.parquet (LONG format)...", flush=True)
from lake.load_lake import load_prices

prices = load_prices(start="2010-01-01")
print(f"  Loaded: close={prices['close'].shape}")

# Build LONG format: date | code | close | volume | amount
dfs = []
for field in ['close', 'volume', 'amount']:
    df = prices[field].stack().reset_index()
    df.columns = ['date', 'code', field]
    dfs.append(df)

long = dfs[0]
for d in dfs[1:]:
    long = long.merge(d, on=['date', 'code'], how='outer')

long.to_parquet(DAILY_ALL, index=False)
print(f"  Saved: {DAILY_ALL}")
print(f"  Rows: {len(long)}  Codes: {long['code'].nunique()}")
print("\nDone. Ready for re-run.")
