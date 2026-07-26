"""HMM macro liquidity stress overlay with trend filter and health monitoring.

This overlay does NOT modify the core strategy. It sits on top and provides
a daily exposure multiplier in [0.0, 1.0] based on:

  1. HMM 3-state model trained on 4 macro indicators
  2. Trend filter: only exit when market is also declining
  3. Health monitoring: alerts when the overlay stops working

Usage:
    from core.overlays import HMMMacroOverlay, OverlayConfig, OverlayMonitor

    cfg = OverlayConfig()
    overlay = HMMMacroOverlay(cfg)
    monitor = OverlayMonitor(cfg)

    # Daily signal
    exposure = overlay.signal(target_date, close_panel, amount_panel)

    # Monthly health check
    status = monitor.check(exposure_series, baseline_ret, overlay_ret)
    if status.is_failing:
        logger.warning(f"Overlay failing: {status.reason}")
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverlayConfig:
    """Immutable configuration for the HMM macro overlay."""

    # HMM parameters
    lookback: int = 250          # training window (trading days)
    retrain_days: int = 60       # HMM cache step
    hmm_max_iter: int = 100
    hmm_tol: float = 1e-4

    # Decision parameters (validated by walk-forward)
    stress_threshold: float = 0.05
    floor: float = 0.0           # minimum exposure when triggered
    trend_window: int = 3        # days for market-return trend filter

    # Health monitoring
    health_check_freq: str = "M"  # "M"=monthly, "W"=weekly
    max_stress_free_months: int = 6   # trigger review if no stress for 6 months
    max_bull_stress_months: int = 6   # trigger review if stress misses rebounds for 6 months
    rebound_horizon: int = 20     # days after stress to check if rebound was missed

    # Operational
    min_history_days: int = 250   # minimum data before first signal
    feature_set: str = "macro"    # "macro" = 4 indicators

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> OverlayConfig:
        return cls(**{k: v for k, v in d.items() if k in {f.name for f in cls.__dataclass_fields__.values()}})


# ---------------------------------------------------------------------------
# HMM core (same math as research scripts, standalone)
# ---------------------------------------------------------------------------

class _ConstrainedGaussianHMM:
    """Internal HMM implementation with no external dependencies."""

    def __init__(self, n_states: int = 3, max_iter: int = 100, tol: float = 1e-4):
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
        self.pi: np.ndarray | None = None
        self.A: np.ndarray | None = None
        self.means: np.ndarray | None = None
        self.vars: np.ndarray | None = None

    # -- helpers --
    @staticmethod
    def _logsumexp(a: np.ndarray, axis: int = -1) -> np.ndarray:
        m = np.max(a, axis=axis, keepdims=True)
        return np.squeeze(m, axis=axis) + np.log(
            np.sum(np.exp(a - m), axis=axis, keepdims=True).squeeze(axis=axis)
        )

    def _init_params(self, X: np.ndarray) -> None:
        n, d = X.shape
        self.pi = np.ones(self.n_states) / self.n_states
        self.A = np.array(
            [[0.8, 0.2, 0.0],
             [0.0, 0.8, 0.2],
             [0.2, 0.0, 0.8]],
            dtype="float64",
        )
        idx = np.argsort(X[:, 0])
        splits = np.array_split(idx, self.n_states)
        self.means = np.zeros((self.n_states, d), dtype="float64")
        self.vars = np.zeros((self.n_states, d), dtype="float64")
        for state, s in enumerate(splits):
            block = X[s]
            self.means[state] = block.mean(axis=0)
            self.vars[state] = block.var(axis=0) + 1e-2

    def _log_emission(self, X: np.ndarray) -> np.ndarray:
        T, d = X.shape
        log_b = np.zeros((T, self.n_states), dtype="float64")
        for j in range(self.n_states):
            diff = X - self.means[j]
            log_b[:, j] = -0.5 * np.sum(
                np.log(2 * np.pi * self.vars[j]) + diff * diff / self.vars[j],
                axis=1,
            )
        return log_b

    def _forward_backward(self, log_b: np.ndarray):
        T = log_b.shape[0]
        S = self.n_states
        log_alpha = np.zeros((T, S), dtype="float64")
        log_alpha[0] = np.log(self.pi + 1e-15) + log_b[0]
        for t in range(1, T):
            for j in range(S):
                log_trans = log_alpha[t - 1] + np.log(self.A[:, j] + 1e-15)
                log_alpha[t, j] = log_b[t, j] + self._logsumexp(log_trans)

        log_beta = np.zeros((T, S), dtype="float64")
        for t in range(T - 2, -1, -1):
            for i in range(S):
                log_trans = np.log(self.A[i, :] + 1e-15) + log_b[t + 1] + log_beta[t + 1]
                log_beta[t, i] = self._logsumexp(log_trans)

        log_likelihood = self._logsumexp(log_alpha[-1])
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

    def fit(self, X: np.ndarray):
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

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        log_b = self._log_emission(X)
        gamma, _, _ = self._forward_backward(log_b)
        return gamma


# ---------------------------------------------------------------------------
# Macro features
# ---------------------------------------------------------------------------

def _macro_features(close: pd.DataFrame, amount: pd.DataFrame) -> pd.DataFrame:
    """Build 4 macro indicators from price/amount panels."""
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


# ---------------------------------------------------------------------------
# HMM stress signal builder
# ---------------------------------------------------------------------------

def _build_stress_signal(
    features: pd.DataFrame,
    lookback: int = 250,
    retrain_days: int = 60,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> pd.Series:
    """Return P(Stress) series indexed by features.index."""
    dates = features.index
    feature_cols = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]
    stress_prob = pd.Series(np.nan, index=dates, dtype="float64")
    refit_dates = list(dates[lookback :: retrain_days])
    model_cache: dict = {}

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
                hmm = _ConstrainedGaussianHMM(
                    n_states=3, max_iter=max_iter, tol=tol
                ).fit(X_train_norm)
                ratios = [
                    (j, (hmm.means[j] * sigma + mu)[0]) for j in range(3)
                ]
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


# ---------------------------------------------------------------------------
# Overlay class
# ---------------------------------------------------------------------------

class HMMMacroOverlay:
    """HMM macro liquidity stress overlay with trend filter.

    Usage:
        overlay = HMMMacroOverlay(OverlayConfig())
        overlay.fit(close_panel, amount_panel)   # pre-compute once
        exposure = overlay.signal(target_date)    # daily call
    """

    FEATURE_COLS = ["risk_appetite", "volatility", "liquidity", "ma_diffusion"]

    def __init__(self, config: OverlayConfig | None = None):
        self.cfg = config or OverlayConfig()
        self._features: pd.DataFrame | None = None
        self._stress_prob: pd.Series | None = None
        self._mkt_ret: pd.Series | None = None
        self._dates: pd.DatetimeIndex | None = None
        self._is_fitted = False

    # -- public API --

    def fit(self, close: pd.DataFrame, amount: pd.DataFrame) -> HMMMacroOverlay:
        """Pre-compute macro features and HMM stress signal."""
        self._features = _macro_features(close, amount)
        self._dates = self._features.index
        logger.info(
            "HMMMacroOverlay.fit: %s days x %s features",
            len(self._features),
            len(self.FEATURE_COLS),
        )

        if len(self._features) < self.cfg.min_history_days:
            raise ValueError(
                f"Need {self.cfg.min_history_days} days of history, "
                f"got {len(self._features)}"
            )

        self._stress_prob = _build_stress_signal(
            self._features,
            lookback=self.cfg.lookback,
            retrain_days=self.cfg.retrain_days,
            max_iter=self.cfg.hmm_max_iter,
            tol=self.cfg.hmm_tol,
        )
        self._mkt_ret = close.pct_change(fill_method=None).mean(axis=1)
        self._is_fitted = True
        logger.info(
            "HMMMacroOverlay.fit complete. Stress prob range: [%.3f, %.3f]",
            self._stress_prob.min(),
            self._stress_prob.max(),
        )
        return self

    def signal(
        self,
        target_date: pd.Timestamp | str,
        close: pd.DataFrame | None = None,
        amount: pd.DataFrame | None = None,
    ) -> float:
        """Return exposure multiplier for target_date (0.0 ~ 1.0).

        If fit() was called, this is O(1) lookup.
        If fit() was NOT called, computes from scratch (slower).
        """
        dt = pd.Timestamp(target_date).normalize()

        if not self._is_fitted or close is not None:
            # On-demand compute (e.g. first call or new data)
            close = close if close is not None else self._close
            amount = amount if amount is not None else self._amount
            self.fit(close, amount)

        if dt not in self._dates:
            logger.debug("Date %s not in feature index, returning full exposure", dt)
            return 1.0

        pos = self._dates.get_loc(dt)
        if pos < self.cfg.trend_window:
            return 1.0

        sp = self._stress_prob.iloc[pos]
        trend = self._mkt_ret.iloc[pos - self.cfg.trend_window + 1 : pos + 1].sum()

        if sp > self.cfg.stress_threshold and trend < 0:
            return self.cfg.floor
        return 1.0

    def exposure_series(self, close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
        """Return full exposure series for all dates in close."""
        self.fit(close, amount)
        sp = self._stress_prob.reindex(self._dates).fillna(0.0)
        trend = (
            self._mkt_ret.reindex(self._dates)
            .fillna(0.0)
            .rolling(self.cfg.trend_window)
            .sum()
        )
        mask = (sp > self.cfg.stress_threshold) & (trend < 0)
        exposure = pd.Series(1.0, index=self._dates, dtype="float64")
        exposure[mask] = self.cfg.floor
        return exposure

    def health_metrics(
        self,
        exposure: pd.Series,
        strategy_ret: pd.Series,
    ) -> pd.DataFrame:
        """Monthly health table: stress frequency + stress-period performance."""
        df = pd.DataFrame({
            "exposure": exposure.reindex(strategy_ret.index).fillna(1.0),
            "ret": strategy_ret,
        })
        df["month"] = df.index.to_period("M")
        df["is_stress"] = df["exposure"] < 1.0 - 1e-6

        monthly = df.groupby("month").agg(
            stress_days=("is_stress", "sum"),
            total_days=("is_stress", "count"),
            stress_ret=("ret", lambda x: x[df.loc[x.index, "is_stress"]].sum()),
            nonstress_ret=("ret", lambda x: x[~df.loc[x.index, "is_stress"]].sum()),
            all_ret=("ret", "sum"),
        )
        monthly["stress_freq"] = monthly["stress_days"] / monthly["total_days"]
        monthly["stress_cumulative"] = monthly["stress_ret"].cumsum()
        monthly["nonstress_cumulative"] = monthly["nonstress_ret"].cumsum()
        return monthly

    def to_dict(self) -> dict:
        return {
            "config": self.cfg.to_dict(),
            "fitted": self._is_fitted,
            "feature_days": len(self._features) if self._features is not None else 0,
        }


# ---------------------------------------------------------------------------
# Health monitor
# ---------------------------------------------------------------------------

@dataclass
class HealthStatus:
    is_failing: bool
    reason: str
    months_since_stress: int
    months_bull_stress: int
    last_check: pd.Timestamp | None = None
    detail: dict = field(default_factory=dict)


class OverlayMonitor:
    """Monitors overlay health and triggers failure review."""

    def __init__(self, config: OverlayConfig | None = None):
        self.cfg = config or OverlayConfig()
        self._history: list[dict] = []

    def check(
        self,
        exposure: pd.Series,
        baseline_ret: pd.Series,
        overlay_ret: pd.Series,
    ) -> HealthStatus:
        """Run monthly health check.

        Failure conditions:
          1. No stress events for N consecutive months
             → overlay may have gone silent (HMM mis-calibrated)
          2. Stress events consistently miss rebounds for N consecutive months
             → overlay is selling into dips that recover quickly
        """
        df = pd.DataFrame({
            "exposure": exposure.reindex(baseline_ret.index).fillna(1.0),
            "baseline": baseline_ret,
            "overlay": overlay_ret,
        })
        df["month"] = df.index.to_period("M")
        df["is_stress"] = df["exposure"] < 1.0 - 1e-6

        monthly = df.groupby("month").agg(
            stress_days=("is_stress", "sum"),
            total_days=("is_stress", "count"),
            baseline_ret=("baseline", "sum"),
            overlay_ret=("overlay", "sum"),
        )
        monthly["has_stress"] = monthly["stress_days"] > 0

        # Condition 1: consecutive months without stress
        months_no_stress = 0
        for has_stress in reversed(monthly["has_stress"].tolist()):
            if has_stress:
                break
            months_no_stress += 1

        # Condition 2: stress months where overlay underperforms baseline
        # (i.e. we sold and market went up)
        monthly["overlay_worse"] = monthly["overlay_ret"] < monthly["baseline_ret"]
        monthly_bull_stress = 0
        for worse in reversed(monthly["overlay_worse"].tolist()):
            if not worse:
                break
            monthly_bull_stress += 1

        is_failing = (
            months_no_stress >= self.cfg.max_stress_free_months
            or monthly_bull_stress >= self.cfg.max_bull_stress_months
        )

        reason = ""
        if months_no_stress >= self.cfg.max_stress_free_months:
            reason = f"No stress for {months_no_stress} consecutive months"
        elif monthly_bull_stress >= self.cfg.max_bull_stress_months:
            reason = f"Stress missed rebounds for {monthly_bull_stress} consecutive months"

        status = HealthStatus(
            is_failing=is_failing,
            reason=reason,
            months_since_stress=months_no_stress,
            months_bull_stress=monthly_bull_stress,
            last_check=pd.Timestamp.now(),
            detail={
                "n_months": len(monthly),
                "avg_stress_freq": float(monthly["stress_days"].sum() / monthly["total_days"].sum()),
                "months_no_stress": months_no_stress,
                "months_bull_stress": monthly_bull_stress,
            },
        )

        self._history.append({
            "date": status.last_check.isoformat(),
            **status.detail,
            "is_failing": is_failing,
            "reason": reason,
        })
        return status

    def history(self) -> pd.DataFrame:
        return pd.DataFrame(self._history)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._history, ensure_ascii=False, indent=2))
