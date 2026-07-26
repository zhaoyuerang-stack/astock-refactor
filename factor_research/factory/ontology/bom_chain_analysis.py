"""BOM-driven Supply Chain Ontology & Cost Propagation Engine (基于BOM的上下游产业链与成本传导引擎)

实现基于物理/物料清单(BOM - Bill of Materials)的上下游产业链因果传导分析：
1. 建立产品 BOM 物料关系网，将上游原材料（Raw Materials）与下游制成品（Products）通过重量/成本权重绑定。
2. 当 DeepSeek 在上游研报中提取到原材料价格异动或供给短缺时，利用 BOM 权重计算直接成本冲击。
3. 结合下游产品的定价权指数（Pricing Power），推导下游行业的毛利冲击值（Margin Shock），从而精确预测产业链业绩恶化或改善的拐点。
"""

import sys
from dataclasses import dataclass
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app_config.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BOMItem:
    """BOM（物料清单）中的单一原材料项。"""
    material_name: str               # 原材料名称 (如 "碳酸锂", "硅片")
    weight_ratio: float              # 该原材料在总成本/售价中的占比 (0-1)
    upstream_industry: str           # 生产该原材料的上游行业 (如 "有色金属", "化工")


@dataclass(frozen=True)
class ProductBOM:
    """制成品的 BOM 结构与定价权定义。"""
    product_name: str                # 产品名称 (如 "三元锂电池", "MCU芯片")
    downstream_industry: str         # 该产品所属的下游行业 (如 "新能源汽车", "半导体设计")
    items: tuple[BOMItem, ...]       # BOM 组成项
    pricing_power: float             # 终端定价权指数 (0-1)。1表示能完全向下游转嫁成本，0表示无法转嫁


# ══════════════════════════════════════════════════
# 产业 BOM 知识库预设 (典型中下游产业)
# ══════════════════════════════════════════════════
DEFAULT_BOM_DATABASE = [
    # 锂电池 BOM：极度依赖上游锂矿/碳酸锂
    ProductBOM(
        product_name="动力锂电池",
        downstream_industry="新能源汽车",
        pricing_power=0.40,  # 汽车竞争激烈，成本转嫁能力中等偏低
        items=(
            BOMItem("碳酸锂", 0.35, "有色金属"),  # 占电池约 35% 成本
            BOMItem("正极材料", 0.20, "化学制品"),
            BOMItem("隔膜与电解液", 0.15, "化学制品"),
            BOMItem("铜箔铝箔", 0.08, "有色金属")
        )
    ),
    # 智能手机/消费电子 BOM：核心为芯片与屏幕
    ProductBOM(
        product_name="智能手机",
        downstream_industry="消费电子",
        pricing_power=0.60,  # 品牌溢价使头部厂商有一定转嫁能力
        items=(
            BOMItem("SoC芯片", 0.25, "半导体"),  # 占手机约 25% 成本
            BOMItem("OLED屏幕", 0.18, "电子元器件"),
            BOMItem("摄像头模组", 0.12, "光学组件"),
            BOMItem("外壳与结构件", 0.08, "精密制造")
        )
    ),
    # 半导体制造 BOM：晶圆代工对化学原料与硅片的依赖
    ProductBOM(
        product_name="晶圆制造/代工",
        downstream_industry="半导体制造",
        pricing_power=0.85,  # 代工产能吃紧时具有极高的议价/转嫁能力
        items=(
            BOMItem("半导体硅片", 0.30, "电子化学品"),
            BOMItem("光刻胶与显影液", 0.15, "电子化学品"),
            BOMItem("电子特气", 0.10, "特种气体"),
            BOMItem("石英及石墨件", 0.05, "工业材料")
        )
    )
]


# ══════════════════════════════════════════════════
# BOM 成本传导引擎 (BOM Cost Propagation Engine)
# ══════════════════════════════════════════════════
class BOMChainAnalyzer:
    
    def __init__(self, bom_db: list[ProductBOM] = None):
        self.bom_db = bom_db or DEFAULT_BOM_DATABASE
        
    def calculate_cost_shock(self, material_price_changes: dict[str, float]) -> dict[str, dict]:
        """根据上游原材料价格变动，计算下游制成品的直接成本冲击值及利润空间受挤压程度。
        
        计算公式：
        1. 原材料成本冲击：CostShock = Sum( 原材料价格变化率 * BOM权重 )
        2. 净毛利压力 (Margin Shock)：MarginShock = -CostShock * (1 - PricingPower)
           解释：如果定价权 (Pricing Power) 为 1，则毛利率不受影响（可以完全提价转嫁给消费者）；
                 如果定价权为 0，则全部成本上升转化为毛利率的直接萎缩。
        """
        results = {}
        for bom in self.bom_db:
            total_shock = 0.0
            triggered_items = []
            
            for item in bom.items:
                if item.material_name in material_price_changes:
                    price_change = material_price_changes[item.material_name]
                    # 原材料价格变动贡献度 = 价格变动率 * 成本占比
                    contribution = price_change * item.weight_ratio
                    total_shock += contribution
                    triggered_items.append({
                        "material": item.material_name,
                        "weight": item.weight_ratio,
                        "price_change": price_change,
                        "cost_contribution": contribution
                    })
            
            if triggered_items:
                # 净毛利冲击 (取负数表示利润受损，正数表示利润扩张)
                margin_shock = -total_shock * (1.0 - bom.pricing_power)
                results[bom.product_name] = {
                    "product_name": bom.product_name,
                    "downstream_industry": bom.downstream_industry,
                    "raw_cost_shock": total_shock,
                    "margin_shock": margin_shock,
                    "pricing_power": bom.pricing_power,
                    "details": triggered_items
                }
                
        return results


# ══════════════════════════════════════════════════
# 测试与运行演示
# ══════════════════════════════════════════════════
def run_bom_demo():
    logger.info("==================================================")
    logger.info("启动 BOM 驱动的产业链因果成本传导分析 (BOM Engine)")
    logger.info("==================================================")
    
    # 模拟上游有色金属和化工研报中提取到的原材料即期价格变化率 (Price Change)
    # 例如：碳酸锂价格暴涨 50%，半导体硅片价格上涨 15%
    upstream_shocks = {
        "碳酸锂": 0.50,            # 锂矿紧缺，价格上涨 50%
        "半导体硅片": 0.15,         # 硅片大厂提价 15%
        "光刻胶与显影液": 0.10,     # 上涨 10%
        "铜箔铝箔": 0.05            # 铜价微涨 5%
    }
    
    analyzer = BOMChainAnalyzer()
    
    logger.info("[*] 正在输入上游原材料价格变动参数:")
    for material, change in upstream_shocks.items():
        logger.info(f"  原材料: {material:<10} | 价格变动率: {change:+.1%}")
        
    logger.info("\n[*] 执行下游 BOM 传导计算...")
    shocks = analyzer.calculate_cost_shock(upstream_shocks)
    
    logger.info("\n================ 下游产业毛利率冲击预测 ================")
    for prod_name, res in shocks.items():
        logger.info(f"\n产品: 【{prod_name}】 ──▶ 下游行业: {res['downstream_industry']}")
        logger.info(f"  ├─ 定价权指数 (Pricing Power)   : {res['pricing_power']:.2f}")
        logger.info(f"  ├─ 上游BOM综合原料成本上涨幅度   : {res['raw_cost_shock']:+.2%}")
        logger.info(f"  ├─ 预估行业综合毛利率受损 (Margin Shock): {res['margin_shock']:+.2%}")
        logger.info("  └─ 触发现货传导细节:")
        for det in res['details']:
            logger.info(f"      • {det['material']:<8} (占比 {det['weight']:.0%}): 提价 {det['price_change']:+.1%} -> 贡献成本涨幅 {det['cost_contribution']:+.2%}")
            
    logger.info("\n[量化因子化与组合优化对接说明]:")
    logger.info("1. BOM 传导计算得到的 Margin Shock 直接作为行业 alpha 修正项。")
    logger.info("2. 在本例中，动力电池原料成本暴涨导致下游新能源汽车毛利受损 -11.0%，")
    logger.info("   即使该行业个股量价动量强劲，组合优化器也会因为 -11.0% 的基本面扣分，自动降低新能源汽车的持仓权重，")
    logger.info("   实现基于物理产业链的“智能避险”。")
    logger.info("==================================================")


if __name__ == "__main__":
    run_bom_demo()
