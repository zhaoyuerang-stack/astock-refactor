"""Agent 工具白名单 + 不越权分级(SPEC §9.2 / WEB_DESIGN §14.2 / ADR-037)。

铁律:Agent 只能调白名单工具;中/高风险动作 requires_human_confirmation。
"""
from __future__ import annotations

RISK_READONLY = "readonly"
RISK_LOW = "low"
RISK_MID = "mid"
RISK_HIGH = "high"

REQUIRES_CONFIRMATION = {RISK_MID, RISK_HIGH}


def requires_confirmation(risk_level: str) -> bool:
    return risk_level in REQUIRES_CONFIRMATION


from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str
    desc: str
    fn: Callable | None
    args: tuple[str, ...] = ()


def tool_registry() -> dict[str, Tool]:
    """白名单工具。读类 = readonly; L0 probe = low; run_backtest = mid; 高风险 = 提案。"""
    from contracts.evidence import EvidenceTier
    from services.agent.evidence import wrap_tool_result
    from services.agent.protocols import list_protocols
    from services.read import experiments as ex
    from services.read import factors as fac
    from services.read import portfolio as pf
    from services.read import registry as reg
    from services.read import risk as rk
    from services.read import state as st
    from services.read.data_gap import data_gap_audit
    from services.read.stocks import resolve_stock_code, stock_profile
    from services.read.strategy_idea import check_strategy_idea

    def _idea(idea: str):
        raw = check_strategy_idea(idea)
        env = wrap_tool_result(
            tool_name="strategy_idea_check",
            result=raw,
            evidence_tier=EvidenceTier.PRECHECK,
            protocol_id="idea_precheck",
            summary=(raw.get("trust") or {}).get("headline") or "strategy idea precheck",
            limits=list(raw.get("limits") or []),
        )
        out = raw if isinstance(raw, dict) else {"value": raw}
        out = {**out, "evidence_envelope": env.as_public_dict()}
        return out

    def _gap(idea: str):
        raw = data_gap_audit(idea)
        env = wrap_tool_result(
            tool_name="data_gap_audit",
            result=raw,
            evidence_tier=EvidenceTier.PRECHECK,
            protocol_id="data_gap_audit",
            summary=f"missing={len(raw.get('missing') or [])}",
        )
        return {**raw, "evidence_envelope": env.as_public_dict()}

    def _probe(
        factor_name: str,
        idea: str = "",
        start: str = "2018-01-01",
        cutoff: str = "2022-12-31",
        end: str = "2024-12-31",
        universe: str = "all",
        full: str = "true",
    ):
        from services.actions.run_signal_probe import run_signal_probe

        raw = run_signal_probe(
            factor_name=factor_name,
            idea=idea,
            start=start,
            cutoff=cutoff,
            end=end,
            universe=universe,
            full=full,
        )
        tier = EvidenceTier.L0_PROBE if raw.get("registered") else EvidenceTier.PRECHECK
        env = wrap_tool_result(
            tool_name="run_signal_probe",
            result=raw,
            evidence_tier=tier,
            protocol_id="proxy_or_signal_probe",
            summary=raw.get("note") or "l0 probe receipt",
        )
        return {**raw, "evidence_envelope": env.as_public_dict()}

    def _backtest(**kw):
        from services.actions.run_backtest import run_backtest

        raw = run_backtest(**kw).model_dump()
        env = wrap_tool_result(
            tool_name="run_backtest",
            result=raw,
            evidence_tier=EvidenceTier.ENGINE,
            protocol_id="engine_backtest",
            summary="BacktestEngine result (not admission)",
            requires_human_confirmation=True,
        )
        return {**raw, "evidence_envelope": env.as_public_dict()}

    def _propose(action_kind: str, target: str = "", rationale: str = ""):
        from services.agent.proposals import propose_high_risk_action

        raw = propose_high_risk_action(
            action_kind=action_kind, target=target, rationale=rationale,
        )
        env = wrap_tool_result(
            tool_name="propose_high_risk_action",
            result=raw,
            evidence_tier=EvidenceTier.NARRATIVE,
            protocol_id="nine_gate_or_promote",
            summary=f"proposal {action_kind} executed=False",
            requires_human_confirmation=True,
        )
        return {**raw, "evidence_envelope": env.as_public_dict()}

    return {
        "data_quality": Tool(
            "data_quality", RISK_READONLY, "数据质量状态",
            lambda: st.data_quality().model_dump(),
        ),
        "resolve_stock_code": Tool(
            "resolve_stock_code", RISK_READONLY,
            "把股票名称或用户文本解析为 6 位 A 股代码；无法确认时返回 null",
            lambda query: resolve_stock_code(query),
            ("query",),
        ),
        "stock_profile": Tool(
            "stock_profile", RISK_READONLY,
            "读取个股价格日期、收益、估值、资金流和数据来源画像",
            lambda code: stock_profile(code),
            ("code",),
        ),
        "strategy_idea_check": Tool(
            "strategy_idea_check", RISK_READONLY,
            "策略想法边界预检 + Evidence Envelope(precheck)",
            _idea,
            ("idea",),
        ),
        "data_gap_audit": Tool(
            "data_gap_audit", RISK_READONLY,
            "湖/注册表字段可达性审计;缺字段诚实列出(ADR-037)",
            _gap,
            ("idea",),
        ),
        "list_protocols": Tool(
            "list_protocols", RISK_READONLY,
            "列出 ADR-037 验证协议注册表",
            lambda: list_protocols(),
        ),
        "market_state": Tool(
            "market_state", RISK_READONLY, "当前持仓/动作状态",
            lambda: st.market_state().model_dump(),
        ),
        "factors": Tool(
            "factors", RISK_READONLY, "alpha 因子家族",
            lambda: [f.model_dump() for f in fac.list_factors()],
        ),
        "strategies": Tool(
            "strategies", RISK_READONLY, "母策略台账",
            lambda: [s.model_dump() for s in reg.list_strategies()],
        ),
        "portfolio": Tool(
            "portfolio", RISK_READONLY, "当前/目标组合",
            lambda: pf.current_portfolio().model_dump(),
        ),
        "risk": Tool(
            "risk", RISK_READONLY, "风控评估",
            lambda: rk.risk_report().model_dump(),
        ),
        "experiments": Tool(
            "experiments", RISK_READONLY, "假设池漏斗",
            lambda: ex.funnel().model_dump(),
        ),
        # Receipt-only L0 is cheap and non-admitting; keep readonly so desktop Strict rail can call it.
        # Full heavy probe remains scripts/research/signal_source_probe.py (human/ops).
        "run_signal_probe": Tool(
            "run_signal_probe", RISK_READONLY,
            "L0 probe 回执(非 alpha);end 必须 < holdout boundary;不入册",
            _probe,
            ("factor_name",),
        ),
        "run_backtest": Tool(
            "run_backtest", RISK_MID, "跑生产口径回测(须确认)",
            _backtest,
        ),
        "propose_high_risk_action": Tool(
            "propose_high_risk_action", RISK_HIGH,
            "高风险动作仅提案(promote/register/onboarding/deploy),永不执行",
            _propose,
            ("action_kind",),
        ),
        "rebalance": Tool("rebalance", RISK_HIGH, "调仓执行", None),
    }
