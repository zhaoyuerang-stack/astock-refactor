"""行业研报逻辑传导链条提取管线 (Industry Logical Chain Pipeline)

利用内置 DeepSeek 对特意行业研报进行深度结构化解读，提取多节点因果传导链条 (LogicalChain)，
并将其映射到系统的核心 Hypothesis 本体中。
"""

import json
import sys
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory.ontology import (
    EconomicThesis,
    Hypothesis,
    LogicalChain,
    NodeChange,
    TransmissionNode,
    TransmissionNodeCategory,
)
from providers.llm_adapter import get_adapter

OUT_DIR = ROOT / "data_lake" / "research_signals" / "logic_chains"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════
# Step 1: Prompt 构造 (基于行业特异性)
# ══════════════════════════════════════════════════
def _construct_ontology_prompt(industry: str) -> str:
    """结合行业知识库特征，为 DeepSeek 定制提取 Prompt。"""
    system_prompt = (
        "你是一个专业的买方量化研究员。你需要从研报文本中梳理出“核心经济传导链条”。\n"
        "你需要将文本解读为一系列逻辑传导节点 (TransmissionNodes) 组成的链条 (LogicalChain)。\n\n"
        "每个节点必须包含以下属性：\n"
        "- name: 变量/指标名称（例如：飞天茅台批价、LME铜即期价格、晶圆代工开工率、EPS）\n"
        f"- category: 必须是以下之一：{[c.value for c in TransmissionNodeCategory]}\n"
        f"- change: 必须是以下之一：{[dir.value for dir in NodeChange]}\n"
        "- evidence: 支撑该节点状态的研报原文字句\n"
        "- numeric_value: 数值（如有具体百分比或价格，否则填 null）\n\n"
        "请严格只输出一个 JSON 对象，格式如下：\n"
        "{\n"
        '  "industry": "行业名称",\n'
        '  "mechanism_summary": "一句话因果机制总结",\n'
        '  "nodes": [\n'
        '    {"name": "...", "category": "...", "change": "...", "evidence": "...", "numeric_value": ...}\n'
        '  ],\n'
        '  "target_factor_hypothesis_name": "建议该链条映射成的量化因子名称（英文字符，如 copper_inventory_momentum）"\n'
        "}\n\n"
    )

    # 行业特定引导
    if "消费" in industry or "白酒" in industry:
        system_prompt += (
            "【注意：大消费行业】\n"
            "着眼于：库存周转率、核心大单品批价变化、销售费用率、直营/线上渠道占比。整个链条应该反映：\n"
            "渠道库存去化 -> 批价企稳/上行 -> 毛利改善 -> 业绩超预期。\n"
            "请将这些节点按因果顺序排列。"
        )
    elif "周期" in industry or "金属" in industry or "化工" in industry:
        system_prompt += (
            "【注意：周期品行业】\n"
            "着眼于：供需缺口、库存天数/库存分位数、即期现货价格、行业开工率、产品价差。整个链条应该反映：\n"
            "供给收缩/需求扩张 -> 现货价格上涨 -> 价差走阔 -> 业绩释放。\n"
            "请将这些节点按因果顺序排列。"
        )
    elif "科技" in industry or "半导体" in industry or "芯片" in industry:
        system_prompt += (
            "【注意：硬科技行业】\n"
            "着眼于：产业周期景气度（如代工厂产能利用率、订单出货比 BB Ratio）、核心芯片规格 ASP、研发费用占比、下游出货速度。整个链条应该反映：\n"
            "代工利用率上升 -> ASP 提价 -> 毛利释放/营收扩张 -> EPS 上修。\n"
            "请将这些节点按因果顺序排列。"
        )

    return system_prompt


# ══════════════════════════════════════════════════
# Step 2: 提取引擎与解析
# ══════════════════════════════════════════════════
def extract_logical_chain(industry: str, report_text: str) -> LogicalChain | None:
    """调用 DeepSeek 提取特定行业的逻辑传导链条。"""
    adapter = get_adapter()
    system_prompt = _construct_ontology_prompt(industry)

    if not adapter.available():
        # Fallback/Mock 数据展示
        print(f"[!] DeepSeek 适配器不可用，生成针对 {industry} 的 Mock 逻辑链条本体对象")
        if "周期" in industry or "金属" in industry:
            nodes = (
                TransmissionNode("LME铜即期价格", TransmissionNodeCategory.PRICE, NodeChange.UP, "伦铜现货收盘突破10000美元/吨", 10250.0),
                TransmissionNode("全球铜库存分位数", TransmissionNodeCategory.DEMAND, NodeChange.DOWN, "全球铜显性库存处于近十年3%极低水平", 0.03),
                TransmissionNode("铜矿熔炼价差 TC/RC", TransmissionNodeCategory.COST, NodeChange.DOWN, "由于铜矿紧缺，国内熔炼TC/RC均价差暴跌至历史低点", -12.5),
                TransmissionNode("铜企毛利率", TransmissionNodeCategory.MARGIN, NodeChange.UP, "受益于现货价格上行及自备矿比例，预计二季度综合毛利率提升3.5个百分点", 0.035),
                TransmissionNode("归母净利润", TransmissionNodeCategory.EARNINGS, NodeChange.UP, "预计2026年净利润同比增长25%", 0.25)
            )
            return LogicalChain(industry, nodes, "低库存下伦铜价格上涨，自备矿比例高的铜企将实现显著毛利与利润增量", "copper_low_inventory_premium")
        elif "消费" in industry or "白酒" in industry:
            nodes = (
                TransmissionNode("经销商渠道库存周转天数", TransmissionNodeCategory.DEMAND, NodeChange.DOWN, "五粮液普五渠道库存回落至1.2个月的健康区间", 36.0),
                TransmissionNode("核心大单品批价", TransmissionNodeCategory.PRICE, NodeChange.UP, "普五批价在端午节后回升5元至965元", 965.0),
                TransmissionNode("直营渠道销售占比", TransmissionNodeCategory.COST, NodeChange.DOWN, "直营占比进一步提升至45%，销售费用率降低1.2%", 0.45),
                TransmissionNode("归母净利润", TransmissionNodeCategory.EARNINGS, NodeChange.UP, "预计2026年EPS将达68.2元", 68.2)
            )
            return LogicalChain(industry, nodes, "渠道去库存导致核心单品批价回升，伴随直营占比提升，驱动利润扩张", "liquor_wholesale_price_rebound")
        else:
            return LogicalChain(industry, (), "未识别的行业模板", "generic_hypothesis")

    # 调用 DeepSeek API
    print(f"[*] 正在调用 DeepSeek 解析 {industry} 研报的逻辑链条...")
    user_prompt = f"研报文本内容如下:\n---\n{report_text}\n---"
    
    response = adapter.complete(system_prompt, user_prompt, max_tokens=1500)
    if not response:
        print("[!] DeepSeek 返回内容为空")
        return None

    try:
        # 提取 JSON 部分
        start = response.find("{")
        end = response.rfind("}")
        if start >= 0 and end > start:
            response = response[start : end + 1]

        data = json.loads(response)
        
        # 将 JSON 反序列化为本体类
        nodes = []
        for n in data.get("nodes", []):
            node = TransmissionNode(
                name=n["name"],
                category=TransmissionNodeCategory(n["category"]),
                change=NodeChange(n["change"]),
                evidence=n["evidence"],
                numeric_value=n.get("numeric_value")
            )
            nodes.append(node)
            
        chain = LogicalChain(
            industry=data["industry"],
            nodes=tuple(nodes),
            mechanism_summary=data["mechanism_summary"],
            target_hypothesis_name=data.get("target_factor_hypothesis_name", "derived_factor")
        )
        return chain
    except Exception as e:
        print(f"[!] 反序列化逻辑链条失败: {e}. 原文首部: {response[:150]!r}")
        return None


# ══════════════════════════════════════════════════
# Step 3: 将逻辑传导链条转化为量化 Hypothesis
# ══════════════════════════════════════════════════
def map_logical_chain_to_hypothesis(chain: LogicalChain, report_id: str) -> Hypothesis:
    """将提取出的逻辑链条转换为量化系统核心的 Hypothesis 对象，为其注入经济学底层逻辑。"""
    
    # 梳理出传导关系作为描述
    node_flow = " -> ".join([f"[{n.category.value.upper()}] {n.name}({n.change.value})" for n in chain.nodes])
    
    # 构造 EconomicThesis
    thesis = EconomicThesis(
        mechanism=chain.mechanism_summary,
        citation=f"研报提取(ID:{report_id}) 行业:{chain.industry}",
        falsifiability=f"当 {chain.nodes[-1].name}（最后一环）出现 {NodeChange.DOWN.value} 时，链条证伪。"
    )
    
    # 构造 Hypothesis
    hyp = Hypothesis(
        name=chain.target_hypothesis_name,
        description=f"基于研报逻辑传导链条自动推导因子。路径: {node_flow}",
        factor_fn_name=f"factors.derived.{chain.target_hypothesis_name}",
        factor_params={"lookback_days": 90, "weight_decay": 0.9},
        thesis=thesis,
        source="llm_paper",
        source_ref=report_id
    )
    
    return hyp


# ══════════════════════════════════════════════════
# Step 4: 运行演示
# ══════════════════════════════════════════════════
def run_industry_demo():
    print("==================================================")
    print("启动行业研报逻辑传导链条 (Logical Chain) 提取")
    print("==================================================")

    # 1. 模拟一个周期品（有色金属）研报文本
    copper_report = (
        "【有色金属周报】伦铜现货收盘价上攻至 10250 美元/吨，创历史新高。\n"
        "目前由于主要产地铜矿开采受限，国内冶炼厂原料短缺，熔炼加工费 TC/RC 价差已暴跌至 -12.5 美元/吨的历史冰点。\n"
        "与此相对应，全球铜显性库存在二季度持续去化，目前处于近十年 3% 分位数的极低水平。\n"
        "由于紧缺态势持续，拥有高比例自备矿的头部铜企将充分享受矿价上涨红利，"
        "预计二季度毛利率将大幅提升 3.5 个百分点，2026年全年归母净利润同比增长有望突破 25% 。"
    )

    # 2. 提取逻辑链条 (以周期品模板运行)
    chain = extract_logical_chain("周期品", copper_report)
    if chain:
        print("\n[✔ 成功提取逻辑传导链条本体]:")
        print(f"行业: {chain.industry}")
        print(f"核心机制: {chain.mechanism_summary}")
        print("逻辑节点传导图:")
        for idx, node in enumerate(chain.nodes):
            arrow = " ──▶ " if idx < len(chain.nodes) - 1 else ""
            print(f"  ({node.category.value.upper()}) {node.name} [{node.change.value}] {arrow}")
            print(f"    └─ 证据: \"{node.evidence}\"")

        # 3. 将链条转化为因子假设 Hypothesis
        hyp = map_logical_chain_to_hypothesis(chain, "REPORT_COPPER_001")
        print("\n[✔ 成功映射至量化 Hypothesis 本体]:")
        print(f"因子假设名称: {hyp.name}")
        print(f"描述: {hyp.description}")
        print(f"经济学机制 (EconomicThesis.mechanism): {hyp.thesis.mechanism}")
        print(f"可证伪条件 (EconomicThesis.falsifiability): {hyp.thesis.falsifiability}")
        print(f"数据依赖: {hyp.data_dependencies}")

        # 4. 落盘保存逻辑信号
        output_file = OUT_DIR / f"{hyp.name}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(chain.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n[+] 结构化逻辑链条已保存至: {output_file.relative_to(ROOT)}")
        
    print("==================================================")


if __name__ == "__main__":
    run_industry_demo()
