"""Market regime classification.

5 regimes based on volatility, returns, and momentum direction:
  BULL          — positive returns, moderate vol
  BEAR          — negative returns, moderate vol
  CHOP          — low vol, flat returns
  PANIC         — high vol + negative momentum (crash)
  UPSIDE_CRISIS — high vol + positive momentum (2024-2025 small-cap mania)

Usage:
  >>> from portfolio.regime import classify
  >>> regimes = classify(returns, vol_lookback=20, ret_lookback=60)
"""
import numpy as np
import pandas as pd


def classify(
    returns: pd.Series,
    vol_lookback: int = 20,
    ret_lookback: int = 60,
    vol_percentile: float = 0.80,
) -> pd.Series:
    """Classify each day into one of 5 regimes.

    Args:
        returns: daily market/index returns
        vol_lookback: window for realized volatility
        ret_lookback: window for cumulative return (momentum direction)
        vol_percentile: threshold for "high vol" regime

    Returns:
        pd.Series with values: 'bull', 'bear', 'chop', 'panic', 'upside_crisis'
    """
    r = returns.dropna()
    if len(r) < vol_lookback:
        return pd.Series("chop", index=r.index)

    # Rolling metrics
    roll_vol = r.rolling(vol_lookback, min_periods=10).std() * np.sqrt(252)
    roll_ret = r.rolling(ret_lookback, min_periods=20).mean() * 252  # annualized

    # High vol threshold (rolling percentile, adaptive)
    vol_thresh = roll_vol.rolling(504, min_periods=252).quantile(vol_percentile)

    is_high_vol = roll_vol > vol_thresh

    # Within high vol: direction from momentum
    mom_direction = np.sign(roll_ret)

    # Within low vol: classify by return level
    ret_thresh_bull = 0.10   # >10% annual = bull
    ret_thresh_bear = -0.10  # <-10% annual = bear

    regimes = pd.Series("chop", index=r.index)
    regimes[is_high_vol & (mom_direction > 0)] = "upside_crisis"
    regimes[is_high_vol & (mom_direction <= 0)] = "panic"
    regimes[~is_high_vol & (roll_ret > ret_thresh_bull)] = "bull"
    regimes[~is_high_vol & (roll_ret < ret_thresh_bear)] = "bear"

    return regimes


def regime_stats(returns: pd.Series, regimes: pd.Series) -> pd.DataFrame:
    """Compute annualized return and % of days for each regime."""
    common_idx = returns.dropna().index.intersection(regimes.dropna().index)
    r = returns.loc[common_idx]
    reg = regimes.loc[common_idx]

    rows = []
    for label in ["bull", "bear", "chop", "panic", "upside_crisis"]:
        mask = reg == label
        if mask.sum() < 10:
            rows.append({"regime": label, "pct_days": 0.0, "annual": 0.0,
                         "vol": 0.0, "sharpe": 0.0, "n_days": 0})
            continue
        sub = r[mask]
        ann = float(sub.mean() * 252)
        vol = float(sub.std() * np.sqrt(252))
        sh = ann / vol if vol > 0 else 0.0
        rows.append({"regime": label, "pct_days": mask.mean(), "annual": ann,
                     "vol": vol, "sharpe": sh, "n_days": mask.sum()})
    return pd.DataFrame(rows).set_index("regime")


def defensive_grade(
    candidate_returns: pd.Series,
    bear_regime_mask: pd.Series,
    existing_live_returns: dict[str, pd.Series],
    relative_improvement: float = 0.02,
    corr_threshold: float = 0.5,
) -> dict:
    """Grade a candidate for DEFENSIVE status.

    A candidate qualifies as DEFENSIVE if:
      1. During BEAR regime, its annualized return is at least `relative_improvement`
         (2pp) better than the average LIVE strategy in bear markets.
         (Absolute threshold abandoned: in A-shares, everything loses in bear markets.
          The value is losing LESS, not making money.)
      2. Average correlation to existing LIVE strategies < corr_threshold (0.5)

    Returns dict with grade info.
    """
    # Bear regime performance — candidate
    common = candidate_returns.dropna().index.intersection(bear_regime_mask.dropna().index)
    bear_mask = bear_regime_mask.loc[common]
    bear_sub = candidate_returns.loc[common][bear_mask]

    if len(bear_sub) < 20:
        return {"grade": "INSUFFICIENT_DATA", "bear_annual": 0,
                "baseline_bear_annual": 0, "improvement": 0, "bear_n_days": len(bear_sub)}

    bear_ann = float(bear_sub.mean() * 252)

    # Bear regime performance — LIVE baseline (average)
    live_bear_annuals = []
    for _, live_r in existing_live_returns.items():
        common_l = live_r.dropna().index.intersection(bear_regime_mask.dropna().index)
        if len(common_l) < 20: continue
        l_bear = live_r.loc[common_l][bear_regime_mask.loc[common_l]]
        if len(l_bear) > 20:
            live_bear_annuals.append(float(l_bear.mean() * 252))
    baseline_bear = float(np.mean(live_bear_annuals)) if live_bear_annuals else bear_ann

    improvement = bear_ann - baseline_bear  # positive = candidate loses LESS

    # Correlation to existing LIVE
    corrs = []
    for _, live_r in existing_live_returns.items():
        common_idx = candidate_returns.dropna().index.intersection(live_r.dropna().index)
        if len(common_idx) > 100:
            corr = candidate_returns.loc[common_idx].corr(live_r.loc[common_idx])
            corrs.append(corr)
    avg_corr = float(np.mean(corrs)) if corrs else 1.0

    bear_ok = improvement > relative_improvement
    corr_ok = avg_corr < corr_threshold

    if bear_ok and corr_ok:
        grade = "LIVE_D"
    elif bear_ok:
        grade = "DEFENSIVE_CANDIDATE (corr too high)"
    elif corr_ok and improvement > 0.01:
        grade = "LOW_CORR (bear improvement marginal)"
    elif corr_ok:
        grade = "LOW_CORR (no bear advantage)"
    else:
        grade = "NO_DEFENSIVE_VALUE"

    return {
        "grade": grade,
        "bear_annual": bear_ann,
        "baseline_bear_annual": baseline_bear,
        "improvement": improvement,
        "bear_n_days": len(bear_sub),
        "avg_corr_to_live": avg_corr,
        "bear_ok": bear_ok,
        "corr_ok": corr_ok,
    }


def calculate_regime_confidence(
    returns: pd.Series,
    regimes: pd.Series,
    window: int = 20
) -> pd.Series:
    """Calculate the confidence score (0 to 1) for the current market regime.

    Uses rolling regime persistence over a window. If the regime is flipping frequently,
    confidence is low.
    """
    # Persistence: fraction of last N days where regime was same as yesterday
    same_reg = (regimes == regimes.shift(1)).astype(float)
    persistence = same_reg.rolling(window, min_periods=5).mean()
    return persistence.fillna(0.5).clip(0.0, 1.0)


class PolicyEngine:
    """Institutional Risk Budget Policy Engine.

    Adjusts risk boundaries and leverage maximums depending on regime and confidence.
    """
    def __init__(self, default_max_exposure: float = 1.25):
        self.default_max_exposure = default_max_exposure

    def get_max_exposure(self, regime: str, confidence: float) -> float:
        """Determine max allowable equity exposure."""
        r = regime.lower()
        if r in ["bear", "panic"]:
            if confidence > 0.8:
                return 0.30
            elif confidence > 0.5:
                return 0.60
            else:
                return 0.90
        elif r == "chop":
            if confidence > 0.7:
                return 0.80
            return 1.00
        return self.default_max_exposure
