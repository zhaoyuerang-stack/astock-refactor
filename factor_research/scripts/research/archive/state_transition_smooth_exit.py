# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Smooth predictive exit using continuous stress-probability scaling.

Key insight from the discrete trigger experiment:
  - Binary triggers had 67% false alarms, each missing rebounds
  - Lead time was positive (~10d) but net effect on returns was negative

This version replaces the binary trigger with continuous exposure scaling:
  exposure_t = floor + (1-floor) * (1 - stress_pred_t)^power

Only active when accum_prob >= threshold (i.e., we are in the latent accumulation
regime). Outside that regime, exposure = 1.0 (full).

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/state_transition_smooth_exit.py
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
# HMM (copy from hmm_exit_smallcap)
# ---------------------------------------------------------------------------

def logsumexp(a, axis=None):
    a = np.asarray(a, dtype="float64")
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True) + 1e-300)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class ConstrainedGaussianHMM:
    STABLE = 0
    ACCUM = 1
    STRESS = 2

    def __init__(self, n_states=3, max_iter=80, tol=1e-4, random_state=7):
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
        self.random_state = random_state
        self.pi = None
        self.A = None
        self.means = None
        self.vars = None
        self.allowed = np.array(
            [[1, 1, 0], [0, 1, 1], [1, 0, 1]], dtype=bool,
        )

    def _init_params(self, X):
        n_samples, n_features = X.shape
        self.pi = np.full(self.n_states, 1.0 / self.n_states)
        self.A = np.array(
            [[0.86, 0.14, 0.00],
             [0.00, 0.86, 0.14],
             [0.14, 0.00, 0.86]], dtype="float64",
        )
        order = np.argsort(X[:, 0])
        splits = np.array_split(order, self.n_states)
        self.means = np.zeros((self.n_states, n_features), dtype="float64")
        self.vars = np.zeros((self.n_states, n_features), dtype="float64")
        for state, idx in enumerate(splits):
            block = X[idx]
            self.means[state] = block.mean(axis=0)
            self.vars[state] = block.var(axis=0) + 1e-3

    def _log_emission_probs(self, X):
        out = np.zeros((len(X), self.n_states), dtype="float64")
        for state in range(self.n_states):
            diff = X - self.means[state]
            out[:, state] = -0.5 * np.sum(
                np.log(2 * np.pi * self.vars[state]) + diff * diff / self.vars[state],
                axis=1,
            )
        return out

    def _forward_backward(self, log_b):
        n = len(log_b)
        log_A = np.full_like(self.A, -np.inf, dtype="float64")
        log_A[self.A > 0] = np.log(self.A[self.A > 0])
        alpha = np.zeros((n, self.n_states), dtype="float64")
        beta = np.zeros((n, self.n_states), dtype="float64")
        alpha[0] = np.log(self.pi + 1e-300) + log_b[0]
        for t in range(1, n):
            alpha[t] = log_b[t] + logsumexp(alpha[t - 1][:, None] + log_A, axis=0)
        beta[-1] = 0.0
        for t in range(n - 2, -1, -1):
            beta[t] = logsumexp(log_A + log_b[t + 1] + beta[t + 1], axis=1)
        ll = logsumexp(alpha[-1])
        gamma_log = alpha + beta
        gamma_log = gamma_log - logsumexp(gamma_log, axis=1)[:, None]
        gamma = np.exp(gamma_log)
        if not np.isfinite(gamma).all():
            raise FloatingPointError("non-finite HMM posterior")
        xi = np.zeros((n - 1, self.n_states, self.n_states), dtype="float64")
        for t in range(n - 1):
            xlog = alpha[t][:, None] + log_A + log_b[t + 1] + beta[t + 1] - ll
            xlog = xlog - logsumexp(xlog)
            xi[t] = np.exp(xlog)
        return gamma, xi, ll

    def fit(self, X):
        X = np.asarray(X, dtype="float64")
        self._init_params(X)
        prev_ll = -np.inf
        for _ in range(self.max_iter):
            log_b = self._log_emission_probs(X)
            gamma, xi, ll = self._forward_backward(log_b)
            if not np.isfinite(ll):
                raise FloatingPointError("non-finite HMM likelihood")
            if np.isfinite(prev_ll) and abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll
            self.pi = gamma[0] + 1e-4
            self.pi = self.pi / self.pi.sum()
            counts = xi.sum(axis=0) + np.where(self.allowed, 1e-3, 0.0)
            counts[~self.allowed] = 0.0
            row_sums = counts.sum(axis=1, keepdims=True)
            fallback = np.where(self.allowed, 1.0, 0.0)
            fallback = fallback / fallback.sum(axis=1, keepdims=True)
            self.A = np.where(row_sums > 0, counts / np.maximum(row_sums, 1e-300), fallback)
            old_means = self.means.copy()
            old_vars = self.vars.copy()
            weights = gamma.sum(axis=0)
            for state in range(self.n_states):
                if weights[state] <= 1e-4:
                    self.means[state] = old_means[state]
                    self.vars[state] = old_vars[state]
                    continue
                self.means[state] = (gamma[:, state][:, None] * X).sum(axis=0) / weights[state]
                diff = X - self.means[state]
                self.vars[state] = (gamma[:, state][:, None] * diff * diff).sum(axis=0) / weights[state]
            self.vars = np.clip(self.vars, 1e-4, None)
            if not (np.isfinite(self.means).all() and np.isfinite(self.vars).all() and np.isfinite(self.A).all()):
                raise FloatingPointError("non-finite HMM parameters")
        return self

    def filter_posteriors(self, X):
        X = np.asarray(X, dtype="float64")
        log_b = self._log_emission_probs(X)
        log_A = np.full_like(self.A, -np.inf, dtype="float64")
        log_A[self.A > 0] = np.log(self.A[self.A > 0])
        alpha = np.zeros((len(X), self.n_states), dtype="float64")
        alpha[0] = np.log(self.pi + 1e-300) + log_b[0]
        alpha[0] -= logsumexp(alpha[0])
        for t in range(1, len(X)):
            alpha[t] = log_b[t] + logsumexp(alpha[t - 1][:, None] + log_A, axis=0)
            alpha[t] -= logsumexp(alpha[t])
        return np.exp(alpha)

    def predict_stress_prob(self, post_t, horizon=1):
        A_h = np.linalg.matrix_power(self.A, horizon)
        future_dist = post_t @ A_h
        return float(future_dist[self.STRESS])


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def make_features(close, amount, feature_set="small_market"):
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
    small_ret = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
    mkt_ret = ret.mean(axis=1)
    small_nav = (1.0 + small_ret.fillna(0.0)).cumprod()
    mkt_nav = (1.0 + mkt_ret.fillna(0.0)).cumprod()
    base = pd.DataFrame(index=close.index)
    base["small_ret_1d"] = small_ret
    base["small_ret_5d"] = small_ret.rolling(5).sum()
    base["small_vol_20d"] = small_ret.rolling(20).std()
    base["small_dd_60d"] = small_nav / small_nav.rolling(60).max() - 1.0
    if feature_set == "small_market":
        base["mkt_ret_5d"] = mkt_ret.rolling(5).sum()
        base["mkt_vol_20d"] = mkt_ret.rolling(20).std()
        base["mkt_dd_60d"] = mkt_nav / mkt_nav.rolling(60).max() - 1.0
    elif feature_set != "small_only":
        raise ValueError(f"unknown feature_set: {feature_set}")
    return base.replace([np.inf, -np.inf], np.nan).dropna()


def standardize(train, block):
    mu = train.mean(axis=0)
    sigma = train.std(axis=0).replace(0, np.nan).fillna(1.0)
    return ((block - mu) / sigma).clip(-8, 8)


# ---------------------------------------------------------------------------
# Pre-compute HMM posteriors
# ---------------------------------------------------------------------------

def precompute_hmm_posteriors(features, lookback=1260, retrain_days=60):
    dates = features.index
    horizons = [3, 5, 10]
    result = {h: {
        "accum_prob": pd.Series(np.nan, index=dates, dtype="float64"),
        "stress_pred": pd.Series(np.nan, index=dates, dtype="float64"),
        "state_trace": pd.Series(np.nan, index=dates, dtype="float64"),
    } for h in horizons}

    refit_dates = list(dates[lookback :: retrain_days])
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

        train_x = standardize(train, train).values
        block_x = standardize(train, pd.concat([train.tail(1), block])).values

        try:
            model = ConstrainedGaussianHMM(max_iter=35).fit(train_x)
            post = model.filter_posteriors(block_x)[1:]
            for i, idx in enumerate(block.index):
                p = post[i]
                for h in horizons:
                    result[h]["accum_prob"].loc[idx] = p[model.ACCUM]
                    result[h]["stress_pred"].loc[idx] = model.predict_stress_prob(p, horizon=h)
                    result[h]["state_trace"].loc[idx] = p.argmax()
        except FloatingPointError:
            for idx in block.index:
                for h in horizons:
                    result[h]["accum_prob"].loc[idx] = 0.0
                    result[h]["stress_pred"].loc[idx] = 0.0
                    result[h]["state_trace"].loc[idx] = 0.0

    for h in horizons:
        result[h]["accum_prob"] = result[h]["accum_prob"].shift(1)
        result[h]["stress_pred"] = result[h]["stress_pred"].shift(1)
        result[h]["state_trace"] = result[h]["state_trace"].shift(1)
    return result


# ---------------------------------------------------------------------------
# Smooth exposure from stress_pred
# ---------------------------------------------------------------------------

def smooth_exposure(accum_prob, stress_pred, accum_threshold=0.30, floor=0.7, power=1.0):
    """Continuous exposure: floor + (1-floor) * (1 - stress_pred)^power
    Only active when accum_prob >= threshold.
    """
    mask = accum_prob.fillna(0.0) >= accum_threshold
    sp = stress_pred.fillna(0.0).clip(0.0, 1.0)
    de_risk = (1.0 - sp) ** power
    exposure = pd.Series(1.0, index=accum_prob.index, dtype="float64")
    exposure[mask] = floor + (1.0 - floor) * de_risk[mask]
    return exposure.clip(0.0, 1.0)


# ---------------------------------------------------------------------------
# Backtest helpers
# ---------------------------------------------------------------------------

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

    features = make_features(close, amount, "small_market")
    print(f"Features: {features.shape[1]} dims x {len(features)} days", flush=True)

    # Pre-compute HMM posteriors
    precomputed = {}
    for lookback in [756, 1260]:
        print(f"Pre-computing HMM posteriors for lookback={lookback}...", flush=True)
        precomputed[lookback] = precompute_hmm_posteriors(features, lookback=lookback, retrain_days=60)
        print("  Done.", flush=True)

    # Grid search: smooth exposure variants
    configs = []
    for lookback in [756, 1260]:
        for horizon in [3, 5, 10]:
            for accum_th in [0.20, 0.30, 0.45]:
                for floor in [0.5, 0.7, 0.8, 0.9]:
                    for power in [1.0, 2.0, 3.0]:
                        configs.append({
                            "lookback": lookback,
                            "horizon": horizon,
                            "accum_threshold": accum_th,
                            "floor": floor,
                            "power": power,
                        })

    baseline_row = summarize("v2.0 baseline", baseline_ret, base["detail"],
                             baseline_ret, smallcap_timing,
                             pd.Series(1.0, index=close.index))
    rows = [baseline_row]

    print(f"\nGrid search: {len(configs)} smooth exposure combinations...", flush=True)
    for i, conf in enumerate(configs):
        pc = precomputed[conf["lookback"]]
        h = conf["horizon"]
        accum_prob = pc[h]["accum_prob"]
        stress_pred = pc[h]["stress_pred"]

        exposure = smooth_exposure(
            accum_prob,
            stress_pred,
            accum_threshold=conf["accum_threshold"],
            floor=conf["floor"],
            power=conf["power"],
        )

        timing = smallcap_timing.reindex(close.index).fillna(0.0) * exposure
        ret, detail = backtest_weights(close, scheduled, timing, cfg)

        label = (
            f"smooth_lb{conf['lookback']}_hor{conf['horizon']}_th{conf['accum_threshold']:.2f}_"
            f"floor{conf['floor']:.1f}_pow{conf['power']:.0f}"
        )
        row = summarize(label, ret, detail, baseline_ret, timing, exposure)
        row.update(conf)
        row["rule"] = label
        rows.append(row)

        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{len(configs)}] {label[:45]:<45} "
                  f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
                  f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
                  f"曝光{row['overlay_exposure_2018']:.1%}", flush=True)

    result = pd.DataFrame(rows)
    base_row = result[result["label"] == "v2.0 baseline"].iloc[0]
    for col in ["annual_2018", "maxdd_2018", "sharpe_2018", "calmar_2018",
                "turnover_ann_2018", "cost_ann_2018", "trade_days_2018"]:
        result[f"delta_{col}"] = result[col] - base_row[col]

    # Sort by calmar first, then return
    result = result.sort_values(
        ["sharpe_2018", "annual_2018", "maxdd_2018"],
        ascending=[False, False, False],
        na_position="last",
    )

    result_path = OUT_DIR / "state_transition_smooth_exit.csv"
    summary_path = OUT_DIR / "state_transition_smooth_exit_summary.json"
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
            "Continuous exposure scaling: exposure = floor + (1-floor) * (1 - stress_pred)^power",
            "Only active when accum_prob >= threshold (latent accumulation regime).",
            "No binary triggers; smooth daily adjustment avoids false-alarm rebound misses.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top smooth predictive exit rules (by Sharpe) ===", flush=True)
    cols = ["label", "annual_2018", "maxdd_2018", "sharpe_2018", "calmar_2018",
            "overlay_exposure_2018", "delta_annual_2018", "delta_maxdd_2018"]
    for _, row in result[result["label"] != "v2.0 baseline"].head(15)[cols].iterrows():
        print(
            f"{row['label'][:50]:<50} "
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
