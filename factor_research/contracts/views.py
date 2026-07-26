"""Phase 0 API 读/响应 DTO —— 端点实际返回的形状。

与 ``models``(产品 write-schema)分开:这里是"读出来给前端看"的视图,
可独立于持久化 schema 演进。纯 DTO,不 import 业务层。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from contracts.models import AgentOutput


class BacktestResult(BaseModel):
    """``services.actions.run_backtest`` 的返回。

    字段直接对应 ``core.engine.BacktestResult.metrics`` + detail 摘要,
    保证"service 结果 == strategy_lake.py"可逐项比对。
    """
    annual: float
    vol: float
    sharpe: float
    maxdd: float
    calmar: float
    hit: bool
    n: int
    turnover_annual: float
    cost_annual: float
    yearly_returns: dict[int, float] = Field(default_factory=dict)
    n_stocks: int
    n_days: int
    start: str
    end: str
    family: str = ""
    version: str = ""


class StrategyView(BaseModel):
    """registry 中一个 (family, version) 的只读视图。"""
    strategy_id: str          # f"{family}/{version}"
    family: str
    family_name: str = ""
    family_status: str = ""
    version: str = ""
    status: str = ""
    hypothesis: str = ""
    regime: str = ""
    desc: str = ""
    data_scope: dict | str = ""  # 台账里 data_scope 是结构化 dict(source/口径/指标),少数旧版本可能是字符串
    metrics: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    notes: str = ""
    capacity_m: float = 0.0
    admission: dict = Field(default_factory=dict)   # 双轨准入 {track, rationale}
    nine_gate: dict = Field(default_factory=dict)   # Nine-Gate 审计摘要 {dsr_p, gate4_verdict, n_trials, ...}
    style_betas: dict = Field(default_factory=dict)        # 家族级风格暴露 {size, value, momentum, ...} —— 判断是否风格伪装
    failure_boundaries: dict = Field(default_factory=dict)  # 家族级失效边界 {max_drawdown, max_drawdown_days, ...}
    decay_signal: str = ""                                  # 家族级失效信号(死因/下一步触发条件,注册时写的静态文字)
    decay_check: dict = Field(default_factory=dict)         # 版本级实测衰减结果 {decayed, rolling_3y_sharpe_latest, reasons, checked_at}


class StrategyDetailView(BaseModel):
    """台账版本详情 + 以 family/version 关联的研究运行和专项产物。"""
    strategy: StrategyView
    research_runs: list[dict] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)


class FactorView(BaseModel):
    """alpha 家族(因子)的只读视图(registry 家族级派生)。"""
    name: str                 # family id
    display_name: str = ""
    hypothesis: str = ""
    regime: str = ""
    n_versions: int = 0
    n_registered: int = 0     # 该家族「在册」版本数(0 = 候选/噪音池,未产出有效 alpha)
    status: str = ""


# ── Phase 2 状态层 ─────────────────────────────────────────────────────────────
class DataQualityView(BaseModel):
    """数据质量状态(读 data_lake/quality_report.json)。

    区分真问题与 A股正常现象(铁律#7):severe = 负价/OHLC错;
    跳变>50% 多为除权/涨跌停,单列不计入 severe。
    """
    total: int
    clean: int
    clean_ratio: float
    issue_breakdown: dict = Field(default_factory=dict)
    n_flagged: int = 0
    flagged_sample: list[dict] = Field(default_factory=list)
    severe_count: int = 0           # 真问题股票数(负价/OHLC错)
    jump_count: int = 0             # 跳变标记数(含正常现象)
    verdict: str = ""               # 可用 / 关注 / 不建议回测
    triage_summary: dict = Field(default_factory=dict)
    production_blocked: bool = False
    backtest_blocked: bool = False
    duckdb: dict | None = None      # 可选:DuckDB 即席复核


class StockProfileView(BaseModel):
    """Single-stock data profile backed by data_lake."""
    code: str
    name: str = ""
    price_cny: float | None = None
    basic_date: str | None = None
    latest_price: dict = Field(default_factory=dict)
    returns: dict = Field(default_factory=dict)
    daily_basic: dict = Field(default_factory=dict)
    moneyflow: dict = Field(default_factory=dict)
    data_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FactorHealthView(BaseModel):
    """策略/因子健康(读 reports/factor_health.json)。"""
    name: str
    sharpe: float = 0.0
    momentum_6m: float = 0.0
    trend: str = ""
    as_of: str = ""   # 报告数据截至日(reports/factor_health.json::updated);周期生成非实时,明示时效


class MarketStateView(BaseModel):
    """当前持仓/动作状态(读 signals/state.json)。"""
    current_position: str = ""
    last_action: str = ""
    last_signal_date: str | None = None
    last_rebalance_date: str | None = None
    n_holdings: int = 0


# ── Phase 3 组合 / 风控 ─────────────────────────────────────────────────────────
class Holding(BaseModel):
    code: str
    weight: float


class PortfolioView(BaseModel):
    """当前组合(实盘/纸面)+ 目标组合(选股层 top-N)。"""
    nav: float = 0.0
    cash: float = 0.0
    current_positions: list[Holding] = Field(default_factory=list)
    stance: str = ""              # 当前动作(如 空仓观望)
    regime: str = ""              # bull/bear/...
    note: str = ""                # 如 BEAR→国债ETF
    target_holdings: list[Holding] = Field(default_factory=list)
    target_as_of: str | None = None
    target_note: str = ""


class RiskRuleCheck(BaseModel):
    rule: str
    threshold: float
    current: float | None = None   # None = 无法计算(如空仓/缺数据)
    status: str = "ok"             # ok | warn | breach | na
    note: str = ""


class RiskReport(BaseModel):
    """风控评估:逐条规则 + 超限生成的控制动作。"""
    evaluated_on: str = ""         # target / current
    checks: list[RiskRuleCheck] = Field(default_factory=list)
    control_actions: list[dict] = Field(default_factory=list)  # ControlAction dicts
    verdict: str = "正常"          # 正常 | 预警 | 超限


# ── Phase 4 研究实验 ────────────────────────────────────────────────────────────
class FunnelView(BaseModel):
    """假设池漏斗:DRAFTED→QUEUED→L0~L3→PROMOTED;DISCARDED/SHELVED 旁路。"""
    total: int = 0
    stages: list[dict] = Field(default_factory=list)  # [{stage, count}]
    side: list[dict] = Field(default_factory=list)
    discard_ratio: float = 0.0
    registered: int = 0


class HypothesisView(BaseModel):
    id: str
    name: str = ""
    factor_fn_name: str = ""
    factor_params: dict = Field(default_factory=dict)
    timing_fn_name: str | None = None
    status: str = ""
    source: str = ""               # mutation | llm_paper | anomaly | manual
    mechanism: str = ""
    citation: str = ""
    created_at: str = ""


class RegisteredExperimentView(BaseModel):
    """已晋级登记的实验(台账版本)+ 可复现键(config_hash)。"""
    strategy_id: str
    family_name: str = ""
    version: str = ""
    status: str = ""
    date: str = ""
    config_hash: str = ""
    config: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    data_scope: dict = Field(default_factory=dict)


class ResearchRunView(BaseModel):
    """Machine-readable research script run archived in research_ledger."""
    run_id: str = ""
    script: str = ""
    hypothesis: str = ""
    source: str = ""
    run_at: str = ""
    data_vintage: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    verdict: str = ""
    next_action: str = ""
    decision_state: str = ""   # refuted | pending_review | shadow | promote | informational
    artifact_paths: list[str] = Field(default_factory=list)
    notes: str = ""


class ResearchRunIndexView(BaseModel):
    """Read-optimized index derived from immutable research-run ledger rows."""
    generated_at: str = ""
    summary: dict = Field(default_factory=dict)
    latest_runs: list[ResearchRunView] = Field(default_factory=list)


class ResearchDraftView(BaseModel):
    draft_id: str
    title: str = ""
    source: str = ""
    mechanism: str = ""
    citation: str = ""
    factor_fn_name: str = ""
    factor_params: dict = Field(default_factory=dict)
    timing_fn_name: str | None = None
    timing_params: dict = Field(default_factory=dict)
    data_dependencies: list[str] = Field(default_factory=list)
    status: str = "active"
    linked_work_id: str = ""
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""


class ResearchDraftCreateRequest(BaseModel):
    title: str
    source: str = "manual"
    mechanism: str = ""
    citation: str = ""
    factor_fn_name: str = ""
    factor_params: dict = Field(default_factory=dict)
    timing_fn_name: str | None = None
    timing_params: dict = Field(default_factory=dict)
    data_dependencies: list[str] = Field(default_factory=list)


class ResearchDraftUpdateRequest(BaseModel):
    title: str | None = None
    source: str | None = None
    mechanism: str | None = None
    citation: str | None = None
    factor_fn_name: str | None = None
    factor_params: dict | None = None
    timing_fn_name: str | None = None
    timing_params: dict | None = None
    data_dependencies: list[str] | None = None
    status: str | None = None
    linked_work_id: str | None = None


class ResearchReviewView(BaseModel):
    review_id: str
    kind: str
    item_id: str
    action: str
    notes: str = ""
    reviewer: str = "human"
    reviewed_at: str = ""
    migrated_from: str = ""


class ResearchReviewRequest(BaseModel):
    action: str
    notes: str = ""
    reviewer: str = "human"


class ResearchWorkItemView(BaseModel):
    work_id: str
    kind: str
    item_id: str
    title: str = ""
    source: str = ""
    raw_status: str = ""
    status: str = ""          # blocked | review | ready | running | completed | archived
    stage: str = ""
    mechanism: str = ""
    citation: str = ""
    updated_at: str = ""
    next_action: str = ""
    blocked_reason: str = ""
    latest_result: dict = Field(default_factory=dict)
    review: ResearchReviewView | None = None


class ResearchWorkItemListView(BaseModel):
    items: list[ResearchWorkItemView] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ResearchWorkItemDetailView(BaseModel):
    item: ResearchWorkItemView
    evidence: dict = Field(default_factory=dict)
    runs: list[dict] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)


class ResearchWorkItemActionRequest(BaseModel):
    start: str = "2018-01-01"
    sample_dates: int | None = 120
    version: str = "v1.0"
    target_status: str = ""


class AutoResearchCandidateView(BaseModel):
    """Auto Factor Research candidate submitted as controlled JSON AST."""
    fingerprint: str
    status: str = ""
    source: str = ""
    ast: dict = Field(default_factory=dict)
    complexity_score: float = 0.0
    max_auto_stage: str = ""
    notes: str = ""
    created_at: str = ""


class AutoResearchReviewItemView(BaseModel):
    """Candidate promoted to human review. This is not LIVE registration."""
    fingerprint: str
    status: str = ""
    decision: str = ""
    reason: str = ""
    candidate: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    review_action: str = ""    # "" = 待复核;approve / reject = 已人工决策
    reviewer_notes: str = ""
    reviewed_at: str = ""
    promote_job_id: str = ""
    target_status: str = ""
    registry_status: str = ""
    promote_detail: str = ""
    promote_error: str = ""


class AutoResearchReviewRequest(BaseModel):
    action: str        # approve | reject
    notes: str = ""


class AutoResearchFunnelView(BaseModel):
    """AutoResearch Lite funnel counts."""
    total: int = 0
    stages: list[dict] = Field(default_factory=list)
    review_queue: int = 0


class AutoResearchRunResultView(BaseModel):
    fingerprint: str
    status: str = ""
    decision: str = ""
    reason: str = ""
    protocols: list[str] = Field(default_factory=list)


class AutoResearchRunResponse(BaseModel):
    vintage_id: str = ""
    max_stage: str = "l0"
    results: list[AutoResearchRunResultView] = Field(default_factory=list)


class AutoResearchPromoteResponse(BaseModel):
    """APPROVED 候选 → workflow phase1~4 正式入册的结果。"""
    fingerprint: str
    hypothesis_name: str = ""
    version: str = ""
    registered: bool = False
    detail: str = ""
    target_status: str = ""
    registry_status: str = ""
    phase_summary: dict = Field(default_factory=dict)


class AutoResearchLLMGenResponse(BaseModel):
    """LLM 生成候选并走真实验证线的结果。"""
    model: str = ""
    requested: int = 0
    accepted: int = 0
    rejected: list[str] = Field(default_factory=list)   # 每条 = 拒绝原因
    run: AutoResearchRunResponse = Field(default_factory=AutoResearchRunResponse)


class AutoResearchChampionView(BaseModel):
    fingerprint: str
    island: int = 0
    generation: int = 0
    icir: float = 0.0
    expr: str = ""
    status: str = ""
    decision: str = ""
    reason: str = ""
    novelty: float = 0.0     # 行为新颖性 [0,1]:vs 已评估候选+参考池的最近邻距离
    corr_to_book: float = 0.0  # 对在册 ACTIVE 组合的有符号收益相关(负=防御腿,边际价值高)
    turnover: float = 0.0    # top-N 成员相邻期流失率 [0,1](高=换手快=成本高)
    fitness: float = 0.0     # |ICIR| + novelty_w×novelty − corr_w×corr_to_book − turnover_w×turnover
    # ADR-022 种子溯源:该冠军的种子起源(deterministic_seed / llm_seed / derived+ancestor_origins)。
    # 含 llm_seed 起源 → 搜索空间可能含金库语义,人工审视/晋级时额外审视。
    provenance: dict = Field(default_factory=dict)


class AutoResearchIslandSearchResponse(BaseModel):
    """多岛屿进化搜索结果。"""
    vintage_id: str = ""
    islands: int = 0
    generations: int = 0
    evaluated: int = 0
    seeded_by: str = "seeds"        # seeds | llm
    champions: list[AutoResearchChampionView] = Field(default_factory=list)


class ActionTokenView(BaseModel):
    """Local action confirmation token for write/costly endpoints."""
    header: str = "X-Action-Token"
    token: str = ""
    source: str = ""                # env | file


class ActionJobView(BaseModel):
    """Process-local action job returned by minute-level endpoints."""
    job_id: str
    kind: str = ""
    status: str = "queued"          # queued | running | succeeded | failed
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    result: dict | None = None
    error: str = ""
    context: dict = Field(default_factory=dict)


class AutoResearchOOSChampionView(BaseModel):
    fingerprint: str
    expr: str = ""
    train_icir: float = 0.0
    train_status: str = ""
    train_decision: str = ""
    train_novelty: float = 0.0
    train_corr_to_book: float = 0.0
    train_turnover: float = 0.0
    train_fitness: float = 0.0
    oos_icir: float | None = None
    oos_ic_mean: float | None = None
    oos_decision: str = ""
    oos_reason: str = ""


class AutoResearchWalkForwardResponse(BaseModel):
    """元级 walk-forward 搜索结果:训练截断于 cutoff,冠军一次性 OOS 评分。"""
    vintage_id: str = ""
    cutoff: str = ""
    oos_start: str = ""
    oos_end: str = ""
    islands: int = 0
    generations: int = 0
    evaluated: int = 0
    seeded_by: str = "seeds"        # seeds | llm
    champions: list[AutoResearchOOSChampionView] = Field(default_factory=list)


# ── Phase 5 Agent ──────────────────────────────────────────────────────────────
class AgentAskRequest(BaseModel):
    request: str
    context: dict = Field(default_factory=dict)   # {current_page, selected_object_id, ...}
    messages: list[dict] = Field(default_factory=list)  # [{role:user|assistant, content}]


class AgentAskResponse(BaseModel):
    output: AgentOutput
    task_id: str
    tool: str | None = None
    risk: str | None = None        # 命中工具的风险级
    llm_ready: bool = False        # 是否已接真 LLM(当前规则式 = False)


class AgentSessionCreateRequest(BaseModel):
    page_context: str = ""
    title: str = "AI 会话"
    user_id: str = "local"


class AgentSessionAskRequest(BaseModel):
    request: str
    context: dict = Field(default_factory=dict)


class AgentSessionMessageView(BaseModel):
    role: str = ""
    content: str = ""
    created_at: str = ""
    metadata: dict = Field(default_factory=dict)


class AgentSessionView(BaseModel):
    session_id: str
    user_id: str = "local"
    title: str = "AI 会话"
    page_context: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    messages: list[AgentSessionMessageView] = Field(default_factory=list)


class AgentSessionAskResponse(AgentAskResponse):
    session: AgentSessionView


class AgentKnowledgeSourceView(BaseModel):
    source_id: str
    source_type: str
    title: str
    source_path: str


# ── Phase 6 系统设置 / 审计 ─────────────────────────────────────────────────────
class SystemConfigView(BaseModel):
    cost: dict = Field(default_factory=dict)        # 含 locked=True(成本铁律只读)
    strategy: dict = Field(default_factory=dict)
    risk_policy: dict = Field(default_factory=dict)
    data: dict = Field(default_factory=dict)
    ai_model: dict = Field(default_factory=dict)
    services: list[dict] = Field(default_factory=list)
    quarantine_ranges: int = 0


class AuditEntry(BaseModel):
    kind: str = ""        # agent | control | backtest | config | review
    summary: str = ""
    detail: str = ""
    status: str = ""
    actor: str = ""       # human | system | agent


class AuditView(BaseModel):
    entries: list[AuditEntry] = Field(default_factory=list)
    total: int = 0


# ── LLM 配置(UI 可写)──────────────────────────────────────────────────────────
class LLMConfigView(BaseModel):
    """给前端的当前 LLM 配置 —— key 永不回传明文。"""
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    has_key: bool = False
    key_hint: str = ""          # 形如 "sk-…ab",仅提示
    llm_ready: bool = False


class LLMConfigSet(BaseModel):
    """前端提交的配置。api_key 留空 = 保留原 key(不覆盖)。"""
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    api_key: str | None = None


class LLMTestResult(BaseModel):
    ok: bool = False
    message: str = ""


# ── 模拟盘跟单(P5 债券轮动 · web 操作卡/流水/净值)────────────────────────────
class PaperTradeRow(BaseModel):
    """trades.csv 单行 —— 已成交记录。"""
    date: str = ""
    code: str = ""
    name: str = ""
    side: str = ""              # BUY | SELL
    shares: int = 0
    price: float = 0.0
    notional: float = 0.0
    cost: float = 0.0
    cash_after: float = 0.0


class PaperBlockedRow(BaseModel):
    """真实盘约束未成交(停牌/一字板/ETF 无数据)。"""
    side: str = ""
    code: str = ""
    name: str = ""
    reason: str = ""


class PaperPositionRow(BaseModel):
    code: str = ""
    name: str = ""
    shares: int = 0
    cost: float = 0.0
    price: float | None = None  # None = 停牌
    mv: float = 0.0
    pnl: float = 0.0
    asset: str = "stock"        # stock | etf


class PaperPlanItem(BaseModel):
    """明日计划单腿(参考价=信号日收盘,实际按 T+1 成交价模式)。"""
    action: str = ""            # BUY | SELL
    code: str = ""
    name: str = ""
    ref_price: float = 0.0
    est_shares: int = 0
    est_notional: float = 0.0


class BondInstructionView(BaseModel):
    """债券 ETF 轮动指令卡(P5)。"""
    active: bool = False
    side: str = ""              # BUY | SELL | HOLD | ""
    authorized: bool = True
    blocked_reason: str = ""
    code: str = "511010"
    name: str = "国债ETF"
    ref_price: float = 0.0
    est_shares: int = 0
    est_notional: float = 0.0
    shares_held: int = 0
    note: str = ""


class CandidateStockRow(BaseModel):
    code: str = ""
    name: str = ""


class TradePlanView(BaseModel):
    """今日操作卡:今日成交 + 明日计划 + 轮动指令 + 账户 + 确认状态。"""
    signal_date: str = ""
    account_date: str = ""
    last_exec_signal_date: str = ""
    generated_at: str = ""
    stale: bool = False
    stale_reason: str = ""
    regime: str = ""
    regime_dist: float = 0.0
    in_market: bool = False
    band_exposure: float = 0.0
    action: str = ""
    small_index_vs_ma16: float = 0.0
    binary_in_market_shadow: bool = False
    base_in_market: bool = False
    executed: list[PaperTradeRow] = Field(default_factory=list)
    blocked: list[PaperBlockedRow] = Field(default_factory=list)
    plan: list[PaperPlanItem] = Field(default_factory=list)
    bond: BondInstructionView | None = None
    positions: list[PaperPositionRow] = Field(default_factory=list)
    candidates: list[CandidateStockRow] = Field(default_factory=list)
    nav: float = 0.0
    cash: float = 0.0
    position_value: float = 0.0
    total_return: float = 0.0
    disclaimer: str = ""


class NavPoint(BaseModel):
    date: str = ""
    nav: float = 0.0
    cash: float = 0.0
    position_value: float = 0.0
    total_return: float = 0.0


class NavCurveView(BaseModel):
    points: list[NavPoint] = Field(default_factory=list)
    inception: str = ""
    init_capital: float = 0.0
    latest_nav_date: str = ""
    latest_nav: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0


# ── paper 多账户并行实测(WS-D 执行侧,R-PROD-001「不下单 ≠ 不实测」)────────
class PaperAccountNavPoint(BaseModel):
    date: str = ""
    nav: float = 0.0
    total_return: float = 0.0


class PaperAccountView(BaseModel):
    """一个候选策略版本的独立 paper 账户:状态机 + NAV 曲线 + 回测偏差。"""
    name: str = ""                  # "{family}.{version}"
    family: str = ""
    version: str = ""
    status: str = ""                # active | frozen | blocked | degraded | unknown
    reason: str = ""                # blocked/degraded/unknown 的可读原因
    opened_at: str = ""
    frozen_at: str = ""
    last_update_date: str = ""
    nav_points: list[PaperAccountNavPoint] = Field(default_factory=list)
    latest_nav: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    backtest_deviation: dict = Field(default_factory=dict)  # {available, reason} 或
    # {available: True, window_start, window_end, paper_cumulative_return,
    #  backtest_cumulative_return, cumulative_deviation, tracking_error, common_days}


class PaperAccountsListView(BaseModel):
    """多账户并排展示的顶层视图。顺序 = 后端产物顺序,前端不得重排名(R-PROD-001)。"""
    healthy: bool = True
    error: str = ""                 # healthy=False 时的可读原因(summary.json 缺失/过期名单等)
    generated_at: str = ""
    accounts: list[PaperAccountView] = Field(default_factory=list)


class PaperTradesView(BaseModel):
    trades: list[PaperTradeRow] = Field(default_factory=list)
    total: int = 0


class ProductionReadinessView(BaseModel):
    """生产信号发布前的统一闸门结果。"""
    allowed: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    data_date: str = ""
    expected_trade_date: str = ""
    governance_status: str = ""
    decay_status: str = ""
    paper_status: str = ""
    trading_day_status: str = ""
    data_issue_status: str = ""
    data_issue_categories: list[str] = Field(default_factory=list)
    generated_at: str = ""


class TradeReadinessView(BaseModel):
    allowed_to_trade: bool
    data_status: str
    model_version: str
    factor_health: str
    portfolio_risk: str
    cost_forecast: str
    liquidity_status: str
    regime_status: str
    regime_confidence: float
    kill_switch_status: str
    human_approval_required: bool
    details: dict = Field(default_factory=dict)


class GovernanceView(BaseModel):
    model_cards: list[dict] = Field(default_factory=list)
    validation_reports: list[dict] = Field(default_factory=list)
    experiments_ledger: list[dict] = Field(default_factory=list)
    committees: list[dict] = Field(default_factory=list)


class DeclaredLeg(BaseModel):
    """部署清单声明的一条腿(原文,未经 fail-closed 校验)。"""
    family: str = ""
    version: str = ""
    spec_hash: str = ""
    role: str = ""


class LegEvidence(BaseModel):
    """单条声明腿的结构化证据链:声明 vs 注册表逐项对照,可追溯到阻断根因。"""
    family: str = ""
    version: str = ""
    role: str = ""
    declared_spec_hash: str = ""
    registry_found: bool = False
    registry_status: str = ""
    registry_spec_hash: str = ""
    status_deployable: bool = False
    spec_hash_match: bool = False
    blocking_reason: str = ""  # 空字符串 = 该腿无阻断


class SystemTruthView(BaseModel):
    """系统真相层:把「声明的部署 / 已验证的部署 / 是否允许生产」三态显式分开。

    根 ``CLAUDE.md`` 防自欺纪律:registry 退役 / spec_hash 漂移必须 fail-closed,
    而不是靠 manifest 里 ``status: active`` 当事实源。本视图让前端/运维/agent 一眼区分:
    ``declared``(清单声称在跑什么,可能指向已降级版本)
      ≠ ``verified``(通过 fail-closed 校验后真正可激活的身份,否则为空)
      ≠ ``production_allowed``(今日数据/治理/decay/paper 全过才放行)。

    本视图不重算任何判定:``readiness`` 内嵌既有唯一权威
    ``runtime.production_readiness.get_production_readiness`` 的完整结果。
    """
    as_of: str = ""
    production_allowed: bool = False
    # declared —— 清单原文(可能指向已降级版本)
    declared_present: bool = False
    declared_deployment_id: str = ""
    declared_status: str = ""  # manifest 顶层 status(如 "active")
    declared_legs: list[DeclaredLeg] = Field(default_factory=list)
    # verified —— 通过 fail-closed 校验后真正可激活的身份
    verified: bool = False
    verified_deployment_id: str = ""
    verified_legs: list[DeclaredLeg] = Field(default_factory=list)
    verify_error: str = ""  # verified=False 时的 fail-closed 根因
    # 证据与阻断
    blocking_reasons: list[str] = Field(default_factory=list)
    evidence_chain: list[LegEvidence] = Field(default_factory=list)
    truth_sources: dict = Field(default_factory=dict)
    readiness: dict = Field(default_factory=dict)  # 内嵌完整 readiness 闸门


class GateDiag(BaseModel):
    """单门诊断(**派生·非裁决**):从 registry 扁平 nine_gate 字段映射到 9-Gate 各门,
    定位「卡在哪一门」。权威裁决始终是 ``nine_gate_policy.decide_nine_gate``,本结构只诊断。"""
    gate: str = ""           # G2_IC / G4_OOS / G8_DSR ...
    name: str = ""
    status: str = "unknown"  # passed | failed | unknown(字段缺失)
    actual: str = ""         # 实际信号值(字符串化,便于前端直显)
    threshold: str = ""      # 通过阈值
    source_field: str = ""   # 取自 nine_gate 哪个扁平字段(可追溯)


class CandidateReadiness(BaseModel):
    """一个候选/参考版本的「晋级就绪」诊断行。"""
    family: str = ""
    version: str = ""
    stage: str = ""                       # 台账 status(候选/参考/...)
    authoritative_verdict: str = ""       # decide_nine_gate().code —— 唯一权威(PASSED/FAILED/PENDING/RUN_FAILED)
    audited: bool = False                 # nine_gate 是否跑过(空={}=从未审计)
    distance_to_register: int = 0         # 未过门数;从未审计 = 最大距离(9)
    single_blocker: str = ""              # 唯一卡点门 + 根因
    marginal_action: str = ""             # 启发式边际动作(advisory,非裁决)
    gate_diag: list[GateDiag] = Field(default_factory=list)
    info_cluster: str = ""                # 信息簇(高相关家族集合)
    crowding: float | None = None         # 家族对其它家族的最大相关(None=无相关数据)
    dsr_p: float | None = None
    pbo: float | None = None
    n_trials: int | None = None


class PromotionReadinessView(BaseModel):
    """Alpha 工厂「晋级就绪」驾驶舱:回答「下一个该推进哪个候选、卡它的那一个约束是什么」。

    决策导向(``DECISION_COCKPITS.md`` 驾驶舱①):按「距入册」排序而非按收益,
    指出每个候选的唯一卡点门 + 边际动作 + 信息簇拥挤度。诚实护栏:
    权威裁决归 ``decide_nine_gate``;逐门诊断仅定位卡点,绝不改写 passed_all;
    从未审计的版本如实标「需先跑 9-Gate」,不伪造门距。
    """
    as_of: str = ""
    lead_candidate: str = ""              # 最接近入册者
    lead_blocker: str = ""               # 全台唯一瓶颈(顶部裁决条)
    research_steer: str = ""             # 研究重心建议(继续/换向)
    candidates: list[CandidateReadiness] = Field(default_factory=list)
    cluster_map: dict = Field(default_factory=dict)  # 拥挤度/冗余率/最拥挤簇
    truth_sources: dict = Field(default_factory=dict)


class GateVerdict(BaseModel):
    """验证闸门(DECISION_COCKPITS 驾驶舱②)的一行:某版本的 9-Gate R2P 裁决。

    决策:这个版本能否独立验证通过 → 入册?权威裁决 = ``decide_nine_gate``(只认 passed_all),
    逐门诊断(gate_diag)定位卡点(诊断·非裁决)。"""
    family: str = ""
    version: str = ""
    stage: str = ""                       # 台账 status
    verdict: str = ""                     # PASSED | FAILED | PENDING | RUN_FAILED(权威)
    verdict_label: str = ""               # 中文展示(审计通过/未通过/待完整审计/审计失败)
    audited: bool = False
    register_blocker: str = ""            # 阻挡入册的唯一根因(权威 reasons 优先)
    gate_diag: list[GateDiag] = Field(default_factory=list)  # 复用逐门诊断结构
    dsr_p: float | None = None
    pbo: float | None = None
    n_trials: int | None = None


class GateVerdictsView(BaseModel):
    """验证闸门②:全注册表逐版本的 9-Gate 裁决面 + 计数概览。"""
    as_of: str = ""
    summary: dict = Field(default_factory=dict)  # 按 verdict 计数 + audited 数
    verdicts: list[GateVerdict] = Field(default_factory=list)
    truth_sources: dict = Field(default_factory=dict)


# ── 信任校准首屏(Trust Calibration) ─────────────────────────────────────
# 决策:「用户在看 KPI 前,当前策略池有多可信 / 哪里最可能是假 alpha 或已在失效?」
# 定位:**over-trust 防护带**,不是新判定。裁决权威仍是
#   ``core.analysis.nine_gate_policy.decide_nine_gate``(经 get_gate_verdicts 复用)。
# 诚实护栏:
#   - banner 只做「聚合」不「重算」,永远不比其权威输入更绿(fail-closed)。
#   - holdout 只展示原始事实(边界/genesis),完整性判定归 check_holdout_compliance,本视图不自判。
#   - decay_signal / failure_boundaries 是 §7.1 论点字段(该盯什么),非实时状态;
#     实时衰减权威 = reports/decay_status.json(缺失则如实标「未监控」,绝不用论点字段冒充实时)。

class TrustSignal(BaseModel):
    """一条信任维度:名字 + 状态 + 证据 + 权威来源(可追溯,非本视图裁决)。"""
    key: str = ""                 # overfit_guard | oos_regime | audit_coverage | holdout | decay_watch
    label: str = ""               # 中文展示名
    status: str = "info"          # ok | attention | blocked | info(info=仅陈述事实,不参与裁绿)
    evidence: str = ""            # 一句话机械证据
    authority: str = ""           # 该维度的权威来源(谁说了算)


class TrustStrategyRow(BaseModel):
    """信任逐行:某版本的 over-trust 相关事实(复用 gate 裁决 + 论点字段,均标注来源)。"""
    family: str = ""
    version: str = ""
    stage: str = ""                       # 台账 status(候选/在册/参考/退役)
    verdict: str = ""                     # 权威 9-Gate 裁决(PASSED/FAILED/PENDING/RUN_FAILED)
    verdict_label: str = ""
    audited: bool = False
    dsr_p: float | None = None            # 多重检验惩罚 p(越小越稳)
    dsr_significant: bool | None = None   # DSR 是否显著(<0.05)
    bull_sharpe: float | None = None      # 牛市 regime(自标字段,非 IS/OOS 臆测)
    bear_sharpe: float | None = None      # 熊市 regime
    wf_sharpe: float | None = None        # walk-forward(样本外稳健性)
    decay_thesis: str = ""                # §7.1 预期失效信号(论点·非实时)
    failure_thesis: str = ""              # §7.1 失效边界(论点·非实时)
    trust_note: str = ""                  # 该行一句话 over-trust 提示


class TrustCalibrationView(BaseModel):
    """信任校准首屏:StatusBanner 综合裁决 + 逐维度信号 + 逐策略行(渐进式披露)。

    banner_status 直接映射 web StatusBanner 的 status prop
    (ready|attention|blocked|neutral);headline→title,detail→detail。"""
    as_of: str = ""
    banner_status: str = "neutral"        # ready | attention | blocked | neutral
    headline: str = ""                    # 一句话信任裁决(StatusBanner title)
    detail: str = ""                      # 一句话支撑(StatusBanner detail)
    signals: list[TrustSignal] = Field(default_factory=list)
    strategies: list[TrustStrategyRow] = Field(default_factory=list)
    truth_sources: dict = Field(default_factory=dict)
    honesty: str = ""                     # 本视图定位与边界的显式声明


# ── 决策收件箱(Decision Inbox)──────────────────────────────────────────
# 产品主界面从「人巡视看板」翻转为「系统找人」:收件箱只装**需要人裁决的事项**,
# 每项 = 一个决策问句 + 已装配证据 + canonical 动作入口(advisory,人执行)。
# 诚实护栏:
#   - 本视图**只聚合权威事实源,不做任何新判定**(裁决权威见各 item.authority);
#   - 空收件箱 = 「确认过所有事实源且无待裁决」,与「事实源读不到」严格区分
#     (all_sources_readable=False 时禁止宣称"无需介入",fail-closed);
#   - actions 是 advisory 导航(R-LLM-001 / ADR-030):指向 canonical 入口由人执行,
#     本视图不执行任何写动作。

class DecisionAction(BaseModel):
    """收件箱事项的一个候选动作:指向 canonical 入口(advisory,人执行)。"""
    label: str = ""            # 动作文案(如「批准进入 shadow」)
    entrypoint: str = ""       # canonical 入口(命令/函数/API 路径)
    allowed: bool = True       # services.read.action_policy 裁决(advisory)
    reason: str = ""           # 允/拒理由(来自 action_policy 或事实源)


class DecisionItem(BaseModel):
    """一张待裁决卡片:一个决策 + 支撑证据 + 后果 + 动作。"""
    key: str = ""              # 稳定 id(如 review:<fingerprint> / deployment:fail-closed)
    kind: str = ""             # registered_failed|deployment|review|decay|data|steer|source_error
    severity: str = "info"     # blocked | attention | info(info=常设建议,不计入待裁决数)
    title: str = ""            # 一句话决策问句
    evidence: list[str] = Field(default_factory=list)   # 机械证据(已装配,可直显)
    consequence: str = ""      # 不裁决的后果
    actions: list[DecisionAction] = Field(default_factory=list)
    authority: str = ""        # 该事项谁说了算(权威来源,非本视图)
    drilldown: str = ""        # 证据抽屉 API 路径(溯源入口)


class DecisionInboxView(BaseModel):
    """决策收件箱:今天需要人裁决的 0-N 件事。

    headline 语义(三态,严格区分):
    ① 有待裁决 → 「今天需要你裁决 N 件事」;
    ② 全源可读且无待裁决 → 「今天无需你介入」(收件箱为空 = 系统健康,是功能不是空态);
    ③ 任一事实源不可读 → 「收件箱不完整」(**不得**宣称无事,fail-closed)。"""
    as_of: str = ""
    headline: str = ""
    pending_count: int = 0     # blocked+attention 事项数(info 不计)
    all_sources_readable: bool = True
    items: list[DecisionItem] = Field(default_factory=list)
    truth_sources: dict = Field(default_factory=dict)
    honesty: str = ""


class DailyBriefView(BaseModel):
    """今日简报:打开产品的唯一首屏。回答三问:
    系统自己干了什么 / 世界有什么变化 / 今天需要我裁决几件事。

    诚实护栏:trust banner 直接复用 ``get_trust_calibration``(不重算不改写);
    各 section 事实源不可读时如实标 unknown,绝不填默认值假绿。"""
    as_of: str = ""
    # 信任裁决(复用 trust_calibration,原样透传)
    trust_banner_status: str = "neutral"
    trust_headline: str = ""
    # 今天需要你裁决几件事(来自 decision_inbox)
    decision_count: int = 0
    decision_headline: str = ""
    top_decisions: list[DecisionItem] = Field(default_factory=list)  # 最多 3 张预览
    # 系统自己干了什么(autoresearch 漏斗 + 近期活动)
    system_activity: dict = Field(default_factory=dict)
    # 世界有什么变化(数据新鲜度 / 衰减 / paper 实测)
    world_state: dict = Field(default_factory=dict)
    truth_sources: dict = Field(default_factory=dict)
    honesty: str = ""


# ── 语义卡片只读门面(services.read.semantics) ────────────────────────────────
# 零新存储、零第二真相源:字段一律从 factors.registry / manifest / 台账 PULL。
# 源头缺字段 → 空值 + 显式 missing 标记,绝不填充默认口径文本。

class FactorSemanticsView(BaseModel):
    """因子语义卡片 —— PULL 自 factors.registry.FactorRecord。"""
    name: str
    definition: str = ""
    params: dict = Field(default_factory=dict)
    data: list = Field(default_factory=list)   # 数据依赖(源为 tuple,视图侧 list)
    input: str = ""
    searchable: bool = False
    evidence: str = ""
    source_hash: str = ""


class DatasetSemanticsView(BaseModel):
    """数据集语义卡片 —— PULL 自 data_lake/_manifest.json 与 tushare_manifest.json。

    contract_missing=True 表示 manifest 条目无 contract 键;此时 contract 恒为 {}。
    不得编造口径文本。
    """
    name: str
    source_manifest: str = ""   # "core" | "tushare"
    last_check: str = ""
    fields: list = Field(default_factory=list)
    path: str = ""
    contract: dict = Field(default_factory=dict)
    contract_missing: bool = True


class StrategySemanticsView(BaseModel):
    """策略语义卡片 —— PULL 自 services.read.registry.list_strategies()。"""
    family: str
    version: str = ""
    hypothesis: str = ""
    regime: str = ""
    decay_signal: str = ""
    status: str = ""
    nine_gate_present: bool = False  # nine_gate 非空 dict


class SemanticInventoryView(BaseModel):
    """三类语义实体清单(名字 + 计数),供 Agent/Web 一跳枚举。"""
    factors: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list)  # "family/version"
    n_factors: int = 0
    n_datasets: int = 0
    n_strategies: int = 0
