# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""A-D exploration with CORRECT metrics (annualized returns, 2018+)."""
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
baseline_ret = base["returns"]
scheduled = base["scheduled_weights"]
smallcap_timing = base["timing"].astype(float)
mkt_ret = close.pct_change(fill_method=None).mean(axis=1)

# Pre-compute shared features
ret_panel = close.pct_change(fill_method=None)
has_trade = amount > 0
risk_appetite = ((ret_panel > 0) & has_trade).sum(axis=1) / has_trade.sum(axis=1)
volatility = mkt_ret.rolling(20).std()
market_amount = amount.sum(axis=1)
liquidity = market_amount / market_amount.rolling(20).mean()
ma20 = close.rolling(20).mean()
valid = ma20.notna() & close.notna()
above_ma = (close > ma20) & valid
ma_diffusion = above_ma.sum(axis=1) / valid.sum(axis=1)
ret_dispersion = ret_panel.std(axis=1)
extreme_loss = ((ret_panel < -0.03) & has_trade).sum(axis=1) / has_trade.sum(axis=1)
amount_5d = market_amount.rolling(5).mean()
turnover_surge = market_amount / amount_5d


def annualReturn(series):
    r = series.fillna(0)
    n_years = max(1, len(r) / 252)
    return (1 + r).prod() ** (1.0 / n_years) - 1


def compute_metrics(ret):
    """Compute correct metrics."""
    ret_2018 = ret[ret.index.year >= 2018]
    nav = (1 + ret.fillna(0)).cumprod()
    return {
        "annual_2018": annualReturn(ret_2018),
        "maxdd_2018": (nav / nav.cummax() - 1).min(),
        "sharpe_2018": ret_2018.mean() / ret_2018.std() * np.sqrt(252),
        "exposure_2018": 1.0,  # placeholder
    }


# === Reference: pure HMM tw=3 (best from previous work) ===
# This is just the trend filter without HMM, to set baseline
exp_ref = pd.Series(1.0, index=close.index, dtype="float64")
trend_3d = mkt_ret.rolling(3).sum()
exp_ref[trend_3d < 0] = 0.0
timing_ref = smallcap_timing * exp_ref
ret_ref, _ = backtest_weights(close, scheduled, timing_ref, cfg)
ref_metrics = compute_metrics(ret_ref)
ref_metrics["exposure_2018"] = float(exp_ref[exp_ref.index.year >= 2018].mean())
ref_metrics["name"] = "REF_trend_only_tw3"
ref_metrics["direction"] = "REF"

# === Direction A: Multi-strategy overlay ===
def hmm_exposure(sp, mkt, threshold=0.05, tw=3, floor=0.0):
    trend = mkt.reindex(close.index).fillna(0.0).rolling(tw).sum()
    mask = (sp.reindex(close.index).fillna(0.0) > threshold) & (trend < 0)
    exp = pd.Series(1.0, index=close.index, dtype="float64")
    exp[mask] = floor
    return exp

# HMM tw=3 baseline
sys.path.insert(0, str(ROOT / "core" / "overlays"))
from hmm_macro_overlay import _ConstrainedGaussianHMM


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
                hmm = _ConstrainedGaussianHMM(n_states=3, max_iter=80, tol=1e-4).fit(X_train_norm)
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

# Build base 4-feature HMM stress
features_base = pd.DataFrame({
    "risk_appetite": risk_appetite, "volatility": volatility,
    "liquidity": liquidity, "ma_diffusion": ma_diffusion,
}, index=close.index).replace([np.inf, -np.inf], np.nan).dropna()
print("[A] building HMM base signal...", flush=True)
sp_base = build_stress_signal(features_base)

exp_hmm = hmm_exposure(sp_base, mkt_ret)
timing_hmm = smallcap_timing * exp_hmm
ret_hmm, _ = backtest_weights(close, scheduled, timing_hmm, cfg)
hmm_metrics = compute_metrics(ret_hmm)
hmm_metrics["exposure_2018"] = float(exp_hmm[exp_hmm.index.year >= 2018].mean())
hmm_metrics["name"] = "A0_HMM_tw3"
hmm_metrics["direction"] = "A"

# A1-A4: combinations with mom/vol
small_ret = mkt_ret
small_20d = small_ret.rolling(20).mean()
vol_20d = small_ret.rolling(20).std()

# mom down
mom_down = (small_20d.reindex(close.index).fillna(0.0) < 0)
high_vol = (vol_20d.reindex(close.index).fillna(0.0) > 0.025)

# A1: HMM AND mom_down
exp_a1 = exp_hmm.copy()
exp_a1[~mom_down] = 1.0
ret_a1, _ = backtest_weights(close, scheduled, smallcap_timing * exp_a1, cfg)
m_a1 = compute_metrics(ret_a1)
m_a1["exposure_2018"] = float(exp_a1[exp_a1.index.year >= 2018].mean())
m_a1["name"] = "A1_HMM_AND_mom_down"
m_a1["direction"] = "A"

# A2: HMM AND high_vol
exp_a2 = exp_hmm.copy()
exp_a2[~high_vol] = 1.0
ret_a2, _ = backtest_weights(close, scheduled, smallcap_timing * exp_a2, cfg)
m_a2 = compute_metrics(ret_a2)
m_a2["exposure_2018"] = float(exp_a2[exp_a2.index.year >= 2018].mean())
m_a2["name"] = "A2_HMM_AND_high_vol"
m_a2["direction"] = "A"

# A3: HMM AND (mom OR vol)
combined = (mom_down | high_vol)
exp_a3 = exp_hmm.copy()
exp_a3[~combined] = 1.0
ret_a3, _ = backtest_weights(close, scheduled, smallcap_timing * exp_a3, cfg)
m_a3 = compute_metrics(ret_a3)
m_a3["exposure_2018"] = float(exp_a3[exp_a3.index.year >= 2018].mean())
m_a3["name"] = "A3_HMM_AND_mom_or_vol"
m_a3["direction"] = "A"

# A4: HMM AND (mom AND vol both confirm)
both = (mom_down & high_vol)
exp_a4 = exp_hmm.copy()
exp_a4[~both] = 1.0
ret_a4, _ = backtest_weights(close, scheduled, smallcap_timing * exp_a4, cfg)
m_a4 = compute_metrics(ret_a4)
m_a4["exposure_2018"] = float(exp_a4[exp_a4.index.year >= 2018].mean())
m_a4["name"] = "A4_HMM_AND_both"
m_a4["direction"] = "A"

# === Direction B: Enhanced HMM features ===
print("[B] running enhanced features...", flush=True)
b_results = []
for name, extra in [
    ("B_base_4feat", {}),
    ("B1_4plus_dispersion", {"ret_dispersion": ret_dispersion}),
    ("B2_4plus_extreme_loss", {"extreme_loss": extreme_loss}),
    ("B3_4plus_turnover_surge", {"turnover_surge": turnover_surge}),
    ("B4_4plus_3new", {"ret_dispersion": ret_dispersion, "extreme_loss": extreme_loss, "turnover_surge": turnover_surge}),
]:
    cols = {"risk_appetite": risk_appetite, "volatility": volatility,
            "liquidity": liquidity, "ma_diffusion": ma_diffusion}
    cols.update(extra)
    features_b = pd.DataFrame(cols, index=close.index).replace([np.inf, -np.inf], np.nan).dropna()
    sp_b = build_stress_signal(features_b)
    exp_b = hmm_exposure(sp_b, mkt_ret)
    ret_b, _ = backtest_weights(close, scheduled, smallcap_timing * exp_b, cfg)
    m_b = compute_metrics(ret_b)
    m_b["exposure_2018"] = float(exp_b[exp_b.index.year >= 2018].mean())
    m_b["name"] = name
    m_b["direction"] = "B"
    b_results.append(m_b)

# === Direction C: Adaptive parameters ===
print("[C] running adaptive params...", flush=True)
mkt_60d = mkt_ret.rolling(60).sum()
is_bull = (mkt_60d > 0.05).reindex(close.index).fillna(False)
is_bear = (mkt_60d < -0.05).reindex(close.index).fillna(False)
is_neutral = ~(is_bull | is_bear)
vol_q = volatility.rolling(252, min_periods=60).rank(pct=True).reindex(close.index)
is_calm = (vol_q < 0.5).fillna(False)
is_volatile = (vol_q > 0.7).fillna(False)

sp_aligned = sp_base.reindex(close.index).fillna(0.0)
mkt_aligned = mkt_ret.reindex(close.index).fillna(0.0)
trend_3d = mkt_aligned.rolling(3).sum()

# C_base: fixed
mask_c0 = (sp_aligned > 0.05) & (trend_3d < 0)
exp_c0 = pd.Series(1.0, index=close.index, dtype="float64")
exp_c0[mask_c0.fillna(False)] = 0.0
ret_c0, _ = backtest_weights(close, scheduled, smallcap_timing * exp_c0, cfg)
m_c0 = compute_metrics(ret_c0)
m_c0["exposure_2018"] = float(exp_c0[exp_c0.index.year >= 2018].mean())
m_c0["name"] = "C_base_fixed"
m_c0["direction"] = "C"

# C1: vol-regime threshold
threshold_map = pd.Series(0.05, index=close.index)
threshold_map[is_calm] = 0.10
threshold_map[is_volatile] = 0.02
mask_c1 = (sp_aligned > threshold_map) & (trend_3d < 0)
exp_c1 = pd.Series(1.0, index=close.index, dtype="float64")
exp_c1[mask_c1.fillna(False)] = 0.0
ret_c1, _ = backtest_weights(close, scheduled, smallcap_timing * exp_c1, cfg)
m_c1 = compute_metrics(ret_c1)
m_c1["exposure_2018"] = float(exp_c1[exp_c1.index.year >= 2018].mean())
m_c1["name"] = "C1_vol_regime_threshold"
m_c1["direction"] = "C"

# C2: trend-regime tw
mask_c2 = pd.Series(False, index=close.index)
for regime, tw in [(is_bull, 5), (is_neutral, 3), (is_bear, 2)]:
    t = mkt_aligned.rolling(tw).sum()
    m = (sp_aligned > 0.05) & (t < 0)
    mask_c2 = mask_c2 | (regime & m)
exp_c2 = pd.Series(1.0, index=close.index, dtype="float64")
exp_c2[mask_c2.fillna(False)] = 0.0
ret_c2, _ = backtest_weights(close, scheduled, smallcap_timing * exp_c2, cfg)
m_c2 = compute_metrics(ret_c2)
m_c2["exposure_2018"] = float(exp_c2[exp_c2.index.year >= 2018].mean())
m_c2["name"] = "C2_trend_regime_tw"
m_c2["direction"] = "C"

# C3: full adaptive
mask_c3 = pd.Series(False, index=close.index)
regimes_c3 = [
    (is_calm & is_bull, 0.10, 5), (is_calm & is_neutral, 0.08, 3), (is_calm & is_bear, 0.05, 2),
    (is_neutral & is_bull, 0.07, 5), (is_neutral & is_neutral, 0.05, 3), (is_neutral & is_bear, 0.04, 2),
    (is_volatile & is_bull, 0.04, 5), (is_volatile & is_neutral, 0.03, 3), (is_volatile & is_bear, 0.02, 2),
]
for regime, th, tw in regimes_c3:
    t = mkt_aligned.rolling(tw).sum()
    m = (sp_aligned > th) & (t < 0)
    mask_c3 = mask_c3 | (regime & m)
exp_c3 = pd.Series(1.0, index=close.index, dtype="float64")
exp_c3[mask_c3.fillna(False)] = 0.0
ret_c3, _ = backtest_weights(close, scheduled, smallcap_timing * exp_c3, cfg)
m_c3 = compute_metrics(ret_c3)
m_c3["exposure_2018"] = float(exp_c3[exp_c3.index.year >= 2018].mean())
m_c3["name"] = "C3_full_adaptive"
m_c3["direction"] = "C"

# === Direction D: Continuous position scaling ===
print("[D] running continuous scaling...", flush=True)
in_down = (trend_3d < 0)

# D_base: binary
exp_d0 = pd.Series(1.0, index=close.index, dtype="float64")
mask = (sp_aligned > 0.05) & in_down
exp_d0[mask] = 0.0
ret_d0, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d0, cfg)
m_d0 = compute_metrics(ret_d0)
m_d0["exposure_2018"] = float(exp_d0[exp_d0.index.year >= 2018].mean())
m_d0["name"] = "D_base_binary"
m_d0["direction"] = "D"

# D1: linear
exp_d1 = pd.Series(1.0, index=close.index, dtype="float64")
exp_d1[in_down] = (1.0 - sp_aligned[in_down]).clip(0.0, 1.0)
ret_d1, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d1, cfg)
m_d1 = compute_metrics(ret_d1)
m_d1["exposure_2018"] = float(exp_d1[exp_d1.index.year >= 2018].mean())
m_d1["name"] = "D1_linear"
m_d1["direction"] = "D"

# D2: power 2
exp_d2 = pd.Series(1.0, index=close.index, dtype="float64")
exp_d2[in_down] = (1.0 - sp_aligned[in_down]).clip(0.0, 1.0) ** 2.0
ret_d2, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d2, cfg)
m_d2 = compute_metrics(ret_d2)
m_d2["exposure_2018"] = float(exp_d2[exp_d2.index.year >= 2018].mean())
m_d2["name"] = "D2_power2"
m_d2["direction"] = "D"

# D3: power 3
exp_d3 = pd.Series(1.0, index=close.index, dtype="float64")
exp_d3[in_down] = (1.0 - sp_aligned[in_down]).clip(0.0, 1.0) ** 3.0
ret_d3, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d3, cfg)
m_d3 = compute_metrics(ret_d3)
m_d3["exposure_2018"] = float(exp_d3[exp_d3.index.year >= 2018].mean())
m_d3["name"] = "D3_power3"
m_d3["direction"] = "D"

# D4: soft threshold linear
exp_d4 = pd.Series(1.0, index=close.index, dtype="float64")
ratio_d4 = ((sp_aligned - 0.05) / 0.30).clip(0.0, 1.0)
exp_d4[in_down] = 1.0 - ratio_d4[in_down]
ret_d4, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d4, cfg)
m_d4 = compute_metrics(ret_d4)
m_d4["exposure_2018"] = float(exp_d4[exp_d4.index.year >= 2018].mean())
m_d4["name"] = "D4_soft_threshold_linear"
m_d4["direction"] = "D"

# D5: soft threshold power 2
exp_d5 = pd.Series(1.0, index=close.index, dtype="float64")
ratio_d5 = ((sp_aligned[in_down] - 0.05) / 0.30).clip(0.0, 1.0)
exp_d5[in_down] = 1.0 - ratio_d5 ** 2.0
ret_d5, _ = backtest_weights(close, scheduled, smallcap_timing * exp_d5, cfg)
m_d5 = compute_metrics(ret_d5)
m_d5["exposure_2018"] = float(exp_d5[exp_d5.index.year >= 2018].mean())
m_d5["name"] = "D5_soft_threshold_power2"
m_d5["direction"] = "D"

# === Aggregate ===
all_results = [ref_metrics, hmm_metrics, m_a1, m_a2, m_a3, m_a4] + b_results + [m_c0, m_c1, m_c2, m_c3] + [m_d0, m_d1, m_d2, m_d3, m_d4, m_d5]
df = pd.DataFrame(all_results)
df = df[["direction", "name", "annual_2018", "maxdd_2018", "sharpe_2018", "exposure_2018"]]
df = df.sort_values("sharpe_2018", ascending=False)
df.to_csv(OUT / "abcd_quick_results.csv", index=False)

print("\n========== A-D Exploration (CORRECTED METRICS) ==========", flush=True)
print(f"{'Dir':<5} {'Name':<35} {'Annual2018':>11} {'MaxDD2018':>10} {'Sharpe2018':>10} {'Exposure':>9}", flush=True)
print("-" * 90, flush=True)
for _, r in df.iterrows():
    print(f"{r['direction']:<5} {r['name']:<35} {r['annual_2018']:>+10.1%} {r['maxdd_2018']:>+9.1%} {r['sharpe_2018']:>9.2f} {r['exposure_2018']:>8.1%}", flush=True)

print("\n--- Best per direction ---", flush=True)
for letter in ["REF", "A", "B", "C", "D"]:
    sub = df[df["direction"] == letter]
    if sub.empty: continue
    best = sub.iloc[0]
    print(f"  {letter}: {best['name']:<35} annual={best['annual_2018']:+.1%} maxdd={best['maxdd_2018']:+.1%} sharpe={best['sharpe_2018']:.2f}", flush=True)

print("\n--- Top 5 overall ---", flush=True)
for _, r in df.head(5).iterrows():
    print(f"  {r['direction']}: {r['name']:<35} annual={r['annual_2018']:+.1%} maxdd={r['maxdd_2018']:+.1%} sharpe={r['sharpe_2018']:.2f}", flush=True)

print(f"\nWrote: {OUT / 'abcd_quick_results.csv'}", flush=True)
