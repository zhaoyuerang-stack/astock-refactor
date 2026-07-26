"""Moving Average Timing Overlay for Defensive Risk Management (LOOP_ENGINEERING §5).

Decoupled from specific strategy files to allow independent parameter audit and reuse.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.analysis.walk_forward import deflated_sharpe


class MovingAverageOverlay:
    """Moving Average timing overlay based on custom index.

    Default index is the equal-weighted small-cap stock universe.
    """

    def __init__(
        self,
        ma_window: int = 16,
        volume_rank_threshold: float = 0.5,
        rolling_rank_window: int = 20,
    ):
        self.ma_window = ma_window
        self.volume_rank_threshold = volume_rank_threshold
        self.rolling_rank_window = rolling_rank_window

    def build_index_nav(self, close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
        """Calculate the underlying index NAV (default: small-cap)."""
        ret = close.pct_change(fill_method=None).replace(
            [float("inf"), float("-inf")], float("nan")
        )
        small_mask = (
            amount.rolling(self.rolling_rank_window)
            .mean()
            .rank(axis=1, pct=True)
            < self.volume_rank_threshold
        )
        small_idx = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
        return (1.0 + small_idx.fillna(0.0)).cumprod()

    def exposure_series(self, close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
        """Generate binary exposure series (T-1 signal applied to T)."""
        nav = self.build_index_nav(close, amount)
        ma_line = nav.rolling(self.ma_window).mean()
        return (nav > ma_line).shift(1, fill_value=False).astype(float)

    def band_exposure_series(
        self,
        close: pd.DataFrame,
        amount: pd.DataFrame,
        multiplier: float = 8.0,
        max_exposure: float = 1.5,
    ) -> pd.Series:
        """Generate dynamic band exposure series (clamped [0, max_exposure])."""
        nav = self.build_index_nav(close, amount)
        ma_line = nav.rolling(self.ma_window).mean()
        dist = nav / ma_line - 1.0
        exposure = (1.0 + dist * multiplier) * (dist > 0)
        return exposure.clip(0.0, max_exposure).shift(1, fill_value=0.0).fillna(0.0)

    def signal(
        self,
        target_date,
        close: pd.DataFrame,
        amount: pd.DataFrame,
        mode: str = "binary",
        multiplier: float = 8.0,
        max_exposure: float = 1.5,
    ) -> float:
        """Generate exposure signal for the trading day after target_date."""
        nav = self.build_index_nav(close, amount)
        ma_line = nav.rolling(self.ma_window).mean()
        dt = pd.Timestamp(target_date).normalize()
        if dt not in nav.index:
            return 1.0 if mode == "binary" else 0.0

        if mode == "binary":
            return 1.0 if nav.loc[dt] > ma_line.loc[dt] else 0.0
        else:
            dist = nav.loc[dt] / ma_line.loc[dt] - 1.0
            if dist <= 0:
                return 0.0
            exposure = 1.0 + dist * multiplier
            return float(np.clip(exposure, 0.0, max_exposure))

    def audit_parameters(
        self,
        close: pd.DataFrame,
        amount: pd.DataFrame,
        windows: list[int] | None = None,
        bond_returns: pd.Series | None = None,
    ) -> dict:
        """Audit the parameter space of the moving average window.

        Calculates the returns of the timed index, Sharpe ratios, and DSR.
        """
        if windows is None:
            windows = list(range(5, 61))  # default search grid: MA5 to MA60

        nav = self.build_index_nav(close, amount)
        idx_ret = nav.pct_change(fill_method=None).fillna(0.0)

        results = []
        best_sr = float("-inf")
        best_window = 16

        for w in windows:
            ma = nav.rolling(w).mean()
            # shifted timing: signal at T-1 applied to T
            in_mkt = (nav > ma).shift(1, fill_value=False).astype(float)

            if bond_returns is not None:
                # BEAR regime: earn bond returns; BULL regime: earn stock index returns
                aligned_bond = bond_returns.reindex(idx_ret.index).fillna(0.0)
                strategy_ret = idx_ret * in_mkt + aligned_bond * (1.0 - in_mkt)
            else:
                strategy_ret = idx_ret * in_mkt

            ann_ret = float(strategy_ret.mean() * 252)
            ann_vol = float(strategy_ret.std() * np.sqrt(252))
            sr = ann_ret / ann_vol if ann_vol > 0 else 0.0

            results.append({
                "window": w,
                "annual_return": ann_ret,
                "annual_volatility": ann_vol,
                "sharpe_ratio": sr,
                "max_drawdown": float((strategy_ret.cumsum() - strategy_ret.cumsum().cummax()).min())
            })

            if sr > best_sr:
                best_sr = sr
                best_window = w

        # Calculate observed metrics of the selected window (self.ma_window)
        # Select the returns of our chosen parameter (self.ma_window)
        chosen_ma = nav.rolling(self.ma_window).mean()
        chosen_in_mkt = (nav > chosen_ma).shift(1, fill_value=False).astype(float)
        if bond_returns is not None:
            aligned_bond = bond_returns.reindex(idx_ret.index).fillna(0.0)
            chosen_ret = idx_ret * chosen_in_mkt + aligned_bond * (1.0 - chosen_in_mkt)
        else:
            chosen_ret = idx_ret * chosen_in_mkt

        chosen_ret = chosen_ret.dropna()
        n_periods = len(chosen_ret)
        skew = float(chosen_ret.skew()) if n_periods > 2 else 0.0
        kurt = float(chosen_ret.kurtosis() + 3.0) if n_periods > 2 else 3.0  # excess to absolute

        # Audit using Lopez de Prado DSR
        observed_sr = float(chosen_ret.mean() * 252 / (chosen_ret.std() * np.sqrt(252) + 1e-9))
        n_trials = len(windows)

        dsr_report = deflated_sharpe(
            observed_sr=observed_sr,
            n_trials=n_trials,
            n_periods=n_periods,
            skew=skew,
            kurt=kurt,
            annualized=True,
        )

        return {
            "chosen_window": self.ma_window,
            "chosen_sharpe": observed_sr,
            "best_window": best_window,
            "best_sharpe": best_sr,
            "n_trials": n_trials,
            "dsr_p_value": dsr_report["p_value"],
            "dsr_significant": dsr_report["significant_05"],
            "expected_max_sr": dsr_report["e_max_sr"],
            "results": results,
        }
