"""Walk-Forward validation with purge, Deflated Sharpe Ratio, and PBO.

Purged WF
    Rolling train/test windows with a purge gap to prevent information
    leakage from factors with lookback windows.

DSR (Deflated Sharpe Ratio)
    Corrects for multiple testing: after trying M strategies, what is the
    probability that the observed Sharpe is real vs luck?
    López de Prado & Bailey (2014).

PBO (Probability of Backtest Overfitting)
    CSCV-based: what fraction of random IS/OOS splits would rank the best
    IS strategy below median OOS?
    Bailey, Borwein, López de Prado, Zhu (2017).

Usage::

    from core.analysis.walk_forward import (
        walk_forward_windows, deflated_sharpe, pbo_cscv, wf_metrics
    )

    # 1. Define WF windows with purge
    windows = walk_forward_windows(
        dates, train_years=3, test_years=1, purge_days=80,
    )

    # 2. After running FactorSpace.evaluate(), check DSR
    dsr, p_value = deflated_sharpe(
        observed_sr=1.80, n_trials=12, n_periods=2043,
    )

    # 3. PBO via CSCV
    pbo = pbo_cscv(returns_dict, n_splits=100)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

# ---------------------------------------------------------------------------
# Purge + WF window generation
# ---------------------------------------------------------------------------

def purge_days(factor_windows: list[int], extra: int = 0) -> int:
    """Minimum purge gap to prevent overlap contamination.

    A factor with window W uses data from [t-W, t].  If the test period
    starts at T, training data up to T-1 would contain factor values that
    embed information from T-1-W to T-1.  To guarantee zero overlap, purge
    at least W + 1 days between train and test.

    Parameters
    ----------
    factor_windows : list[int]
        Lookback windows of all factors in the search space (in trading days).
    extra : int
        Additional safety margin (e.g. rebalance_freq).

    Returns
    -------
    int
        Minimum purge gap in trading days.
    """
    return max(factor_windows) + extra + 1 if factor_windows else extra + 1


def walk_forward_windows(
    dates: pd.DatetimeIndex,
    train_years: int = 3,
    test_years: int = 1,
    purge_days: int = 80,
    min_train_days: int = 500,
) -> list[dict]:
    """Generate purged walk-forward window pairs.

    Returns a list of {train_start, train_end, test_start, test_end} dicts.
    Each test period starts at least *purge_days* after train_end.
    """
    years = sorted(dates.year.unique())
    approx_train = pd.Timedelta(days=int(train_years * 252))
    purge = pd.Timedelta(days=purge_days)

    windows = []
    i = 0
    while i < len(years):
        # Test window: test_years starting from years[i]
        test_start_yr = years[i]
        test_end_yr = test_start_yr + test_years - 1

        if test_end_yr > years[-1]:
            break

        test_start = dates[dates.year == test_start_yr][0]
        test_end_candidates = dates[dates.year == test_end_yr]
        if len(test_end_candidates) == 0:
            test_end = dates[dates.year <= test_end_yr][-1]
        else:
            test_end = test_end_candidates[-1]

        # Train window: ends purge_days before test_start
        train_end_cutoff = test_start - purge
        train_start_cutoff = train_end_cutoff - approx_train

        # Use whatever data is available (handle edge case at start of series)
        train_candidates = dates[dates <= train_end_cutoff]
        if len(train_candidates) < min_train_days:
            i += 1
            continue

        train_end = train_candidates[-1]
        train_start_candidates = dates[(dates >= train_start_cutoff) & (dates <= train_end_cutoff)]
        if len(train_start_candidates) == 0:
            # Use earliest available data
            train_start = dates[0]
            if (train_end - train_start).days < min_train_days / 252 * 365:
                i += 1
                continue
        else:
            train_start = train_start_candidates[0]

        windows.append({
            "train_start": train_start,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
        })
        i += 1

    return windows


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio (DSR)
# ---------------------------------------------------------------------------

def deflated_sharpe(
    observed_sr: float,
    n_trials: int,
    n_periods: int,
    skew: float = 0.0,
    kurt: float = 3.0,
    annualized: bool = True,
) -> dict:
    """Compute Deflated Sharpe Ratio and p-value.

    DSR ≈ 0 → observed SR equals E[max SR | H0] (no edge).
    DSR ≫ 1 → observed SR significantly exceeds noise ceiling.
    p < 0.05 → statistically significant after M multiple trials.

    Formula from López de Prado & Bailey (2014), Theorem 1.

    Parameters
    ----------
    observed_sr : float
        Observed Sharpe ratio (annualized if annualized=True).
    n_trials : int
        Number of strategies / parameter combinations tested.
    n_periods : int
        Number of return observations (daily).
    skew : float
        Return skewness.
    kurt : float
        Return kurtosis (normal = 3.0).
    annualized : bool
        If True, *observed_sr* is annualized; we convert to the
        non-annualized test statistic internally.

    Returns
    -------
    dict
        {"dsr": float, "p_value": float, "e_max_sr": float,
         "observed_tstat": float, "significant_05": bool}
    """
    # Convert annualized SR to test statistic:  t = SR_ann * sqrt(T/252)
    freq = 252 if annualized else 1
    tstat = observed_sr * np.sqrt(n_periods / freq) if annualized else observed_sr

    # Expected maximum t-statistic under H0 for M trials
    # Var[t | H0] ≈ 1 + moment corrections
    excess = kurt - 3.0
    var_t = 1.0 + 0.5 * skew**2 + excess / 4.0
    std_t = np.sqrt(max(var_t, 1.0))

    e_max_t = _expected_max_normal(n_trials) * std_t

    # Deflated t-statistic
    t_deflated = tstat - e_max_t
    dsr = t_deflated / std_t if std_t > 0 else float("nan")
    p_value = 1.0 - norm.cdf(dsr) if not np.isnan(dsr) else 1.0

    # Convert e_max back to annualized for reporting
    e_max_annual = e_max_t / np.sqrt(n_periods / freq) if n_periods > 0 else 0.0

    return {
        "dsr": float(dsr),
        "p_value": float(p_value),
        "e_max_sr": float(e_max_annual),   # annualized, for display
        "e_max_t": float(e_max_t),          # test-statistic scale
        "observed_tstat": float(tstat),
        "significant_05": bool(p_value < 0.05),
    }


def _expected_max_normal(m: int) -> float:
    """E[max of m i.i.d. standard normals] — asymptotic + small-m correction."""
    if m <= 1:
        return 0.0
    if m <= 10:
        gamma = 0.5772156649015329
        return (1 - gamma) * norm.ppf(1 - 1.0 / m) + gamma * norm.ppf(
            1 - 1.0 / (m * np.e)
        )
    log_m = np.log(m)
    return np.sqrt(2 * log_m) - (np.log(log_m) + np.log(4 * np.pi)) / (
        2 * np.sqrt(2 * log_m)
    )


# ---------------------------------------------------------------------------
# PBO — Probability of Backtest Overfitting (CSCV)
# ---------------------------------------------------------------------------

def pbo_cscv(
    returns_dict: dict[str, pd.Series],
    n_splits: int = 100,
    seed: int = 42,
) -> dict:
    """Probability of Backtest Overfitting via Combinatorial Stratified CV.

    For each of *n_splits* random train/test splits:
    1. Randomly split the time series into IS and OOS halves (stratified).
    2. Compute Sharpe for each strategy in IS and OOS.
    3. Check if the best IS strategy underperforms OOS median.

    PBO = fraction of splits where best_IS < median_OOS.

    PBO < 0.10 → low overfitting risk.
    PBO > 0.30 → high overfitting risk.

    Parameters
    ----------
    returns_dict : dict[str, pd.Series]
        {strategy_name: daily_returns} for all M strategies.
    n_splits : int
        Number of random IS/OOS splits (default 100).
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    dict
        {"pbo": float, "n_splits": int, "n_strategies": int,
         "is_best_ranks": list, "risk_level": str}
    """
    rng = np.random.RandomState(seed)
    names = list(returns_dict.keys())
    M = len(names)
    if M < 2:
        return {"pbo": 0.0, "n_splits": n_splits, "n_strategies": M,
                "risk_level": "insufficient", "note": "Need ≥2 strategies"}

    # Align all returns to common index
    common_idx = returns_dict[names[0]].index
    for n in names[1:]:
        common_idx = common_idx.intersection(returns_dict[n].index)
    T = len(common_idx)
    if T < 100:
        return {"pbo": 1.0, "n_splits": n_splits, "n_strategies": M,
                "risk_level": "insufficient", "note": f"Only {T} observations"}

    half = T // 2
    overfit_count = 0
    is_best_ranks = []

    for _ in range(n_splits):
        # Randomly select half the observations as IS (stratified: keep order)
        start = rng.randint(0, T - half)
        is_idx = common_idx[start : start + half]
        oos_idx = common_idx.difference(is_idx)

        is_sharpes = {}
        oos_sharpes = {}
        for name in names:
            r = returns_dict[name].reindex(common_idx)
            r_is = r.loc[is_idx].dropna()
            r_oos = r.loc[oos_idx].dropna()
            if len(r_is) > 50:
                is_sharpes[name] = _annual_sharpe(r_is)
            if len(r_oos) > 50:
                oos_sharpes[name] = _annual_sharpe(r_oos)

        if len(is_sharpes) < 2 or len(oos_sharpes) < 2:
            continue

        # Rank in IS, find best
        is_ranked = sorted(is_sharpes.items(), key=lambda x: x[1], reverse=True)
        best_is_name = is_ranked[0][0]

        # Where does the best IS strategy rank in OOS?
        oos_ranked = sorted(oos_sharpes.items(), key=lambda x: x[1], reverse=True)
        oos_ranks = {name: i + 1 for i, (name, _) in enumerate(oos_ranked)}
        best_oos_rank = oos_ranks.get(best_is_name, M)
        is_best_ranks.append(best_oos_rank)

        # Median OOS Sharpe
        median_oos_sr = np.median([v for _, v in oos_ranked])
        best_is_oos_sr = oos_sharpes.get(best_is_name, -999)
        if best_is_oos_sr < median_oos_sr:
            overfit_count += 1

    pbo = overfit_count / n_splits if n_splits > 0 else 0.0

    if pbo < 0.10:
        risk = "low"
    elif pbo < 0.30:
        risk = "moderate"
    else:
        risk = "high"

    return {
        "pbo": float(pbo),
        "n_splits": n_splits,
        "n_strategies": M,
        "is_best_ranks": is_best_ranks,
        "risk_level": risk,
        "mean_oos_rank": float(np.mean(is_best_ranks)) if is_best_ranks else M,
    }


# ---------------------------------------------------------------------------
# WF metrics aggregation
# ---------------------------------------------------------------------------

@dataclass
class WFMetrics:
    """Aggregate metrics from multiple walk-forward OOS windows."""
    annual: float          # Mean annualized return
    sharpe: float          # Annualized Sharpe (OOS aggregate)
    maxdd: float           # Maximum drawdown
    calmar: float
    n_windows: int         # Number of WF windows
    n_positive: int        # Windows with positive return
    yearly: dict[int, float]  # Year → return
    window_returns: list[dict]  # Per-window detail

    @property
    def win_rate(self) -> float:
        return self.n_positive / self.n_windows if self.n_windows > 0 else 0.0

    def summary(self) -> str:
        return (
            f"annual={self.annual:+.1%} sharpe={self.sharpe:.2f} "
            f"maxdd={self.maxdd:.1%} calmar={self.calmar:.2f} "
            f"WF win={self.n_positive}/{self.n_windows}"
        )


def wf_metrics(
    oos_returns_list: list[pd.Series],
    eval_start: str = None,
) -> WFMetrics:
    """Aggregate metrics from a list of OOS window return series.

    Parameters
    ----------
    oos_returns_list : list[pd.Series]
        One daily return series per WF test window.
    eval_start : str, optional
        Overall eval start for yearly breakdown.

    Returns
    -------
    WFMetrics
    """
    if not oos_returns_list:
        return WFMetrics(annual=0, sharpe=0, maxdd=0, calmar=0,
                         n_windows=0, n_positive=0, yearly={},
                         window_returns=[])

    # Concatenate all OOS windows (gaps filled with NaN, which we drop)
    all_ret = pd.concat(oos_returns_list).sort_index().dropna()
    if eval_start:
        all_ret = all_ret.loc[eval_start:]

    ann = float(all_ret.mean() * 252)
    dd = float(((1 + all_ret).cumprod() / (1 + all_ret).cumprod().cummax() - 1).min())
    vol = float(all_ret.std() * np.sqrt(252))
    sh = ann / vol if vol > 0 else 0.0
    cal = ann / abs(dd) if dd < 0 else 0.0

    n_pos = sum(1 for r in oos_returns_list if r.sum() > 0)
    yearly = {}
    if len(all_ret) > 200:
        for y, g in all_ret.groupby(all_ret.index.year):
            yearly[y] = (1 + g).prod() - 1

    window_detail = []
    for i, r in enumerate(oos_returns_list):
        if len(r) < 10:
            continue
        w_ann = float(r.mean() * 252)
        w_dd = float(((1+r).cumprod()/(1+r).cumprod().cummax()-1).min())
        window_detail.append({
            "window": i,
            "start": str(r.index[0].date()),
            "end": str(r.index[-1].date()),
            "n_days": len(r),
            "annual": w_ann,
            "maxdd": w_dd,
        })

    return WFMetrics(
        annual=ann, sharpe=sh, maxdd=dd, calmar=cal,
        n_windows=len(oos_returns_list), n_positive=n_pos,
        yearly=yearly, window_returns=window_detail,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _annual_sharpe(returns: pd.Series) -> float:
    """Annualized Sharpe from daily returns."""
    if len(returns) < 20:
        return 0.0
    mean = returns.mean()
    std = returns.std()
    return float(mean / std * np.sqrt(252)) if std > 0 else 0.0
