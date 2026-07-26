"""建立产业逻辑传导知识图谱 (Build Industry Causal Knowledge Graph)

该脚本读取 data_lake/research_signals/logic_chains/ 下的所有行业传导链，
将多个逻辑链条中的传导节点（TransmissionNode）和因果关联（Edges）融合成一个统一的、
面向全市场的“产业因果关系知识图谱”（Node-Link 格式），保存至 data_lake/research_signals/industry_knowledge_graph.json。
"""

import json
import sys
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOGIC_CHAIN_DIR = ROOT / "data_lake" / "research_signals" / "logic_chains"
GRAPH_OUTPUT_FILE = ROOT / "data_lake" / "research_signals" / "industry_knowledge_graph.json"

def build_graph():
    if not LOGIC_CHAIN_DIR.exists():
        print(f"[!] 逻辑链目录不存在: {LOGIC_CHAIN_DIR}，无法构建知识图谱")
        return
        
    json_files = list(LOGIC_CHAIN_DIR.glob("*.json"))
    print(f"[*] 发现 {len(json_files)} 个行业逻辑链条文件，开始融合构建产业知识图谱...")
    
    nodes_dict = {}  # name -> node properties
    links_list = []  # list of link properties
    
    for jf in json_files:
        if jf.name == "industry_knowledge_graph.json":
            continue
        try:
            chain = json.loads(jf.read_text(encoding="utf-8"))
            industry = chain.get("industry", "未知行业")
            hyp_name = chain.get("target_factor_hypothesis_name", chain.get("target_hypothesis_name", ""))
            nodes = chain.get("nodes", [])
            
            # 1. 注册/合并节点
            for node in nodes:
                name = node.get("name", "").strip()
                if not name:
                    continue
                    
                category = node.get("category", "")
                change = node.get("change", "")
                evidence = node.get("evidence", "")
                num_val = node.get("numeric_value")
                
                if name not in nodes_dict:
                    nodes_dict[name] = {
                        "id": name,
                        "category": category,
                        "industries": {industry},
                        "hypotheses": {hyp_name} if hyp_name else set(),
                        "last_change": change,
                        "evidence": evidence,
                        "numeric_value": num_val
                    }
                else:
                    # 合并属性
                    nodes_dict[name]["industries"].add(industry)
                    if hyp_name:
                        nodes_dict[name]["hypotheses"].add(hyp_name)
                    # 保留最新的非空值
                    if change:
                        nodes_dict[name]["last_change"] = change
                    if evidence:
                        nodes_dict[name]["evidence"] = evidence
                    if num_val is not None:
                        nodes_dict[name]["numeric_value"] = num_val

            # 2. 建立因果边关系 (当前节点 -> 下一节点)
            for i in range(len(nodes) - 1):
                source_name = nodes[i].get("name", "").strip()
                target_name = nodes[i+1].get("name", "").strip()
                if not source_name or not target_name:
                    continue
                    
                evidence = nodes[i+1].get("evidence", "")
                
                links_list.append({
                    "source": source_name,
                    "target": target_name,
                    "industry": industry,
                    "hypothesis": hyp_name,
                    "evidence": evidence
                })
                
        except Exception as e:
            print(f"[!] 解析文件 {jf.name} 失败: {e}")

    # 将集合转换为列表，以便 JSON 序列化
    nodes_list = []
    for _, props in nodes_dict.items():
        props["industries"] = sorted(list(props["industries"]))
        props["hypotheses"] = sorted(list(props["hypotheses"]))
        nodes_list.append(props)
        
    graph_data = {
        "nodes": nodes_list,
        "links": links_list,
        "meta": {
            "total_nodes": len(nodes_list),
            "total_links": len(links_list),
            "updated_at": Path(LOGIC_CHAIN_DIR).stat().st_mtime if json_files else 0
        }
    }
    
    # 落盘保存
    GRAPH_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    GRAPH_OUTPUT_FILE.write_text(json.dumps(graph_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] 成功生成全市场产业知识图谱: {GRAPH_OUTPUT_FILE.name}")
    print(f"    - 节点总数 (Nodes): {len(nodes_list)}")
    print(f"    - 因果关联总数 (Links): {len(links_list)}")

if __name__ == "__main__":
    build_graph()
