# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""HMM exit overlay for the small-cap strategy.

Independent research script; it only reads data_lake/core backtest helpers and
writes result artifacts under reports/research. It does not touch production
signals, registry, scheduled jobs, or the core strategy implementation.

Usage:
  /usr/bin/python3 -m scripts.research.archive.hmm_exit_smallcap
"""
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics, yearly_returns
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def logsumexp(a, axis=None):
    a = np.asarray(a, dtype="float64")
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True) + 1e-300)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class ConstrainedGaussianHMM:
    """3-state diagonal Gaussian HMM with constrained cyclic transitions."""

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
            [
                [1, 1, 0],
                [0, 1, 1],
                [1, 0, 1],
            ],
            dtype=bool,
        )

    def _init_params(self, X):
        n_samples, n_features = X.shape
        self.pi = np.full(self.n_states, 1.0 / self.n_states)
        self.A = np.array(
            [
                [0.86, 0.14, 0.00],
                [0.00, 0.86, 0.14],
                [0.14, 0.00, 0.86],
            ],
            dtype="float64",
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


@dataclass(frozen=True)
class HMMGrid:
    feature_set: str
    lookback: int
    retrain_days: int
    risk_threshold: float
    exposure_mode: str = "binary"
    risk_floor: float = 0.0
    risk_power: float = 1.0
    max_iter: int = 35


def make_features(close, amount, feature_set):
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


def exposure_from_risk(risk_prob, grid):
    if grid.exposure_mode == "binary":
        return (risk_prob < grid.risk_threshold).astype(float)
    if grid.exposure_mode == "soft":
        de_risk = (1.0 - risk_prob.clip(0.0, 1.0)) ** grid.risk_power
        return grid.risk_floor + (1.0 - grid.risk_floor) * de_risk
    raise ValueError(f"unknown exposure_mode: {grid.exposure_mode}")


def hmm_exit_signal(features, grid):
    dates = features.index
    risk_prob = pd.Series(np.nan, index=dates, dtype="float64")
    state_trace = pd.Series(np.nan, index=dates, dtype="float64")
    refit_dates = list(dates[grid.lookback :: grid.retrain_days])

    for start_pos, refit_date in enumerate(refit_dates):
        train_end = dates.get_loc(refit_date)
        train = features.iloc[train_end - grid.lookback : train_end]
        if len(train) < grid.lookback:
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
            model = ConstrainedGaussianHMM(max_iter=grid.max_iter).fit(train_x)
            train_post = model.filter_posteriors(train_x)
            # State risk must be learned from the next tradable return. The HMM
            # observes features at T, and exposure changes can only apply at T+1.
            realized = train.iloc[:, 0].shift(-1).reset_index(drop=True)
            state_means = []
            for state in range(model.n_states):
                valid = realized.notna().values
                weight = train_post[:, state] * valid
                if weight.sum() <= 1e-8:
                    state_means.append(np.inf)
                else:
                    state_means.append(float((weight * realized.fillna(0.0).values).sum() / weight.sum()))
            risk_state = int(np.argmin(state_means))
            post = model.filter_posteriors(block_x)[1:]
            block_risk = post[:, risk_state]
        except FloatingPointError:
            post = np.zeros((len(block), 3), dtype="float64")
            block_risk = np.zeros(len(block), dtype="float64")
        idx = block.index
        risk_prob.loc[idx] = block_risk
        state_trace.loc[idx] = post.argmax(axis=1)

    # HMM sees close-to-close features at T; trade/exposure decision is only
    # available on T+1, so shift one day to avoid a look-ahead exit.
    shifted_risk = risk_prob.shift(1)
    shifted_state = state_trace.shift(1)
    exposure = exposure_from_risk(shifted_risk.fillna(1.0), grid)
    return exposure, shifted_risk, shifted_state


def slice_metrics(ret, start_year):
    sliced = ret[ret.index.year >= start_year]
    return metrics(sliced)


def row_for(label, ret, baseline, extra=None):
    m2018 = slice_metrics(ret, 2018)
    m2023 = slice_metrics(ret, 2023)
    m2010 = slice_metrics(ret, 2010)
    out = {
        "label": label,
        "annual_2018": m2018["annual"],
        "maxdd_2018": m2018["maxdd"],
        "sharpe_2018": m2018["sharpe"],
        "calmar_2018": m2018["calmar"],
        "annual_2023": m2023["annual"],
        "maxdd_2023": m2023["maxdd"],
        "sharpe_2023": m2023["sharpe"],
        "annual_2010": m2010["annual"],
        "maxdd_2010": m2010["maxdd"],
        "sharpe_2010": m2010["sharpe"],
        "corr_v2_2018": ret[ret.index.year >= 2018].corr(baseline[baseline.index.year >= 2018]),
        "exposure_2018": float((ret[ret.index.year >= 2018] != 0).mean()),
    }
    if extra:
        out.update(extra)
    return out


def fmt_pct(x):
    return f"{x:+.1%}"


def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close, amount = base["close"], base["amount"]
    baseline_ret = base["returns"]
    scheduled = base["scheduled_weights"]
    smallcap_timing = base["timing"].astype(float)

    model_grids = [
        HMMGrid("small_market", 756, 60, 0.55),
        HMMGrid("small_market", 1260, 60, 0.55),
        HMMGrid("small_only", 756, 60, 0.55),
        HMMGrid("small_only", 1260, 60, 0.55),
    ]
    exposure_variants = [
        {"exposure_mode": "binary", "risk_threshold": 0.50, "risk_floor": 0.0, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.50, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.70, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.80, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.85, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.90, "risk_power": 1.0},
        {"exposure_mode": "soft", "risk_threshold": 0.50, "risk_floor": 0.95, "risk_power": 1.0},
    ]

    rows = [row_for("v2.0 baseline", baseline_ret, baseline_ret)]
    details = []
    feature_cache = {}

    for model_grid in model_grids:
        print(f"Training HMM risk model: {model_grid}", flush=True)
        features = feature_cache.get(model_grid.feature_set)
        if features is None:
            features = make_features(close, amount, model_grid.feature_set)
            feature_cache[model_grid.feature_set] = features
        _, risk_prob, state_trace = hmm_exit_signal(features, model_grid)
        aligned_risk = risk_prob.reindex(close.index)

        for variant in exposure_variants:
            grid = HMMGrid(
                model_grid.feature_set,
                model_grid.lookback,
                model_grid.retrain_days,
                variant["risk_threshold"],
                variant["exposure_mode"],
                variant["risk_floor"],
                variant["risk_power"],
                model_grid.max_iter,
            )
            aligned_hmm = exposure_from_risk(aligned_risk.fillna(1.0), grid)
            combined_timing = smallcap_timing.reindex(close.index).fillna(0.0) * aligned_hmm
            ret, detail = backtest_weights(close, scheduled, combined_timing, cfg)

            label = (
                f"hmm_{grid.exposure_mode} {grid.feature_set} lb{grid.lookback} "
                f"rt{grid.retrain_days} th{grid.risk_threshold:.2f} "
                f"floor{grid.risk_floor:.2f} pow{grid.risk_power:.1f}"
            )
            active_2018 = combined_timing[combined_timing.index.year >= 2018]
            rows.append(
                row_for(
                    label,
                    ret,
                    baseline_ret,
                    {
                        **asdict(grid),
                        "timing_on_rate_2018": float(active_2018.mean()),
                        "hmm_on_rate_2018": float(aligned_hmm[aligned_hmm.index.year >= 2018].mean()),
                    },
                )
            )
            details.append(
                pd.DataFrame(
                    {
                        "hmm_on": aligned_hmm,
                        "combined_timing": combined_timing,
                        "risk_prob": aligned_risk,
                        "state": state_trace.reindex(close.index),
                        "ret": ret.reindex(close.index),
                    }
                ).assign(label=label)
            )

        details.append(
            pd.DataFrame(
                {
                    "hmm_on": np.nan,
                    "combined_timing": np.nan,
                    "risk_prob": aligned_risk,
                    "state": state_trace.reindex(close.index),
                    "ret": np.nan,
                }
            ).assign(
                label=(
                    f"hmm_risk_raw {model_grid.feature_set} "
                    f"lb{model_grid.lookback} rt{model_grid.retrain_days}"
                )
            )
        )

    result = pd.DataFrame(rows).sort_values(
        ["sharpe_2018", "annual_2018", "maxdd_2018"], ascending=[False, False, False]
    )
    result_path = OUT_DIR / "hmm_exit_smallcap_results.csv"
    detail_path = OUT_DIR / "hmm_exit_smallcap_daily.csv"
    summary_path = OUT_DIR / "hmm_exit_smallcap_summary.json"
    result.to_csv(result_path, index=False)
    pd.concat(details).to_csv(detail_path)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    best = variants.iloc[0].to_dict()
    best_calmar = variants.sort_values(
        ["calmar_2018", "annual_2018", "maxdd_2018"],
        ascending=[False, False, False],
    ).iloc[0].to_dict()
    years = yearly_returns(baseline_ret[baseline_ret.index.year >= 2010]).to_dict()
    summary = {
        "config": cfg.to_dict(),
        "baseline": rows[0],
        "best_by_sharpe_2018": best,
        "best_by_calmar_2018": best_calmar,
        "baseline_yearly_returns": {str(k): float(v) for k, v in years.items()},
        "notes": [
            "Uses 2010 warm-up and core.backtest realistic costs.",
            "HMM features are shifted by one trading day before use to avoid look-ahead.",
            "This is an experiment artifact only; no production code or registry was changed.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== HMM exit + small-cap result (sorted by 2018+ Sharpe) ===")
    cols = ["label", "annual_2018", "maxdd_2018", "sharpe_2018", "annual_2023", "maxdd_2023"]
    for _, row in result[cols].iterrows():
        print(
            f"{row['label']:<48} "
            f"2018年化{fmt_pct(row['annual_2018'])} 回撤{fmt_pct(row['maxdd_2018'])} "
            f"夏普{row['sharpe_2018']:.2f} | "
            f"2023年化{fmt_pct(row['annual_2023'])} 回撤{fmt_pct(row['maxdd_2023'])}"
        )
    print(f"\nWrote: {result_path}")
    print(f"Wrote: {detail_path}")
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
