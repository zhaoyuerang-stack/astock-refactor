"""Bargaining Power & Supply-Chain Core Status Model (产业链议价权量化模型)

用真实财务数据量化企业在上下游产业链中的"核心地位/议价权":
1. 商业信用议价权指数 BPI = (应付账款+应付票据 - 应收账款-应收票据) / 营业总成本
   正值越高 → 越能无偿占用上下游资金,议价权越强。
2. 现金循环周期 CCC = DSO(应收天数) + DIO(存货天数) - DPO(应付天数)
   越短(甚至为负)→ 产业链话语权越大。
3. 定价权综合分 ∈ [0,1]:融合毛利率 / BPI / CCC。

纯函数引擎:接受 FinancialProfile,不加载数据、不调 LLM。
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FinancialProfile:
    """个股财务快照(用于议价权计算)。缺失项用 None,引擎按"未数据"处理,绝不编造。"""
    code: str
    name: str = ""
    revenue: float | None = None         # 营业收入
    cost: float | None = None            # 营业总成本(oper_cost)
    ebit: float | None = None            # 息税前利润
    receivables: float | None = None     # 应收账款 + 应收票据
    payables: float | None = None        # 应付账款 + 应付票据
    inventory: float | None = None       # 存货余额
    gross_margin: float | None = None    # 毛利率(若直接给,优先于 ebit/revenue 推算)


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _days(numerator: float | None, base: float | None) -> float | None:
    """周转天数 = 余额 / 流量 × 365;流量非正或缺失 → None。"""
    if numerator is None or base is None or base <= 0:
        return None
    return numerator / base * 365.0


class BargainingPowerEstimator:
    """产业链核心地位量化评估器。所有产出可为 None(缺数据时),不臆造。"""

    def bpi(self, fp: FinancialProfile) -> float | None:
        if fp.payables is None or fp.receivables is None or not fp.cost or fp.cost <= 0:
            return None
        return (fp.payables - fp.receivables) / fp.cost

    def dso(self, fp: FinancialProfile) -> float | None:
        return _days(fp.receivables, fp.revenue)

    def dio(self, fp: FinancialProfile) -> float | None:
        return _days(fp.inventory, fp.cost)

    def dpo(self, fp: FinancialProfile) -> float | None:
        return _days(fp.payables, fp.cost)

    def ccc(self, fp: FinancialProfile) -> float | None:
        dso, dio, dpo = self.dso(fp), self.dio(fp), self.dpo(fp)
        if dso is None or dio is None or dpo is None:
            return None
        return dso + dio - dpo

    def margin(self, fp: FinancialProfile) -> float | None:
        if fp.gross_margin is not None:
            return fp.gross_margin
        if fp.ebit is not None and fp.revenue and fp.revenue > 0:
            return fp.ebit / fp.revenue
        return None

    def pricing_power_score(self, fp: FinancialProfile, max_ccc: float = 120.0) -> float | None:
        """定价权综合分 ∈ [0,1]:毛利(40%) + BPI 商业信用(30%) + CCC 现金周期(30%)。
        三要素全缺 → None;部分缺 → 用可得要素按其权重归一化(不拿 0 充数)。"""
        parts: list[tuple[float, float]] = []   # (score, weight)
        m = self.margin(fp)
        if m is not None:
            parts.append((_clip(m / 0.5, 0.0, 1.0), 0.4))   # 50%+ 毛利满分
        bpi = self.bpi(fp)
        if bpi is not None:
            parts.append((1.0 / (1.0 + math.exp(-3.0 * bpi)), 0.3))
        ccc = self.ccc(fp)
        if ccc is not None:
            parts.append((_clip(1.0 - max(0.0, ccc) / max_ccc, 0.0, 1.0), 0.3))
        if not parts:
            return None
        wsum = sum(w for _, w in parts)
        return float(sum(s * w for s, w in parts) / wsum)

    def assess(self, fp: FinancialProfile) -> dict:
        """一次性产出全部指标(供读层/Agent 使用)。"""
        return {
            "code": fp.code,
            "name": fp.name,
            "bpi": self.bpi(fp),
            "ccc_days": self.ccc(fp),
            "dso_days": self.dso(fp),
            "dio_days": self.dio(fp),
            "dpo_days": self.dpo(fp),
            "gross_margin": self.margin(fp),
            "pricing_power_score": self.pricing_power_score(fp),
        }
