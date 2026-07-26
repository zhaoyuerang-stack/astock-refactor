"""Regime-aware marginal contribution evaluation.

Upgrades the previous "full-sample Sharpe delta" to a regime-weighted score
that can identify defensive assets (bear protection > chop contribution).

Grade system:
  LIVE_P  (Pillar)     — delta_regime > 0.20, all regimes positive
  LIVE_K  (Core)       — delta_regime > 0.10, bear positive
  LIVE_C  (Complement) — delta_regime > 0.05, some regime advantage
  LIVE_D  (Defensive)  — bear annual > -10%, low corr to LIVE, any full-sample Sharpe
  SHELVE               — no positive marginal contribution

Usage:
  >>> from portfolio.marginal import evaluate
  >>> report = evaluate(candidate_returns, existing_live, market_returns)
"""
import numpy as np
import pandas as pd

from portfolio.composer import metrics as port_metrics
from portfolio.regime import classify, defensive_grade


def evaluate(
    candidate_returns: pd.Series,
    candidate_name: str,
    existing_live: dict[str, pd.Series],
    market_returns: pd.Series,
) -> dict:
    """Regime-aware marginal evaluation of a candidate strategy.

    Args:
        candidate_returns: daily returns of the candidate
        candidate_name: strategy name
        existing_live: {name: daily_returns} of current LIVE strategies
        market_returns: market index daily returns (for regime classification)

    Returns:
        dict with grade, scores, regime breakdown, recommendation
    """
    # ── Regime classification ──
    regimes = classify(market_returns)
    regime_summary = _regime_summary(candidate_returns, regimes)

    # ── Build baseline portfolio (existing LIVE, equal weight) ──
    live_df = pd.DataFrame(existing_live).dropna()
    baseline_ret = live_df.mean(axis=1)  # equal weight
    baseline_m = port_metrics(baseline_ret)

    # ── Portfolio with candidate added (equal weight) ──
    combined = pd.DataFrame({**existing_live, candidate_name: candidate_returns}).dropna()
    combined_ret = combined.mean(axis=1)
    combined_m = port_metrics(combined_ret)

    # ── Full-sample deltas ──
    delta_sharpe = combined_m["sharpe"] - baseline_m["sharpe"]
    delta_annual = combined_m["annual"] - baseline_m["annual"]
    delta_maxdd = combined_m["maxdd"] - baseline_m["maxdd"]  # negative = improvement

    # ── Regime-weighted score ──
    # Weights: chop 0.4, bull 0.2, bear 0.25, panic 0.10, upside_crisis 0.05
    regime_weights = {"chop": 0.40, "bull": 0.20, "bear": 0.25,
                      "panic": 0.10, "upside_crisis": 0.05}

    regime_score = 0.0
    regime_details = {}
    for reg_label, weight in regime_weights.items():
        reg_mask = regimes == reg_label
        if reg_mask.sum() < 20:
            continue

        # Baseline in this regime
        base_reg = baseline_ret.loc[baseline_ret.index.intersection(regimes.index)][reg_mask]
        # Combined in this regime
        comb_reg = combined_ret.loc[combined_ret.index.intersection(regimes.index)][reg_mask]

        if len(base_reg) < 20:
            continue

        base_sharpe = _safe_sharpe(base_reg)
        comb_sharpe = _safe_sharpe(comb_reg)
        delta = comb_sharpe - base_sharpe
        regime_score += weight * delta
        regime_details[reg_label] = {
            "base_sharpe": base_sharpe, "comb_sharpe": comb_sharpe,
            "delta": delta, "weight": weight, "n_days": len(base_reg),
        }

    # ── Defensive check ──
    # corr_threshold 0.55 → 0.60 (2026-06-07): 实测 factory 候选 ret_zscore_cross_n60
    # 和 mom_n_n60 熊市改善 +6pp 但 corr 0.59 卡住；放宽到 0.60 让这些真防御资产入档。
    # A 股长仓多因子 corr 物理下限 ~0.42，0.60 仍远低于 LIVE 内部 0.85+ 共动。
    bear_mask = regimes == "bear"
    def_grade = defensive_grade(candidate_returns, bear_mask, existing_live,
                                relative_improvement=0.015, corr_threshold=0.60)

    # ── Correlation to LIVE ──
    corrs = {}
    for name, live_r in existing_live.items():
        common = candidate_returns.dropna().index.intersection(live_r.dropna().index)
        if len(common) > 100:
            corrs[name] = float(candidate_returns.loc[common].corr(live_r.loc[common]))
    avg_corr = float(np.mean(list(corrs.values()))) if corrs else 1.0

    # ── Auto-grade ──
    grade = _auto_grade(delta_sharpe, regime_score, delta_maxdd, def_grade, avg_corr,
                        candidate_returns, baseline_ret, markets_returns=market_returns)

    return {
        "candidate": candidate_name,
        "grade": grade,
        "full_sample": {
            "delta_sharpe": delta_sharpe,
            "delta_annual": delta_annual,
            "delta_maxdd": delta_maxdd,
            "baseline_sharpe": baseline_m["sharpe"],
            "combined_sharpe": combined_m["sharpe"],
            "baseline_maxdd": baseline_m["maxdd"],
            "combined_maxdd": combined_m["maxdd"],
        },
        "regime_weighted_score": regime_score,
        "regime_details": regime_details,
        "defensive": def_grade,
        "correlation": {"avg_corr": avg_corr, "per_live": corrs},
        "regime_summary": regime_summary,
        "recommendation": _recommendation(grade, def_grade, avg_corr, delta_sharpe),
    }


def _auto_grade(delta_sharpe, regime_score, delta_maxdd, def_grade, avg_corr,
                candidate_ret=None, baseline_ret=None, markets_returns=None):
    """Determine LIVE grade based on all signals.

    Priority: DEFENSIVE first (it's the special case we're building for),
    then pillar/core/complement by regime score.
    """
    # DEFENSIVE: bear protection is the primary value (relative improvement)
    if def_grade["grade"] == "LIVE_D":
        if def_grade.get("bear_ok") and def_grade.get("corr_ok"):
            return "LIVE_D"

    # Check if there's any defensive value worth noting
    if def_grade["bear_ok"] and delta_maxdd < -0.02:
        # Bear protection + DD improvement → at least LIVE_C
        if regime_score > 0.20:
            return "LIVE_K"  # exceptional: both defensive AND high contribution
        return "LIVE_C"

    # Standard regime-weighted grading
    if regime_score > 0.20 and delta_sharpe > 0.10:
        return "LIVE_P"
    elif regime_score > 0.10:
        return "LIVE_K"
    elif regime_score > 0.03 or delta_sharpe > 0.03:
        return "LIVE_C"
    else:
        return "SHELVE"


def _recommendation(grade, def_grade, avg_corr, delta_sharpe) -> str:
    """Human-readable recommendation."""
    if grade == "LIVE_D":
        return (f"DEFENSIVE ASSET: bear protection (bear_ann={def_grade['bear_annual']:+.1%}). "
                f"Add 5-15% allocation to reduce portfolio maxdd during bear markets. "
                f"Full-sample Sharpe contribution may be low — this is expected for defensive assets.")
    elif grade == "LIVE_P":
        return "PILLAR: strong across all regimes. Max allocation 30%+."
    elif grade == "LIVE_K":
        return "CORE: solid contribution. Allocation 15-30%."
    elif grade == "LIVE_C":
        return "COMPLEMENT: niche value. Allocation 5-15%."
    else:
        if def_grade["bear_ok"] and avg_corr < 0.5:
            return (f"NEAR MISS: bear protection OK (bear_ann={def_grade['bear_annual']:+.1%}) "
                    f"but full-sample contribution too weak. Consider if portfolio DD is a concern.")
        return "No positive marginal contribution. Shelve for now."


def _regime_summary(returns: pd.Series, regimes: pd.Series) -> dict:
    """Compute annualized return in each regime."""
    common = returns.dropna().index.intersection(regimes.dropna().index)
    r = returns.loc[common]; reg = regimes.loc[common]
    out = {}
    for label in ["bull", "bear", "chop", "panic", "upside_crisis"]:
        mask = reg == label
        if mask.sum() < 10:
            out[label] = {"annual": 0.0, "n_days": mask.sum()}
        else:
            sub = r[mask]
            out[label] = {"annual": float(sub.mean() * 252), "n_days": mask.sum()}
    return out


def _safe_sharpe(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 20 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(252))
