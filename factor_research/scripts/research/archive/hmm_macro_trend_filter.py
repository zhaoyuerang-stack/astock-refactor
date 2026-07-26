# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""HMM macro exit with trend filter (Scheme B).

Only exit when BOTH conditions are met:
  1. P(Stress) > threshold
  2. Market cumulative return over N days < 0

This avoids false alarms during bull markets where P(Stress) may be elevated
but prices are still rising.

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/hmm_macro_trend_filter.py
"""
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

    # Market return for trend filter
    mkt_ret = close.pct_change(fill_method=None).mean(axis=1)

    baseline_row = summarize("v2.0 baseline", baseline_ret, base["detail"],
                             baseline_ret, smallcap_timing,
                             pd.Series(1.0, index=close.index))
    rows = [baseline_row]

    # Grid: threshold x floor x trend_window
    configs = []
    for threshold in [0.05, 0.10, 0.15, 0.20, 0.30]:
        for floor in [0.0, 0.2, 0.3]:
            for trend_window in [3, 5, 10, 20]:
                configs.append({"threshold": threshold, "floor": floor, "trend_window": trend_window})

    print(f"\nTrend-filter sweep: {len(configs)} combinations...", flush=True)
    for i, conf in enumerate(configs):
        sp = stress_prob.reindex(close.index).fillna(0.0)
        trend = mkt_ret.reindex(close.index).fillna(0.0).rolling(conf["trend_window"]).sum()

        # Exit only when BOTH stress AND negative trend
        mask = (sp > conf["threshold"]) & (trend < 0)
        exposure = pd.Series(1.0, index=close.index, dtype="float64")
        exposure[mask] = conf["floor"]

        timing = smallcap_timing.reindex(close.index).fillna(0.0) * exposure
        ret, detail = backtest_weights(close, scheduled, timing, cfg)

        label = f"th{conf['threshold']:.2f}_fl{conf['floor']:.1f}_tw{conf['trend_window']}"
        row = summarize(label, ret, detail, baseline_ret, timing, exposure)
        row.update(conf)
        row["rule"] = label
        rows.append(row)

        stress_days = int(mask.sum())
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(configs)}] {label:<20} "
                  f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
                  f"夏普{row['sharpe_2018']:.2f} 应激{stress_days}d", flush=True)

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

    result.to_csv(OUT_DIR / "hmm_macro_trend_filter.csv", index=False)

    # Print top 15
    print("\n=== Top 15 by Sharpe ===", flush=True)
    for _, row in result[result["label"] != "v2.0 baseline"].head(15).iterrows():
        print(f"{row['label']:<20} "
              f"年化{row['annual_2018']:+.1%}({row['delta_annual_2018']:+.1%}) "
              f"回撤{row['maxdd_2018']:+.1%}({row['delta_maxdd_2018']:+.1%}) "
              f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
              f"曝光{row['overlay_exposure_2018']:.1%}", flush=True)

    # Yearly breakdown for best combo
    best = result[result["label"] != "v2.0 baseline"].iloc[0]
    print(f"\n=== Yearly breakdown for best: {best['label']} ===", flush=True)

    sp = stress_prob.reindex(close.index).fillna(0.0)
    trend = mkt_ret.reindex(close.index).fillna(0.0).rolling(int(best["trend_window"])).sum()
    mask = (sp > best["threshold"]) & (trend < 0)
    exposure = pd.Series(1.0, index=close.index, dtype="float64")
    exposure[mask] = best["floor"]
    timing = smallcap_timing.reindex(close.index).fillna(0.0) * exposure
    ret, _ = backtest_weights(close, scheduled, timing, cfg)

    print(f"{'Year':>6} {'Base':>8} {'HMM':>8} {'Delta':>7} {'BaseDD':>8} {'HMMDD':>8} {'Stress':>7}", flush=True)
    for year in sorted(set(baseline_ret.index.year)):
        b = baseline_ret[baseline_ret.index.year == year]
        r = ret[ret.index.year == year]
        sp_y = stress_prob.reindex(b.index)
        trend_y = mkt_ret.reindex(b.index).fillna(0.0).rolling(int(best["trend_window"])).sum()
        mask_y = (sp_y > best["threshold"]) & (trend_y < 0)
        base_ann = (1 + b.fillna(0)).prod() - 1
        hmm_ann = (1 + r.fillna(0)).prod() - 1
        base_nav = (1 + b.fillna(0)).cumprod()
        hmm_nav = (1 + r.fillna(0)).cumprod()
        base_dd = (base_nav / base_nav.cummax() - 1).min()
        hmm_dd = (hmm_nav / hmm_nav.cummax() - 1).min()
        stress_days = int(mask_y.sum())
        print(f"{year:>6} {base_ann:>+7.1%} {hmm_ann:>+7.1%} {hmm_ann-base_ann:>+6.1%} "
              f"{base_dd:>+7.1%} {hmm_dd:>+7.1%} {stress_days:>7}", flush=True)

    print(f"\nWrote: {OUT_DIR / 'hmm_macro_trend_filter.csv'}", flush=True)


if __name__ == "__main__":
    main()
