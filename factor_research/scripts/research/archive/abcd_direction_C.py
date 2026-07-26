# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Direction C: Adaptive parameters (vol/trend regime) on top of B4 4+3 new features."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

cfg = StrategyConfig(start="2010-01-01")
print("[setup] loading baseline...", flush=True)
base = run_small_cap_strategy(cfg)
close, amount = base["close"], base["amount"]
scheduled = base["scheduled_weights"]
smallcap_timing = base["timing"].astype(float)

sys.path.insert(0, str(ROOT / "core" / "overlays"))
from hmm_macro_overlay import (
    _ConstrainedGaussianHMM as ConstrainedGaussianHMM,
)


def make_features(close, amount):
    ret = close.pct_change(fill_method=None)
    has_trade = amount > 0
    up = (ret > 0) & has_trade
    risk_appetite = up.sum(axis=1) / has_trade.sum(axis=1)
    mkt_ret = ret.mean(axis=1)
    volatility = mkt_ret.rolling(20).std()
    market_amount = amount.sum(axis=1)
    liquidity = market_amount / market_amount.rolling(20).mean()
    ma20 = close.rolling(20).mean()
    valid = ma20.notna() & close.notna()
    above_ma = (close > ma20) & valid
    ma_diffusion = above_ma.sum(axis=1) / valid.sum(axis=1)
    df = pd.DataFrame({
        "risk_appetite": risk_appetite,
        "volatility": volatility,
        "liquidity": liquidity,
        "ma_diffusion": ma_diffusion,
    }, index=close.index)
    return df.replace([np.inf, -np.inf], np.nan).dropna()


def build_stress_signal(features, lookback=250, retrain_days=60):
    dates = features.index
    feature_cols = list(features.columns)
    stress_prob = pd.Series(np.nan, index=dates, dtype="float64")
    refit_dates = list(dates[lookback :: retrain_days])
    model_cache = {}
    for refit_date in refit_dates:
        train_end = dates.get_loc(refit_date)
        train = features.iloc[train_end - lookback : train_end]
        if len(train) < lookback: continue
        next_pos = dates.get_loc(refit_dates[refit_dates.index(refit_date) + 1]) if refit_dates.index(refit_date) + 1 < len(refit_dates) else len(dates)
        block = features.iloc[train_end:next_pos]
        if block.empty: continue
        cache_key = train_end
        X_train = train[feature_cols].values.copy()
        mu = X_train.mean(axis=0); sigma = X_train.std(axis=0); sigma[sigma == 0] = 1.0
        X_train_norm = (X_train - mu) / sigma
        try:
            if cache_key not in model_cache:
                hmm = ConstrainedGaussianHMM(n_states=3, max_iter=80, tol=1e-4).fit(X_train_norm)
                ratios = [(j, (hmm.means[j] * sigma + mu)[0]) for j in range(3)]
                ratios.sort(key=lambda x: x[1])
                stress_idx = ratios[0][0]
                model_cache[cache_key] = (hmm, mu, sigma, stress_idx)
            else:
                hmm, mu, sigma, stress_idx = model_cache[cache_key]
            block_with_tail = pd.concat([train.tail(1), block])
            X_block = block_with_tail[feature_cols].values.copy()
            X_block_norm = (X_block - mu) / sigma
            probs = hmm.predict_proba(X_block_norm)
            for i, idx in enumerate(block.index):
                stress_prob.loc[idx] = probs[i + 1, stress_idx]
        except Exception:
            for idx in block.index: stress_prob.loc[idx] = 0.0
    return stress_prob


def annualReturn(series):
    return (1 + series.fillna(0)).prod() - 1


features = make_features(close, amount)
print("[setup] building stress signal...", flush=True)
stress_prob = build_stress_signal(features, lookback=250, retrain_days=60)
mkt_ret = close.pct_change(fill_method=None).mean(axis=1)


# === Direction C: Adaptive parameters ===
print("[C] starting", flush=True)

mkt_60d = mkt_ret.rolling(60).sum()
is_bull = (mkt_60d > 0.05).reindex(close.index).fillna(False)
is_bear = (mkt_60d < -0.05).reindex(close.index).fillna(False)
is_neutral = ~(is_bull | is_bear)

vol_20d = mkt_ret.rolling(20).std()
vol_q = vol_20d.rolling(252, min_periods=60).rank(pct=True).reindex(close.index)
is_calm = (vol_q < 0.5).fillna(False)
is_volatile = (vol_q > 0.7).fillna(False)

sp = stress_prob.reindex(close.index).fillna(0.0)
mkt = mkt_ret.reindex(close.index).fillna(0.0)


def mask_with(sp, mkt, threshold, tw):
    trend = mkt.rolling(tw).sum()
    return (sp > threshold) & (trend < 0)


mask_base = mask_with(sp, mkt, 0.05, 3)

# C1: vol-regime threshold
threshold_map = pd.Series(0.05, index=close.index)
threshold_map[is_calm] = 0.10
threshold_map[is_volatile] = 0.02
trend_3d = mkt.rolling(3).sum()
mask_c1 = (sp > threshold_map) & (trend_3d < 0)

# C2: trend-regime tw
mask_c2 = pd.Series(False, index=close.index)
for regime, tw in [(is_bull, 5), (is_neutral, 3), (is_bear, 2)]:
    m = mask_with(sp, mkt, 0.05, tw)
    mask_c2 = mask_c2 | (regime & m)

# C3: full adaptive (regime + vol + tw)
mask_c3 = pd.Series(False, index=close.index)
regimes = [
    (is_calm & is_bull, 0.10, 5),
    (is_calm & is_neutral, 0.08, 3),
    (is_calm & is_bear, 0.05, 2),
    (is_neutral & is_bull, 0.07, 5),
    (is_neutral & is_neutral, 0.05, 3),
    (is_neutral & is_bear, 0.04, 2),
    (is_volatile & is_bull, 0.04, 5),
    (is_volatile & is_neutral, 0.03, 3),
    (is_volatile & is_bear, 0.02, 2),
]
for regime, th, tw in regimes:
    m = mask_with(sp, mkt, th, tw)
    mask_c3 = mask_c3 | (regime & m)

rows = []
for name, mask in [("C_base_fixed", mask_base),
                     ("C1_vol_regime_threshold", mask_c1),
                     ("C2_trend_regime_tw", mask_c2),
                     ("C3_full_adaptive", mask_c3)]:
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    exp[mask.fillna(False)] = 0.0
    timing = smallcap_timing.reindex(close.index).fillna(0.0) * exp
    ret, _ = backtest_weights(close, scheduled, timing, cfg)
    nav = (1 + ret.fillna(0)).cumprod()
    rows.append({
        "name": name, "annual_2018": annualReturn(ret[ret.index.year >= 2018]),
        "annual_2010": annualReturn(ret[ret.index.year >= 2010]),
        "maxdd_2018": (nav / nav.cummax() - 1).min(),
        "sharpe_2018": ret[ret.index.year >= 2018].mean() / ret[ret.index.year >= 2018].std() * np.sqrt(252),
        "exposure_2018": float(exp[exp.index.year >= 2018].mean()),
    })
df = pd.DataFrame(rows)
df.to_csv(OUT / "abcd_direction_C.csv", index=False)
print(df.to_string(index=False), flush=True)
print("[C] done. wrote", OUT / "abcd_direction_C.csv", flush=True)
