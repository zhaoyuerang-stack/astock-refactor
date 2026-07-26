# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""HMM stress guard strategy — strict look-ahead-free implementation.

Based on the HMM core algorithm doc. Key fix: P(stress) is computed from
data STRICTLY BEFORE target_date (features at T-1 → decision at T open).

Document's original code had: df_env.index <= target_dt (includes today → leak)
Fixed to: df_env.index < target_dt (only yesterday and earlier)

Usage:
  cd /Users/kiki/astcok/factor_research && python3 scripts/research/hmm_stress_guard_v2.py
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

# ── HMM: EXACT replication of the doc's ConstrainedGaussianHMM ──
class ConstrainedGaussianHMM:
    """3-state constrained Gaussian HMM. Identical to the doc implementation."""
    def __init__(self, n_states=3, max_iter=100, tol=1e-4):
        self.n_states = n_states; self.max_iter = max_iter; self.tol = tol
        self.pi = None; self.A = None; self.means = None; self.vars = None

    def _init_params(self, X):
        n, d = X.shape
        self.pi = np.ones(self.n_states) / self.n_states
        self.A = np.array([[0.8,0.2,0.0],[0.0,0.8,0.2],[0.2,0.0,0.8]], dtype="float64")
        idx = np.argsort(X[:,0]); splits = np.array_split(idx, self.n_states)
        self.means = np.zeros((self.n_states, d), dtype="float64")
        self.vars = np.zeros((self.n_states, d), dtype="float64")
        for s, block in enumerate(splits):
            blk = X[block]; self.means[s] = blk.mean(axis=0); self.vars[s] = blk.var(axis=0) + 1e-2

    def _log_emission(self, X):
        T, d = X.shape
        log_b = np.zeros((T, self.n_states), dtype="float64")
        for j in range(self.n_states):
            diff = X - self.means[j]
            log_b[:,j] = -0.5*np.sum(np.log(2*np.pi*self.vars[j])+diff*diff/self.vars[j], axis=1)
        return log_b

    def _logsumexp(self, a, axis=-1):
        m = np.max(a, axis=axis, keepdims=True)
        return np.squeeze(m, axis=axis)+np.log(np.sum(np.exp(a-m), axis=axis, keepdims=True).squeeze(axis=axis))

    def _forward(self, log_b):
        """Forward-only filtering (no backward pass)."""
        T, S = log_b.shape
        log_alpha = np.zeros((T, S), dtype="float64")
        log_alpha[0] = np.log(self.pi + 1e-15) + log_b[0]
        for t in range(1, T):
            for j in range(S):
                log_trans = log_alpha[t-1] + np.log(self.A[:, j] + 1e-15)
                log_alpha[t, j] = log_b[t, j] + self._logsumexp(log_trans)
        # Normalize each row
        gamma = np.exp(log_alpha - log_alpha.max(axis=1, keepdims=True))
        gamma = gamma / gamma.sum(axis=1, keepdims=True)
        return gamma

    def _forward_backward(self, log_b):
        T = log_b.shape[0]; S = self.n_states
        log_alpha = np.zeros((T, S), dtype="float64")
        log_alpha[0] = np.log(self.pi + 1e-15) + log_b[0]
        for t in range(1, T):
            for j in range(S):
                log_trans = log_alpha[t-1] + np.log(self.A[:, j] + 1e-15)
                log_alpha[t, j] = log_b[t, j] + self._logsumexp(log_trans)
        log_beta = np.zeros((T, S), dtype="float64")
        for t in range(T-2, -1, -1):
            for i in range(S):
                log_trans = np.log(self.A[i,:] + 1e-15) + log_b[t+1] + log_beta[t+1]
                log_beta[t, i] = self._logsumexp(log_trans)
        log_ll = self._logsumexp(log_alpha[-1])
        log_gamma = log_alpha + log_beta - log_ll
        gamma = np.exp(log_gamma)
        log_xi = np.zeros((T-1, S, S), dtype="float64")
        for t in range(T-1):
            for i in range(S):
                for j in range(S):
                    log_xi[t,i,j] = log_alpha[t,i]+np.log(self.A[i,j]+1e-15)+log_b[t+1,j]+log_beta[t+1,j]-log_ll
        return gamma, np.exp(log_xi), log_ll

    def fit(self, X):
        X = np.asarray(X, dtype="float64"); self._init_params(X)
        old_ll = -np.inf
        for _ in range(self.max_iter):
            log_b = self._log_emission(X); gamma, xi, ll = self._forward_backward(log_b)
            if abs(ll - old_ll) < self.tol: break
            old_ll = ll
            self.pi = gamma[0]/(gamma[0].sum()+1e-15)
            new_A = xi.sum(axis=0)/(gamma[:-1].sum(axis=0)[:,None]+1e-15)
            new_A[0,2]=0.0; new_A[1,0]=0.0; new_A[2,1]=0.0
            self.A = new_A/(new_A.sum(axis=1, keepdims=True)+1e-15)
            for j in range(self.n_states):
                g = gamma[:,j]; gs = g.sum()+1e-15
                self.means[j] = (g[:,None]*X).sum(axis=0)/gs
                diff = X - self.means[j]; self.vars[j] = (g[:,None]*diff*diff).sum(axis=0)/gs + 1e-5
        return self

    def forward_filter(self, X):
        """Forward-only: no future data. Returns gamma for each t."""
        return self._forward(self._log_emission(X))


# ── 4 macro features ──
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
        "risk_appetite": risk_appetite, "volatility": volatility,
        "liquidity": liquidity, "ma_diffusion": ma_diffusion,
    }, index=close.index)
    return df.replace([np.inf, -np.inf], np.nan).dropna()


# ── Build stress signal (strictly look-ahead-free) ──
def build_stress_signal(features, min_history=250, retrain_step=60):
    """
    For each target_date T:
      1. Train HMM on data < T (strictly before)
      2. Forward-filter on last 60 days < T
      3. Take last filtered probability → P(Stress) at T-1
      4. Shift: P(Stress)_{T-1} → decision at T open

    60-day cache: re-train only when history grows by 60 days.
    """
    FEATURES = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]
    dates = features.index
    stress_prob = pd.Series(np.nan, index=dates, dtype="float64")
    model_cache = {}

    for i, target_dt in enumerate(dates):
        # Only use data STRICTLY BEFORE target_dt
        df_hist = features[features.index < target_dt]
        if len(df_hist) < min_history: continue

        # 60-day cache key
        cache_key = (len(df_hist) // retrain_step) * retrain_step

        if cache_key not in model_cache:
            X = df_hist[FEATURES].values.copy()
            X_mean = X.mean(axis=0); X_std = X.std(axis=0)
            X_std[X_std == 0] = 1.0
            X_norm = (X - X_mean) / X_std
            try:
                hmm = ConstrainedGaussianHMM(n_states=3, max_iter=100, tol=1e-4).fit(X_norm)
                ratios = [(j, (hmm.means[j] * X_std + X_mean)[0]) for j in range(3)]
                ratios.sort(key=lambda x: x[1]); stress_idx = ratios[0][0]
                model_cache[cache_key] = (hmm, X_mean, X_std, stress_idx)
            except Exception:
                model_cache[cache_key] = None
                continue

        entry = model_cache[cache_key]
        if entry is None: continue
        hmm, X_mean, X_std, stress_idx = entry

        # Forward-filter on last 60 days STRICTLY BEFORE target_dt
        today_env = df_hist.tail(60)  # already < target_dt
        if today_env.empty: continue
        X_today = today_env[FEATURES].values.copy()
        X_today_norm = (X_today - X_mean) / X_std
        try:
            probs = hmm.forward_filter(X_today_norm)
            stress_prob.loc[target_dt] = probs[-1, stress_idx]
        except Exception: pass

    # Shift(1): T-1 probability → T decision
    return stress_prob.shift(1)


# ── Main ──
def main():
    print("Loading data...", flush=True)
    close, vol, amount = load_price_panels("2010-01-01")
    factor = small_cap_factor(amount, 60)
    timing, _, _ = small_cap_timing(close, amount, 16)
    scheduled = build_rebalance_weights(factor, close, 25, 20)
    cfg = StrategyConfig(start="2010-01-01")

    features = make_features(close, amount)
    print(f"Features: {features.shape}", flush=True)
    print("Building HMM stress signal (strictly no look-ahead)...", flush=True)
    stress = build_stress_signal(features, min_history=250, retrain_step=60)
    print(f"  Non-null: {stress.notna().sum()}", flush=True)

    # Baseline
    ret_v20, _ = backtest_weights(close, scheduled, timing.astype(float), cfg)
    def cagr(ret):
        r=ret.fillna(0); n=max(len(r)/252,1)
        return (1+r).cumprod().iloc[-1]**(1/n)-1

    # Grid: threshold × floor
    print("\nGrid search...", flush=True)
    results = []
    for th in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
        for fl in [0.0, 0.3, 0.5]:
            mask = stress.fillna(0.0) > th
            exp = pd.Series(1.0, index=close.index, dtype="float64")
            exp[mask.reindex(close.index).fillna(False)] = fl
            t = timing.astype(float).reindex(close.index).fillna(0.0) * exp
            ret, _ = backtest_weights(close, scheduled, t, cfg)
            a = cagr(ret[ret.index.year>=2018])
            s = ret[ret.index.year>=2018].mean()/ret[ret.index.year>=2018].std()*np.sqrt(252)
            d = float(((1+ret.fillna(0)).cumprod()/(1+ret.fillna(0)).cumprod().cummax()-1).min())
            results.append({"th":th,"fl":fl,"annual":a,"sharpe":s,"maxdd":d})
            print(f"  th={th:.2f} fl={fl:.1f}  annual={a:+.1%}  sharpe={s:.2f}  maxdd={d:+.1%}", flush=True)

    df = pd.DataFrame(results).sort_values("sharpe", ascending=False)
    df.to_csv(OUT / "hmm_stress_guard_v2.csv", index=False)

    print("\n=== HMM Stress Guard v2 Results ===")
    print(f"Best: th={df.iloc[0]['th']:.2f} fl={df.iloc[0]['fl']:.1f} "
          f"annual={df.iloc[0]['annual']:+.1%} sharpe={df.iloc[0]['sharpe']:.2f}")
    print(f"v2.0: annual={cagr(ret_v20[ret_v20.index.year>=2018]):+.1%} "
          f"sharpe={ret_v20[ret_v20.index.year>=2018].mean()/ret_v20[ret_v20.index.year>=2018].std()*np.sqrt(252):.2f}")


if __name__ == "__main__":
    main()
