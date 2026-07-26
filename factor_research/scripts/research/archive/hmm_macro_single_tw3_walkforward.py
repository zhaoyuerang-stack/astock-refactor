# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Walk-forward validation focused on single tw=3 vs alternative trend windows.

Re-runs walk-forward with:
  - Wider window grid (tw ∈ {2, 3, 5, 7, 10, 15, 20}) to test if tw=3 is truly best
  - Multiple stress thresholds (0.05, 0.10, 0.20)
  - Multiple floors (0.0, 0.2)

Goal: confirm tw=3 is robustly best across years, not a lucky pick.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/hmm_macro_single_tw3_walkforward.py
"""
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
OUT_DIR.mkdir(parents=True, exist_ok=True)


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
                log_trans = log_alpha[t - 1] + np.log(self.A[:, j] + 1e-15)
                log_alpha[t, j] = log_b[t, j] + logsumexp(log_trans)
        log_beta = np.zeros((T, S), dtype="float64")
        for t in range(T - 2, -1, -1):
            for i in range(S):
                log_trans = np.log(self.A[i, :] + 1e-15) + log_b[t + 1] + log_beta[t + 1]
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
                        + log_b[t + 1, j]
                        + log_beta[t + 1, j]
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
    feature_cols = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]
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
        X_train = train[feature_cols].values.copy()
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
            X_block = block_with_tail[feature_cols].values.copy()
            X_block_norm = (X_block - mu) / sigma
            probs = hmm.predict_proba(X_block_norm)
            block_post = probs[1:, stress_idx]
            for i, idx in enumerate(block.index):
                stress_prob.loc[idx] = block_post[i]
        except Exception:
            for idx in block.index:
                stress_prob.loc[idx] = 0.0
    return stress_prob


def run_combo(close, scheduled, timing_base, cfg, stress_prob, mkt_ret, threshold, floor, trend_window):
    sp = stress_prob.reindex(close.index).fillna(0.0)
    trend = mkt_ret.reindex(close.index).fillna(0.0).rolling(trend_window).sum()
    mask = (sp > threshold) & (trend < 0)
    exposure = pd.Series(1.0, index=close.index, dtype="float64")
    exposure[mask] = floor
    timing = timing_base.reindex(close.index).fillna(0.0) * exposure
    ret, _ = backtest_weights(close, scheduled, timing, cfg)
    return ret


def sharpe_of(ret):
    r = ret.fillna(0)
    if r.std() == 0:
        return 0.0
    return r.mean() / r.std() * np.sqrt(252)


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
    mkt_ret = close.pct_change(fill_method=None).mean(axis=1)

    # Wider window grid to test if tw=3 is truly robust
    windows = [2, 3, 5, 7, 10, 15, 20]
    thresholds = [0.05, 0.10, 0.20]
    floors = [0.0, 0.2]
    combos = [(t, f, w) for t in thresholds for f in floors for w in windows]
    print(f"\nCombo grid: {len(combos)} combinations", flush=True)

    # Pre-compute all combo returns
    print("Pre-computing combo returns...", flush=True)
    combo_rets = {}
    for i, (th, fl, w) in enumerate(combos):
        ret = run_combo(close, scheduled, smallcap_timing, cfg, stress_prob, mkt_ret, th, fl, w)
        combo_rets[(th, fl, w)] = ret
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(combos)} done", flush=True)

    # Walk-forward
    years = sorted(set(baseline_ret.index.year))
    wf_years = [y for y in years if y >= 2015]

    print(f"\nWalk-forward: {len(wf_years)} years...", flush=True)
    print("  Pre-test hypothesis: tw=3 should dominate window selection", flush=True)
    print("  If false → overfitting concern", flush=True)
    print("  If true → robust signal", flush=True)

    wf_returns = []
    baseline_annual = []
    fixed_tw3_returns = []
    param_history = []

    fixed_tw3_ret = combo_rets[(0.05, 0.0, 3)]

    for y in wf_years:
        is_years = [year for year in years if year < y]
        if len(is_years) < 3:
            continue

        # Find best combo by IS Sharpe
        best_sharpe = -999
        best_params = None
        for params, ret in combo_rets.items():
            is_r = ret[ret.index.year.isin(is_years)]
            if len(is_r) < 100:
                continue
            s = sharpe_of(is_r)
            if s > best_sharpe:
                best_sharpe = s
                best_params = params

        oos_r = combo_rets[best_params][combo_rets[best_params].index.year == y]
        fixed_tw3_oos = fixed_tw3_ret[fixed_tw3_ret.index.year == y]
        base_oos = baseline_ret[baseline_ret.index.year == y]

        wf_ann = (1 + oos_r.fillna(0)).prod() - 1
        fixed_ann = (1 + fixed_tw3_oos.fillna(0)).prod() - 1
        base_ann = (1 + base_oos.fillna(0)).prod() - 1

        wf_returns.append(wf_ann)
        fixed_tw3_returns.append(fixed_ann)
        baseline_annual.append(base_ann)
        param_history.append({
            "year": y,
            "params": str(best_params),
            "threshold": best_params[0],
            "floor": best_params[1],
            "window": best_params[2],
            "is_sharpe": best_sharpe,
            "oos_ret": wf_ann,
            "fixed_tw3_ret": fixed_ann,
            "base_ret": base_ann,
        })

        th, fl, w = best_params
        print(f"  {y}: IS best=(th={th:.2f}, fl={fl:.1f}, tw={w:>2d}) "
              f"IS_Sharpe={best_sharpe:.2f} | "
              f"WF={wf_ann:+.1%} Fixed-tw3={fixed_ann:+.1%} Base={base_ann:+.1%}", flush=True)

    # Summary
    wf_series = pd.Series(wf_returns, index=wf_years)
    fixed_series = pd.Series(fixed_tw3_returns, index=wf_years)
    base_series = pd.Series(baseline_annual, index=wf_years)

    print("\n=== Walk-Forward Summary (2015-2026) ===", flush=True)
    print(f"{'Metric':<20} {'Walk-Forward':>14} {'Fixed-tw3':>14} {'Baseline':>14}", flush=True)
    print(f"{'Annualized':<20} {wf_series.mean():>+13.1%} {fixed_series.mean():>+13.1%} {base_series.mean():>+13.1%}", flush=True)
    print(f"{'Sharpe':<20} {sharpe_of(wf_series):>13.2f} {sharpe_of(fixed_series):>13.2f} {sharpe_of(base_series):>13.2f}", flush=True)
    print(f"{'Min Yearly':<20} {wf_series.min():>+13.1%} {fixed_series.min():>+13.1%} {base_series.min():>+13.1%}", flush=True)
    print(f"{'Win vs Base':<20} {(wf_series > base_series).mean():>13.0%} {(fixed_series > base_series).mean():>13.0%}", flush=True)

    # Parameter stability
    print("\n=== Parameter Selection History ===", flush=True)
    for p in param_history:
        print(f"  {p['year']}: th={p['threshold']:.2f} fl={p['floor']:.1f} "
              f"tw={p['window']:>2d}  (IS Sharpe={p['is_sharpe']:.2f})", flush=True)

    # Aggregate stats
    unique_combos = set(p["params"] for p in param_history)
    print(f"\n  Unique combinations selected: {len(unique_combos)} / {len(param_history)} years", flush=True)

    # Window-specific analysis
    window_counts = {}
    for p in param_history:
        window_counts[p["window"]] = window_counts.get(p["window"], 0) + 1
    print(f"\n  Window frequency: {sorted(window_counts.items(), key=lambda x: -x[1])}", flush=True)

    # Save results
    df = pd.DataFrame(param_history)
    df.to_csv(OUT_DIR / "hmm_macro_single_tw3_walkforward.csv", index=False)
    print(f"\nWrote: {OUT_DIR / 'hmm_macro_single_tw3_walkforward.csv'}", flush=True)


if __name__ == "__main__":
    main()
