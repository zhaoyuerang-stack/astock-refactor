"""SPEC §7 核心数据模型(Pydantic v2)。

这是产品的 write-schema 契约,但**不是运行时本体的真相源**。

铁律:纯 DTO,不 import 任何业务层。

单一本体来源:Hypothesis / Experiment / ExperimentResult 的运行时本体唯一定义在
``factory.ontology``(内容哈希冻结 dataclass,被 factory/workflow/knowledge 20+ 文件承重)。
为消除"两套语言说同一件事",这三个曾在本文件重复定义的 DTO 已删除——需要这些概念时
一律 import ``factory.ontology``。

本文件现保留:
  · 尚未接线的产品 write-schema 占位:FactorDefinition / Strategy / PortfolioState
  · 真实在用的活跃 DTO:ControlAction / AgentOutput / AgentTask
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── §7.2 FactorDefinition ──────────────────────────────────────────────────────
class FactorDefinition(BaseModel):
    factor_id: str
    name: str
    hypothesis_id: str = ""
    formula: str = ""
    lookback_window: int = 0
    frequency: str = "daily"  # daily|weekly|monthly
    universe: str = ""
    neutralization_method: str = "none"  # none|industry|market_cap|industry_market_cap
    winsorize_method: str = ""
    standardize_method: str = ""
    direction: str = "positive"  # positive|negative
    created_at: datetime | None = None


# ── §7.3 Experiment / §7.4 ExperimentResult ────────────────────────────────────
# 运行时本体唯一来源 = factory.ontology.{Experiment, ExperimentResult}(见模块 docstring)。
# 此处不再重复定义 Pydantic DTO。


# ── §7.5 Strategy ──────────────────────────────────────────────────────────────
class Strategy(BaseModel):
    strategy_id: str
    name: str
    type: str = "factor_topn"  # factor_topn|multi_factor|breakout|rotation|timing
    hypothesis_id: str = ""
    entry_rule: str = ""
    exit_rule: str = ""
    position_rule: str = ""
    stop_loss_rule: str = ""
    regime_filter: str = ""
    risk_budget: float = 0.0
    status: str = "draft"  # draft|active|paused|retired


# ── §7.6 PortfolioState ────────────────────────────────────────────────────────
class PortfolioState(BaseModel):
    portfolio_id: str
    date: Optional[date] = None  # noqa: UP045 —— 字段名遮蔽类型名;改写成 date | None 时,pydantic get_type_hints 会在类命名空间把 date 解析成字段默认 None,None|None 直接 TypeError
    nav: float = 0.0
    cash_ratio: float = 0.0
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    var_95_1d: float = 0.0
    industry_exposure: dict = Field(default_factory=dict)
    factor_exposure: dict = Field(default_factory=dict)
    risk_status: str = "normal"  # normal|watch|warning|breach|stopped


# ── §7.7 ControlAction ─────────────────────────────────────────────────────────
class ControlAction(BaseModel):
    action_id: str
    date: datetime | None = None
    object_type: str = ""  # data|factor|strategy|portfolio|experiment|agent
    object_id: str = ""
    trigger_state: str = ""
    action: str = ""  # increase|decrease|pause|stop|retest|archive|report|alert
    reason: str = ""
    recommendation: str = ""
    requires_confirmation: bool = True
    executed: bool = False
    executed_by: str = ""  # human|system|agent


# ── §7.8 AgentTask + §9.4 agent_output ─────────────────────────────────────────
class AgentCitation(BaseModel):
    """Knowledge/tool source cited by an Agent answer."""
    source_id: str = ""
    source_type: str = ""  # system_manual|rules|runtime|research|ui_context
    title: str = ""
    source_path: str = ""
    excerpt: str = ""


class AgentOutput(BaseModel):
    """SPEC §9.4 结构化输出。"""
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    risk: list[str] = Field(default_factory=list)
    recommendation: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    citations: list[AgentCitation] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    suggested_navigation: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    requires_human_confirmation: bool = False


class AgentTask(BaseModel):
    task_id: str
    page_context: str = ""  # overview|data|factor|backtest|portfolio|risk|experiment|assistant|settings
    user_request: str = ""
    context_refs: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending|running|completed|failed
    output_type: str = ""  # explanation|report|recommendation|check|summary
    output: str = ""
    confidence: float = 0.0
    created_at: datetime | None = None
