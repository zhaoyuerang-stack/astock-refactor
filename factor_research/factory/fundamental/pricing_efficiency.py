"""Pricing Efficiency & Valuation-Lag Model (预期差与估值滞后量化模型)

评估"基本面是否已被股价提前反应":
1. 市场已反应分 ∈ [0,1]:估值历史分位(PE/PB)+ 近 20/60 日动量拥挤度 + 分析师上修比例。
2. 预期差 PricingGap = 基本面景气分 − 市场已反应分。
   > +0.20 → 传导滞后/低估(LAGGED_OPPORTUNITY);< −0.20 → 提前透支(PRICED_IN_RISK);否则合理。

纯函数引擎:接受 MarketPricingProfile + 基本面分,不加载数据、不调 LLM。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class PricingState(Enum):
    LAGGED_OPPORTUNITY = "lagged_opportunity"   # 传导滞后,潜在低估
    PRICED_IN_RISK = "priced_in_risk"           # 提前反应,透支风险
    FAIRLY_PRICED = "fairly_priced"             # 定价合理


@dataclass(frozen=True)
class MarketPricingProfile:
    """个股市场交易与估值快照。缺失项用 None。"""
    code: str
    name: str = ""
    pe_percentile: float | None = None       # PE 历史分位 (0-1)
    pb_percentile: float | None = None       # PB 历史分位 (0-1)
    return_20d: float | None = None          # 近 20 日收益率
    return_60d: float | None = None          # 近 60 日收益率
    analyst_revision_ratio: float | None = None  # 近 30 日分析师上修比例 (0-1);无数据则 None


class PricingGapEstimator:
    """预期差评估器。"""

    def market_reaction_score(self, mp: MarketPricingProfile) -> float | None:
        """股价对基本面的"已反应程度" ∈ [0,1]:估值分位 + 动量拥挤 + 分析师上修;
        按可得要素的权重归一化(缺的不拿 0 充数)。"""
        parts: list[tuple[float, float]] = []
        pcts = [p for p in (mp.pe_percentile, mp.pb_percentile) if p is not None]
        if pcts:
            parts.append((sum(pcts) / len(pcts), 0.4))
        if mp.return_20d is not None or mp.return_60d is not None:
            r20 = mp.return_20d if mp.return_20d is not None else 0.0
            r60 = mp.return_60d if mp.return_60d is not None else 0.0
            avg_ret = r20 * 0.6 + r60 * 0.4
            parts.append((1.0 / (1.0 + math.exp(-10.0 * (avg_ret - 0.15))), 0.4))  # 15% 涨幅为中点
        if mp.analyst_revision_ratio is not None:
            parts.append((mp.analyst_revision_ratio, 0.2))
        if not parts:
            return None
        wsum = sum(w for _, w in parts)
        return float(sum(s * w for s, w in parts) / wsum)

    def pricing_gap(self, fundamental_score: float | None, mp: MarketPricingProfile,
                    band: float = 0.20) -> tuple[float | None, PricingState | None]:
        reaction = self.market_reaction_score(mp)
        if fundamental_score is None or reaction is None:
            return None, None
        gap = fundamental_score - reaction
        if gap > band:
            state = PricingState.LAGGED_OPPORTUNITY
        elif gap < -band:
            state = PricingState.PRICED_IN_RISK
        else:
            state = PricingState.FAIRLY_PRICED
        return gap, state

    def assess(self, fundamental_score: float | None, mp: MarketPricingProfile) -> dict:
        gap, state = self.pricing_gap(fundamental_score, mp)
        return {
            "code": mp.code,
            "name": mp.name,
            "reaction_score": self.market_reaction_score(mp),
            "fundamental_score": fundamental_score,
            "pricing_gap": gap,
            "pricing_state": state.value if state else None,
        }
