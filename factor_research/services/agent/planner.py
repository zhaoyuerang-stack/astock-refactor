"""Agent planner —— 请求入口 ask():经 route_skill 路由并落 AgentTask 审计。

不越权铁律(SPEC §9.2):
- readonly 工具 → 自动执行并解读。
- mid/high 工具(回测/调仓)→ **不执行**,返回 requires_human_confirmation 的提案。
- Agent 只能调 tool_registry 白名单;绝不直写台账/下单。

路由由 services.agent.skills.route_skill 负责(确定性安全前置 → DeepSeek 结构化
意图解析 → 关键词离线降级);解读/引用由各 Skill 产出结构化 AgentOutput。
每次产出落 AgentTask 审计(data_lake/agent/agent_tasks.jsonl)。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from contracts.models import AgentTask
from services.agent.skills import route_skill

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)
_TASK_LOG = ROOT / "data_lake" / "agent" / "agent_tasks.jsonl"


def _task_id(request: str, context: dict) -> str:
    return "t-" + hashlib.sha1(f"{request}|{context.get('current_page','')}".encode()).hexdigest()[:10]


def _log_task(task: AgentTask) -> None:
    try:
        _TASK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _TASK_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(task.model_dump(), ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        # 审计落盘失败不阻断主路径(调用方仍拿 AgentOutput),但必须留痕
        logger.warning("agent task audit log write failed: %s", exc)


def ask(request: str, context: dict | None = None) -> dict:
    """返回 {output: AgentOutput, task_id, tool, risk, llm_ready}。"""
    context = context or {}
    task = AgentTask(task_id=_task_id(request, context),
                     page_context=context.get("current_page", ""),
                     user_request=request, status="running")
    skill = route_skill(request, context)
    result = skill.answer(request, context)
    out = result["output"]
    tool_name = result.get("tool")
    if tool_name:
        task.tools_used = [tool_name]
    task.output_type = "explanation"
    task.status = "completed"
    task.output = out.summary
    task.confidence = out.confidence
    task.context_refs = [c.source_path for c in out.citations]
    _log_task(task)
    return {"output": out.model_dump(), "task_id": task.task_id,
            "tool": tool_name, "risk": result.get("risk"), "llm_ready": result.get("llm_ready", False)}
