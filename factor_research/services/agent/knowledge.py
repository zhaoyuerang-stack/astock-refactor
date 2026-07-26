"""Layered local knowledge retrieval for the Agent.

This is intentionally lightweight: deterministic local source discovery and
keyword scoring first, with a shape that can later be swapped for embeddings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = PROJECT_ROOT / "factor_research"


@dataclass(frozen=True)
class KnowledgeSource:
    source_id: str
    source_type: str
    title: str
    path: Path


@dataclass(frozen=True)
class KnowledgeHit:
    source_id: str
    source_type: str
    title: str
    source_path: str
    text: str
    score: float


_SOURCE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("claude", "rules", "操作宪法与安全边界", "CLAUDE.md"),
    ("spec", "rules", "系统规格与架构", "SPEC.md"),
    ("web_design", "system_manual", "Web 工作台设计", "WEB_DESIGN.md"),
    ("runbook", "system_manual", "运行手册", "RUNBOOK.md"),
    ("lessons", "research", "经验教训", "LESSONS.md"),
    ("decisions", "research", "决策记录", "DECISIONS.md"),
    ("system_manual", "system_manual", "系统手册", "docs/system_manual.html"),
    ("data_dimensions", "system_manual", "数据维度说明", "factor_research/docs/data_dimensions.md"),
    ("data_infra", "system_manual", "数据基础设施", "factor_research/docs/data_infrastructure.md"),
    ("engine_usage", "system_manual", "回测引擎使用", "factor_research/docs/engine_usage.md"),
    ("ontology", "research", "研究本体词汇表", "factor_research/docs/ontology_glossary.md"),
    ("strategy_eval", "research", "策略评价说明", "factor_research/docs/strategy_evaluation.md"),
)

_IMPORTANT_TERMS = {
    "ai", "agent", "llm", "下单", "调仓", "不越权", "自动", "确认", "风险",
    "数据", "质量", "策略", "台账", "组合", "风控", "回测", "页面", "使用",
}

# 查询里出现这些词 → 用户问的是系统"此刻"的真实状态,应引用 runtime 而非静态文档。
_RUNTIME_QUERY_MARKERS = ("目前", "实时", "状态", "当下", "现在", "实际", "当前", "最新", "运行", "真实")


def list_knowledge_sources() -> list[KnowledgeSource]:
    sources: list[KnowledgeSource] = []
    for source_id, source_type, title, rel in _SOURCE_SPECS:
        path = PROJECT_ROOT / rel
        if path.exists():
            sources.append(KnowledgeSource(source_id, source_type, title, path))
    return sources


def _strip_markup(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&gt;", ">").replace("&lt;", "<")
    return re.sub(r"[ \t]+", " ", text)


def _chunks(text: str, *, max_len: int = 900) -> list[str]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    out: list[str] = []
    for block in blocks:
        block = re.sub(r"\s+", " ", block).strip()
        if len(block) <= max_len:
            out.append(block)
            continue
        for start in range(0, len(block), max_len):
            piece = block[start:start + max_len].strip()
            if piece:
                out.append(piece)
    return out


@lru_cache(maxsize=1)
def _indexed_chunks() -> tuple[KnowledgeHit, ...]:
    hits: list[KnowledgeHit] = []
    for src in list_knowledge_sources():
        try:
            raw = src.path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raw = src.path.read_text(encoding="utf-8", errors="ignore")
        text = _strip_markup(raw) if src.path.suffix.lower() == ".html" else raw
        rel_path = src.path.relative_to(PROJECT_ROOT).as_posix()
        for i, chunk in enumerate(_chunks(text)):
            hits.append(
                KnowledgeHit(
                    source_id=f"{src.source_id}:{i}",
                    source_type=src.source_type,
                    title=src.title,
                    source_path=rel_path,
                    text=chunk,
                    score=0.0,
                )
            )
    return tuple(hits)


def _tokens(query: str) -> set[str]:
    q = (query or "").lower()
    ascii_terms = set(re.findall(r"[a-z0-9_]{2,}", q))
    cjk_terms = {term for term in _IMPORTANT_TERMS if term.lower() in q}
    chars = {ch for ch in q if "\u4e00" <= ch <= "\u9fff"}
    return ascii_terms | cjk_terms | chars


def _score(query: str, text: str, source_type: str) -> float:
    q = (query or "").lower()
    t = text.lower()
    tokens = _tokens(query)
    score = 0.0
    for token in tokens:
        if token and token in t:
            score += 2.0 if token in _IMPORTANT_TERMS else 1.0

    if any(k in q for k in ("下单", "买入", "卖出", "调仓", "交易")):
        if any(k in text for k in ("不越权", "不自动下单", "永不执行", "需人工", "二次确认")):
            score += 8.0
        if source_type == "rules":
            score += 2.0
    if any(k in q for k in ("怎么用", "使用", "页面", "导航")) and source_type == "system_manual":
        score += 3.0
    if any(k in q for k in ("策略", "失效", "研究", "台账")) and source_type == "research":
        score += 3.0
    if source_type == "runtime":
        if any(k in q for k in _RUNTIME_QUERY_MARKERS):
            score += 4.0
    return score


def _get_dynamic_system_info() -> str:
    """Format the current system status as a Markdown document."""
    try:
        from services.read import experiments as ex
        from services.read import factors as fac
        from services.read import portfolio as pf
        from services.read import registry as reg
        from services.read import risk as rk
        from services.read import state as st
        
        # 不带空行:标题与第一节合并成同一 chunk,避免产出"只有标题没有内容"的空心
        # chunk(它靠标题关键词高分挤占检索席位,却答不出任何真实状态)。
        lines = ["# 系统当前真实运行与状态数据 (System Runtime Status)"]
        
        # 1. 数据质量 (Data Quality)
        try:
            # Avoid running the parquet scan inside DuckDB to keep performance snappy
            dq = st.data_quality(with_duckdb=False)
            lines.append("## 数据质量状态 (Data Quality)")
            lines.append(f"- 结论/评估 (verdict): {dq.verdict}")
            lines.append(f"- 股票总数 (total): {dq.total}")
            lines.append(f"- 干净数据只数 (clean): {dq.clean}")
            lines.append(f"- 干净比例 (clean_ratio): {dq.clean_ratio:.2%}")
            lines.append(f"- 真问题数 (severe_count): {dq.severe_count}")
            lines.append(f"- 跳变只数 (jump_count): {dq.jump_count}")
            if dq.n_flagged:
                lines.append(f"- 标记异常只数 (n_flagged): {dq.n_flagged}")
            lines.append("")
        except Exception as e:
            lines.append(f"数据质量获取失败: {e}\n")

        # 2. 市场与持仓状态 (Market/Stance State)
        try:
            ms = st.market_state()
            lines.append("## 市场与运行状态 (Market & Execution State)")
            lines.append(f"- 当前持仓阶段/态势 (current_position): {ms.current_position}")
            lines.append(f"- 上次动作 (last_action): {ms.last_action}")
            lines.append(f"- 上次信号日期 (last_signal_date): {ms.last_signal_date or '无'}")
            lines.append(f"- 上次调仓日期 (last_rebalance_date): {ms.last_rebalance_date or '无'}")
            lines.append(f"- 当前持股只数 (n_holdings): {ms.n_holdings}")
            lines.append("")
        except Exception as e:
            lines.append(f"市场状态获取失败: {e}\n")

        # 3. 组合持仓 (Portfolio)
        try:
            # Set with_target=False to avoid running factors small cap calculations on every query
            port = pf.current_portfolio(with_target=False)
            lines.append("## 当前组合持仓 (Portfolio Status)")
            lines.append(f"- 组合姿态 (stance): {port.stance}")
            lines.append(f"- 状态/机制 (regime): {port.regime}")
            lines.append(f"- 现金余额 (cash): {port.cash:.2f} 元")
            lines.append(f"- 持仓说明 (note): {port.note}")
            lines.append(f"- 当前持仓数: {len(port.current_positions)}")
            if port.current_positions:
                lines.append("- 当前持仓详情:")
                for pos in port.current_positions[:15]:
                    lines.append(f"  * {pos.code}: 权重 {pos.weight:.2%}")
            lines.append("")
        except Exception as e:
            lines.append(f"组合信息获取失败: {e}\n")

        # 4. 风控评估 (Risk Check)
        try:
            risk = rk.risk_report()
            lines.append("## 风控评估报告 (Risk Report)")
            lines.append(f"- 风控总评结论 (verdict): {risk.verdict}")
            if risk.checks:
                lines.append("- 风控规则检查项:")
                for c in risk.checks:
                    lines.append(f"  * {c.rule}: 当前值 {c.current} / 阈值 {c.threshold} -> 状态: {c.status}")
            if risk.control_actions:
                lines.append("- 风控建议控制动作:")
                for a in risk.control_actions:
                    lines.append(f"  * {a.action_type}: {a.recommendation} (触发规则: {a.trigger_rule})")
            lines.append("")
        except Exception as e:
            lines.append(f"风控评估获取失败: {e}\n")

        # 5. 因子家族 (Factors)
        try:
            factors = fac.list_factors()
            lines.append("## 因子家族台账 (Alpha Factors)")
            lines.append(f"- 因子家族总数: {len(factors)}")
            for f in factors:
                lines.append(f"- 因子家族「{f.name}」({f.display_name or '无'}):")
                lines.append(f"  * 版本数: {f.n_versions}")
                lines.append(f"  * 当前版本: {f.current_version}")
                lines.append(f"  * 说明: {f.description}")
            lines.append("")
        except Exception as e:
            lines.append(f"因子家族获取失败: {e}\n")

        # 6. 母策略台账 (Strategies)
        try:
            strategies = reg.list_strategies()
            lines.append("## 策略台账 (Registered Strategies)")
            lines.append(f"- 在册策略总数: {len(strategies)}")
            for s in strategies:
                lines.append(f"- 策略「{s.strategy_id}」({s.name}): 状态: {s.status}, 责任人: {s.owner or '无'}, 描述: {s.description}")
            lines.append("")
        except Exception as e:
            lines.append(f"策略台账获取失败: {e}\n")

        # 7. 因子健康状态 (Factor Health)
        try:
            healths = st.strategy_health()
            lines.append("## 因子运行健康度 (Factor Health)")
            for h in healths:
                lines.append(f"- 因子「{h.name}」: 夏普比率 (sharpe): {h.sharpe:.2f}, 6个月动量: {h.momentum_6m:.2%}, 趋势: {h.trend}")
            lines.append("")
        except Exception as e:
            lines.append(f"因子健康获取失败: {e}\n")

        # 8. 假设池漏斗 (Experiments Funnel)
        try:
            fun = ex.funnel()
            lines.append("## 假设探索漏斗 (Experiments Funnel)")
            lines.append(f"- 总候选数: {fun.total}")
            lines.append(f"- 淘汰率: {fun.discard_ratio:.2%}")
            lines.append(f"- 已登记数: {fun.registered}")
            if fun.stages:
                lines.append("- 各阶段分布:")
                for stg in fun.stages:
                    lines.append(f"  * {stg.get('stage')}: {stg.get('count')} 个")
            lines.append("")
        except Exception as e:
            lines.append(f"假设漏斗获取失败: {e}\n")

        return "\n".join(lines)
    except Exception as e:
        return f"# 系统实时运行与状态数据获取失败\n异常信息: {e}"


def retrieve_knowledge(query: str, *, limit: int = 5) -> list[KnowledgeHit]:
    # 1. Static chunks from files
    static_hits = _indexed_chunks()
    
    # 2. Dynamically generated live system status chunks
    dynamic_text = _get_dynamic_system_info()
    dynamic_hits = []
    for i, chunk in enumerate(_chunks(dynamic_text)):
        dynamic_hits.append(
            KnowledgeHit(
                source_id=f"runtime_status:{i}",
                source_type="runtime",
                title="系统实时状态与运行数据",
                source_path="runtime_status",
                text=chunk,
                score=0.0,
            )
        )
        
    all_hits = list(static_hits) + dynamic_hits
    
    scored = [
        KnowledgeHit(
            source_id=h.source_id,
            source_type=h.source_type,
            title=h.title,
            source_path=h.source_path,
            text=h.text,
            score=_score(query, h.text, h.source_type),
        )
        for h in all_hits
    ]
    found = [h for h in scored if h.score > 0]
    found.sort(key=lambda h: (-h.score, h.source_type, h.source_path, h.source_id))
    top = found[:limit]

    # 实时状态类查询保留一个 runtime 席位:静态文档(CLAUDE/DECISIONS 等)会随时间增长,
    # 纯分数排序下会把 runtime chunk 挤出前 limit,导致"当前状态"问题答不出真实状态。
    if any(k in (query or "") for k in _RUNTIME_QUERY_MARKERS) and top \
            and not any(h.source_type == "runtime" for h in top):
        best_runtime = next((h for h in found if h.source_type == "runtime"), None)
        if best_runtime is not None:
            top = top[:-1] + [best_runtime]
    return top


def citation_from_hit(hit: KnowledgeHit) -> dict:
    excerpt = hit.text.strip()
    if len(excerpt) > 220:
        excerpt = excerpt[:217].rstrip() + "..."
    return {
        "source_id": hit.source_id,
        "source_type": hit.source_type,
        "title": hit.title,
        "source_path": hit.source_path,
        "excerpt": excerpt,
    }

