"""Market-wide HMM liquidity stress guard.

The model uses four observable broad-market features to infer a hidden
liquidity-stress state with a constrained 3-state Gaussian HMM.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_COLUMNS = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]


@dataclass(frozen=True)
class HMMStressConfig:
    lookback: int = 1260
    retrain_days: int = 60
    threshold: float = 0.15
    max_iter: int = 35
    filter_days: int = 60


# B008 修复:frozen dataclass 不可变,模块级单例做缺省,与旧内联缺省语义等价。
_DEFAULT_CFG = HMMStressConfig()


def logsumexp(a, axis=None):
    a = np.asarray(a, dtype="float64")
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True) + 1e-300)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class ConstrainedGaussianHMM:
    """3-state diagonal Gaussian HMM with constrained cyclic transitions."""

    def __init__(self, n_states=3, max_iter=80, tol=1e-4):
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
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
                [0.80, 0.20, 0.00],
                [0.00, 0.80, 0.20],
                [0.20, 0.00, 0.80],
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


def standardize(train, block):
    mu = train.mean(axis=0)
    sigma = train.std(axis=0, ddof=0).replace(0, np.nan).fillna(1.0)
    return ((block - mu) / sigma).clip(-8, 8)


def build_market_features(close, amount):
    """Build the four market-internal features used by the stress model."""
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    active = amount.gt(0) & close.notna()

    ma20 = close.rolling(20).mean()
    valid_ma20 = ma20.notna()
    ma_diffusion = (close.gt(ma20) & valid_ma20).sum(axis=1) / valid_ma20.sum(axis=1).replace(0, np.nan)
    ma_diffusion = ma_diffusion.fillna(0.5).round(4)

    up_ratio = (close.gt(close.shift(1)) & active).sum(axis=1) / active.sum(axis=1).replace(0, np.nan)
    risk_appetite = up_ratio.fillna(0.5)

    market_amount = amount.sum(axis=1, min_count=1)
    liquidity = (market_amount / market_amount.rolling(20).mean()).replace([np.inf, -np.inf], np.nan).fillna(1.0)

    market_ret = ret.where(active).mean(axis=1)
    volatility = market_ret.rolling(20).std().fillna(0.0).round(6)

    out = pd.DataFrame(index=close.index)
    out["risk_appetite"] = risk_appetite
    out["volatility"] = volatility
    out["liquidity"] = liquidity
    out["ma_diffusion"] = ma_diffusion
    return out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).dropna()


def identify_stress_state(model, train_features):
    """Return the HMM state with the lowest original-space risk appetite."""
    mu = train_features.mean(axis=0).reindex(FEATURE_COLUMNS)
    sigma = train_features.std(axis=0, ddof=0).reindex(FEATURE_COLUMNS).replace(0, np.nan).fillna(1.0)
    original_means = pd.DataFrame(
        model.means * sigma.values + mu.values,
        columns=FEATURE_COLUMNS,
    )
    return int(original_means["risk_appetite"].idxmin())


def hmm_stress_probability(features, cfg=_DEFAULT_CFG):
    """Return shifted stress probability, state trace, and stress-state trace.

    This is the backtest-safe vector path: features observed at T are shifted
    to affect T+1 exposure.
    """
    features = features[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).dropna()
    dates = features.index
    prob = pd.Series(np.nan, index=dates, dtype="float64")
    state_trace = pd.Series(np.nan, index=dates, dtype="float64")
    stress_state_trace = pd.Series(np.nan, index=dates, dtype="float64")
    refit_dates = list(dates[cfg.lookback :: cfg.retrain_days])

    for pos, refit_date in enumerate(refit_dates):
        train_end = dates.get_loc(refit_date)
        train = features.iloc[train_end - cfg.lookback : train_end]
        if pos + 1 < len(refit_dates):
            next_pos = dates.get_loc(refit_dates[pos + 1])
        else:
            next_pos = len(dates)
        block = features.iloc[train_end:next_pos]
        if len(train) < cfg.lookback or block.empty:
            continue

        train_x = standardize(train, train).values
        block_x = standardize(train, pd.concat([train.tail(1), block])).values
        try:
            model = ConstrainedGaussianHMM(max_iter=cfg.max_iter).fit(train_x)
            stress_state = identify_stress_state(model, train)
            post = model.filter_posteriors(block_x)[1:]
            block_prob = post[:, stress_state]
        except FloatingPointError:
            post = np.zeros((len(block), 3), dtype="float64")
            block_prob = np.zeros(len(block), dtype="float64")
            stress_state = np.nan

        prob.loc[block.index] = block_prob
        state_trace.loc[block.index] = post.argmax(axis=1)
        stress_state_trace.loc[block.index] = stress_state

    return prob.shift(1), state_trace.shift(1), stress_state_trace.shift(1)


def latest_hmm_stress(features, target_date=None, cfg=_DEFAULT_CFG, model_cache=None):
    """Calculate latest same-day stress probability with a 60-day filter window."""
    model_cache = model_cache if model_cache is not None else {}
    features = features[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan).dropna()
    if features.empty:
        return {"prob_stress": 0.0, "stress_state": None, "cache_key": None}

    target_dt = pd.Timestamp(target_date or features.index[-1]).normalize()
    target_rows = features[features.index <= target_dt]
    train_hist = features[features.index < target_dt]
    if len(train_hist) < cfg.lookback or target_rows.empty:
        return {"prob_stress": 0.0, "stress_state": None, "cache_key": None}

    cache_key = (len(train_hist) // cfg.retrain_days) * cfg.retrain_days
    if cache_key not in model_cache:
        train = train_hist.tail(cfg.lookback)
        train_x = standardize(train, train).values
        model = ConstrainedGaussianHMM(max_iter=cfg.max_iter).fit(train_x)
        stress_state = identify_stress_state(model, train)
        model_cache[cache_key] = (model, train, stress_state)

    model, train, stress_state = model_cache[cache_key]
    block = target_rows.tail(cfg.filter_days)
    block_x = standardize(train, block).values
    post = model.filter_posteriors(block_x)
    return {
        "prob_stress": float(post[-1, stress_state]),
        "stress_state": int(stress_state),
        "cache_key": int(cache_key),
    }


def guard_exposure(prob, threshold=0.15, mode="binary", stress_floor=0.0):
    if mode == "binary":
        return (prob.fillna(1.0) <= threshold).astype(float)
    if mode == "floor":
        return pd.Series(np.where(prob.fillna(1.0) > threshold, stress_floor, 1.0), index=prob.index)
    raise ValueError(f"unknown mode: {mode}")
