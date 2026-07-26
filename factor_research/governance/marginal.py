"""容量/边际贡献感知的适应度(LOOP_ENGINEERING.md §5.3)。

若适应度只看单腿夏普,loop 会进化出 N 个同质变体(illiq+size 相关 0.82=同一赌注)。
边际真 alpha = 候选收益对在册组合残差化后的夏普——残差弱 = 冗余,不该入册。

口径全透明:只用日收益。在册组合用逆波动率合成。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REDUNDANT_CORR = 0.7      # 与 book 相关高于此
WEAK_RESID_SHARPE = 0.3   # 且残差夏普低于此 → 判冗余
GOOD_RESID_SHARPE = 0.5   # 残差夏普高于此 → 有边际 alpha


def _inv_vol_book(book_rets: dict[str, pd.Series]) -> pd.Series:
    df = pd.DataFrame(book_rets).dropna()
    if df.empty:
        return pd.Series(dtype="float64")
    iv = 1.0 / df.std()
    w = iv / iv.sum()
    return (df * w).sum(axis=1)


def marginal_alpha(candidate_ret: pd.Series, book_rets: dict[str, pd.Series]) -> dict:
    """候选对当前在册组合的边际真 alpha。

    candidate_ret:候选(裸因子 L0 建议)日收益。
    book_rets:在册腿 name->日收益(空 dict = 无在册,候选即全部边际)。
    返回 beta/corr/残差夏普年化 + 边际判决。
    """
    from engine.metrics import metrics
    c = pd.Series(candidate_ret).dropna()
    if not book_rets:
        m = metrics(c)
        return {"corr_to_book": 0.0, "beta": 0.0,
                "residual_sharpe": round(m["sharpe"], 3), "residual_annual": round(m["annual"], 4),
                "marginal_verdict": "首腿(无在册组合,全部边际)"}

    book = _inv_vol_book(book_rets)
    common = c.index.intersection(book.index)
    if len(common) < 100:
        return {"marginal_verdict": "样本不足", "n": len(common)}
    cc, bb = c.reindex(common), book.reindex(common)
    corr = float(cc.corr(bb))
    var_b = float(bb.var())
    beta = float(np.cov(cc, bb)[0, 1] / var_b) if var_b > 0 else 0.0
    resid = cc - beta * bb        # 残差 = 候选去掉对 book 的暴露
    m = metrics(resid)
    rs = round(m["sharpe"], 3)

    if abs(corr) >= REDUNDANT_CORR and rs < WEAK_RESID_SHARPE:
        verdict = "冗余(与在册组合同质,边际夏普弱)"
    elif rs >= GOOD_RESID_SHARPE:
        verdict = "✅有边际alpha(去 book 暴露后仍赚钱)"
    else:
        verdict = "边际弱(夏普0.3~0.5)"
    return {"corr_to_book": round(corr, 3), "beta": round(beta, 3),
            "residual_sharpe": rs, "residual_annual": round(m["annual"], 4),
            "marginal_verdict": verdict}
