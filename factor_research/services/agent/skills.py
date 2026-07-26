"""Agent skills with explicit source boundaries.

The Agent chooses one skill; each skill owns one question domain and one source
class. This prevents runtime facts, stock data, and manual docs from blending.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from contracts.models import AgentCitation, AgentOutput
from providers.llm_adapter import get_adapter, llm_ready
from services.agent.knowledge import citation_from_hit, retrieve_knowledge
from services.agent.tools import tool_registry

logger = logging.getLogger(__name__)


class AgentSkill(Protocol):
    name: str

    def answer(self, request: str, context: dict) -> dict:
        ...


# ── 防编造数字护栏 + DeepSeek 讲解 ───────────────────────────────────────────────
# 讲解归 LLM,数字归数据:让 DeepSeek 自由讲解,但用确定性代码两道护栏校验。
# 按"数值语义"判定(非字符串):放行小数↔百分比(×100)与四舍五入(0.5% 容差),
# 仍挡住凭空数字。计数类篡改由第二道"头部数字必须保留"独立守住。
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_REL_TOL = 0.005


def _floats(text: str) -> list[float]:
    out: list[float] = []
    for t in _NUM_RE.findall(text or ""):
        try:
            out.append(float(t))
        except ValueError as exc:
            # 非数字 token 跳过(正则已收窄,极端 token 仍可能失败),但必须留痕
            logger.warning("skip non-float token %r: %s", t, exc)
    return out


def _grounded(n: float, reals: list[float]) -> bool:
    """n 是否能在真实数据里找到来源(允许原值 / ×100 百分比 / ÷100,带四舍五入容差)。"""
    for r in reals:
        for cand in (r, r * 100.0, r / 100.0):
            if abs(n - cand) <= _REL_TOL * max(abs(cand), 1.0):
                return True
    return False


def _salient(nums: list[float]) -> list[float]:
    """只把"有编造风险的金融数字"纳入(a)校验:小数 或 ≥100 的数。
    裸小整数(序号 1./2.、日期片段 12、"早5天")是结构性噪声,不校验,免误杀讲解。"""
    return [n for n in nums if n != int(n) or abs(n) >= 100]


def _number_guard(narration: str, summary: str, allowed_text: str) -> bool:
    """① narration 里每个"显著数字"都须能在工具数据里找到近似来源(防编造金融事实);
    ② 确定性 summary 的头部数字必须在 narration 里复述(防篡改关键计数,含小计数)。"""
    reals = _floats(allowed_text)
    narr = _floats(narration)
    if any(not _grounded(n, reals) for n in _salient(narr)):
        return False
    if any(not _grounded(s, narr) for s in _floats(summary)):
        return False
    return True


def _facts_text(out: AgentOutput, data) -> str:
    parts = [out.summary, *out.evidence, json.dumps(data, ensure_ascii=False, default=str)]
    return " ".join(p for p in parts if p)


def _narrate(out: AgentOutput, request: str, context: dict, tool_name: str, data) -> AgentOutput:
    """DeepSeek 把工具事实讲解成自然语言替换 summary;过数字护栏才采纳,否则保留确定性摘要。
    evidence/citations/source_types/导航一律不动(硬数据永远在)。"""
    adapter = get_adapter()
    if not adapter.available():
        return out
    # 思考型模型偶发空内容 → 讲解丢失退回干巴摘要;空则重试一次
    narration = adapter.synthesize(request, context, tool_name, data) \
        or adapter.synthesize(request, context, tool_name, data)
    if narration and _number_guard(narration, out.summary, _facts_text(out, data)):
        out.summary = narration.strip()
    return out


# ── LLM 结构化意图解析(agent 大脑;替代关键词清单)─────────────────────────────
_STATUS_TOOLS = ["strategies", "risk", "portfolio", "data_quality", "factors", "experiments"]
_INTENT_SYSTEM = (
    "你是研究平台的意图解析器。读用户问题,只输出一个 JSON(不要解释),字段如下:\n"
    '{"skill": "system_status|stock_data|system_guide",\n'
    ' "tool": "strategies|risk|portfolio|data_quality|factors|experiments|null",\n'
    ' "intent": "ranking|count|named|each|overview|fundamental|quote|null",\n'
    ' "rank_by": "sharpe|calmar|annual|maxdd|null",\n'
    ' "entity": "<股票6位代码 或 策略名;无则 null>"}\n\n'
    "判定规则:\n"
    "- skill: 问系统运行事实(策略/风控/组合/数据质量/因子/实验)→ system_status;"
    "问某只股票(行情/估值/基本面/议价权/产业链/是否高估)→ stock_data;问怎么用/页面/规则/为什么不能下单 → system_guide。\n"
    "- tool(仅 system_status):问题落在哪类运行事实就选哪个,拿不准给 null。\n"
    "- intent(strategies):求最优/最好/排名/某指标最高 → ranking;点名某个策略 → named;"
    "每个/各个/逐个/全部列出 → each;问数量/有几个 → count;笼统了解 → overview。\n"
    "- intent(stock_data):问基本面/议价权/产业链地位/估值贵不贵/预期差/值不值得 → fundamental;"
    "只问行情/股价/涨跌/资金流 → quote。\n"
    "- rank_by:从问法推断排序指标(夏普/sharpe、卡玛/calmar、年化/annual、回撤/maxdd);没指明给 null(后端默认夏普)。\n"
    "- entity:个股给6位代码;点名策略给那个名字(可中文或英文);否则 null。\n"
    "只输出 JSON。"
)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _parse_intent(request: str, context: dict) -> dict | None:
    """DeepSeek 一次结构化意图解析;不可用/失败/非法 → None(由调用方回退关键词)。
    带最近对话历史,让"你自己查啊""那只呢"这类后续追问能继承上文的实体/意图。"""
    adapter = get_adapter()
    if not adapter.available():
        return None
    history = context.get("messages") or []
    history_text = "\n".join(
        f"{m.get('role', '')}: {m.get('content', '')}" for m in history[-6:] if isinstance(m, dict) and m.get("content")
    )
    user = f"页面={context.get('current_page', '')}\n最近对话:\n{history_text}\n\n当前用户问题:{request}"
    # 思考型模型(deepseek-v4-flash)偶发空内容 → 误回退到错路由;空则重试一次
    for _ in range(2):
        raw = adapter.complete(_INTENT_SYSTEM, user, max_tokens=800, timeout=20)
        obj = _extract_json(raw or "")
        if obj and obj.get("skill") in ("system_status", "stock_data", "system_guide"):
            return obj
        if raw:   # 有内容但非合法意图 → 不是空响应问题,别白重试
            break
    return None


# 当前页面 → 该页运行事实工具(无关键词时的兜底,认不出则不兜底)
_PAGE_TOOL = {
    "overview": "strategies",
    "data": "data_quality",
    "risk": "risk",
    "portfolio": "portfolio",
    "factors": "factors",
    "experiments": "experiments",
}


def extract_stock_code(request: str) -> str | None:
    m = re.search(r"(?<!\d)(\d{6})(?:\.(?:sh|sz|SH|SZ))?(?!\d)", request or "")
    return m.group(1) if m else None


def _runtime_sources(out: AgentOutput, context: dict, nav: list[str]) -> AgentOutput:
    out.citations = []
    out.source_types = sorted({"runtime", "ui_context"} if context else {"runtime"})
    out.suggested_navigation = nav
    return out


def _doc_sources(out: AgentOutput, request: str, context: dict) -> AgentOutput:
    hits = retrieve_knowledge(request, limit=4)
    out.citations = [AgentCitation(**citation_from_hit(h)) for h in hits]
    source_types = {h.source_type for h in hits}
    if context:
        source_types.add("ui_context")
    out.source_types = sorted(source_types)
    out.suggested_navigation = [f"/{context['current_page']}"] if context.get("current_page") else []
    return out


def _wants_best_strategy(request: str) -> bool:
    """仅用于 LLM 不可用时的离线降级判别(在线由 intent=ranking 决定)。"""
    return any(k in (request or "") for k in (
        "哪个", "哪一个", "最好", "最佳", "最优", "最强", "最高", "第一", "排名", "排行", "表现最", "效果最",
    ))


def _metric_line(s: dict) -> str:
    m = s.get("metrics") or {}
    return (f"{s['strategy_id']}: status={s['status']}, annual={m.get('annual')}, "
            f"sharpe={m.get('sharpe')}, maxdd={m.get('maxdd')}, calmar={m.get('calmar')}")


# 排序口径:键 → (中文名, 取值函数)。maxdd 为负,越接近 0 越好 → 统一 reverse=True 即可。
_RANK_LABELS = {"sharpe": "夏普", "calmar": "卡玛", "annual": "年化", "maxdd": "最大回撤"}


def _rank_value(s: dict, rank_by: str) -> float:
    v = (s.get("metrics") or {}).get(rank_by)
    return float(v) if v is not None else -1e9   # 缺该指标 → 垫底


def _ranking_out(live: list[dict], rank_by: str | None) -> tuple[AgentOutput, object]:
    rank_by = rank_by if rank_by in _RANK_LABELS else "sharpe"
    ranked = sorted(live, key=lambda s: _rank_value(s, rank_by), reverse=True)
    if not ranked:
        return _count_out([], []), []
    best = ranked[0]
    m = best.get("metrics") or {}
    out = AgentOutput(
        summary=(
            f"按{_RANK_LABELS[rank_by]}排序,当前在册策略里最佳是 {best['strategy_id']}: "
            f"年化 {float(m.get('annual') or 0.0):.2%}, 最大回撤 {float(m.get('maxdd') or 0.0):.2%}, "
            f"夏普 {float(m.get('sharpe') or 0.0):.2f}, 卡玛 {float(m.get('calmar') or 0.0):.2f}。"
        ),
        evidence=[_metric_line(s) for s in ranked[:5]],
        recommendation=["排序口径可改:夏普 / 卡玛 / 年化 / 回撤 / 样本外 WF"],
        confidence=0.9,
    )
    payload = {"rank_by": rank_by, "ranked": [{"id": s["strategy_id"], **(s.get("metrics") or {})} for s in ranked[:8]]}
    return out, payload


def _count_out(data: list[dict], live: list[dict]) -> AgentOutput:
    return AgentOutput(
        summary=f"台账 {len(data)} 个版本,在册 {len(live)}。",
        evidence=[f"{s['strategy_id']}: {s['status']}" for s in data[:8]],
        confidence=0.85,
    )


def _named_out(named: list[dict]) -> tuple[AgentOutput, object]:
    fam = named[0]
    out = AgentOutput(
        summary=f"{fam['family']} 家族,命中 {len(named)} 个版本。",
        evidence=[f"假设: {fam.get('hypothesis', '') or '未登记'}",
                  f"适用市况: {fam.get('regime', '') or '未登记'}",
                  *[_metric_line(s) for s in named[:8]]],
        recommendation=["绩效为历史回测口径,不代表未来,不构成买卖建议"],
        confidence=0.85,
    )
    # 喂带标注的精确计数,避免 DeepSeek 自己数版本数出错(→被护栏否决回退)
    payload = {"family": fam["family"], "命中版本数": len(named),
               "versions": [{"id": s["strategy_id"], "status": s["status"], **(s.get("metrics") or {})} for s in named]}
    return out, payload


def _each_out(data: list[dict]) -> tuple[AgentOutput, object]:
    live_count = sum(1 for s in data if s["status"] == "在册")
    display_list = [s for s in data if s["status"] == "在册"]
    if not display_list:
        display_list = [s for s in data if s["status"] in ("参考", "候选")]
    proj = [
        {"id": s["strategy_id"], "status": s["status"], "family_name": s.get("family_name"),
         "hypothesis": (s.get("hypothesis") or "")[:50], **(s.get("metrics") or {})}
        for s in display_list[:30]
    ]
    out = AgentOutput(
        summary=f"在册 {live_count} 个策略,台账共 {len(data)} 个版本。" if live_count > 0 else f"在册 0 个策略（已全部降级防守；当前候选/参考共 {len(display_list)} 个），台账共 {len(data)} 个版本。",
        evidence=[_metric_line(s) for s in display_list[:10]],
        recommendation=["绩效为历史回测口径,不构成买卖建议"],
        confidence=0.85,
    )
    return out, {"在册策略数": live_count, "台账版本总数": len(data), "在册策略": proj}


def _match_family(data: list[dict], hint: str | None) -> list[dict]:
    """把 LLM 抽出的策略名(或原文)对齐到真实 family——不存在就返回空(挡 LLM 幻觉名)。"""
    if not hint:
        return []
    h = hint.lower()
    return [
        s for s in data
        if (s.get("family") and len(s["family"]) >= 3 and s["family"].lower() in h)
        or (s.get("family_name") and s["family_name"] in hint)
    ]


def _strategies_answer(data: list[dict], request: str, intent: str | None = None,
                       rank_by: str | None = None, entity: str | None = None) -> tuple[AgentOutput, object]:
    """返回 (确定性 out, 给 DeepSeek 讲解用的 data)。
    intent 由 LLM 给出时纯意图驱动;intent=None(LLM 不可用)时回退关键词判别。"""
    live = [s for s in data if s["status"] == "在册"]
    rank_pool = live if live else [s for s in data if s["status"] in ("参考", "候选")]
    if intent is None:                                   # ── 离线降级:关键词 ──
        named = _match_family(data, request)
        if named:
            return _named_out(named)
        if any(k in (request or "") for k in ("每个", "各个", "逐个", "分别", "都有哪些", "哪些策略", "所有策略", "各策略")):
            return _each_out(data)
        if _wants_best_strategy(request):
            return _ranking_out(rank_pool, rank_by)
        return _count_out(data, live), data
    # ── 在线:LLM 意图驱动(排名/计数仍由代码算)──
    if intent == "named":
        named = _match_family(data, entity) or _match_family(data, request)
        if named:
            return _named_out(named)
        return _count_out(data, live), data              # 名字没对上(LLM 抽错)→ 安全退回
    if intent == "ranking":
        return _ranking_out(rank_pool, rank_by)
    if intent == "each":
        return _each_out(data)
    return _count_out(data, live), data                  # count / overview / 兜底


def _portfolio_output(data: dict) -> AgentOutput:
    return AgentOutput(
        summary=(
            f"当前 {data['stance']}({data['regime']});现金 {data['cash']/10000:.0f}万,"
            f"持仓 {len(data['current_positions'])} 只;目标 {len(data['target_holdings'])} 只。"
        ),
        evidence=[data.get("note", ""), data.get("target_note", "")],
        recommendation=["调仓前过风控页"],
        confidence=0.85,
    )


def _factors_output(data: list[dict]) -> AgentOutput:
    return AgentOutput(
        summary=f"台账 {len(data)} 个 alpha 家族:" + "、".join(d["name"] for d in data),
        evidence=[f"{d['display_name'] or d['name']}({d['n_versions']}版本)" for d in data],
        confidence=0.85,
    )


def _experiments_output(data: dict) -> AgentOutput:
    return AgentOutput(
        summary=f"假设池 {data['total']} 候选,淘汰率 {data['discard_ratio']:.0%},已登记 {data['registered']}。",
        evidence=[f"{s['stage']}: {s['count']}" for s in data["stages"]],
        confidence=0.85,
    )


_PRICING_STATE_CN = {
    "lagged_opportunity": "传导滞后/估值偏低",
    "priced_in_risk": "提前透支/偏高",
    "fairly_priced": "定价合理",
}


def _pct(v) -> str:
    return f"{v:.1%}" if isinstance(v, (int, float)) else "缺数据"


def _fundamental_output(p: dict) -> AgentOutput:
    """个股基本面画像 → 确定性 summary + evidence(数字全来自 fundamental_profile)。"""
    q, b, v, pr = p.get("quality", {}), p.get("bargaining", {}), p.get("valuation", {}), p.get("pricing", {})
    code, name = p.get("code"), p.get("name")
    gap, state = pr.get("pricing_gap"), pr.get("pricing_state")
    state_cn = _PRICING_STATE_CN.get(state, "—")

    evidence = []
    evidence.append(
        f"质量:毛利率 {_pct(q.get('gross_margin'))}、净利率 {_pct(q.get('net_margin'))}、"
        f"ROE {q.get('roe')}、营收同比 {q.get('or_yoy')}%、净利同比 {q.get('netprofit_yoy')}%"
    )
    if b.get("bpi") is not None:
        _d = lambda x: f"{x:.0f}" if isinstance(x, (int, float)) else "—"
        evidence.append(
            f"议价权:BPI {b['bpi']:+.3f}、现金循环周期 {_d(b.get('ccc_days'))} 天"
            f"(应收 {_d(b.get('dso_days'))}/存货 {_d(b.get('dio_days'))}/应付 {_d(b.get('dpo_days'))} 天)"
        )
    else:
        evidence.append("议价权:BPI/CCC 缺资负表科目(待摄取),当前定价权分仅按毛利估算")
    if b.get("pricing_power_score") is not None:
        evidence.append(f"定价权综合分 {b['pricing_power_score']:.2f}(0-1)")
    evidence.append(
        f"估值:PE {v.get('pe')}(历史分位 {_pct(v.get('pe_pctile'))})、"
        f"PB {v.get('pb')}(分位 {_pct(v.get('pb_pctile'))});近20日 {_pct(v.get('ret_20d'))}、近60日 {_pct(v.get('ret_60d'))}"
    )

    gap_txt = f"{gap:+.2f}" if isinstance(gap, (int, float)) else "缺数据"
    summary = f"{code} {name} 基本面:预期差 {gap_txt}({state_cn}),估值处历史 PE {_pct(v.get('pe_pctile'))} / PB {_pct(v.get('pb_pctile'))} 分位。"
    return AgentOutput(
        summary=summary,
        evidence=evidence,
        risk=(["净利同比下滑,增收不增利" ] if isinstance(q.get("netprofit_yoy"), (int, float)) and q["netprofit_yoy"] < 0 else []),
        recommendation=["基本面/估值画像,辅助研究判断,不构成买卖建议"],
        confidence=0.85,
    )


def _stock_output(data: dict) -> AgentOutput:
    rets = data.get("returns", {})
    basic = data.get("daily_basic", {})
    mf = data.get("moneyflow", {})
    code, name = data.get("code"), data.get("name")
    price, bdate = data.get("price_cny"), data.get("basic_date")
    evidence = []
    if price is not None:
        evidence.append(f"真实股价(不复权)≈ {price} 元(据总市值/总股本,{bdate})")
    evidence.append(f"近20日收益 {rets['ret_20d']:.2%}" if rets.get("ret_20d") is not None else "20日收益缺数据")
    evidence.append(f"近60日收益 {rets['ret_60d']:.2%}" if rets.get("ret_60d") is not None else "60日收益缺数据")
    if basic:
        evidence.append(f"PE {basic.get('pe')}, PE_TTM {basic.get('pe_ttm')}, PB {basic.get('pb')}, PS {basic.get('ps')}")
        if basic.get("total_mv") is not None:
            evidence.append(f"总市值 {float(basic['total_mv'])/1e4:.0f} 亿元, 换手率 {basic.get('turnover_rate')}%")
    if mf and mf.get("net_mf_amount") is not None:
        evidence.append(f"主力资金净流入 {mf.get('net_mf_amount')} 万元")
    head = (f"{code} {name} 最新股价约 {price} 元。" if price is not None
            else f"{code} {name} 数据画像(缺市值,无法推算真实股价)。")
    return AgentOutput(
        summary=head,
        evidence=evidence,
        recommendation=["股价为不复权真实价(price/daily 后复权价非股价);数据画像,不构成买卖建议"],
        confidence=0.9,
    )


@dataclass(frozen=True)
class SystemStatusSkill:
    name: str = "system_status"
    forced_tool: str | None = None   # LLM 意图解析指定的工具;None 则走关键词/页面
    intent: str | None = None        # strategies 子意图:ranking/count/named/each/overview
    rank_by: str | None = None       # ranking 排序口径:sharpe/calmar/annual/maxdd
    entity: str | None = None        # 点名的策略

    def answer(self, request: str, context: dict) -> dict:
        tools = tool_registry()
        if any(k in request for k in ("调仓", "下单", "执行", "卖出", "买入", "降仓")):
            tool = tools["rebalance"]
            out = AgentOutput(
                summary=f"「{tool.desc}」属 {tool.risk} 风险动作,需人工二次确认后才执行——Agent 不自动执行。",
                recommendation=[f"如确认,请在对应页面手动触发 {tool.name}"],
                risk=["高风险动作默认不执行"],
                requires_human_confirmation=True,
                confidence=0.9,
            )
            out = _doc_sources(out, "为什么 AI 不能直接下单", context)
            out.source_types = sorted(set(out.source_types + ["runtime"]))
            out.suggested_navigation = ["/trade-readiness", "/portfolio"]
            return {"output": out, "tool": "rebalance", "risk": tool.risk, "llm_ready": llm_ready()}
        if any(k in request for k in ("回测", "backtest", "复测")):
            tool = tools["run_backtest"]
            out = AgentOutput(
                summary=f"「{tool.desc}」属 {tool.risk} 风险动作,需人工二次确认后才执行——Agent 不自动执行。",
                recommendation=[f"如确认,请在对应页面手动触发 {tool.name}"],
                requires_human_confirmation=True,
                confidence=0.9,
            )
            return {"output": _runtime_sources(out, context, ["/experiments"]), "tool": "run_backtest", "risk": tool.risk, "llm_ready": llm_ready()}
        page = context.get("current_page", "")
        if self.forced_tool in tools and tools[self.forced_tool].risk == "readonly":
            tool_name = self.forced_tool   # 路由层(DeepSeek 调度)已选定只读工具
        elif any(k in request for k in ("策略", "台账", "母策略", "在册")):
            tool_name = "strategies"
        elif any(k in request for k in ("数据质量", "数据", "脏", "缺失")):
            tool_name = "data_quality"
        elif any(k in request for k in ("风控", "风险", "回撤", "超限")):
            tool_name = "risk"
        elif any(k in request for k in ("组合", "持仓", "仓位")):
            tool_name = "portfolio"
        elif any(k in request for k in ("因子", "alpha", "IC", "ic")):
            tool_name = "factors"
        elif any(k in request for k in ("实验", "假设", "漏斗", "候选")):
            tool_name = "experiments"
        else:
            # 无关键词 → 按当前页面领域兜底;页面也认不出 → 给可问清单,绝不默认堆"台账39"
            tool_name = _PAGE_TOOL.get(page)

        if tool_name is None:
            out = AgentOutput(
                summary="我可以回答系统的运行事实:策略台账 / 风控 / 组合 / 数据质量 / 因子 / 实验漏斗;问个股请给 6 位代码,问怎么用可说「系统怎么用」。",
                next_actions=["试问「当前风控如何」「数据质量怎样」「哪个策略最好」"],
                confidence=0.4,
            )
            return {"output": _runtime_sources(out, context, ["/overview"]), "tool": None, "risk": "readonly", "llm_ready": llm_ready()}

        tool = tools[tool_name]
        data = tool.fn()
        narrate_data = data   # 给 DeepSeek 讲解用的数据(策略细节档会换成子集/投影)
        if tool_name == "strategies":
            out, narrate_data = _strategies_answer(data, request, self.intent, self.rank_by, self.entity)
            nav = ["/experiments"]
        elif tool_name == "data_quality":
            out = AgentOutput(
                summary=f"数据质量「{data['verdict']}」:全市场 {data['total']} 只,真问题 {data['severe_count']} 只,跳变 {data['jump_count']}(多为除权/涨跌停)。",
                evidence=[f"clean {data['clean']}/{data['total']}"],
                recommendation=["跳变不等于脏数据,勿据 clean_ratio 误判"],
                confidence=0.9,
            )
            nav = ["/data"]
        elif tool_name == "risk":
            out = AgentOutput(
                summary=f"风控判定「{data['verdict']}」,{len(data['control_actions'])} 项控制动作待确认。",
                evidence=[f"{c['rule']}: {c['current']}/{c['threshold']} → {c['status']}" for c in data["checks"]],
                confidence=0.9,
            )
            nav = ["/risk"]
        elif tool_name == "portfolio":
            out = _portfolio_output(data)
            nav = ["/portfolio"]
        elif tool_name == "factors":
            out = _factors_output(data)
            nav = ["/factors"]
        elif tool_name == "experiments":
            out = _experiments_output(data)
            nav = ["/experiments"]
        else:
            out = AgentOutput(summary=str(data)[:300], confidence=0.75)
            nav = ["/overview"]

        out = _narrate(out, request, context, tool_name, narrate_data)
        return {
            "output": _runtime_sources(out, context, nav),
            "tool": tool_name,
            "risk": tool.risk,
            "llm_ready": llm_ready(),
        }


@dataclass(frozen=True)
class StockDataSkill:
    name: str = "stock_data"
    entity: str | None = None   # LLM 抽出的股票名/代码
    intent: str | None = None   # fundamental=基本面/议价权/预期差画像;否则=行情快照

    def answer(self, request: str, context: dict) -> dict:
        from services.read.stocks import resolve_stock_code
        # 名字或代码都能解析:先看 LLM 抽的 entity,再看原文(含历史里带过的名字)
        code = resolve_stock_code(self.entity or "") or resolve_stock_code(request)
        if not code:
            out = AgentOutput(
                summary="没认出是哪只股票。给个股票名或 6 位代码就行(如「贵州茅台」或 600519)。",
                confidence=0.3,
            )
            return {"output": _runtime_sources(out, context, ["/data"]), "tool": "stock_profile", "risk": "readonly", "llm_ready": llm_ready()}

        # 基本面/产业链/预期差深度画像
        if self.intent == "fundamental":
            from services.read.fundamentals import fundamental_profile
            try:
                prof = fundamental_profile(code)
            except (FileNotFoundError, ValueError):
                out = AgentOutput(summary=f"数据基础设施里没有 {code} 的财务/行情数据,无法做基本面画像。", confidence=0.3)
                return {"output": _runtime_sources(out, context, ["/data"]), "tool": "fundamental_profile", "risk": "readonly", "llm_ready": llm_ready()}
            out = _narrate(_fundamental_output(prof), request, context, "fundamental_profile",
                           {k: v for k, v in prof.items() if k != "data_sources"})
            return {"output": _runtime_sources(out, context, ["/data", "/factors"]), "tool": "fundamental_profile", "risk": "readonly", "llm_ready": llm_ready()}

        tool = tool_registry()["stock_profile"]
        try:
            data = tool.fn(code)
        except (FileNotFoundError, ValueError):
            out = AgentOutput(summary=f"数据基础设施里没有 {code} 的行情数据,无法画像。", confidence=0.3)
            return {"output": _runtime_sources(out, context, ["/data"]), "tool": "stock_profile", "risk": "readonly", "llm_ready": llm_ready()}
        # 喂给 DeepSeek 的数据剔除后复权 OHLC(防止把后复权价当股价讲),只留真实股价+估值+收益+资金流
        b = data.get("daily_basic", {})
        narrate_data = {
            "code": data.get("code"), "name": data.get("name"),
            "真实股价_元_不复权": data.get("price_cny"), "对应日期": data.get("basic_date"),
            "近20日收益率": data.get("returns", {}).get("ret_20d"),
            "近60日收益率": data.get("returns", {}).get("ret_60d"),
            "估值": {k: b.get(k) for k in ("pe", "pe_ttm", "pb", "ps", "ps_ttm", "total_mv", "circ_mv", "turnover_rate", "dv_ratio", "dv_ttm")},
            "资金流_万元": data.get("moneyflow", {}),
            "说明": "股价为不复权真实价;后复权 OHLC 已排除勿引用;不构成买卖建议",
        }
        out = _narrate(_stock_output(data), request, context, "stock_profile", narrate_data)
        return {
            "output": _runtime_sources(out, context, ["/data"]),
            "tool": "stock_profile",
            "risk": tool.risk,
            "llm_ready": llm_ready(),
        }


@dataclass(frozen=True)
class SystemGuideSkill:
    name: str = "system_guide"

    def answer(self, request: str, context: dict) -> dict:
        adapter = get_adapter()
        hits = retrieve_knowledge(request, limit=4)
        if adapter.available() and hits:
            system_prompt = (
                "你是系统使用说明助手。只基于提供的系统手册/规则片段回答怎么使用系统。"
                "不要回答当前运行数量、个股行情或投资建议。"
            )
            context_pieces = [f"【来源: {h.title} ({h.source_path})】\n{h.text}" for h in hits]
            prose = adapter.complete(
                system_prompt,
                f"用户请求: {request}\n\n系统手册片段:\n\n" + "\n\n---\n\n".join(context_pieces),
                max_tokens=1500,
            )
            out = AgentOutput(summary=prose or "未找到足够的系统使用说明。", confidence=0.8 if prose else 0.4)
        else:
            out = AgentOutput(
                summary="我是系统使用助手。可问:页面怎么用、策略入册流程、AI 为什么不能下单、回测口径是什么。",
                confidence=0.6,
            )
        return {"output": _doc_sources(out, request, context), "tool": None, "risk": None, "llm_ready": llm_ready()}


def _keyword_skill(text: str) -> AgentSkill:
    """确定性关键词路由,仅作 LLM 不可用 / 解析失败时的离线降级。"""
    if any(k in text for k in ("怎么用", "如何使用", "使用", "说明", "手册", "工具", "页面", "导航")):
        return SystemGuideSkill()
    if any(k in text for k in ("能干", "能做", "干什么", "功能", "可以做", "能帮我", "怎么开始", "入门")):
        return SystemGuideSkill()
    if any(k in text for k in ("为什么", "为何", "不能", "不可以", "规则", "边界")) and any(
        k in text for k in ("下单", "买入", "卖出", "调仓", "交易")
    ):
        return SystemGuideSkill()
    return SystemStatusSkill()


def route_skill(request: str, context: dict | None = None) -> AgentSkill:
    """Agent 调度:确定性安全前置(LLM 不可覆盖)→ DeepSeek 结构化意图解析 → 离线关键词降级。"""
    context = context or {}
    text = request or ""
    # 1) 安全前置:抽到 6 位股票代码必走数据源(确定性提取,非措辞判断,LLM 不可覆盖)
    if extract_stock_code(text):
        return StockDataSkill()
    # 2) DeepSeek 结构化意图解析:意图/子意图/排序口径/实体全部由 LLM 理解(不靠关键词清单)
    intent = _parse_intent(request, context)
    if intent:
        skill = intent.get("skill")
        if skill == "system_guide":
            return SystemGuideSkill()
        if skill == "stock_data":
            return StockDataSkill(entity=intent.get("entity"), intent=intent.get("intent"))   # entity 解析+fundamental 分流
        tool = intent.get("tool")
        return SystemStatusSkill(
            forced_tool=tool if tool in _STATUS_TOOLS else None,
            intent=intent.get("intent"),
            rank_by=intent.get("rank_by"),
            entity=intent.get("entity"),
        )
    # 3) LLM 不可用 / 解析失败 → 离线关键词降级
    return _keyword_skill(text)
