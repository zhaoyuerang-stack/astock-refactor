"""Agent capability dispatcher — single throat for tool execution (ADR-037).

2026-07-22 P1-4② 自 apps/agent_cli.py 下沉:能力分派器是 agent 控制环的执行
喉咙,属 services.agent 层;apps/agent_cli.py 只留 argparse 壳并 re-export
本模块公共名(AgentCliError/call_capability/capability_catalog),公共面不变。
审计仍在 call_capability 内(services.agent.audit),单一喉咙设计不变。

Readonly tools run freely. Mid-risk tools require confirm_token matching
ASTOCK_MID_CONFIRM_TOKEN. High-risk tools that only propose are treated as
callable when risk is high but fn is not None (proposals); rebalance stays blocked.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.agent.tools import (
    RISK_HIGH,
    RISK_MID,
    RISK_READONLY,
    Tool,
    tool_registry,
)

logger = logging.getLogger(__name__)

# Optional args for tools that accept more than required minimum.
_OPTIONAL_ARGS = {
    "run_signal_probe": {"idea", "start", "cutoff", "end", "universe", "full"},
    "propose_high_risk_action": {"target", "rationale"},
    "run_backtest": {"start", "top_n", "rebalance_days", "factor_window", "timing_ma", "family", "version"},
}


class AgentCliError(RuntimeError):
    """Expected user/tool error with a stable CLI exit path."""


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError) as exc:
            # numpy scalar 转换失败则回退 str(value) 路径,不阻断 CLI 序列化
            logger.warning("agent_cli _jsonable item() failed: %s", exc)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _catalog_tools(registry: dict[str, Tool] | None = None) -> dict[str, Tool]:
    """Expose tools that are executable via CLI (callable fn)."""
    tools = registry if registry is not None else tool_registry()
    return {name: tool for name, tool in tools.items() if callable(tool.fn)}


def capability_catalog(registry: dict[str, Tool] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.name,
            "description": tool.desc,
            "risk": tool.risk,
            "arguments": list(tool.args),
            "requires_confirm": tool.risk in {RISK_MID, RISK_HIGH} and tool.risk != RISK_HIGH
            or tool.risk == RISK_MID,
        }
        for tool in _catalog_tools(registry).values()
    ]


def _check_confirm(tool: Tool, confirm_token: str | None) -> None:
    if tool.risk != RISK_MID:
        return
    expected = os.environ.get("ASTOCK_MID_CONFIRM_TOKEN", "")
    if not expected:
        raise AgentCliError(
            "mid-risk capability requires ASTOCK_MID_CONFIRM_TOKEN in environment "
            "and matching --confirm-token (ADR-037 HITL)"
        )
    if not confirm_token or confirm_token != expected:
        raise AgentCliError(
            "mid-risk capability denied: missing or invalid --confirm-token"
        )


def call_capability(
    name: str,
    arguments: dict[str, Any] | None = None,
    registry: dict[str, Tool] | None = None,
    *,
    confirm_token: str | None = None,
    readonly_only: bool = False,
    audit_context: dict[str, Any] | None = None,
) -> Any:
    """Execute one capability; append-only audit via services.agent.audit (ADR-037 P6.4)."""
    from services.agent.audit import audit_event

    tools = registry if registry is not None else tool_registry()
    tool = tools.get(name)

    def _audit_ctx(risk: str | None) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "risk": risk,
            "confirm_token_present": bool(confirm_token),
            "readonly_only": bool(readonly_only),
        }
        if audit_context:
            ctx.update(audit_context)
        return ctx

    def _audit_error(exc: BaseException, risk: str | None) -> None:
        audit_event(
            name,
            arguments,
            outcome="error",
            error=exc,
            context=_audit_ctx(risk),
        )

    if tool is None:
        exc = AgentCliError(f"unknown capability: {name}")
        _audit_error(exc, None)
        raise exc
    if not callable(tool.fn):
        exc = AgentCliError(f"capability is proposal-only / not executable: {name}")
        _audit_error(exc, tool.risk)
        raise exc
    if readonly_only and tool.risk != RISK_READONLY:
        exc = AgentCliError(f"capability is not available in readonly mode: {name}")
        _audit_error(exc, tool.risk)
        raise exc
    if tool.risk == RISK_HIGH and name == "rebalance":
        exc = AgentCliError(f"capability is not available in readonly mode: {name}")
        _audit_error(exc, tool.risk)
        raise exc

    try:
        _check_confirm(tool, confirm_token)
    except AgentCliError as exc:
        _audit_error(exc, tool.risk)
        raise

    supplied = arguments or {}
    if not isinstance(supplied, dict):
        exc = AgentCliError("arguments must be a JSON object")
        _audit_error(exc, tool.risk)
        raise exc
    required = set(tool.args)
    optional = _OPTIONAL_ARGS.get(name, set())
    provided = set(supplied)
    missing = sorted(required - provided)
    unexpected = sorted(provided - required - optional)
    if missing:
        exc = AgentCliError(f"missing arguments for {name}: {', '.join(missing)}")
        _audit_error(exc, tool.risk)
        raise exc
    if unexpected:
        exc = AgentCliError(f"unexpected arguments for {name}: {', '.join(unexpected)}")
        _audit_error(exc, tool.risk)
        raise exc
    # Only pass known keys
    kwargs = {k: v for k, v in supplied.items() if k in required or k in optional}

    try:
        result = tool.fn(**kwargs)
    except Exception as exc:
        _audit_error(exc, tool.risk)
        raise

    audit_event(
        name,
        arguments,
        outcome="ok",
        context=_audit_ctx(tool.risk),
        result=result,
    )
    return result
