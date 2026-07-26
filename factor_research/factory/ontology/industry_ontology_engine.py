"""Ontological Industry Prediction Engine (本体论行业预测引擎)

实现基于研报本体（Ontology）因果逻辑链条的行业状态估计与业绩预测。
核心逻辑：
1. 信号搜集：从各股/大势研报中搜集逻辑节点状态（SUPPLY, DEMAND, PRICE, CAPACITY等）。
2. 拓扑聚合：将公司级/碎片化的因果节点，按“行业本体树”聚合为行业级节点状态，计算共识度（Consensus）。
3. 因果传导概率推理（Bayesian/Causal Propagation）：利用因果传导路径与转移概率，
   从上游因果节点（供给收缩、需求扩张、ASP上涨）推导下游节点（毛利扩张、业绩释放）在未来的发生概率，实现行业预测。
4. 行业轮动输出：计算行业“景气预测得分”，为组合优化层提供行业配置权重。
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app_config.log import get_logger
from factory.ontology.report_logic import NodeChange, TransmissionNodeCategory

logger = get_logger(__name__)


@dataclass(frozen=True)
class CausalLink:
    """因果连接关系本体：定义两个语义分类之间的传导概率与滞后天数。"""
    source_cat: TransmissionNodeCategory
    target_cat: TransmissionNodeCategory
    probability: float               # 传导发生概率 (0-1)
    lag_days: int                    # 传导滞后天数 (平均反应在财务报表或股价上的时间)


# 默认的全局因果传导网络设计 (基于金融学先验知识)
DEFAULT_CAUSAL_NETWORK = [
    # 供给收缩 / 需求扩张 -> 提价
    CausalLink(TransmissionNodeCategory.SUPPLY, TransmissionNodeCategory.PRICE, 0.75, 20),
    CausalLink(TransmissionNodeCategory.DEMAND, TransmissionNodeCategory.PRICE, 0.85, 15),
    
    # 提价 / 效率提升 -> 毛利改善
    CausalLink(TransmissionNodeCategory.PRICE, TransmissionNodeCategory.MARGIN, 0.80, 30),
    CausalLink(TransmissionNodeCategory.CAPACITY, TransmissionNodeCategory.MARGIN, 0.70, 45),
    
    # 原材料上涨 -> 毛利恶化 (负传导在计算中会取反)
    CausalLink(TransmissionNodeCategory.COST, TransmissionNodeCategory.MARGIN, 0.75, 30),
    
    # 毛利改善 -> 业绩释放 (EPS/利润上修)
    CausalLink(TransmissionNodeCategory.MARGIN, TransmissionNodeCategory.EARNINGS, 0.90, 40),
]


@dataclass
class AggregatedNodeState:
    """聚合后的行业节点状态。"""
    name: str
    category: TransmissionNodeCategory
    score: float                     # 综合景气得分 (-1.0 到 1.0)
    consensus: float                 # 券商/分析师共识度 (0-1)
    sample_count: int                # 支撑此节点的研报样本数


@dataclass
class IndustryState:
    """行业状态快照：包含各个因果层级节点的聚合得分。"""
    industry: str
    aggregated_nodes: dict[TransmissionNodeCategory, AggregatedNodeState] = field(default_factory=dict)
    
    def get_score(self, cat: TransmissionNodeCategory) -> float:
        if cat in self.aggregated_nodes:
            return self.aggregated_nodes[cat].score
        return 0.0

    def get_consensus(self, cat: TransmissionNodeCategory) -> float:
        if cat in self.aggregated_nodes:
            return self.aggregated_nodes[cat].consensus
        return 0.0


# ══════════════════════════════════════════════════
# 行业预测核心计算引擎 (Predictive Engine)
# ══════════════════════════════════════════════════
class IndustryOntologyPredictor:
    
    def __init__(self, causal_network: list[CausalLink] = None):
        self.network = causal_network or DEFAULT_CAUSAL_NETWORK
        
    def aggregate_signals(self, raw_signals: list[dict]) -> list[IndustryState]:
        """第一步：将零散的研报 JSON 信号，按行业及节点类别进行横截面聚合。"""
        # 按行业整理数据
        industry_data: dict[str, dict[TransmissionNodeCategory, list[tuple[float, float]]]] = {}
        
        for sig in raw_signals:
            ind = sig.get("industry")
            if not ind:
                continue
            if ind not in industry_data:
                industry_data[ind] = {cat: [] for cat in TransmissionNodeCategory}
                
            for node in sig.get("nodes", []):
                try:
                    cat = TransmissionNodeCategory(node["category"])
                    change = NodeChange(node["change"])
                    
                    # 将变化方向转化为数值：UP = 1.0, DOWN = -1.0, STABLE = 0.0
                    val = 1.0 if change == NodeChange.UP else -1.0 if change == NodeChange.DOWN else 0.0
                    
                    # 权重：分析师给出的情绪分（若无则默认为 1.0）
                    sentiment = sig.get("sentiment_score", 1.0)
                    
                    industry_data[ind][cat].append((val, sentiment))
                except (ValueError, KeyError):
                    continue
                    
        # 计算每个行业的聚合状态
        states = []
        for ind, cats in industry_data.items():
            state = IndustryState(industry=ind)
            for cat, samples in cats.items():
                if not samples:
                    continue
                
                vals = [s[0] for s in samples]
                sents = [s[1] for s in samples]
                
                # 综合景气度得分 = 变化方向均值 * 平均研报情绪 (归一化到 [-1, 1])
                avg_val = sum(vals) / len(vals)
                avg_sent = sum(sents) / len(sents)
                score = avg_val * abs(avg_sent)
                
                # 券商共识度 = 意见方向相同者的比例 (如 80% 同意上涨)
                directions = [v > 0 for v in vals if v != 0]
                if directions:
                    maj_ratio = sum(directions) / len(directions)
                    consensus = max(maj_ratio, 1 - maj_ratio) # 0.5 - 1.0
                else:
                    consensus = 0.5
                    
                state.aggregated_nodes[cat] = AggregatedNodeState(
                    name=cat.value,
                    category=cat,
                    score=score,
                    consensus=consensus,
                    sample_count=len(samples)
                )
            states.append(state)
            
        return states

    def predict_future_earnings(self, state: IndustryState) -> float:
        """第二步：利用因果网络拓扑结构，执行传导概率推理。
        
        预测目标：下游的业绩释放概率与强度 (EARNINGS Score)。
        因果概率计算模型：
        下游预测值 = Sum( 上游节点实际观测值 * 因果连边概率 ) 逐级向下传播。
        """
        # 建立当前状态节点分值映射
        inferred_scores = {cat: state.get_score(cat) for cat in TransmissionNodeCategory}
        
        # 按照传导顺序拓扑排序进行计算 (从供给需求 -> 价格 -> 毛利 -> 盈利)
        # 本系统固定传导拓扑为：
        # Layer 1: SUPPLY, DEMAND, COST
        # Layer 2: PRICE, CAPACITY
        # Layer 3: MARGIN
        # Layer 4: EARNINGS
        
        # 1. 传导至 PRICE
        price_inflows = []
        for link in self.network:
            if link.target_cat == TransmissionNodeCategory.PRICE:
                # 原理：上游强度 * 传导率
                price_inflows.append(inferred_scores[link.source_cat] * link.probability)
        if price_inflows:
            # 融合观测得分与推导得分 (各占 50% 权重)
            inferred_scores[TransmissionNodeCategory.PRICE] = (
                inferred_scores[TransmissionNodeCategory.PRICE] * 0.5 + (sum(price_inflows) / len(price_inflows)) * 0.5
            )

        # 2. 传导至 MARGIN
        margin_inflows = []
        for link in self.network:
            if link.target_cat == TransmissionNodeCategory.MARGIN:
                source_score = inferred_scores[link.source_cat]
                # 成本上升对利润空间是负面传导，取反
                if link.source_cat == TransmissionNodeCategory.COST:
                    source_score = -source_score
                margin_inflows.append(source_score * link.probability)
        if margin_inflows:
            inferred_scores[TransmissionNodeCategory.MARGIN] = (
                inferred_scores[TransmissionNodeCategory.MARGIN] * 0.5 + (sum(margin_inflows) / len(margin_inflows)) * 0.5
            )

        # 3. 传导至 EARNINGS
        earnings_inflows = []
        for link in self.network:
            if link.target_cat == TransmissionNodeCategory.EARNINGS:
                earnings_inflows.append(inferred_scores[link.source_cat] * link.probability)
        if earnings_inflows:
            predicted_earnings = (sum(earnings_inflows) / len(earnings_inflows))
            return predicted_earnings
            
        return inferred_scores[TransmissionNodeCategory.EARNINGS]

    def rank_industries(self, states: list[IndustryState]) -> list[tuple[str, float]]:
        """第三步：对所有行业未来的业绩释放概率进行排名，作为组合配置依据。"""
        rankings = []
        for state in states:
            pred_score = self.predict_future_earnings(state)
            rankings.append((state.industry, pred_score))
            
        # 按得分降序排列
        return sorted(rankings, key=lambda x: x[1], reverse=True)


# ══════════════════════════════════════════════════
# 测试与运行演示
# ══════════════════════════════════════════════════
def run_prediction_demo():
    logger.info("==================================================")
    logger.info("启动核心量化引擎：基于本体的行业景气与业绩预测")
    logger.info("==================================================")
    
    # 模拟从信号目录读取到的 3 个行业的研报逻辑链数据
    mock_signals = [
        # 1. 有色金属行业研报汇总表现 (供给严重收缩，现货提价强劲)
        {
            "industry": "有色金属",
            "sentiment_score": 0.85,
            "nodes": [
                {"category": "supply", "change": "down", "name": "产能限制"},
                {"category": "demand", "change": "down", "name": "全球库存"},
                {"category": "price", "change": "up", "name": "LME铜价"}
            ]
        },
        # 2. 食品饮料行业研报汇总表现 (渠道去库存见效，批价回暖，直营占比降低了销售费用)
        {
            "industry": "食品饮料",
            "sentiment_score": 0.70,
            "nodes": [
                {"category": "demand", "change": "down", "name": "渠道库存"},
                {"category": "price", "change": "up", "name": "飞天批价"},
                {"category": "cost", "change": "down", "name": "销售费用率"}
            ]
        },
        # 3. 半导体行业研报汇总表现 (下游订单增长，晶圆代工产能满载，但面临原材料成本上涨)
        {
            "industry": "半导体",
            "sentiment_score": 0.65,
            "nodes": [
                {"category": "demand", "change": "up", "name": "设计商订单"},
                {"category": "capacity", "change": "up", "name": "晶圆代工开工率"},
                {"category": "cost", "change": "up", "name": "硅片原材料价格"}
            ]
        }
    ]
    
    predictor = IndustryOntologyPredictor()
    
    # 1. 节点聚合
    logger.info("[*] 聚合研报碎片化节点中...")
    states = predictor.aggregate_signals(mock_signals)
    for state in states:
        logger.info(f"\n行业: {state.industry}")
        for cat, node in state.aggregated_nodes.items():
            logger.info(f"  └─ 节点: {cat.value:<10} | 得分: {node.score:+.2f} | 券商共识: {node.consensus:.1%} | 样本数: {node.sample_count}")
            
    # 2. 逻辑传导与业绩预测
    logger.info("\n[*] 执行本体因果网络传导推理，预测行业未来业绩释放强度...")
    rankings = predictor.rank_industries(states)
    
    logger.info("\n================ 行业景气预测排行榜 ================")
    for rank, (ind, score) in enumerate(rankings, 1):
        logger.info(f" 排名 {rank}: 【{ind:<6}】 未来业绩释放强度预测值: {score:+.3f}")
        
    logger.info("\n[量化组合管理接入说明]:")
    logger.info("预测得分将直接注入 portfolio/composer.py 优化器。")
    logger.info("在满足行业集中度上限的前提下，优化器将向预测分最高的行业进行战术配置超配。")
    logger.info("==================================================")


if __name__ == "__main__":
    run_prediction_demo()
