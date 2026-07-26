"""Alpha / Overlay 分账(根因分析 #4)。

历史:系统优化「因子 + MA16 + veto + 杠杆」的头条绩效,把择时/风控 overlay 误记成新 alpha
(large-cap illiq 裸因子夏普 −1.04,加 MA16 变 +1.24,被当 alpha)。本模块从结构上分离:
  - bare_alpha = 裸因子(L0,去 overlay)绩效 → **唯一的 alpha 记账**
  - overlay_contribution = full − bare 的差额 → **只记风险贡献**,绝不记为 alpha

判旗:裸因子夏普≈0/反向但完整夏普高 = overlay 在制造 alpha(造假模式)→ 拒。
"""
from __future__ import annotations

import pandas as pd

REAL_ALPHA_SHARPE = 0.8     # 裸因子夏普 ≥ 此 = 真 alpha
MANUFACTURE_SHARPE = 0.3    # 裸因子夏普 < 此但完整高 = overlay 在造 alpha


def split_alpha_overlay(bare_ret: pd.Series, full_ret: pd.Series) -> dict:
    """分离裸因子 alpha 与 overlay 风险贡献。

    bare_ret:L0 裸因子(无择时/杠杆/veto)日收益。
    full_ret:完整策略(含 overlay)日收益。
    """
    from engine.metrics import metrics
    mb, mf = metrics(pd.Series(bare_ret).dropna()), metrics(pd.Series(full_ret).dropna())

    sharpe_delta = round(mf["sharpe"] - mb["sharpe"], 3)
    dd_improvement = round(abs(mb["maxdd"]) - abs(mf["maxdd"]), 4)  # 正 = overlay 收窄回撤
    annual_delta = round(mf["annual"] - mb["annual"], 4)

    bare_is_alpha = mb["sharpe"] >= REAL_ALPHA_SHARPE
    overlay_manufactures = (mb["sharpe"] < MANUFACTURE_SHARPE) and (mf["sharpe"] >= REAL_ALPHA_SHARPE)

    if overlay_manufactures:
        role = "⚠️ALPHA来源(造假风险:裸因子≈0/反向,收益全来自overlay)"
        verdict = "拒:overlay 制造 alpha,非真因子"
    elif bare_is_alpha:
        role = "风控(合法:裸因子自身即真 alpha,overlay 只控回撤)"
        verdict = "✅真 alpha + 合法 overlay"
    else:
        role = "风控(裸因子弱,边际 alpha)"
        verdict = "边际:裸因子夏普 0.3~0.8"

    return {
        "bare_alpha": {k: round(mb[k], 4) for k in ("annual", "sharpe", "maxdd")},
        "overlay_contribution": {
            "sharpe_delta": sharpe_delta, "dd_improvement": dd_improvement,
            "annual_delta": annual_delta, "role": role,
        },
        "bare_is_real_alpha": bool(bare_is_alpha),
        "overlay_manufactures_alpha": bool(overlay_manufactures),
        "verdict": verdict,
    }
