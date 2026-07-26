"""Agent 页面上下文(SPEC §14.1 / WEB_DESIGN §14.1)。Phase 0 仅定义形状。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    current_page: str = ""          # overview|data|factor|backtest|...
    selected_object_type: str = ""  # factor|strategy|portfolio|experiment|...
    selected_object_id: str = ""
    date_range: str = ""
    universe: str = ""
    active_filters: dict = Field(default_factory=dict)
    recent_actions: list[str] = Field(default_factory=list)
