"""Report Logical Chain — 研报逻辑传导链条本体。

实现从本体论角度对研报信息进行解读，建立结构化的逻辑因果传导链条，
将定性的行业动态/事实，映射为定量的因子假设输入。
"""

from dataclasses import dataclass, field
from enum import Enum


class TransmissionNodeCategory(Enum):
    """逻辑传导节点的语义分类（本体定义）。"""
    SUPPLY = "supply"               # 供给端（产能、限产、开工率）
    DEMAND = "demand"               # 需求端（订单、出货量、渠道去库存）
    COST = "cost"                   # 成本端（原材料价格、研发费用、销售折让）
    PRICE = "price"                 # 价格端（产品均价 ASP、出厂价、批价）
    CAPACITY = "capacity"           # 产能与效率（产能利用率、产线扩张）
    MARGIN = "margin"               # 盈利能力（毛利率、净利润率）
    EARNINGS = "earnings"           # 业绩表现（归母净利润、EPS、业绩预增）
    VALUATION = "valuation"         # 估值中枢（PE、PB、估值修复）


class NodeChange(Enum):
    """节点指标的变化方向。"""
    UP = "up"                       # 上行/扩张/增加
    DOWN = "down"                   # 下行/收缩/减少
    STABLE = "stable"               # 平稳/持平
    VOLATILE = "volatile"           # 波动剧烈/不确定


@dataclass(frozen=True)
class TransmissionNode:
    """逻辑传导链条中的单一节点（事实/中间变量）。"""

    name: str                       # 节点名称（如 "Feitian Wholesale Price", "DRAM Spot Price"）
    category: TransmissionNodeCategory
    change: NodeChange
    evidence: str                   # 研报中的原文证据/数据支持
    numeric_value: float | None = None  # 数值指标（可选，如 2100元，或 增长率 15%）


@dataclass(frozen=True)
class LogicalChain:
    """研报逻辑传导链条。
    
    因果逻辑：宏观/供给/需求事实 -> 中间经营变量变化 -> 财务/利润输出 -> 因子/策略假说
    """

    industry: str                   # 行业名称（如 "白酒", "有色金属", "半导体"）
    nodes: tuple[TransmissionNode, ...] = field(default_factory=tuple)
    mechanism_summary: str = ""    # 核心传导机制的一句话总结
    target_hypothesis_name: str = "" # 对应的因子假设名称

    def to_dict(self) -> dict:
        return {
            "industry": self.industry,
            "nodes": [
                {
                    "name": n.name,
                    "category": n.category.value,
                    "change": n.change.value,
                    "evidence": n.evidence,
                    "numeric_value": n.numeric_value
                }
                for n in self.nodes
            ],
            "mechanism_summary": self.mechanism_summary,
            "target_hypothesis_name": self.target_hypothesis_name
        }


# ══════════════════════════════════════════════════
# 行业本体论特异性规范模板 (Industry Specific Templates)
# ══════════════════════════════════════════════════
INDUSTRY_ONTOLOGY_TEMPLATES = {
    "周期品": {
        "description": "适用于有色金属、化工、煤炭等强周期行业。传导特征为价格与库存双重驱动。",
        "expected_nodes": ["SUPPLY/DEMAND (产能利用/库存水平)", "PRICE (现货价格)", "MARGIN (毛利率/价差)", "EARNINGS (EPS/净利润)"],
        "prompt_guidance": "着重提取库存分位数、大宗商品即期价格、开工率以及毛利空间（价差）。"
    },
    "大消费": {
        "description": "适用于食品饮料、白酒、家电等行业。传导特征为渠道库存与终端定价权驱动。",
        "expected_nodes": ["DEMAND (渠道去库存/经销商活跃)", "PRICE (批价/终端零售价)", "COST (销售费用率/直营占比)", "MARGIN (毛利扩张)", "EARNINGS (业绩预增)"],
        "prompt_guidance": "着重提取核心大单品批价（如飞天茅台批价）、经销商渠道库存周转天数以及直销渠道占比变化。"
    },
    "硬科技": {
        "description": "适用于半导体、电子、新能源等高研发行业。传导特征为产业周期（设备/库存）与产品迭代驱动。",
        "expected_nodes": ["DEMAND (芯片Book-to-Bill/晶圆代工订单)", "CAPACITY (产能利用率/良率)", "PRICE (产品均价 ASP)", "EARNINGS (业绩改善)"],
        "prompt_guidance": "着重提取核心芯片规格价格（DRAM/NAND 现货价）、主流代工厂产能利用率、订单出货比及研发成果落地。"
    }
}
