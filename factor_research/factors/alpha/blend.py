"""FactorBlend — static and dynamic multi-factor combination.

Static:     Fixed weights, weighted sum of z-scored factor values.
Dynamic:    Weights updated periodically based on rolling IC / ICIR.
            Only factors with positive IC get weight (long-only bias).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from factors.alpha.base import Factor, FactorData


class FactorBlend(Factor):
    """Weighted combination of N factors.

    Parameters
    ----------
    factors : dict[Factor, float or None]
        Factor → initial weight.  ``None`` means "determine dynamically".
    method : str
        ``"static"`` — fixed weights (normalised from given values).
        ``"ic_weighted"`` — weights proportional to rolling IC mean.
        ``"icir_weighted"`` — weights proportional to rolling ICIR.
    lookback : int
        Rolling window in trading days for IC / ICIR calculation.
    rebalance : str
        Pandas offset alias for combination weight updates (e.g. ``"1M"``,
        ``"3M"``).  Independent of per-factor rebalance cadence.
    min_weight : float
        Minimum weight for any factor (floor, before re-normalisation).
    """

    def __init__(
        self,
        factors: dict[Factor, float | None],
        method: str = "static",
        lookback: int = 252,
        rebalance: str = "1M",
        min_weight: float = 0.0,
    ):
        self._factors = factors
        self._method = method
        self._lookback = lookback
        self._rebalance = rebalance
        self._min_weight = min_weight

        # Resolve alias
        _alias = {"ic": "ic_weighted", "icir": "icir_weighted"}
        self._method = _alias.get(method, method)

        if self._method not in ("static", "ic_weighted", "icir_weighted"):
            raise ValueError(f"Unknown blend method: {method!r}")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @property
    def factors(self) -> list[Factor]:
        return list(self._factors.keys())

    @property
    def weights(self) -> dict[Factor, float | None]:
        return dict(self._factors)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute(self, data: FactorData) -> pd.DataFrame:
        if self._method == "static":
            return self._compute_static(data)
        return self._compute_dynamic(data)

    def _compute_static(self, data: FactorData) -> pd.DataFrame:
        """Simple weighted sum of (pre-standardised) factor values."""
        # Normalise provided weights to sum to 1
        raw_weights = {f: w for f, w in self._factors.items() if w is not None}
        total = sum(raw_weights.values())
        if total == 0:
            raise ValueError("FactorBlend: no non-None static weights")

        normed = {f: w / total for f, w in raw_weights.items()}

        result = pd.DataFrame(0.0, index=data.trade_dates, columns=data.codes)
        for factor, weight in normed.items():
            values = factor.compute(data)
            # Standardise before blending (each factor on same scale)
            std = values.std(axis=1)
            mean = values.mean(axis=1)
            z = (values.sub(mean, axis=0).div(std + 1e-8, axis=0))
            result = result.add(z * weight, fill_value=0)
        return result

    def _compute_dynamic(self, data: FactorData) -> pd.DataFrame:
        """IC / ICIR-weighted dynamic combination.

        Algorithm
        ---------
        1. Compute factor values and forward 1d returns.
        2. At each rebalance date, compute rolling IC for each factor.
        3. Weight ∝ max(0, IC_mean)  [ic_weighted]
           Weight ∝ max(0, ICIR)     [icir_weighted]
        4. Z-score each factor cross-sectionally, blend with weights.
        """
        factors = list(self._factors.keys())
        if len(factors) < 2:
            # Single factor — no blending needed
            return factors[0].compute(data)

        # 1. Compute all factor values
        factor_values = {}
        for f in factors:
            factor_values[f] = f.compute(data)
        common_dates = factor_values[factors[0]].index
        common_codes = factor_values[factors[0]].columns

        # 2. Forward 1d return
        fwd_ret = data.close.pct_change().shift(-1)

        # 3. Determine IC rebalance dates
        #    Use the last trading day of each rebalance period
        rebalance_rule = self._rebalance
        rebalance_dates = pd.date_range(
            start=common_dates[0],
            end=common_dates[-1],
            freq=rebalance_rule,
        )
        # Map to nearest actual trading dates
        rd_list = []
        for rd in rebalance_dates:
            candidates = common_dates[common_dates >= rd]
            if len(candidates):
                rd_list.append(candidates[0])

        # 4. Build result day by day with periodically updated weights
        result = pd.DataFrame(0.0, index=common_dates, columns=common_codes)
        current_weights = {f: 1.0 / len(factors) for f in factors}  # initial equal
        next_rebalance_idx = 0

        for i, dt in enumerate(common_dates):
            # Check if we should update weights (using IC up to *previous* day
            # to avoid look-ahead)
            if (next_rebalance_idx < len(rd_list)
                    and dt >= rd_list[next_rebalance_idx]
                    and i >= self._lookback):
                # Compute rolling IC for each factor
                ic_means = {}
                icirs = {}
                lookback_start = max(0, i - self._lookback)
                for f in factors:
                    fv = factor_values[f]
                    ic_series = _rolling_ic(
                        fv.iloc[lookback_start:i],
                        fwd_ret.iloc[lookback_start:i],
                    )
                    ic_means[f] = ic_series.mean()
                    icirs[f] = (ic_series.mean() / ic_series.std()
                                if ic_series.std() > 0 else 0.0)

                # Build weights
                if self._method == "ic_weighted":
                    raw = {f: max(0.0, ic_means[f]) for f in factors}
                else:  # icir_weighted
                    raw = {f: max(0.0, icirs[f]) for f in factors}

                total = sum(raw.values())
                if total > 0:
                    current_weights = {f: raw[f] / total for f in factors}
                # else: keep previous weights

                next_rebalance_idx += 1

            # Blend factors with current weights
            row = pd.Series(0.0, index=common_codes)
            for f in factors:
                fv_row = factor_values[f].loc[dt].reindex(common_codes).fillna(0)
                # Standardise before blending
                z = (fv_row - fv_row.mean()) / (fv_row.std() + 1e-8)
                row = row + z * current_weights[f]
            result.loc[dt] = row

        return result


# ---------------------------------------------------------------------------
# IC helpers
# ---------------------------------------------------------------------------

def _rolling_ic(factor: pd.DataFrame, fwd_ret: pd.DataFrame) -> pd.Series:
    """Compute daily rank IC (Spearman) between factor and forward return."""
    dates = factor.index.intersection(fwd_ret.index)
    ics = {}
    for dt in dates:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            continue
        ic, _ = spearmanr(f[common].values, r[common].values)
        ics[dt] = ic
    return pd.Series(ics).sort_index()


def _ic_summary(ic: pd.Series) -> dict:
    """Summary stats for an IC series."""
    if len(ic) < 10:
        return {"IC_mean": np.nan, "ICIR": np.nan, "IC>0": np.nan}
    return {
        "IC_mean": float(ic.mean()),
        "IC_std": float(ic.std()),
        "ICIR": float(ic.mean() / ic.std()) if ic.std() > 0 else 0.0,
        "IC>0": float((ic > 0).mean()),
    }
