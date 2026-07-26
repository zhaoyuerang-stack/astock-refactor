"""研究报告 NLP 提取与因子建模管线 (Report NLP Pipeline)

本脚本实现了系统如何利用内置的 DeepSeek 对 PDF 格式 of 卖方研报进行结构化分析:
1. 从 data_lake/research_pdf/ 递归扫描新的 PDF 报告。
2. 计算文件哈希 (SHA256) 并使用 data_lake/research_pdf/_inbox_state.json 去重。
3. 使用 opendataloader_pdf (或 fallback 的 pdfplumber/pypdf) 将 PDF 解析为文本。
4. 构造受控 Prompt，调用系统内置 DeepSeek 自动分类并提取结构化信号 (个股/行业逻辑链)。
5. 执行交易日对准校验 (Align Date)，将信号写入 data_lake/research_signals/<date>/*.json 和 data_lake/research_signals/logic_chains/*.json。
6. 生产模式下 API 异常时记录至 reports/research/report_nlp_failures.jsonl，拒绝 Mock 降级。
"""

from __future__ import annotations  # 注解惰性求值:dict|None 等 PEP604 在旧解释器子进程下也不崩

import datetime
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory.ontology.report_logic import (
    LogicalChain,
    NodeChange,
    TransmissionNode,
    TransmissionNodeCategory,
)
from providers.llm_adapter import get_adapter

# 设定输入与输出路径
PDF_DIR = ROOT / "data_lake" / "research_pdf"
SIGNAL_DIR = ROOT / "data_lake" / "research_signals"
LOGIC_CHAIN_DIR = SIGNAL_DIR / "logic_chains"
INBOX_STATE_FILE = PDF_DIR / "_inbox_state.json"
FAILURES_LOG_FILE = ROOT / "reports" / "research" / "report_nlp_failures.jsonl"


# ══════════════════════════════════════════════════
# Helper Functions: Hashing & State Management
# ══════════════════════════════════════════════════

def calculate_file_hash(file_path: Path) -> str:
    """Calculate SHA256 of a file."""
    sha256 = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            sha256.update(data)
    return sha256.hexdigest()


def load_inbox_state() -> dict:
    """Load the processed files state from JSON."""
    if INBOX_STATE_FILE.exists():
        try:
            return json.loads(INBOX_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[!] 读取 _inbox_state.json 失败, 重新创建: {e}")
    return {"processed_files": {}}


def save_inbox_state(state: dict):
    """Save the processed files state to JSON."""
    try:
        INBOX_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        INBOX_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[!] 保存 _inbox_state.json 失败: {e}")


def log_failure(file_path: Path, error_msg: str):
    """Log failures into reports/research/report_nlp_failures.jsonl."""
    try:
        FAILURES_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "file_name": file_path.name,
            "error": error_msg
        }
        with FAILURES_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[!] 写入失败日志失败: {e}")


def safe_relative_path(path: Path) -> str:
    """Return relative path to ROOT if possible, otherwise return str(path)."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def record_report_nlp_research_run(
    *,
    stats: dict,
    demo_mode: bool,
    artifact_paths: list[str],
    ledger=None,
    index_path=None,
) -> dict:
    """Archive one report NLP inbox run into the unified research ledger."""
    from research_ledger.ledger import ResearchRunRecord, record_research_run

    failed = int(stats.get("failed", 0) or 0)
    processed = int(stats.get("processed", 0) or 0)
    verdict = "PENDING_REVIEW" if failed else ("SHADOW" if processed else "PASS")
    next_action = "HUMAN_REVIEW" if failed else ("KEEP_SHADOW" if processed else "REVIEW")
    return record_research_run(
        ResearchRunRecord(
            script="scripts/research/report_nlp_pipeline.py",
            hypothesis="report_nlp_signals",
            source="report_nlp",
            data_vintage={
                "demo_mode": demo_mode,
                "pdf_dir": safe_relative_path(PDF_DIR),
                "signal_dir": safe_relative_path(SIGNAL_DIR),
            },
            metrics=dict(stats or {}),
            verdict=verdict,
            artifact_paths=list(artifact_paths or []),
            next_action=next_action,
            notes=f"processed={processed} failed={failed}",
        ),
        ledger=ledger,
        index_path=index_path,
    )


def get_next_trade_date(report_date_str: str) -> str:
    """Find the next A-share trading date in trade_calendar.parquet after report_date.
    If not found, fall back to report_date + 1 day.
    """
    cal_path = ROOT / "data_lake/meta/trade_calendar.parquet"
    try:
        report_dt = pd.to_datetime(report_date_str)
        if cal_path.exists():
            cal = pd.read_parquet(cal_path)["date"]
            cal = pd.to_datetime(cal).sort_values()
            eligible = cal[cal > report_dt]
            if not eligible.empty:
                return eligible.min().strftime("%Y-%m-%d")
    except Exception as e:
        print(f"[!] 获取下一交易日出错: {e}")
    
    # Fallback to T+1 day
    try:
        dt = datetime.datetime.strptime(report_date_str, "%Y-%m-%d").date()
        return (dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    except Exception:
        return (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════
# Step 1: PDF 解析转换器 (PDF -> Text)
# ══════════════════════════════════════════════════

def convert_pdf_to_text(pdf_path: Path, is_demo: bool = False) -> str:
    """将 PDF 文件转换为文本。
    尝试使用 opendataloader (需 Java), 若不可用则优雅降级为 pdfplumber 或 pypdf。
    """
    print(f"[*] 正在解析 PDF 文件: {pdf_path.name}")
    
    # 1. 尝试使用 opendataloader-pdf
    try:
        # import opendataloader_pdf
        # return opendataloader_pdf.convert(str(pdf_path), format='markdown')
        pass
    except ImportError:
        pass

    # 2. 尝试使用 pdfplumber 降级解析
    try:
        import pdfplumber
        text_list = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                val = page.extract_text()
                if val:
                    text_list.append(val)
        if text_list:
            return "\n".join(text_list)
    except Exception:
        pass

    # 3. 尝试使用 pypdf 降级解析
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        text_list = [page.extract_text() for page in reader.pages if page.extract_text()]
        if text_list:
            return "\n".join(text_list)
    except Exception:
        pass

    # 如果在 demo/测试模式下，且没有解析库，优雅降级返回 mock 文本，以支持演示
    if is_demo:
        print("[!] 未检测到 PDF 解析库 (opendataloader-pdf/pdfplumber/pypdf)，使用 mock 解析文本进行演示。")
        lower_name = pdf_path.name.lower()
        if "maotai" in lower_name or "600519" in lower_name:
            return "贵州茅台 600519 研报内容: 渠道改革红利持续释放，直营占比提升拉动毛利率"
        elif "copper" in lower_name or "金属" in lower_name or "铜" in lower_name or "ap2026" in lower_name:
            # We can also match AP2026 as copper for testing multiple files
            return "有色金属 铜 研报内容: 低库存下伦铜价格上涨，自备矿比例高的铜企将实现显著利润增量"
        elif "semiconductor" in lower_name or "chip" in lower_name or "半导体" in lower_name or "算力" in lower_name:
            return "半导体 算力芯片 研报内容: AI基础设施大模型驱动算力变革，GPU需求爆发，先进代工利用率满载至95%，国产设备材料周期景气上行"
        else:
            return f"{pdf_path.name} 研报内容: 模拟个股研报 000001 平安银行 增持 目标价 15.5 情绪 0.35 赵六"
    else:
        raise RuntimeError("未检测到可用的 PDF 解析库 (opendataloader-pdf/pdfplumber/pypdf) 且未运行在 demo 模式。请安装相应库。")


# ══════════════════════════════════════════════════
# Step 2: 构造 DeepSeek Extractor 并调用
# ══════════════════════════════════════════════════

def _nlp_unified_system_prompt() -> str:
    # 动态读取回测反馈与证伪台账，引导大模型进行研报提取自我迭代
    feedback_str = ""
    feedback_file = ROOT / "reports" / "research" / "report_feedback_ledger.json"
    if feedback_file.exists():
        try:
            data = json.loads(feedback_file.read_text(encoding="utf-8"))
            refuted = data.get("refuted_hypotheses", [])
            if refuted:
                feedback_str = (
                    "\n\n⚠️ 【自我反馈迭代警示 (以下传导链条回测已被证伪)】\n"
                    "以下量化逻辑链在历史 A股 回测中表现极差（已淘汰或证伪），表明相应的经济传导在实际市场中无效：\n"
                )
                for r in refuted:
                    feedback_str += f"- 假说因子: {r['hypothesis_name']}，原因: {r['reason']}，回测绩效: {r['metrics']}\n"
                feedback_str += (
                    "注意：如果你在当前研报中再次识别并提取出上述类似的传导链条，请务必在节点的 'evidence' 字段中予以警示性备注，"
                    "指出该逻辑在 A股 历史回测中已被证伪/表现不佳，并适当调低相关个股/行业的初始情绪分值。\n"
                )
        except Exception as e:
            print(f"[!] 读取反馈台账失败: {e}")

    prompt = (
        "你是一个量化投资研报 NLP 信号结构化提取助手。\n"
        "你需要分析给定的研报文本，首先判断其是【个股研究报告】(stock) 还是【行业/专题研究报告】(industry)。\n"
        "请严格输出一个符合以下 JSON 契约 of JSON 对象。绝对不允许有任何 markdown 代码块包裹（如 ```json）、HTML 标签或额外解释文字：\n\n"
        "{\n"
        '  "report_type": "必须是 \'stock\' | \'industry\' | \'unknown\' 之一",\n'
        '  "report_date": "研报发布日期，格式 YYYY-MM-DD，若未提及则输出今日日期",\n'
        '  "brokerage": "研究机构/券商名称",\n'
        '  "analyst": "分析师姓名，多个名字用逗号分隔",\n\n'
        '  // 下面是个股研报 (report_type=\'stock\') 时填充的字段。如果是 industry，这些字段一律填 null：\n'
        '  "stock_code": "6位数字股票代码，如 600519",\n'
        '  "stock_name": "股票简称，如 贵州茅台",\n'
        '  "rating": "个股评级，仅限 \'买入\' | \'增持\' | \'中性\' | \'减持\' | \'卖出\'",\n'
        '  "target_price": "数值，提取的具体目标价，未提及则为 null",\n'
        '  "sentiment_score": "数值，个股情绪得分，介于 -1.0 (极度悲观) 到 1.0 (极度乐观) 之间",\n'
        '  "key_thesis": "核心看好原因/投资要点，限一句话",\n\n'
        '  // 下面是行业专题研报 (report_type=\'industry\') 时填充的字段。如果是 stock，这些字段一律填 null 或空数组：\n'
        '  "industry": "行业或板块名称，如 \'白酒\' | \'有色金属\' | \'半导体\'",\n'
        '  "mechanism_summary": "该行业的核心经济因果机制的一句话总结",\n'
        '  "target_factor_hypothesis_name": "建议该链条映射成的量化因子英文名称（下划线形式，如 liquor_wholesale_price_rebound）",\n'
        '  "nodes": [\n'
        '    {\n'
        '      "name": "传导变量/指标名称（如：飞天茅台批价、晶圆开工率、全球铜库存）",\n'
        '      "category": "节点分类，必须是 \'supply\' | \'demand\' | \'cost\' | \'price\' | \'capacity\' | \'margin\' | \'earnings\' | \'valuation\' 之一",\n'
        '      "change": "指标变化方向，必须是 \'up\' | \'down\' | \'stable\' | \'volatile\' 之一",\n'
        '      "evidence": "支撑该节点状态的研报原文字句",\n'
        '      "numeric_value": "提取的具体数值（如百分比 0.45 或绝对价位，未提及则填 null）"\n'
        '    }\n'
        '  ]\n'
        "}\n\n"
        "对于【行业/专题研究报告】(industry)，请根据以下具体的重点产业方向进行定制化深度梳理：\n"
        "1. 如果是【大消费/食品饮料/白酒】行业：着重提取核心大单品批价、经销商渠道库存周转天数/月数以及直销渠道占比变化，逻辑链条应反映：渠道去库存 -> 批价企稳/回升 -> 毛利改善 -> 业绩释放。\n"
        "2. 如果是【有色金属/周期品/煤炭/化工】行业：着重提取供需缺口、库存天天数/库存分位数、即期现货价格、行业开工率及毛利价差，逻辑链条应反映：供给收缩/需求扩张 -> 现货价格上涨 -> 价差走阔 -> 业绩释放。\n"
        "3. 如果是【硬科技/半导体/AI/算力】行业：着重提取芯片订单出货比 (BB Ratio)、晶圆代工厂产能利用率、核心芯片规格 ASP 以及下游出货速度，逻辑链条应反映：代工产能利用率上升 -> ASP 提价 -> 毛利释放/营收扩张 -> EPS 上修。\n\n"
        "重要：必须保证 nodes 的顺序体现出因果传导逻辑（如：供给收缩 -> 价格上涨 -> 毛利改善 -> 业绩释放）。"
    )

    if feedback_str:
        prompt += feedback_str

    return prompt


def get_mock_signals_for_text(text: str) -> dict:
    """Generate mock JSON response for testing or demo purposes."""
    if "茅台" in text or "600519" in text:
        return {
            "report_type": "stock",
            "report_date": "2026-06-16",
            "brokerage": "中信证券",
            "analyst": "张三,李四",
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "rating": "买入",
            "target_price": 2100.0,
            "sentiment_score": 0.85,
            "key_thesis": "渠道改革红利持续释放，直营占比提升拉动毛利率",
            "industry": None,
            "mechanism_summary": None,
            "target_factor_hypothesis_name": None,
            "nodes": []
        }
    elif "semiconductor" in text or "半导体" in text or "算力" in text:
        return {
            "report_type": "industry",
            "report_date": "2026-06-17",
            "brokerage": "国信证券",
            "analyst": "张平",
            "stock_code": None,
            "stock_name": None,
            "rating": None,
            "target_price": None,
            "sentiment_score": None,
            "key_thesis": None,
            "industry": "半导体",
            "mechanism_summary": "算力需求爆发驱动代工产能利用率上升，拉动芯片ASP上涨并驱动净利润改善",
            "target_factor_hypothesis_name": "semiconductor_price_momentum",
            "nodes": [
                {"name": "芯片订单出货比 BB Ratio", "category": "demand", "change": "up", "evidence": "半导体BB Ratio连续三个月保持在1.05以上", "numeric_value": 1.08},
                {"name": "代工厂产能利用率", "category": "capacity", "change": "up", "evidence": "8寸及12寸成熟工艺产能利用率满载至95%", "numeric_value": 0.95},
                {"name": "芯片产品 ASP", "category": "price", "change": "up", "evidence": "部分MCU及模拟芯片渠道提价约5%-10%", "numeric_value": 0.08},
                {"name": "归母净利润", "category": "earnings", "change": "up", "evidence": "二季度净利润环比大增15%", "numeric_value": 0.15}
            ]
        }
    elif "铜" in text or "有色金属" in text or "LME铜" in text:
        return {
            "report_type": "industry",
            "report_date": "2026-06-16",
            "brokerage": "中信证券",
            "analyst": "王五",
            "stock_code": None,
            "stock_name": None,
            "rating": None,
            "target_price": None,
            "sentiment_score": None,
            "key_thesis": None,
            "industry": "有色金属",
            "mechanism_summary": "低库存下伦铜价格上涨，自备矿比例高的铜企将实现显著毛利与利润增量",
            "target_factor_hypothesis_name": "copper_low_inventory_premium",
            "nodes": [
                {"name": "全球铜库存", "category": "demand", "change": "down", "evidence": "全球铜显性库存处于近十年3%极低水平", "numeric_value": 0.03},
                {"name": "LME铜即期价格", "category": "price", "change": "up", "evidence": "伦铜现货收盘突破10000美元/吨", "numeric_value": 10250.0},
                {"name": "铜矿熔炼价差 TC/RC", "category": "cost", "change": "down", "evidence": "国内熔炼TC/RC价差暴跌至历史低点", "numeric_value": -12.5},
                {"name": "铜企毛利率", "category": "margin", "change": "up", "evidence": "二季度综合毛利率提升3.5个百分点", "numeric_value": 0.035},
                {"name": "归母净利润", "category": "earnings", "change": "up", "evidence": "预计2026年净利润同比增长25%", "numeric_value": 0.25}
            ]
        }
    else:
        return {
            "report_type": "stock",
            "report_date": "2026-06-16",
            "brokerage": "华泰证券",
            "analyst": "赵六",
            "stock_code": "000001",
            "stock_name": "平安银行",
            "rating": "增持",
            "target_price": 15.5,
            "sentiment_score": 0.35,
            "key_thesis": "资产质量保持稳健，零售业务边际复苏",
            "industry": None,
            "mechanism_summary": None,
            "target_factor_hypothesis_name": None,
            "nodes": []
        }


def extract_signals_via_deepseek(text: str, is_demo: bool = False) -> dict | None:
    """使用内置 DeepSeek 提取研报结构化信息。"""
    if is_demo:
        print("[*] Demo 模式: 自动采用内置 Mock 数据匹配进行演示")
        return get_mock_signals_for_text(text)

    adapter = get_adapter()
    if not adapter.available():
        raise RuntimeError("DeepSeek API 适配器未配置或当前不可用。")

    system = _nlp_unified_system_prompt()
    user = f"研报文本内容如下:\n---\n{text}\n---"
    
    print("[*] 正在调用 DeepSeek API 进行因果与情绪提取...")
    response_text = adapter.complete(system, user, max_tokens=1500)
    if not response_text:
        print("[!] DeepSeek 返回内容为空")
        return None

    try:
        # 清理可能存在的 markdown 代码块包裹
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start >= 0 and end > start:
            response_text = response_text[start : end + 1]
        
        parsed = json.loads(response_text)
        return parsed
    except Exception as e:
        print(f"[!] JSON 解析 DeepSeek 返回结果失败: {e}. 原文首部: {response_text[:150]!r}")
        return None


# ══════════════════════════════════════════════════
# Step 3: 安全闸门与防泄露校验 (Align Date & Register)
# ══════════════════════════════════════════════════

def process_report_file(pdf_path: Path, is_demo: bool = False) -> dict | None:
    """处理单个研报：解析 -> 提取 -> 时间对准 -> 落盘。"""
    try:
        # 1. 转换文本
        text = convert_pdf_to_text(pdf_path, is_demo=is_demo)
        if not text.strip():
            raise ValueError("PDF 文本解析为空")
        
        # 2. 调用 DeepSeek 提取结构化数据
        signals = extract_signals_via_deepseek(text, is_demo=is_demo)
        if not signals:
            raise ValueError("LLM 提取结构化信号失败或返回空")

        # 3. 校验基本字段
        report_type = signals.get("report_type")
        report_date_str = signals.get("report_date")
        if not report_type or not report_date_str:
            raise ValueError(f"缺失核心字段 (report_type={report_type}, report_date={report_date_str})")

        # 4. 根据类型进行校验与分流处理
        if report_type == "stock":
            stock_code = signals.get("stock_code")
            if not stock_code:
                raise ValueError("个股报告缺失 stock_code")
            
            # 对齐下一个可交易日
            effective_date = get_next_trade_date(report_date_str)
            target_dir = SIGNAL_DIR / effective_date
            target_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = target_dir / f"{stock_code}_{pdf_path.stem}.json"
            output_path.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[+] 成功提取个股信号并保存至: {safe_relative_path(output_path)}")
            
        elif report_type == "industry":
            industry = signals.get("industry")
            hypothesis_name = signals.get("target_factor_hypothesis_name")
            if not industry or not hypothesis_name:
                raise ValueError("行业报告缺失 industry 或 target_factor_hypothesis_name")

            # 强校验 logical chain nodes 与枚举一致性
            nodes = []
            for n in signals.get("nodes", []):
                category = TransmissionNodeCategory(n["category"])
                change = NodeChange(n["change"])
                node = TransmissionNode(
                    name=str(n["name"]),
                    category=category,
                    change=change,
                    evidence=str(n["evidence"]),
                    numeric_value=n.get("numeric_value")
                )
                nodes.append(node)

            chain = LogicalChain(
                industry=str(industry),
                nodes=tuple(nodes),
                mechanism_summary=str(signals.get("mechanism_summary", "")),
                target_hypothesis_name=str(hypothesis_name)
            )

            # 保存至 logic_chains/ 目录，供 Web 端直接渲染展示
            LOGIC_CHAIN_DIR.mkdir(parents=True, exist_ok=True)
            output_path = LOGIC_CHAIN_DIR / f"{hypothesis_name}.json"
            output_path.write_text(json.dumps(chain.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[+] 成功提取行业逻辑传导链并保存至: {safe_relative_path(output_path)}")
            
            # 同时也往日期目录里存一份归档
            effective_date = get_next_trade_date(report_date_str)
            archive_dir = SIGNAL_DIR / effective_date
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_path = archive_dir / f"industry_{hypothesis_name}_{pdf_path.stem}.json"
            archive_path.write_text(json.dumps(signals, ensure_ascii=False, indent=2), encoding="utf-8")
            
        else:
            raise ValueError(f"未知的研报类别: {report_type}")

        return signals

    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {str(exc)}"
        print(f"[!] 处理研报 {pdf_path.name} 失败: {err_msg}")
        log_failure(pdf_path, err_msg)
        return None


# ══════════════════════════════════════════════════
# Step 4: 因子合成部分 (保留原有结构)
# ══════════════════════════════════════════════════

def build_report_sentiment_factor(start_date: str, end_date: str, codes: list[str]) -> pd.DataFrame:
    """从结构化 signals 目录下读取并合成时序因子面板。"""
    dates = pd.date_range(start=start_date, end=end_date)
    factor_panel = pd.DataFrame(0.0, index=dates, columns=codes)

    if not SIGNAL_DIR.exists():
        print(f"[!] 信号库 {SIGNAL_DIR} 不存在，返回全 0 面板")
        return factor_panel

    print(f"[*] 正在扫描 {safe_relative_path(SIGNAL_DIR)} 合成研报情绪因子面板...")
    
    # 扫描信号目录并填充时序面板
    for day_dir in sorted(SIGNAL_DIR.glob("[0-9]*-[0-9]*-[0-9]*")):
        day_str = day_dir.name
        day_ts = pd.Timestamp(day_str)
        if day_ts not in factor_panel.index:
            continue
        
        # 寻找该日期下的所有个股 json
        for json_file in day_dir.glob("*.json"):
            if json_file.name.startswith("industry_"):
                continue
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                code = data.get("stock_code")
                score = data.get("sentiment_score")
                if code in factor_panel.columns and score is not None:
                    factor_panel.loc[day_ts, code] = float(score)
            except Exception:
                pass

    # 向后执行指数衰减平滑
    factor_panel = factor_panel.apply(lambda col: col.ewm(halflife=20).mean())
    return factor_panel


# ══════════════════════════════════════════════════
# Step 5: Inbox 扫描管线主入口 (Inbox Loop)
# ══════════════════════════════════════════════════

def run_inbox_pipeline(demo_mode: bool = False, delete_after_process: bool = False) -> dict:
    """扫描整个 data_lake/research_pdf/ 文件夹，批量处理未处理的文件。"""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    LOGIC_CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    
    state = load_inbox_state()
    processed = state.setdefault("processed_files", {})
    
    # 1. 递归扫描目录下所有真实 pdf
    pdf_files = sorted(PDF_DIR.rglob("*.pdf"))
    print(f"[*] Inbox 开始扫描, 发现共 {len(pdf_files)} 个 PDF 文件")
    
    stats = {"scanned": 0, "processed": 0, "skipped": 0, "failed": 0}
    
    for pdf_path in pdf_files:
        stats["scanned"] += 1
        file_name = pdf_path.name
        
        # 计算文件哈希
        try:
            f_hash = calculate_file_hash(pdf_path)
        except Exception as e:
            print(f"[!] 计算文件哈希失败: {file_name}, error: {e}")
            stats["failed"] += 1
            continue
            
        # 去重校验
        if f_hash in processed and processed[f_hash].get("status") == "success":
            stats["skipped"] += 1
            continue
            
        print(f"\n[+] 发现未处理研报: {file_name} (hash={f_hash[:8]})")
        
        # 处理文件
        res = process_report_file(pdf_path, is_demo=demo_mode)
        
        if res is not None:
            processed[f_hash] = {
                "file_name": file_name,
                "processed_at": datetime.datetime.now().isoformat(),
                "status": "success",
                "error_msg": None
            }
            stats["processed"] += 1
            if delete_after_process and not demo_mode:
                try:
                    pdf_path.unlink()
                    print(f"[+] 已删除成功处理的研报文件以节省空间: {file_name}")
                except Exception as e:
                    print(f"[!] 删除文件 {file_name} 失败: {e}")
        else:
            processed[f_hash] = {
                "file_name": file_name,
                "processed_at": datetime.datetime.now().isoformat(),
                "status": "failed",
                "error_msg": "提取返回空或触发异常，详情看 failures 日志"
            }
            stats["failed"] += 1
            
        # 增量保存状态，防止崩盘丢失进度
        save_inbox_state(state)
        
    print("\n" + "="*50)
    print(f"Inbox 扫描结束统计: {stats}")
    print("="*50)
    
    try:
        from scripts.research.build_industry_knowledge_graph import build_graph
        build_graph()
        
        # 自动运行自我反馈与回测指标注入
        from scripts.research.report_feedback_loop import run_feedback_loop
        run_feedback_loop()
    except Exception as e:
        print(f"[!] 自动重建或反馈注入产业知识图谱失败: {e}")

    artifacts = [safe_relative_path(INBOX_STATE_FILE)]
    if FAILURES_LOG_FILE.exists():
        artifacts.append(safe_relative_path(FAILURES_LOG_FILE))
    if LOGIC_CHAIN_DIR.exists():
        artifacts.extend(safe_relative_path(p) for p in sorted(LOGIC_CHAIN_DIR.glob("*.json"))[-20:])
    try:
        record_report_nlp_research_run(
            stats=stats,
            demo_mode=demo_mode,
            artifact_paths=artifacts,
        )
    except Exception as exc:
        print(f"[research-ledger] 研报 NLP 归档失败: {exc}")

    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="研报 NLP Inbox 提取管线")
    parser.add_argument("--demo", action="store_true", help="使用演示模式（支持Mock数据测试）")
    parser.add_argument("--delete", action="store_true", help="分析成功后自动删除 PDF 文件以节省空间")
    args = parser.parse_args()
    
    # 在非 demo 模式下确认 DeepSeek 适配器状态
    if not args.demo:
        adapter = get_adapter()
        if not adapter.available():
            print("🚨 错误: 生产模式运行需要配置 AI 模型。请在 settings.yaml 或环境变量中配置 AI_KEY。")
            sys.exit(1)
            
    run_inbox_pipeline(demo_mode=args.demo, delete_after_process=args.delete)


if __name__ == "__main__":
    main()
