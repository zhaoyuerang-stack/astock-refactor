"""Analyst conviction factor — M5 parallel download + full pipeline.

Downloads historical analyst ratings from East Money for top 200 A-shares.
Builds rolling conviction factor, IC tests, backtests, and WF validates.

Factor: rolling 90-day buy ratio + EPS revision momentum.
Data source: akshare.stock_research_report_em (East Money individual stock reports)

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/analyst_factor.py
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT); sys.path.insert(0, str(ROOT))

from scipy.stats import spearmanr

from factors.small_cap import small_cap_factor, small_cap_timing
from governance.holdout import assert_search_clean, boundary
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

OUT = ROOT / "reports" / "research"; OUT.mkdir(parents=True, exist_ok=True)
ANALYST_DIR = OUT / "analyst_cache"; ANALYST_DIR.mkdir(parents=True, exist_ok=True)
N_WORKERS = 12  # M5 threads
RESEARCH_BOUNDARY = boundary()


# ══════════════════════════════════════════════════
# Step 1: Parallel download analyst reports for top 200 stocks
# ══════════════════════════════════════════════════
def download_one(code, name=""):
    """Download full analyst report history for one stock."""
    cache_f = ANALYST_DIR / f"{code}.parquet"
    if cache_f.exists():
        df = pd.read_parquet(cache_f)
        date_col = '日期' if '日期' in df.columns else 'date'
        df = df[pd.to_datetime(df[date_col]) < RESEARCH_BOUNDARY].copy()
        if len(df) > 5:
            return code, df

    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol=code)
        if df is not None and len(df) > 5:
            date_col = '日期' if '日期' in df.columns else 'date'
            df = df[pd.to_datetime(df[date_col]) < RESEARCH_BOUNDARY].copy()
        if df is not None and len(df) > 5:
            df.to_parquet(cache_f, index=False)
            return code, df
    except Exception:
        pass
    return code, None


def load_top200_codes():
    """Get top 200 stocks by pre-holdout amount (market cap proxy)."""
    _, _, amount = load_price_panels("2018-01-01")
    amount = amount[amount.index < RESEARCH_BOUNDARY]
    assert_search_clean(amount.index, label="analyst factor universe selection")
    avg_amt = amount.iloc[-60:].mean().nlargest(200)
    return [(code, "") for code in avg_amt.index]


print("Downloading analyst reports for top 200 A-shares...", flush=True)
t0 = time.time()
top200 = load_top200_codes()

ratings_data = {}
with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    futures = [ex.submit(download_one, code, name) for code, name in top200]
    for i, f in enumerate(as_completed(futures)):
        code, df = f.result()
        if df is not None and len(df) > 5:
            ratings_data[code] = df
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/200 ({len(ratings_data)} found, {time.time()-t0:.0f}s)", flush=True)

print(f"  Downloaded {len(ratings_data)} stocks in {time.time()-t0:.0f}s", flush=True)
if len(ratings_data) < 50:
    print("  Too few stocks with data. Aborting."); sys.exit(0)


# ══════════════════════════════════════════════════
# Step 2: Build rolling analyst factor
# ══════════════════════════════════════════════════
print("\nBuilding analyst factor panel...", flush=True)

# Per-stock: map each report to a date+rating, build daily time series
close, _, amount = load_price_panels("2018-01-01")
close = close[close.index < RESEARCH_BOUNDARY]
amount = amount[amount.index < RESEARCH_BOUNDARY]
assert_search_clean(close.index, label="analyst factor research panel")
trade_dates = close.index

# Map ratings to numeric scores
RATING_MAP = {"买入": 2.0, "增持": 1.0, "中性": 0.0, "减持": -1.0, "卖出": -2.0, "": np.nan}

# Build daily factor per stock
daily_factors = {}
for code, df in ratings_data.items():
    if code not in close.columns: continue
    df = df.copy()
    # 列名兼容: 缓存parquet可能用'日期'或'date'
    date_col = '日期' if '日期' in df.columns else 'date'
    df['date'] = pd.to_datetime(df[date_col])
    df = df[df['date'] < RESEARCH_BOUNDARY]
    df['rating_score'] = df['东财评级'].map(RATING_MAP)
    df = df.dropna(subset=['rating_score', 'date'])
    df = df.sort_values('date')

    if len(df) < 20: continue

    # Count reports per month, build rolling buy ratio
    df['y_m'] = df['date'].dt.to_period('M')
    monthly = df.groupby('y_m').agg(
        n_reports=('rating_score', 'count'),
        avg_rating=('rating_score', 'mean'),
        buy_pct=('rating_score', lambda x: (x >= 1.0).mean()),
    )
    monthly.index = monthly.index.to_timestamp()
    monthly = monthly.reindex(trade_dates[trade_dates >= monthly.index[0]]).fillna(0)

    # Rolling 90-day smoothed
    buy_pct_90d = monthly['buy_pct'].rolling(90, min_periods=30).mean()
    avg_rating_90d = monthly['avg_rating'].rolling(90, min_periods=30).mean()
    n_reports_90d = monthly['n_reports'].rolling(90, min_periods=30).sum()

    factor = buy_pct_90d * n_reports_90d.clip(0, 30) / 3.0  # scale by coverage
    daily_factors[code] = factor

# Build panel
factor_panel = pd.DataFrame(daily_factors)
print(f"  {len(factor_panel.columns)} stocks with sufficient data", flush=True)

# ══════════════════════════════════════════════════
# Step 3: IC analysis
# ══════════════════════════════════════════════════
print("\nIC analysis...", flush=True)
factor_rank = factor_panel.rank(axis=1, pct=True)  # cross-sectional

fwd = {h: close.pct_change(h).shift(-h) for h in [20, 60]}

for horizon, label in [(20, 'IC20'), (60, 'IC60')]:
    fwd_ret = fwd[horizon]
    ics = []
    for dt in factor_rank.index[::30]:
        if dt not in fwd_ret.index: continue
        f = factor_rank.loc[dt].dropna()
        r = fwd_ret.loc[dt].reindex(f.index).dropna()
        common = f.index.intersection(r.index)
        if len(common) < 20: continue
        ic, _ = spearmanr(f[common], r[common])
        if not np.isnan(ic): ics.append(ic)
    if ics:
        m = np.mean(ics); icir = m / np.std(ics)
        print(f"  {label}: {m:+.4f}  ICIR={icir:.2f}  pos={(np.array(ics)>0).mean():.0%}", flush=True)

# Correlation with v2.1
v21 = small_cap_factor(amount, 30)
corrs = []
for dt in factor_rank.index[::60]:
    if dt not in v21.index: continue
    f = factor_rank.loc[dt].dropna()
    v = v21.loc[dt].dropna()
    cc = f.index.intersection(v.index)
    if len(cc) < 20: continue
    c, _ = spearmanr(f[cc], v[cc])
    if not np.isnan(c): corrs.append(c)
print(f"  Corr with v2.1: {np.mean(corrs):+.3f}  "
      f"({'✅ ORTHOGONAL (<0.3)' if abs(np.mean(corrs))<0.3 else '⚠️ correlated'})", flush=True)

# ══════════════════════════════════════════════════
# Step 4: Quick backtest
# ══════════════════════════════════════════════════
print("\nBacktest...", flush=True)
dates = sorted(factor_rank.dropna(how='all').index)
rebal = [d for i, d in enumerate(dates) if i % 63 == 0]  # quarterly
sched = {}
for rd in rebal:
    if rd not in close.index: continue
    pos = close.index.get_loc(rd)
    eff = close.index[min(pos + 1, len(close.index) - 1)]
    f = factor_rank.loc[rd].dropna()
    if len(f) < 15: continue
    tn = min(15, len(f))
    sched[eff] = pd.Series(1.0 / tn, index=f.nlargest(tn).index)

cfg = StrategyConfig(start="2018-01-01")
ones = pd.Series(1.0, index=close.index, dtype="float64")
ret_analyst, _ = backtest_weights(close, sched, ones, cfg)

def cagr(r):
    rr = r.fillna(0); n = max(len(rr) / 252, 1)
    return (1 + rr).cumprod().iloc[-1] ** (1 / n) - 1

def shrp(r):
    rr = r.fillna(0)
    return rr.mean() / rr.std() * np.sqrt(252) if rr.std() > 0 else 0

# v2.1 reference
f21 = small_cap_factor(amount, 30)
s21 = build_rebalance_weights(f21, close, 30, 15)
t21, _, _ = small_cap_timing(close, amount, 16)
ret21, _ = backtest_weights(close, s21, t21.astype(float), cfg)

print(f"\n  {'':<25} {'Ann':>8} {'Sharpe':>7}")
print(f"  {'Analyst factor':<25} {cagr(ret_analyst[ret_analyst.index.year>=2020]):>+7.1%}  {shrp(ret_analyst[ret_analyst.index.year>=2020]):>5.2f}")
print(f"  {'v2.1 small-cap':<25} {cagr(ret21[ret21.index.year>=2020]):>+7.1%}  {shrp(ret21[ret21.index.year>=2020]):>5.2f}")

# Return correlation
cc = ret_analyst.loc[ret_analyst.index.intersection(ret21.index)].corr(
    ret21.loc[ret_analyst.index.intersection(ret21.index)])
print(f"  Return correlation: {cc:.3f}")

# Yearly
print(f"\n  {'Year':>6} {'Analyst':>9} {'v2.1':>9}")
for y in range(2020, 2027):
    ra = ret_analyst[ret_analyst.index.year == y]
    rb = ret21[ret21.index.year == y]
    if len(ra) < 50: continue
    print(f"  {y:>6} {cagr(ra):>+8.1%} {cagr(rb):>+8.1%}")

# Save
ret_analyst.to_csv(OUT / "analyst_factor_daily.csv")
print(f"\nWrote: {OUT / 'analyst_factor_daily.csv'}")
print(f"Analyst cache: {ANALYST_DIR}")
