"""产业逻辑研报系统自我反馈与回测迭代闭环 (Report System Self-Feedback & Backtest Loop)

本脚本读取策略台账数据库 strategy_versions.json 中的回测绩效指标，
与 data_lake/research_signals/logic_chains/ 下的行业假说进行关联匹配：
1. 计算每条传导链条在 A股 历史上的实证表现（已获显著实证、弱显著、回测证伪/淘汰、待验证）。
2. 将回测绩效（Shrape、年化、最大回撤）与置信度得分（Confidence Score）反向注入到全市场产业知识图谱 industry_knowledge_graph.json 的节点和边中。
3. 导出失败与证伪台账至 reports/research/report_feedback_ledger.json，供 NLP 提取管线动态读取，实现“回测失败反思 -> 引导 LLM 优化提取”的自我反馈迭代。
"""

import json
import sys
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REGISTRY_FILE = ROOT / "strategy_versions.json"
GRAPH_FILE = ROOT / "data_lake" / "research_signals" / "industry_knowledge_graph.json"
FEEDBACK_LEDGER_FILE = ROOT / "reports" / "research" / "report_feedback_ledger.json"

def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        try:
            return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] 读取 strategy_versions.json 失败: {e}")
    return {"families": []}

def find_matching_metrics(registry_data: dict, hypothesis_name: str) -> tuple[dict | None, str | None]:
    """通过多种模糊及精确规则在台账中搜索匹配的回测绩效指标"""
    if not hypothesis_name:
        return None, None

    # 1. 尝试完全匹配 Family ID
    for family in registry_data.get("families", []):
        if family.get("id") == hypothesis_name:
            if family.get("versions"):
                # 获取最新版本指标
                latest_v = family["versions"][-1]
                return latest_v.get("metrics"), family.get("status") or "active"

    # 2. 尝试子串匹配 Family ID
    for family in registry_data.get("families", []):
        family_id = family.get("id", "")
        if family_id and (hypothesis_name in family_id or family_id in hypothesis_name):
            if family.get("versions"):
                latest_v = family["versions"][-1]
                return latest_v.get("metrics"), family.get("status") or "active"

    # 3. 尝试匹配配置中包含该因子的版本
    for family in registry_data.get("families", []):
        for v in family.get("versions", []):
            cfg = v.get("config", {})
            factor_str = str(cfg.get("factor", ""))
            if hypothesis_name in factor_str:
                return v.get("metrics"), family.get("status") or "active"

    return None, None

def classify_performance(metrics: dict | None, family_status: str | None) -> dict:
    """根据回测指标对研究假说进行绩效归类与置信度打分"""
    if not metrics:
        return {
            "status": "untested",
            "label": "待验证假说 (Untested)",
            "confidence": 0.5,
            "metrics": None
        }

    sharpe = metrics.get("sharpe") or metrics.get("wf_sharpe")
    annual = metrics.get("annual") or metrics.get("wf_annual")
    maxdd = metrics.get("maxdd") or metrics.get("wf_maxdd")

    if sharpe is None or annual is None:
        return {
            "status": "untested",
            "label": "指标不全 (Incomplete)",
            "confidence": 0.5,
            "metrics": metrics
        }

    try:
        s = float(sharpe)
        a = float(annual)
        m = float(maxdd) if maxdd is not None else 0.0
    except (ValueError, TypeError):
        return {
            "status": "untested",
            "label": "指标非数值 (Invalid)",
            "confidence": 0.5,
            "metrics": metrics
        }

    # 判定门槛
    if family_status == "discarded" or s < 0.45 or a <= 0.0:
        return {
            "status": "refuted",
            "label": "回测证伪/淘汰 (Refuted)",
            "confidence": 0.15,
            "metrics": {"annual": a, "sharpe": s, "maxdd": m}
        }
    elif s >= 1.0 and a >= 0.15:
        return {
            "status": "verified",
            "label": "已获显著实证 (Verified)",
            "confidence": 0.95,
            "metrics": {"annual": a, "sharpe": s, "maxdd": m}
        }
    else:
        return {
            "status": "weak",
            "label": "信号弱显著 (Weak)",
            "confidence": 0.60,
            "metrics": {"annual": a, "sharpe": s, "maxdd": m}
        }

def run_feedback_loop():
    print("[*] 启动产业因果图谱自我反馈闭环校验...")
    
    registry = load_registry()
    if not GRAPH_FILE.exists():
        print(f"[!] 未找到知识图谱文件: {GRAPH_FILE}，无法注入反馈。")
        return
        
    try:
        graph = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[!] 读取知识图谱 JSON 失败: {e}")
        return

    # 对图谱中的每一个假说进行绩效评估
    evaluated_hypotheses = {}
    
    # 1. 扫描所有的 Link 并注入回测指标
    links = graph.get("links", [])
    for link in links:
        hyp_name = link.get("hypothesis", "")
        if not hyp_name:
            continue
            
        if hyp_name not in evaluated_hypotheses:
            metrics, status = find_matching_metrics(registry, hyp_name)
            classification = classify_performance(metrics, status)
            evaluated_hypotheses[hyp_name] = classification
            
        cls = evaluated_hypotheses[hyp_name]
        link["backtest_status"] = cls["status"]
        link["backtest_label"] = cls["label"]
        link["confidence_score"] = cls["confidence"]
        link["backtest_metrics"] = cls["metrics"]

    # 2. 扫描所有的 Node 并注入综合置信度
    nodes = graph.get("nodes", [])
    for node in nodes:
        node_hyps = node.get("hypotheses", [])
        if not node_hyps:
            node["confidence_score"] = 0.5
            node["backtest_status"] = "untested"
            continue
            
        confidences = []
        statuses = []
        for hyp in node_hyps:
            if hyp not in evaluated_hypotheses:
                metrics, status = find_matching_metrics(registry, hyp)
                classification = classify_performance(metrics, status)
                evaluated_hypotheses[hyp] = classification
            confidences.append(evaluated_hypotheses[hyp]["confidence"])
            statuses.append(evaluated_hypotheses[hyp]["status"])
            
        # 节点的置信度取其所有关联假说的最小值，保证谨慎性原则
        node["confidence_score"] = min(confidences) if confidences else 0.5
        
        # 节点状态汇总：如果包含 refuted 则判定为 refuted，若全为 verified 则为 verified，否则为 weak/untested
        if "refuted" in statuses:
            node["backtest_status"] = "refuted"
        elif all(s == "verified" for s in statuses):
            node["backtest_status"] = "verified"
        elif "weak" in statuses:
            node["backtest_status"] = "weak"
        else:
            node["backtest_status"] = "untested"

    # 写回图谱文件
    GRAPH_FILE.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] 绩效指标与置信度已反向注入产业知识图谱: {GRAPH_FILE.name}")
    
    # 3. 统计并导出失败/证伪的假说台账，供 NLP prompt 动态读取反思
    refuted_list = []
    for hyp, cls in evaluated_hypotheses.items():
        if cls["status"] == "refuted":
            refuted_list.append({
                "hypothesis_name": hyp,
                "metrics": cls["metrics"],
                "reason": cls["label"]
            })
            
    FEEDBACK_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEEDBACK_LEDGER_FILE.write_text(json.dumps({
        "updated_at": Path(GRAPH_FILE).stat().st_mtime if GRAPH_FILE.exists() else 0,
        "refuted_hypotheses": refuted_list
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[+] 自我反馈失败反思台账已导出: {FEEDBACK_LEDGER_FILE.name} (共 {len(refuted_list)} 个失效假说)")

if __name__ == "__main__":
    run_feedback_loop()
