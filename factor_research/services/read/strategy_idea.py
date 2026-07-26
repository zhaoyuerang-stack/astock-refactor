"""策略想法预检 —— 供 Agent 经 CLI 调用的确定性只读能力。

Agent(Pi) 决定何时调用;本模块只读系统事实并返回边界报告。
不宣布有效、不产出伪净值;不按个别因子名写死业务分支。

数据来源:
- CostModel(固定成本)
- data_quality / experiments funnel
- strategy_registry 家族列表 + factors.registry 已注册因子名
"""
from __future__ import annotations

import re
from typing import Any

from core.engine import CostModel

_DEFINITION_FIELDS = (
    "股票池/宇宙",
    "因子或信号定义",
    "调仓频率",
    "持仓数量",
    "样本区间",
    "失败/退役条件",
)

_REBALANCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("daily", re.compile(r"日频|每天|日调仓|daily", re.I)),
    ("weekly", re.compile(r"周频|每周|周调仓|weekly", re.I)),
    ("monthly", re.compile(r"月频|每月|月调仓|monthly", re.I)),
    ("20d", re.compile(r"20\s*日|每\s*20\s*天|rebalance.?20", re.I)),
]

_TOP_N_RE = re.compile(r"(?:持仓|top|前)\s*(\d{1,3})\s*(?:只|支|股)?", re.I)

# 从用户句中抽 token 时跳过的高频虚词(非业务词表)
_STOP_TOKENS = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "as", "is", "be",
    "use", "using", "try", "with", "from", "by", "at", "it", "this", "that",
    "factor", "factors", "strategy", "strategies", "idea", "test", "check",
    "帮我", "请", "用", "作为", "因子", "策略", "试下", "试一试", "一下", "一个",
    "怎么", "如何", "能否", "可以", "是否", "想法", "验证", "回测", "调仓",
}


def _cost_disclosure() -> dict[str, Any]:
    cost = CostModel()
    return {
        "buy_cost": cost.buy_cost,
        "sell_cost": cost.sell_cost,
        "financing_rate": cost.financing_rate,
        "buy_cost_bps": round(cost.buy_cost * 10000, 2),
        "sell_cost_bps": round(cost.sell_cost * 10000, 2),
        "display": (
            f"买侧 {cost.buy_cost * 100:.3f}% / "
            f"卖侧 {cost.sell_cost * 100:.3f}% / "
            f"融资 {cost.financing_rate * 100:.1f}%"
        ),
        "note": "正式回测成本不可调低;UI/Agent 不得为达标改费率",
        "authority": "core.engine.CostModel",
    }


def _match_rebalance(text: str) -> str | None:
    for name, pattern in _REBALANCE_PATTERNS:
        if pattern.search(text):
            return name
    return None


def _match_top_n(text: str) -> int | None:
    m = _TOP_N_RE.search(text or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 500 else None


def _candidate_terms(text: str) -> list[str]:
    """Extract candidate factor/theme terms from free text (no fixed business list)."""
    terms: list[str] = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_]{1,40}|[\u4e00-\u9fff]{2,12}", text or ""):
        token = m.group(0)
        if token.lower() in _STOP_TOKENS or token in _STOP_TOKENS:
            continue
        terms.append(token)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _registered_factor_names() -> set[str]:
    try:
        from factors.registry import FACTOR_REGISTRY, discover

        discover()
        return {str(name) for name in FACTOR_REGISTRY.keys()}
    except Exception:
        return set()


def _list_families():
    from services.read import factors as fac

    return fac.list_factors()


def _match_registry_hits(terms: list[str], registry_names: set[str]) -> list[str]:
    hits: list[str] = []
    lower_map = {n.lower(): n for n in registry_names}
    for term in terms:
        key = term.lower()
        if key in lower_map:
            hits.append(lower_map[key])
            continue
        # partial: registry name contains term or term contains registry name (len>=4)
        for reg_l, reg in lower_map.items():
            if len(term) >= 3 and (term.lower() in reg_l or reg_l in term.lower()):
                hits.append(reg)
                break
    return list(dict.fromkeys(hits))


def _related_families(terms: list[str], limit: int = 8) -> list[dict[str, Any]]:
    families = _list_families()
    scored: list[tuple[int, Any]] = []
    for family in families:
        blob = " ".join(
            [
                str(family.name or ""),
                str(family.display_name or ""),
                str(family.hypothesis or ""),
                str(family.regime or ""),
            ]
        ).lower()
        score = 0
        for term in terms:
            t = term.lower()
            if len(t) < 2:
                continue
            if t in blob:
                score += 2
        if score:
            scored.append((score, family))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [
        {
            "id": family.name,
            "name": family.display_name or family.name,
            "status": family.status,
            "n_registered": family.n_registered,
            "hypothesis": (family.hypothesis or "")[:160],
            "match_score": score,
        }
        for score, family in scored[:limit]
    ]


def _missing_definition_fields(rebalance: str | None, top_n: int | None, has_factor_hit: bool) -> list[str]:
    missing = list(_DEFINITION_FIELDS)
    if rebalance:
        missing = [f for f in missing if f != "调仓频率"]
    if top_n is not None:
        missing = [f for f in missing if f != "持仓数量"]
    if has_factor_hit:
        missing = [f for f in missing if f != "因子或信号定义"]
    return missing


def _implementation_notes(
    terms: list[str],
    registry_hits: list[str],
    related: list[dict[str, Any]],
) -> list[str]:
    """Generic readiness notes derived from live registry/family hits — no per-factor branches."""
    notes: list[str] = []
    technical = [t for t in terms if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,40}", t)]
    unresolved = [t for t in technical if t.lower() not in {h.lower() for h in registry_hits}]
    if unresolved and not registry_hits:
        notes.append(
            "以下候选名在 factors.registry / 台账家族中未命中已注册实现: "
            + ", ".join(unresolved[:8])
            + "。在接入数据源、注册因子并完成 probe 之前，不能当作可回测因子。"
        )
    elif unresolved and registry_hits:
        notes.append(
            "部分候选名未注册: "
            + ", ".join(unresolved[:6])
            + f"；已命中注册因子: {', '.join(registry_hits[:6])}。"
        )
    if not related and not registry_hits and terms:
        notes.append(
            "台账家族与用户用语无明显重叠线索；可能是新方向，也可能只是关键词不足以匹配。"
        )
    return notes


def check_strategy_idea(idea: str) -> dict[str, Any]:
    """Deterministic strategy-idea precheck. Agent decides when to call this."""
    text = (idea or "").strip()
    if not text:
        return {
            "validation_status": "empty_idea",
            "can_claim_valid": False,
            "fake_curve_allowed": False,
            "trust": {
                "banner_status": "neutral",
                "headline": "还没有可预检的策略想法",
                "detail": "请用自然语言描述因子/股票池/调仓/失败条件。",
            },
            "idea_text": "",
            "parsed_hints": {
                "candidate_terms": [],
                "registry_factor_hits": [],
                "rebalance_hint": None,
                "top_n_hint": None,
                "missing_definition_fields": list(_DEFINITION_FIELDS),
            },
            "system_facts": {"cost_model": _cost_disclosure()},
            "required_next_steps": ["用自然语言补充策略想法"],
            "forbidden_claims": [
                "不得宣布策略有效",
                "不得展示伪造净值/夏普/回撤",
                "不得宣称已入册或可实盘",
            ],
            "evidence": ["用户未提供策略想法文本"],
            "limits": ["本结果不是回测,不是 9-Gate,不是入册裁决"],
        }

    terms = _candidate_terms(text)
    rebalance = _match_rebalance(text)
    top_n = _match_top_n(text)
    registry_names = _registered_factor_names()
    registry_hits = _match_registry_hits(terms, registry_names)
    related = _related_families(terms)
    missing = _missing_definition_fields(rebalance, top_n, bool(registry_hits))
    impl_notes = _implementation_notes(terms, registry_hits, related)

    from services.read import experiments as ex
    from services.read import state as st

    quality = st.data_quality(with_duckdb=False).model_dump()
    funnel = ex.funnel().model_dump()
    backtest_blocked = bool(quality.get("backtest_blocked"))
    factor_ready = bool(registry_hits) and not any("未命中已注册实现" in n for n in impl_notes)

    if backtest_blocked:
        banner_status = "blocked"
        headline = "数据质量阻断正式回测;当前只能做想法边界预检"
        detail = "quality_report 标记 backtest_blocked。不得用半截数据假装已验证。"
    elif impl_notes and not factor_ready:
        banner_status = "blocked"
        headline = "想法已接收,但系统中尚无匹配的已注册因子实现"
        detail = impl_notes[0]
    elif missing:
        banner_status = "attention"
        headline = "想法尚未可回测:定义不完整,且未跑确定性门禁"
        detail = f"仍缺 {len(missing)} 项可执行定义;相关匹配只是线索,不是有效性证据。"
    else:
        banner_status = "attention"
        headline = "定义线索较完整,但仍未跑回测与 9-Gate"
        detail = "补齐定义 ≠ 策略有效。下一步必须走 BacktestEngine + 门禁,禁止口头宣布有效。"

    evidence = [
        f"用户想法: {text[:240]}",
        f"成本口径(固定): {_cost_disclosure()['display']}",
        f"数据质量裁决: {quality.get('verdict', '未知')} (clean_ratio={quality.get('clean_ratio')})",
        f"假设池: total={funnel.get('total')} registered={funnel.get('registered')} "
        f"discard_ratio={funnel.get('discard_ratio')}",
        f"从想法抽取候选词: {', '.join(terms[:12]) or '(无)'}",
        f"命中已注册因子: {', '.join(registry_hits) or '(无)'}",
    ]
    evidence.extend(impl_notes)
    if related:
        evidence.append(
            "相关台账家族(线索,非裁决): "
            + ", ".join(f"{item['id']}({item['status']})" for item in related[:5])
        )

    return {
        "validation_status": "idea_precheck",
        "can_claim_valid": False,
        "fake_curve_allowed": False,
        "trust": {
            "banner_status": banner_status,
            "headline": headline,
            "detail": detail,
        },
        "idea_text": text,
        "parsed_hints": {
            "candidate_terms": terms,
            "registry_factor_hits": registry_hits,
            "rebalance_hint": rebalance,
            "top_n_hint": top_n,
            "missing_definition_fields": missing,
        },
        "system_facts": {
            "cost_model": _cost_disclosure(),
            "data_quality": {
                "verdict": quality.get("verdict"),
                "clean_ratio": quality.get("clean_ratio"),
                "severe_count": quality.get("severe_count"),
                "backtest_blocked": backtest_blocked,
                "production_blocked": bool(quality.get("production_blocked")),
            },
            "funnel": {
                "total": funnel.get("total"),
                "registered": funnel.get("registered"),
                "discard_ratio": funnel.get("discard_ratio"),
                "stages": funnel.get("stages"),
            },
            "related_families": related,
            "implementation_notes": impl_notes,
            "factor_ready": factor_ready,
            "registry_size": len(registry_names),
        },
        "required_next_steps": [
            *impl_notes,
            *([f"补齐定义: {field}" for field in missing] if missing else []),
            "用 BacktestEngine + 固定 CostModel 做可审计回测(因子未注册前不可做)",
            "经 workflow / 9-Gate 后才可能入册;Agent 不得代判有效",
            "高风险动作(入册/部署)必须人工确认",
        ],
        "forbidden_claims": [
            "不得宣布策略有效或样本外稳健",
            "不得展示伪造净值曲线 / 夏普 / 回撤",
            "不得把相关家族匹配说成已有同类 alpha",
            "不得把本预检结果当作入册或实盘依据",
        ],
        "evidence": evidence,
        "limits": [
            "本结果是想法边界预检,不是回测绩效",
            "can_claim_valid=false 由确定性代码强制",
            "正式有效性只认 BacktestEngine + 9-Gate + registry 证据",
            "调用方应为 Agent;本函数不负责理解用户意图路由",
        ],
    }
