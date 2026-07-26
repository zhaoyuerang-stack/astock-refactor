"""Mutual Information Auditor — 信息熵驱动的 candidate 评估。

用户洞察 (2026-06-07): small_nav 实验失败本质是数学定理 —— dist 是 small_nav
对 timing 决策的充分统计量,nav 的条件互信息 ≈ 0。

把这个洞察工程化:
  · 给定 existing signals + 一个 candidate signal
  · 算 candidate 对 PnL 的"条件互信息"(给定 existing)
  · 条件 MI ≈ 0 → candidate 无新信息,关闭分支(避免做无意义回测)
  · 条件 MI 高 → 有新信息,值得跑回测

这是工厂前置过滤器: 几秒内排除冗余候选,只跑真正可能新增信息的。

实现使用离散化 + sklearn mutual_info_score (经典 plug-in estimator)。
对超高维或非线性,后续可换 kernel-based MI / KSG estimator。
"""
import numpy as np
import pandas as pd
from sklearn.metrics import mutual_info_score


def _discretize(s: pd.Series, n_bins: int = 10) -> np.ndarray:
    """Quantile-based discretization. Robust to outliers."""
    s = s.dropna()
    if len(s) == 0:
        return np.array([])
    if s.std() < 1e-12:
        return np.zeros(len(s), dtype=int)
    bins = pd.qcut(s.rank(method="first"), q=n_bins, labels=False, duplicates="drop")
    return bins.astype(int).values


def mi(x: pd.Series, y: pd.Series, n_bins: int = 10) -> float:
    """Mutual information between two series (bits)."""
    common = x.dropna().index.intersection(y.dropna().index)
    if len(common) < 50:
        return 0.0
    x_d = _discretize(x.loc[common], n_bins)
    y_d = _discretize(y.loc[common], n_bins)
    return float(mutual_info_score(x_d, y_d) / np.log(2))


def conditional_mi(
    candidate: pd.Series,
    target: pd.Series,
    given: list[pd.Series],
    n_bins: int = 8,
) -> float:
    """Conditional MI: I(candidate; target | given_signals).

    Approximation:
      I(C; T | G) ≈ I(C, G; T) - I(G; T)

    给定 candidate 和 existing,看 candidate 加进去后对 target 的 MI 增量。
    """
    if not given:
        return mi(candidate, target, n_bins)

    common = candidate.dropna().index.intersection(target.dropna().index)
    for g in given:
        common = common.intersection(g.dropna().index)
    if len(common) < 100:
        return 0.0

    # Discretize all
    cand_d = _discretize(candidate.loc[common], n_bins)
    tgt_d = _discretize(target.loc[common], n_bins)
    given_ds = [_discretize(g.loc[common], n_bins) for g in given]

    # Joint codes
    def joint_code(arrs):
        code = arrs[0].copy()
        m = n_bins
        for a in arrs[1:]:
            code = code * m + a
            # Keep manageable: cap bin product
            if code.max() > 10**6:
                # Re-bin
                code = _discretize(pd.Series(code), n_bins)
                m = n_bins
        return code

    mi_given_target = mutual_info_score(joint_code(given_ds), tgt_d) / np.log(2)
    mi_full_target = mutual_info_score(joint_code(given_ds + [cand_d]), tgt_d) / np.log(2)
    return float(max(0.0, mi_full_target - mi_given_target))


def audit_candidate(
    candidate_returns: pd.Series,
    target_returns: pd.Series,
    existing_returns: dict[str, pd.Series],
    n_bins: int = 8,
) -> dict:
    """完整审计一个候选 returns 序列。

    Returns:
        {
            "mi_alone": 单独 MI 与 target,
            "mi_max_existing": 与现有 LIVE 最大 MI (冗余度),
            "conditional_mi": 给定 existing 后的 MI (真实增量),
            "redundancy_ratio": mi_max_existing / mi_alone (越高越冗余),
            "verdict": "VALUABLE" | "REDUNDANT" | "WEAK"
        }
    """
    mi_alone = mi(candidate_returns, target_returns, n_bins)

    if not existing_returns:
        cond_mi = mi_alone
        max_existing_mi = 0.0
    else:
        # MI candidate vs each existing
        existing_mis = [mi(candidate_returns, e, n_bins) for e in existing_returns.values()]
        max_existing_mi = float(max(existing_mis)) if existing_mis else 0.0

        cond_mi = conditional_mi(
            candidate_returns, target_returns,
            list(existing_returns.values()), n_bins
        )

    redundancy = max_existing_mi / (mi_alone + 1e-9)

    if mi_alone < 0.05:
        verdict = "WEAK"           # 信号本身太弱
    elif cond_mi < 0.02:
        verdict = "REDUNDANT"      # 被 existing 覆盖
    else:
        verdict = "VALUABLE"

    return {
        "mi_alone": round(mi_alone, 4),
        "mi_max_existing": round(max_existing_mi, 4),
        "conditional_mi": round(cond_mi, 4),
        "redundancy_ratio": round(redundancy, 3),
        "verdict": verdict,
    }
