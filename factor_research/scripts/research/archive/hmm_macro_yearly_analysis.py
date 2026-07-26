# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Yearly breakdown of HMM macro exit vs baseline."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


def logsumexp(a, axis=None):
    a = np.asarray(a, dtype="float64")
    m = np.max(a, axis=axis, keepdims=True)
    out = m + np.log(np.sum(np.exp(a - m), axis=axis, keepdims=True) + 1e-300)
    if axis is None:
        return float(out.ravel()[0])
    return np.squeeze(out, axis=axis)


class ConstrainedGaussianHMM:
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
        self.A = np.array([[0.8, 0.2, 0.0], [0.0, 0.8, 0.2], [0.2, 0.0, 0.8]], dtype="float64")
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
            log_b[:, j] = -0.5 * np.sum(np.log(2 * np.pi * self.vars[j]) + diff * diff / self.vars[j], axis=1)
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
                    log_xi[t, i, j] = log_alpha[t, i] + np.log(self.A[i, j] + 1e-15) + log_b[t+1, j] + log_beta[t+1, j] - log_likelihood
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


def make_macro_features(close, amount):
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
                ratios = [(j, (hmm.means[j] * sigma + mu)[0]) for j in range(3)]
                ratios.sort(key=lambda x: x[1])
                stress_idx = ratios[0][0]
                model_cache[cache_key] = (hmm, mu, sigma, stress_idx)
            else:
                hmm, mu, sigma, stress_idx = model_cache[cache_key]
            block_with_tail = pd.concat([train.tail(1), block])
            X_block = block_with_tail[features_cols].values.copy()
            X_block_norm = (X_block - mu) / sigma
            probs = hmm.predict_proba(X_block_norm)
            block_post = probs[1:, stress_idx]
            for i, idx in enumerate(block.index):
                stress_prob.loc[idx] = block_post[i]
        except Exception:
            for idx in block.index:
                stress_prob.loc[idx] = 0.0
    return stress_prob


def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline...", flush=True)
    base = run_small_cap_strategy(cfg)
    close, amount = base["close"], base["amount"]
    baseline_ret = base["returns"]
    scheduled = base["scheduled_weights"]
    smallcap_timing = base["timing"].astype(float)

    features = make_macro_features(close, amount)
    print("Building stress signal...", flush=True)
    stress_prob = build_stress_signal(features, lookback=250, retrain_days=60)

    # Best config from grid search
    threshold, floor = 0.10, 0.0
    exposure = (stress_prob.reindex(close.index).fillna(0.0) <= threshold).astype(float) * (1.0 - floor) + floor
    timing = smallcap_timing.reindex(close.index).fillna(0.0) * exposure
    ret, detail = backtest_weights(close, scheduled, timing, cfg)

    # Yearly breakdown
    print("\n=== Yearly breakdown ===", flush=True)
    print(f"{'Year':>6} {'BaseRet':>9} {'HMMRet':>9} {'Delta':>8} {'BaseDD':>9} {'HMMDD':>9} {'StressDays':>11} {'TotalDays':>10}", flush=True)

    years = sorted(set(baseline_ret.index.year))
    rows = []
    for year in years:
        b = baseline_ret[baseline_ret.index.year == year]
        r = ret[ret.index.year == year]
        e = exposure[exposure.index.year == year]
        sp = stress_prob.reindex(b.index)

        base_ann = (1 + b.fillna(0)).prod() - 1
        hmm_ann = (1 + r.fillna(0)).prod() - 1
        delta = hmm_ann - base_ann

        base_nav = (1 + b.fillna(0)).cumprod()
        hmm_nav = (1 + r.fillna(0)).cumprod()
        base_dd = (base_nav / base_nav.cummax() - 1).min()
        hmm_dd = (hmm_nav / hmm_nav.cummax() - 1).min()

        stress_days = int((sp > threshold).sum())
        total_days = len(b)

        marker = ""
        if delta > 0.05:
            marker = " 🚀"
        elif delta > 0.02:
            marker = " +"
        elif delta < -0.05:
            marker = " ⚠️"

        print(f"{year:>6} {base_ann:>+8.1%} {hmm_ann:>+8.1%} {delta:>+7.1%} {base_dd:>+8.1%} {hmm_dd:>+8.1%} {stress_days:>11} {total_days:>10}{marker}", flush=True)
        rows.append({
            "year": year, "base_ret": base_ann, "hmm_ret": hmm_ann, "delta": delta,
            "base_dd": base_dd, "hmm_dd": hmm_dd,
            "stress_days": stress_days, "total_days": total_days,
        })

    # Summary stats
    df = pd.DataFrame(rows)
    pos_years = (df["delta"] > 0).sum()
    neg_years = (df["delta"] <= 0).sum()
    print(f"\nPositive years: {pos_years} / {len(df)} ({pos_years/len(df):.0%})", flush=True)
    print(f"Negative years: {neg_years} / {len(df)} ({neg_years/len(df):.0%})", flush=True)
    print(f"Avg delta: {df['delta'].mean():+.1%}", flush=True)
    print(f"Med delta: {df['delta'].median():+.1%}", flush=True)

    # Stress episodes: consecutive stress days
    sp_aligned = stress_prob.reindex(close.index).fillna(0.0)
    is_stress = sp_aligned > threshold
    episodes = []
    in_episode = False
    start = None
    for dt, flag in is_stress.items():
        if flag and not in_episode:
            in_episode = True
            start = dt
        elif not flag and in_episode:
            in_episode = False
            episodes.append((start, dt))
    if in_episode:
        episodes.append((start, close.index[-1]))

    print(f"\n=== Stress episodes ({len(episodes)} total) ===", flush=True)
    print(f"{'Start':>12} {'End':>12} {'Days':>6} {'BaseRet':>9} {'HMMRet':>9} {'Delta':>8}", flush=True)
    for start, end in episodes[-15:]:  # last 15
        b = baseline_ret[(baseline_ret.index >= start) & (baseline_ret.index <= end)]
        r = ret[(ret.index >= start) & (ret.index <= end)]
        if len(b) < 2:
            continue
        base_cum = (1 + b.fillna(0)).prod() - 1
        hmm_cum = (1 + r.fillna(0)).prod() - 1
        delta = hmm_cum - base_cum
        print(f"{start.strftime('%Y-%m-%d'):>12} {end.strftime('%Y-%m-%d'):>12} {len(b):>6} {base_cum:>+8.1%} {hmm_cum:>+8.1%} {delta:>+7.1%}", flush=True)

    # Save yearly CSV
    out_path = OUT_DIR / "hmm_macro_yearly.csv"
    df.to_csv(out_path, index=False)
    print(f"\nWrote: {out_path}", flush=True)


if __name__ == "__main__":
    main()
