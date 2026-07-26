"""Agent 结构化输出构造(SPEC §9.4)。Phase 0:re-export + 安全默认构造器。"""
from __future__ import annotations

from contracts.models import AgentOutput


def make_output(summary: str = "", *, confidence: float = 0.0,
                requires_human_confirmation: bool = False, **kw) -> AgentOutput:
    """统一构造 AgentOutput,默认标注为研究辅助(非投资建议由 UI 层渲染时附加)。"""
    return AgentOutput(
        summary=summary,
        confidence=confidence,
        requires_human_confirmation=requires_human_confirmation,
        **kw,
    )
