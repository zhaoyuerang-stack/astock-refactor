"""HK V2: 独立 timing + multi-factor combo.

V1 grid 发现:
  · mom252 + notiming sh 0.49 (接近目标)
  · A 股 small_cap MA16 timing 不适合 HK (-0.20 vs +0.49)
  · low_vol60 mdd -32% 最低 (防御)
  · illiq20 稳但弱

V2 思路:
  · HK 独立 timing (HK 全市场 mean return MA)
  · mom252 + HK MA / HK Band
  · multi-factor combo (mom252 + illiq + low_vol 等权)
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.utils import mad_clip, safe_zscore


def load_hk():
    closes, volumes = {}, {}
    for fp in (ROOT / "data_lake/price/hk_daily").glob("*.parquet"):
        try:
            df = pd.read_parquet(fp)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
            elif df.index.name != "date":
                df.index = pd.to_datetime(df.index)
                df = df.sort_index()
            closes[fp.stem] = df["close"]
            volumes[fp.stem] = df["volume"]
        except Exception:
            continue
    close = pd.DataFrame(closes).sort_index()
    volume = pd.DataFrame(volumes).sort_index()
    return close, volume, volume * close


def hk_mkt_timing(close, ma_window):
    """HK 全市场等权 NAV MA timing (替代 A 股 small_cap MA)."""
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    mkt_idx = ret.mean(axis=1).fillna(0)
    mkt_nav = (1 + mkt_idx).cumprod()
    timing = (mkt_nav > mkt_nav.rolling(ma_window).mean()).shift(1, fill_value=False).astype(float)
    dist = mkt_nav / mkt_nav.rolling(ma_window).mean() - 1
    return timing, dist


def hk_band_timing(dist, slope=8, cap=1.5):
    dc = dist.clip(-0.5, 0.5)
    raw = (1.0 + dc * slope).clip(0.0, cap)
    above = (dc > 0).astype(float)
    return (raw * above).shift(1).fillna(0).astype(float)


def build_weights(factor, close, top_n, rebal):
    fd = factor.dropna(how="all").index.intersection(close.index)
    if len(fd) < 50:
        return {}
    w = {}
    for rd in list(fd[::rebal]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index): continue
        eff = close.index[pos + 1]
        fv = factor.loc[rd].dropna()
        act = close.loc[rd].dropna().index
        fv = fv.reindex(act).dropna()
        if len(fv) < top_n: continue
        w[eff] = pd.Series(1.0 / top_n, index=fv.nlargest(top_n).index)
    return w


def run(close, volume, amount, factor, top_n, rebal, timing, lev=1.0, exposure_cap=1.0):
    w = build_weights(factor, close, top_n, rebal)
    if not w: return None
    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(
        start=str(close.index[0].date()),
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=lev))
    return engine.run(Signal(weights=w, timing=timing, exposure_cap=exposure_cap)).returns.dropna()


def m(ret):
    r = ret.dropna()
    if len(r) < 50: return None
    a = float(r.mean() * 252)
    v = float(r.std() * np.sqrt(252))
    cum = (1 + r).cumprod()
    mdd = float((cum / cum.cummax() - 1).min())
    return {"a": a, "sh": a / (v + 1e-9), "mdd": mdd, "cal": a / abs(mdd) if mdd < 0 else 0}


print("Loading HK...")
close, volume, amount = load_hk()
print(f"  {close.shape}")

# Factors
ret = close.pct_change(fill_method=None)
f_mom252 = safe_zscore(mad_clip(close.shift(20) / close.shift(272) - 1))
f_illiq = safe_zscore(mad_clip(
    (close.pct_change(fill_method=None).abs() / (amount.replace(0, np.nan) + 1)).rolling(20).mean()
))
f_lowvol = safe_zscore(mad_clip(-ret.rolling(60).std()))
f_size = safe_zscore(mad_clip(-np.log(amount.rolling(60).mean() + 1)))

# HK independent timings (各 MA 窗口)
print("\n=== Test 1: HK 独立 timing on mom252 ===")
print(f"{'config':<40s} {'ann':>7s} {'sh':>5s} {'mdd':>7s} {'cal':>6s}")
print("-" * 70)

for ma_w in [8, 16, 32, 60]:
    bin_t, dist = hk_mkt_timing(close, ma_w)
    band_t = hk_band_timing(dist, slope=8, cap=1.5)

    # Binary
    r = run(close, volume, amount, f_mom252, top_n=15, rebal=20, timing=bin_t, lev=1.25)
    if r is not None:
        x = m(r)
        if x:
            mark = "  ⭐" if x["sh"] >= 0.5 else ""
            print(f"mom252 + HK_mkt_MA{ma_w}_Binary 1.25x      {x['a']:+7.1%} {x['sh']:+5.2f} "
                  f"{x['mdd']:+7.1%} {x['cal']:+6.2f}{mark}")
    # Band
    r = run(close, volume, amount, f_mom252, top_n=15, rebal=20, timing=band_t, lev=1.0, exposure_cap=1.5)
    if r is not None:
        x = m(r)
        if x:
            mark = "  ⭐" if x["sh"] >= 0.5 else ""
            print(f"mom252 + HK_mkt_MA{ma_w}_Band 1.0x         {x['a']:+7.1%} {x['sh']:+5.2f} "
                  f"{x['mdd']:+7.1%} {x['cal']:+6.2f}{mark}")

# Multi-factor combo
print("\n=== Test 2: Multi-factor 等权 combo ===")
combos = {
    "mom252+illiq": (f_mom252 + f_illiq) / 2,
    "mom252+lowvol": (f_mom252 + f_lowvol) / 2,
    "mom252+illiq+lowvol": (f_mom252 + f_illiq + f_lowvol) / 3,
    "all4_equal": (f_mom252 + f_illiq + f_lowvol + f_size) / 4,
}

bin_t16, dist16 = hk_mkt_timing(close, 16)
for cname, combo_f in combos.items():
    combo_z = safe_zscore(mad_clip(combo_f))
    # 测 3 种 timing
    for tlabel, t, lev, cap in [
        ("notiming", None, 1.25, 1.0),
        ("HK_MA16_binary", bin_t16, 1.25, 1.0),
        ("HK_MA16_band", hk_band_timing(dist16), 1.0, 1.5),
    ]:
        r = run(close, volume, amount, combo_z, top_n=15, rebal=20, timing=t, lev=lev, exposure_cap=cap)
        if r is not None:
            x = m(r)
            if x:
                mark = "  ⭐" if x["sh"] >= 0.5 else ""
                print(f"{cname:<22s} {tlabel:<18s}     {x['a']:+7.1%} {x['sh']:+5.2f} "
                      f"{x['mdd']:+7.1%} {x['cal']:+6.2f}{mark}")

# 找出最佳并测组合
print("\n=== Test 3: 最佳 HK 候选加入 A 股 portfolio ===")
# 用 mom252 + notiming (V1 grid 已知 sh 0.49)
r_best = run(close, volume, amount, f_mom252, top_n=15, rebal=20, timing=None, lev=1.25)
best_m = m(r_best)
print(f"Best HK candidate (mom252 notiming): ann={best_m['a']:+.1%}, sh={best_m['sh']:.2f}, "
      f"mdd={best_m['mdd']:+.1%}")

from portfolio.composer import compose
from portfolio.composer import metrics as pm
from portfolio.strategy_runners import run_active

a_ret = run_active(start="2018-01-01")
base_rp, _ = compose(a_ret, method="risk_parity")
mb = pm(base_rp)
combo = {**a_ret, "HK_mom252": r_best}
combo_rp, _ = compose(combo, method="risk_parity")
mc = pm(combo_rp)
print(f"  A only:          sh={mb['sharpe']:+.2f} cal={mb['calmar']:+.2f} mdd={mb['maxdd']:+.1%}")
print(f"  A + HK_mom252:   sh={mc['sharpe']:+.2f} cal={mc['calmar']:+.2f} mdd={mc['maxdd']:+.1%}")
print(f"  Δ:               sh={mc['sharpe']-mb['sharpe']:+.3f} cal={mc['calmar']-mb['calmar']:+.3f}")
