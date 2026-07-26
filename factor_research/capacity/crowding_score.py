"""Factor Crowding Scores.

Evaluates crowding indicators: institutional ownership clustering,
turnover spikes, and extreme pairwise stock correlations.

strategy_pool_crowding(孤岛回收,ADR-034 后续):把在册策略池当作等权"组合",
复用 calculate_crowding_score 给衰减监控补「拥挤」归因维度——拥挤是 §7 失效模式
与退役归因(§7.4)的正式字段,但此前 decay 报告给不出该维度(本模块建成后零调用方)。
"""
from __future__ import annotations

import pandas as pd


def calculate_crowding_score(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    window: int = 20
) -> pd.Series:
    """Calculate the crowding score of a portfolio over time.

    Method: average pairwise correlation of active holdings weighted by portfolio holdings.
    Higher correlation indicates crowded positions that may suffer from simultaneous liquidation.
    """
    common_idx = weights.index.intersection(returns.index)
    if len(common_idx) < window:
        return pd.Series(0.0, index=weights.index)

    crowding_scores = []
    
    # Calculate rolling pairwise correlation
    for i in range(len(common_idx)):
        if i < window:
            crowding_scores.append(0.0)
            continue
            
        date = common_idx[i]
        w = weights.loc[date]
        active_assets = w[w > 0.0001].index
        
        if len(active_assets) < 2:
            crowding_scores.append(0.0)
            continue

        hist_rets = returns.loc[common_idx[i - window]:date, active_assets]
        corr_matrix = hist_rets.corr().fillna(0.0)
        
        # Weighted average correlation
        w_active = w.loc[active_assets]
        w_active = w_active / w_active.sum()
        
        # Calculate sum(w_i * w_j * corr_ij) excluding diagonal
        score = 0.0
        for asset_i in active_assets:
            for asset_j in active_assets:
                if asset_i != asset_j:
                    score += w_active[asset_i] * w_active[asset_j] * corr_matrix.loc[asset_i, asset_j]
                    
        crowding_scores.append(score)

    return pd.Series(crowding_scores, index=common_idx)


def strategy_pool_crowding(leg_returns: dict[str, pd.Series], *, window: int = 60) -> dict:
    """在册策略池拥挤度(衰减归因的「拥挤」维度,披露非判定)。

    池级:把策略池当等权"组合",复用 calculate_crowding_score(加权两两相关)取最新值;
    逐腿:trailing window 内该腿对其余腿等权组合的相关,|corr|≥阈值标 crowded
    (阈值单一来源 = governance.marginal.REDUNDANT_CORR,与边际冗余判据同口径)。
    诚实拒判:<2 条腿 / 共同样本 < window → computable=False,不给 0 分假绿。
    退役裁决仍归 decay_check + workflow 人工(本函数只补归因维度,§7.4)。
    """
    from governance.marginal import REDUNDANT_CORR

    names = sorted(leg_returns)
    if len(names) < 2:
        return {"computable": False,
                "reason": f"池内 {len(names)} 条腿,无拥挤可言(拥挤是策略间现象)"}
    df = pd.DataFrame({n: pd.Series(leg_returns[n]).dropna() for n in names}).dropna()
    if len(df) < window:
        return {"computable": False,
                "reason": f"共同样本 {len(df)} < window {window},不做拥挤结论"}

    weights = pd.DataFrame(1.0 / len(names), index=df.index, columns=names)
    pool_series = calculate_crowding_score(weights, df, window=window)
    tail = df.tail(window)
    corr_mat = tail.corr()
    per_leg = {}
    for n in names:
        others = tail.drop(columns=[n]).mean(axis=1)
        corr_pool = float(tail[n].corr(others))
        # crowded 判据用**最大两两相关**而非对池均值相关:对抗测试实证后者会被
        # 正交腿稀释漏检双胞胎(3 腿池中 corr≈0.99 的孪生对池均值仅 0.64);
        # 两两口径还能点名"和谁拥挤",归因才可执行。
        pair = corr_mat[n].drop(n).abs()
        max_with = str(pair.idxmax())
        max_corr = float(corr_mat.loc[n, max_with])
        per_leg[n] = {
            "corr_to_pool": round(corr_pool, 3),
            "max_pair_corr": round(max_corr, 3),
            "max_pair_with": max_with,
            "crowded": bool(abs(max_corr) >= REDUNDANT_CORR),
        }
    return {
        "computable": True,
        "window": window,
        "threshold": REDUNDANT_CORR,
        "pool_crowding_latest": round(float(pool_series.iloc[-1]), 3),
        "per_leg": per_leg,
        "note": "披露非判定:拥挤是衰减/退役归因维度(§7 失效模式),裁决仍归 decay_check+workflow",
    }
