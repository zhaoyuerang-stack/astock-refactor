# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""HMM macro liquidity stress exit — based on the HMM core algorithm doc.

Uses 4 macro indicators (not strategy returns):
  - risk_appetite: up_ratio (close > close.shift)
  - volatility: market equal-weight ret rolling 20d std
  - liquidity: market amount / market amount MA20
  - ma_diffusion: pct of stocks above MA20

HMM: 3-state constrained Gaussian, 60-day training window, 60-day retrain.
Decision: P(Stress) > threshold → set exposure = floor.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/hmm_macro_exit.py
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# HMM (same as doc)
# ---------------------------------------------------------------------------

def logsumexp(a, axis=None):
    a = np.asarray(a, dtype="float64")
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True) + 1e-300)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class ConstrainedGaussianHMM:
    """3-state constrained Gaussian HMM with diagonal covariance."""

    def __init__(self, n_states=3, max_iter=100, tol=1e-4):
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
        self.pi = None
        self.A = None
        self.means = None
        self.vars = None

    def _init_params(self, X):
        n, d = X.shape
        self.pi = np.ones(self.n_states) / self.n_states
        self.A = np.array([
            [0.8, 0.2, 0.0],
            [0.0, 0.8, 0.2],
            [0.2, 0.0, 0.8],
        ], dtype="float64")
        idx = np.argsort(X[:, 0])
        splits = np.array_split(idx, self.n_states)
        self.means = np.zeros((self.n_states, d), dtype="float64")
        self.vars = np.zeros((self.n_states, d), dtype="float64")
        for state, s in enumerate(splits):
            block = X[s]
            self.means[state] = block.mean(axis=0)
            self.vars[state] = block.var(axis=0) + 1e-2

    def _log_emission(self, X):
        T, d = X.shape
        log_b = np.zeros((T, self.n_states), dtype="float64")
        for j in range(self.n_states):
            diff = X - self.means[j]
            log_b[:, j] = -0.5 * np.sum(
                np.log(2 * np.pi * self.vars[j]) + diff * diff / self.vars[j],
                axis=1,
            )
        return log_b

    def _forward_backward(self, log_b):
        T = log_b.shape[0]
        S = self.n_states
        log_alpha = np.zeros((T, S), dtype="float64")
        log_alpha[0] = np.log(self.pi + 1e-15) + log_b[0]
        for t in range(1, T):
            for j in range(S):
                log_trans = log_alpha[t-1] + np.log(self.A[:, j] + 1e-15)
                log_alpha[t, j] = log_b[t, j] + logsumexp(log_trans)

        log_beta = np.zeros((T, S), dtype="float64")
        for t in range(T - 2, -1, -1):
            for i in range(S):
                log_trans = np.log(self.A[i, :] + 1e-15) + log_b[t+1] + log_beta[t+1]
                log_beta[t, i] = logsumexp(log_trans)

        log_likelihood = logsumexp(log_alpha[-1])
        log_gamma = log_alpha + log_beta - log_likelihood
        gamma = np.exp(log_gamma)

        log_xi = np.zeros((T - 1, S, S), dtype="float64")
        for t in range(T - 1):
            for i in range(S):
                for j in range(S):
                    log_xi[t, i, j] = (
                        log_alpha[t, i]
                        + np.log(self.A[i, j] + 1e-15)
                        + log_b[t+1, j]
                        + log_beta[t+1, j]
                        - log_likelihood
                    )
        xi = np.exp(log_xi)
        return gamma, xi, log_likelihood

    def fit(self, X):
        X = np.asarray(X, dtype="float64")
        self._init_params(X)
        old_ll = -np.inf
        for _ in range(self.max_iter):
            log_b = self._log_emission(X)
            gamma, xi, ll = self._forward_backward(log_b)
            if abs(ll - old_ll) < self.tol:
                break
            old_ll = ll
            self.pi = gamma[0] / (gamma[0].sum() + 1e-15)
            new_A = xi.sum(axis=0) / (gamma[:-1].sum(axis=0)[:, None] + 1e-15)
            new_A[0, 2] = 0.0
            new_A[1, 0] = 0.0
            new_A[2, 1] = 0.0
            row_sums = new_A.sum(axis=1, keepdims=True)
            self.A = new_A / (row_sums + 1e-15)
            for j in range(self.n_states):
                g = gamma[:, j]
                g_sum = g.sum() + 1e-15
                self.means[j] = (g[:, None] * X).sum(axis=0) / g_sum
                diff = X - self.means[j]
                self.vars[j] = (g[:, None] * diff * diff).sum(axis=0) / g_sum + 1e-5
        return self

    def predict_proba(self, X):
        log_b = self._log_emission(X)
        gamma, _, _ = self._forward_backward(log_b)
        return gamma


# ---------------------------------------------------------------------------
# Macro features
# ---------------------------------------------------------------------------

def make_macro_features(close, amount):
    """Build 4 macro indicators from price/amount panels.
    Returns DataFrame indexed by close.index.
    """
    # 1. risk_appetite: up_ratio
    ret = close.pct_change(fill_method=None)
    has_trade = amount > 0
    up = (ret > 0) & has_trade
    risk_appetite = up.sum(axis=1) / has_trade.sum(axis=1)

    # 2. volatility: equal-weight market ret 20d rolling std
    mkt_ret = ret.mean(axis=1)
    volatility = mkt_ret.rolling(20).std()

    # 3. liquidity: market amount / market amount MA20
    market_amount = amount.sum(axis=1)
    liquidity = market_amount / market_amount.rolling(20).mean()

    # 4. ma_diffusion: pct of stocks above MA20
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


# ---------------------------------------------------------------------------
# HMM stress signal with 60-day cache
# ---------------------------------------------------------------------------

def build_stress_signal(features, lookback=250, retrain_days=60):
    """Build P(Stress) series from macro features using 60-day cached HMM.

    Training: use lookback days to train HMM, cache by retrain_days step.
    Prediction: forward-filter on last 60 days, take last day's P(Stress).
    """
    dates = features.index
    features_cols = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]
    stress_prob = pd.Series(np.nan, index=dates, dtype="float64")

    refit_dates = list(dates[lookback :: retrain_days])
    model_cache = {}

    for start_pos, refit_date in enumerate(refit_dates):
        train_end = dates.get_loc(refit_date)
        train = features.iloc[train_end - lookback : train_end]
        if len(train) < lookback:
            continue
        if start_pos + 1 < len(refit_dates):
            next_pos = dates.get_loc(refit_dates[start_pos + 1])
        else:
            next_pos = len(dates)
        block = features.iloc[train_end:next_pos]
        if block.empty:
            continue

        cache_key = train_end

        X_train = train[features_cols].values.copy()
        mu = X_train.mean(axis=0)
        sigma = X_train.std(axis=0)
        sigma[sigma == 0] = 1.0
        X_train_norm = (X_train - mu) / sigma

        try:
            if cache_key not in model_cache:
                hmm = ConstrainedGaussianHMM(n_states=3, max_iter=100, tol=1e-4).fit(X_train_norm)
                # Identify stress state: lowest risk_appetite mean
                ratios = [(j, (hmm.means[j] * sigma + mu)[0]) for j in range(3)]
                ratios.sort(key=lambda x: x[1])
                stress_idx = ratios[0][0]
                model_cache[cache_key] = (hmm, mu, sigma, stress_idx)
            else:
                hmm, mu, sigma, stress_idx = model_cache[cache_key]

            # Forward-filter on block + last train day for continuity
            block_with_tail = pd.concat([train.tail(1), block])
            X_block = block_with_tail[features_cols].values.copy()
            X_block_norm = (X_block - mu) / sigma
            probs = hmm.predict_proba(X_block_norm)
            block_post = probs[1:, stress_idx]  # skip the tail day

            for i, idx in enumerate(block.index):
                stress_prob.loc[idx] = block_post[i]
        except Exception:
            for idx in block.index:
                stress_prob.loc[idx] = 0.0

    return stress_prob


# ---------------------------------------------------------------------------
# Exposure & backtest
# ---------------------------------------------------------------------------

def exposure_from_stress(stress_prob, threshold=0.15, floor=0.0):
    """Binary exposure: floor when P(Stress) > threshold, else 1.0."""
    return (stress_prob.fillna(0.0) <= threshold).astype(float) * (1.0 - floor) + floor


def slice_metrics(ret, start_year):
    return metrics(ret[ret.index.year >= start_year])


def summarize(label, ret, detail, baseline_ret, timing, exposure):
    row = {"label": label}
    for year in [2018, 2023, 2010]:
        m = slice_metrics(ret, year)
        suffix = str(year)
        row[f"annual_{suffix}"] = m["annual"]
        row[f"maxdd_{suffix}"] = m["maxdd"]
        row[f"sharpe_{suffix}"] = m["sharpe"]
        row[f"calmar_{suffix}"] = m["calmar"]
    ret_2018 = ret[ret.index.year >= 2018]
    base_2018 = baseline_ret[baseline_ret.index.year >= 2018]
    detail_2018 = detail[detail.index.year >= 2018]
    row["corr_v2_2018"] = ret_2018.corr(base_2018)
    row["timing_on_rate_2018"] = float(timing[timing.index.year >= 2018].mean())
    row["overlay_exposure_2018"] = float(exposure[exposure.index.year >= 2018].mean())
    row["turnover_ann_2018"] = float(detail_2018["turnover"].mean() * 252)
    row["cost_ann_2018"] = float(detail_2018["cost"].mean() * 252)
    row["trade_days_2018"] = int((detail_2018["turnover"] > 1e-12).sum())
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline...", flush=True)
    base = run_small_cap_strategy(cfg)
    close, amount = base["close"], base["amount"]
    baseline_ret = base["returns"]
    scheduled = base["scheduled_weights"]
    smallcap_timing = base["timing"].astype(float)

    features = make_macro_features(close, amount)
    print(f"Macro features: {features.shape[1]} dims x {len(features)} days", flush=True)

    print("Building HMM stress signal (60-day cache)...", flush=True)
    stress_prob = build_stress_signal(features, lookback=250, retrain_days=60)
    print(f"  Stress prob range: [{stress_prob.min():.3f}, {stress_prob.max():.3f}]", flush=True)
    print(f"  Non-NaN days: {stress_prob.notna().sum()}", flush=True)

    baseline_row = summarize("v2.0 baseline", baseline_ret, base["detail"],
                             baseline_ret, smallcap_timing,
                             pd.Series(1.0, index=close.index))
    rows = [baseline_row]

    # Grid: threshold x floor variants
    configs = []
    for threshold in [0.10, 0.15, 0.20, 0.30, 0.50]:
        for floor in [0.0, 0.3, 0.5, 0.7, 0.9]:
            configs.append({"threshold": threshold, "floor": floor})

    print(f"\nGrid search: {len(configs)} combinations...", flush=True)
    for i, conf in enumerate(configs):
        exposure = exposure_from_stress(stress_prob, conf["threshold"], conf["floor"])
        timing = smallcap_timing.reindex(close.index).fillna(0.0) * exposure
        ret, detail = backtest_weights(close, scheduled, timing, cfg)

        label = f"macro_th{conf['threshold']:.2f}_floor{conf['floor']:.1f}"
        row = summarize(label, ret, detail, baseline_ret, timing, exposure)
        row.update(conf)
        row["rule"] = label
        rows.append(row)

        stress_days = int((stress_prob.reindex(close.index).fillna(0.0) > conf["threshold"]).sum())
        print(f"  [{i+1}/{len(configs)}] {label:<25} "
              f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
              f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
              f"曝光{row['overlay_exposure_2018']:.1%} 应激{stress_days}d", flush=True)

    result = pd.DataFrame(rows)
    base_row = result[result["label"] == "v2.0 baseline"].iloc[0]
    for col in ["annual_2018", "maxdd_2018", "sharpe_2018", "calmar_2018",
                "turnover_ann_2018", "cost_ann_2018", "trade_days_2018"]:
        result[f"delta_{col}"] = result[col] - base_row[col]

    result = result.sort_values(
        ["sharpe_2018", "annual_2018", "maxdd_2018"],
        ascending=[False, False, False],
        na_position="last",
    )

    result_path = OUT_DIR / "hmm_macro_exit.csv"
    summary_path = OUT_DIR / "hmm_macro_exit_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    best_sharpe = variants.iloc[0].to_dict()
    best_return = variants.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict()
    best_calmar = variants.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict()

    summary = {
        "baseline": baseline_row,
        "best_by_sharpe_2018": best_sharpe,
        "best_by_return_2018": best_return,
        "best_by_calmar_2018": best_calmar,
        "notes": [
            "Uses 4 macro indicators: risk_appetite, volatility, liquidity, ma_diffusion.",
            "HMM: 3-state constrained Gaussian, 250d training, 60d retrain cache.",
            "Decision: P(Stress) > threshold → exposure = floor.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top macro HMM exit rules ===", flush=True)
    cols = ["label", "annual_2018", "maxdd_2018", "sharpe_2018", "calmar_2018",
            "overlay_exposure_2018", "delta_annual_2018", "delta_maxdd_2018"]
    for _, row in result[result["label"] != "v2.0 baseline"].head(10)[cols].iterrows():
        print(
            f"{row['label']:<25} "
            f"年化{row['annual_2018']:+.1%}({row['delta_annual_2018']:+.1%}) "
            f"回撤{row['maxdd_2018']:+.1%}({row['delta_maxdd_2018']:+.1%}) "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
            f"曝光{row['overlay_exposure_2018']:.1%}",
            flush=True,
        )

    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
