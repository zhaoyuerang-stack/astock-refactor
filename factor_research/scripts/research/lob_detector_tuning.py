"""LOB-style detector: comprehensive parameter tuning.

Key hypotheses to test:
  1. Lower threshold → more triggers → maybe better coverage
  2. Drop rising-edge condition (just use threshold crossing)
  3. Test individual channels (not just MAX aggregate)
  4. Adaptive percentile-based thresholds
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

close, vol, amount = load_price_panels("2010-01-01")
factor = small_cap_factor(amount, 60)
timing, _, _ = small_cap_timing(close, amount, 16)
scheduled = build_rebalance_weights(factor, close, 25, 20)
cfg = StrategyConfig(start="2010-01-01")

# v2.0 baseline
ret_v20, _ = backtest_weights(close, scheduled, timing.astype(float), cfg)

# Build channels
ret = close.pct_change(fill_method=None)
has_trade = amount > 0
up_ratio = ((ret > 0) & has_trade).sum(axis=1) / has_trade.sum(axis=1)
mkt_ret = ret.mean(axis=1).fillna(0.0)
mkt_amount = amount.sum(axis=1)
ma20 = close.rolling(20).mean()
valid = ma20.notna() & close.notna()

ch1 = 1.0 - up_ratio
ch2 = mkt_ret.rolling(20).std()
ch3 = 1.0 / (mkt_amount / mkt_amount.rolling(20).mean()).clip(0.3, 5.0)
ch4 = 1.0 - ((close > ma20) & valid).sum(axis=1) / valid.sum(axis=1)

channels = {"breadth": ch1, "vol": ch2, "liq": ch3, "diffusion": ch4}

# Z-score (fixed 252d window)
def zscore(s):
    mu = s.rolling(252, min_periods=60).mean()
    sigma = s.rolling(252, min_periods=60).std().replace(0, 1.0)
    return ((s - mu) / sigma).clip(-5, 10)

ch_z = {k: zscore(v) for k, v in channels.items()}

# ALL channel Z-scores combined for MAX aggregation
score_max = pd.DataFrame(ch_z).max(axis=1)  # MAX of all 4


def make_exposure(trigger, cut_days=5, floor=0.0):
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    for pos in np.flatnonzero(trigger.reindex(close.index).fillna(False).values):
        end = min(len(exp), pos + cut_days + 1)
        exp.iloc[pos:end] = floor
    return exp


def make_trigger(score, threshold, suppress_days, use_rising_edge=True):
    """Build trigger with optional rising-edge condition."""
    if use_rising_edge:
        d1 = score.diff()
        d2 = d1.diff()
        raw = (score >= threshold) & (d1 > 0) & (d2 <= 0)
    else:
        raw = score >= threshold

    trigger = pd.Series(False, index=score.index)
    last_pos = -10**9
    for pos, flag in enumerate(raw.fillna(False).values):
        if flag and pos - last_pos >= suppress_days:
            trigger.iloc[pos] = True
            last_pos = pos

    return trigger.shift(1, fill_value=False)


def cagr(ret):
    r = ret.fillna(0); n = max(len(r) / 252, 1)
    return (1 + r).cumprod().iloc[-1] ** (1 / n) - 1


print("=== LOB Detector Parameter Tuning ===\n")
print(f"v2.0: annual={cagr(ret_v20[ret_v20.index.year >= 2018]):+.1%} "
      f"sharpe={ret_v20[ret_v20.index.year >= 2018].mean() / ret_v20[ret_v20.index.year >= 2018].std() * np.sqrt(252):.2f}\n")

results = []

# Mode 1: lower thresholds + keep rising-edge (original approach)
print("--- Mode 1: Fixed threshold + rising-edge ---")
for th in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
    for cut in [3, 5, 8, 12, 20]:
        for fl in [0.0, 0.2]:
            for suppress in [5, 10]:
                trig = make_trigger(score_max, th, suppress, True)
                exp = make_exposure(trig, cut, fl)
                t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
                ret_s, _ = backtest_weights(close, scheduled, t, cfg)
                r = ret_s[ret_s.index.year >= 2018]
                results.append({
                    "mode": "rising_edge", "th": th, "cut": cut, "fl": fl,
                    "suppress": suppress,
                    "annual": cagr(r), "sharpe": r.mean() / r.std() * np.sqrt(252),
                    "maxdd": float(((1 + r.fillna(0)).cumprod() / (1 + r.fillna(0)).cumprod().cummax() - 1).min()),
                    "n_triggers": int(trig.sum()),
                })

# Mode 2: simple threshold crossing (no rising-edge)
print("--- Mode 2: Simple threshold crossing ---")
for th in [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]:
    for cut in [3, 5, 10, 20]:
        for fl in [0.0, 0.2]:
            for suppress in [5, 10, 20]:
                trig = make_trigger(score_max, th, suppress, False)
                exp = make_exposure(trig, cut, fl)
                t = timing.astype(float).reindex(close.index).fillna(0.0) * exp * 1.0
                ret_s, _ = backtest_weights(close, scheduled, t, cfg)
                r = ret_s[ret_s.index.year >= 2018]
                results.append({
                    "mode": "simple_cross", "th": th, "cut": cut, "fl": fl,
                    "suppress": suppress,
                    "annual": cagr(r), "sharpe": r.mean() / r.std() * np.sqrt(252),
                    "maxdd": float(((1 + r.fillna(0)).cumprod() / (1 + r.fillna(0)).cumprod().cummax() - 1).min()),
                    "n_triggers": int(trig.sum()),
                })

# Mode 3: percentile-based adaptive threshold
print("--- Mode 3: Adaptive percentile threshold ---")
for pct in [80, 85, 90, 92, 95]:
    adapt_th = score_max.rolling(504, min_periods=252).quantile(pct / 100.0)
    for cut in [3, 5, 10, 20]:
        for fl in [0.0, 0.2]:
            for suppress in [5, 10]:
                trig = make_trigger(score_max, adapt_th, suppress, False)
                exp = make_exposure(trig, cut, fl)
                t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
                ret_s, _ = backtest_weights(close, scheduled, t, cfg)
                r = ret_s[ret_s.index.year >= 2018]
                results.append({
                    "mode": "adaptive", "th": pct, "cut": cut, "fl": fl,
                    "suppress": suppress,
                    "annual": cagr(r), "sharpe": r.mean() / r.std() * np.sqrt(252),
                    "maxdd": float(((1 + r.fillna(0)).cumprod() / (1 + r.fillna(0)).cumprod().cummax() - 1).min()),
                    "n_triggers": int(trig.sum()),
                })

# Mode 4: individual channels (test if one channel dominates)
print("--- Mode 4: Individual channels ---")
for ch_name, ch_score in ch_z.items():
    th_base = 2.0
    for th in [1.0, 1.5, 2.0, 2.5]:
        for cut in [5, 10]:
            for fl in [0.0, 0.2]:
                trig = make_trigger(ch_score, th, 10, False)
                exp = make_exposure(trig, cut, fl)
                t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
                ret_s, _ = backtest_weights(close, scheduled, t, cfg)
                r = ret_s[ret_s.index.year >= 2018]
                sh = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
                if sh > 1.3:  # only print decent ones
                    results.append({
                        "mode": f"channel_{ch_name}", "th": th, "cut": cut,
                        "fl": fl, "suppress": 10,
                        "annual": cagr(r), "sharpe": sh,
                        "maxdd": float(((1 + r.fillna(0)).cumprod() / (1 + r.fillna(0)).cumprod().cummax() - 1).min()),
                        "n_triggers": int(trig.sum()),
                    })

df = pd.DataFrame(results).sort_values("sharpe", ascending=False)
df.to_csv(OUT / "lob_tuning_results.csv", index=False)

print("\n=== Top 15 Results ===")
print(f"{'Mode':<25} {'th':>5} {'cut':>4} {'fl':>4} {'supp':>4} {'ann':>7} {'sharpe':>7} {'maxdd':>8} {'trig':>5}")
for _, r in df.head(20).iterrows():
    print(f"{r['mode']:<25} {r['th']:>4.1f} {int(r['cut']):>4d} {r['fl']:>4.1f} {int(r['suppress']):>4d} "
          f"{r['annual']:>+6.1%} {r['sharpe']:>6.2f} {r['maxdd']:>+7.1%} {int(r['n_triggers']):>5d}")

print(f"\nBest: {df.iloc[0]['mode']} annual={df.iloc[0]['annual']:+.1%} sharpe={df.iloc[0]['sharpe']:.2f}")
print(f"\nWrote: {OUT / 'lob_tuning_results.csv'}")
