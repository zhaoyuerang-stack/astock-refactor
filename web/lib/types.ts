// 镜像 factor_research/contracts/views.py(API 响应形状)

export interface BacktestResult {
  annual: number;
  vol: number;
  sharpe: number;
  maxdd: number;
  calmar: number;
  hit: boolean;
  n: number;
  turnover_annual: number;
  cost_annual: number;
  yearly_returns: Record<string, number>;
  n_stocks: number;
  n_days: number;
  start: string;
  end: string;
  family: string;
  version: string;
}

export interface StrategyView {
  strategy_id: string;
  family: string;
  family_name: string;
  family_status: string;
  version: string;
  status: string;
  hypothesis: string;
  regime: string;
  desc: string;
  data_scope: Record<string, unknown> | string;
  metrics: Record<string, number>;
  config: Record<string, unknown>;
  notes: string;
  capacity_m: number;
  admission?: Record<string, any>;   // 双轨准入 {track, rationale}
  nine_gate?: Record<string, any>;   // Nine-Gate 摘要 {dsr_p, gate4_verdict, n_trials, ...}
  style_betas?: Record<string, number>;        // 家族级风格暴露 {size, value, momentum, ...}
  failure_boundaries?: Record<string, number>; // 家族级失效边界 {max_drawdown, max_drawdown_days}
  decay_signal?: string;                        // 家族级失效信号(死因/触发条件,注册时写的静态文字)
  decay_check?: Record<string, any>;            // 版本级实测衰减结果 {decayed, rolling_3y_sharpe_latest, reasons, checked_at}
}

export interface StrategyDetailView {
  strategy: StrategyView;
  research_runs: ResearchRunView[];
  artifacts: Record<string, any>;
}

export interface FactorView {
  name: string;
  display_name: string;
  hypothesis: string;
  regime: string;
  n_versions: number;
  n_registered?: number;   // 在册版本数(0 = 候选/噪音池)
  status: string;
}

// Phase 2 状态层
export interface DataQualityView {
  total: number;
  clean: number;
  clean_ratio: number;
  issue_breakdown: Record<string, number>;
  n_flagged: number;
  flagged_sample: { code: string; issues: string[] }[];
  severe_count: number;
  jump_count: number;
  verdict: string;
  triage_summary?: Record<string, any>;
  production_blocked?: boolean;
  backtest_blocked?: boolean;
  duckdb: {
    available: boolean;
    note?: string;
    rows?: number;
    codes?: number;
    date_range?: string;
    nonpositive_close?: number;
    quarantined_ranges?: number;
  } | null;
}

export interface FactorHealthView {
  name: string;
  sharpe: number;
  momentum_6m: number;
  trend: string;
  as_of?: string; // 报告数据截至日;周期生成非实时
}

export interface MarketStateView {
  current_position: string;
  last_action: string;
  last_signal_date: string | null;
  last_rebalance_date: string | null;
  n_holdings: number;
}

// Phase 3 组合 / 风控
export interface Holding {
  code: string;
  weight: number;
}

export interface PortfolioView {
  nav: number;
  cash: number;
  current_positions: Holding[];
  stance: string;
  regime: string;
  note: string;
  target_holdings: Holding[];
  target_as_of: string | null;
  target_note: string;
}

export interface RiskRuleCheck {
  rule: string;
  threshold: number;
  current: number | null;
  status: "ok" | "warn" | "breach" | "na";
  note: string;
}

export interface ControlAction {
  action_id: string;
  object_type: string;
  object_id: string;
  trigger_state: string;
  action: string;
  reason: string;
  recommendation: string;
  requires_confirmation: boolean;
  executed: boolean;
  executed_by: string;
}

export interface RiskReport {
  evaluated_on: string;
  checks: RiskRuleCheck[];
  control_actions: ControlAction[];
  verdict: "正常" | "预警" | "超限";
}

// Phase 4 研究实验
export interface FunnelView {
  total: number;
  stages: { stage: string; count: number }[];
  side: { stage: string; count: number }[];
  discard_ratio: number;
  registered: number;
}

export interface HypothesisView {
  id: string;
  name: string;
  factor_fn_name: string;
  factor_params: Record<string, unknown>;
  timing_fn_name: string | null;
  status: string;
  source: string;
  mechanism: string;
  citation: string;
  created_at: string;
}

export interface RegisteredExperimentView {
  strategy_id: string;
  family_name: string;
  version: string;
  status: string;
  date: string;
  config_hash: string;
  config: Record<string, unknown>;
  metrics: Record<string, number>;
  data_scope: Record<string, unknown>;
}

export interface ResearchDraftView {
  draft_id: string;
  title: string;
  source: string;
  mechanism: string;
  citation: string;
  factor_fn_name: string;
  factor_params: Record<string, unknown>;
  timing_fn_name: string | null;
  timing_params: Record<string, unknown>;
  data_dependencies: string[];
  status: string;
  linked_work_id: string;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface ResearchReviewView {
  review_id: string;
  kind: string;
  item_id: string;
  action: "approve" | "reject" | string;
  notes: string;
  reviewer: string;
  reviewed_at: string;
  migrated_from: string;
}

export interface ResearchWorkItemView {
  work_id: string;
  kind: "draft" | "hypothesis" | "autoresearch" | string;
  item_id: string;
  title: string;
  source: string;
  raw_status: string;
  status: "blocked" | "review" | "ready" | "running" | "completed" | "archived" | string;
  stage: string;
  mechanism: string;
  citation: string;
  updated_at: string;
  next_action: string;
  blocked_reason: string;
  latest_result: Record<string, any>;
  review: ResearchReviewView | null;
}

export interface ResearchWorkItemListView {
  items: ResearchWorkItemView[];
  counts: Record<string, number>;
}

export interface ResearchWorkItemDetailView {
  item: ResearchWorkItemView;
  evidence: Record<string, any>;
  runs: Record<string, any>[];
  raw: Record<string, any>;
}

export interface ResearchRunView {
  run_id: string;
  script: string;
  hypothesis: string;
  source: string;
  run_at: string;
  data_vintage: Record<string, any>;
  metrics: Record<string, any>;
  verdict: string;
  next_action: string;
  decision_state: string;
  artifact_paths: string[];
  notes: string;
}

export interface ResearchRunIndexView {
  generated_at: string;
  summary: Record<string, any>;
  latest_runs: ResearchRunView[];
}

export interface AutoResearchCandidateView {
  fingerprint: string;
  status: string;
  source: string;
  ast: Record<string, unknown>;
  complexity_score: number;
  max_auto_stage: string;
  notes: string;
  created_at: string;
}

export interface AutoResearchReviewItemView {
  fingerprint: string;
  status: string;
  decision: string;
  reason: string;
  candidate: Record<string, unknown>;
  metrics: Record<string, unknown>;
  review_action: string; // "" = 待复核;approve / reject = 已人工决策
  reviewer_notes: string;
  reviewed_at: string;
}

export interface AutoResearchFunnelView {
  total: number;
  stages: { stage: string; count: number }[];
  review_queue: number;
}

export interface AutoResearchRunResultView {
  fingerprint: string;
  status: string;
  decision: string;
  reason: string;
  protocols: string[];
}

export interface AutoResearchRunResponse {
  vintage_id: string;
  max_stage: string;
  results: AutoResearchRunResultView[];
}

export interface AutoResearchPromoteResponse {
  fingerprint: string;
  hypothesis_name: string;
  version: string;
  registered: boolean;
  detail: string;
}

export interface AutoResearchLLMGenResponse {
  model: string;
  requested: number;
  accepted: number;
  rejected: string[];
  run: AutoResearchRunResponse;
}

export interface AutoResearchChampionView {
  fingerprint: string;
  island: number;
  generation: number;
  icir: number;
  expr: string;
  status: string;
  decision: string;
  reason: string;
}

export interface AutoResearchIslandSearchResponse {
  vintage_id: string;
  islands: number;
  generations: number;
  evaluated: number;
  seeded_by: string;
  champions: AutoResearchChampionView[];
}

export interface ActionTokenView {
  header: string;
  token: string;
  source: string;
}

export interface ActionJobView {
  job_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  created_at: string;
  started_at: string;
  finished_at: string;
  result: Record<string, unknown> | null;
  error: string;
  context?: Record<string, unknown>;
}

// Phase 5 Agent
export interface AgentCitation {
  source_id: string;
  source_type: string;
  title: string;
  source_path: string;
  excerpt: string;
}

export interface AgentMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AgentSessionMessage {
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
}

export interface AgentSession {
  session_id: string;
  user_id: string;
  title: string;
  page_context: string;
  status: string;
  created_at: string;
  updated_at: string;
  messages: AgentSessionMessage[];
}

export interface AgentOutput {
  summary: string;
  evidence: string[];
  risk: string[];
  recommendation: string[];
  next_actions: string[];
  citations: AgentCitation[];
  source_types: string[];
  suggested_navigation: string[];
  confidence: number;
  requires_human_confirmation: boolean;
}

export interface AgentAskResponse {
  output: AgentOutput;
  task_id: string;
  tool: string | null;
  risk: string | null;
  llm_ready: boolean;
}

export interface AgentSessionAskResponse extends AgentAskResponse {
  session: AgentSession;
}

// Phase 6 系统设置 / 审计
export interface SystemConfigView {
  cost: Record<string, number | boolean>;
  strategy: Record<string, unknown>;
  risk_policy: Record<string, number>;
  data: Record<string, unknown>;
  ai_model: { llm_ready: boolean; provider: string; mode: string; model?: string; base_url?: string };
  services: { name: string; status: string }[];
  quarantine_ranges: number;
}

export interface AuditEntry {
  kind: string;
  summary: string;
  detail: string;
  status: string;
  actor: string;
}

export interface AuditView {
  entries: AuditEntry[];
  total: number;
}

export interface LLMConfigView {
  provider: string;
  model: string;
  base_url: string;
  has_key: boolean;
  key_hint: string;
  llm_ready: boolean;
}

export interface LLMTestResult {
  ok: boolean;
  message: string;
}

// ── 模拟盘跟单(P5 债券轮动 · 操作卡/流水/净值)──────────────────────────────
export interface PaperTradeRow {
  date: string;
  code: string;
  name: string;
  side: "BUY" | "SELL" | string;
  shares: number;
  price: number;
  notional: number;
  cost: number;
  cash_after: number;
}

export interface PaperBlockedRow {
  side: string;
  code: string;
  name: string;
  reason: string;
}

export interface PaperPositionRow {
  code: string;
  name: string;
  shares: number;
  cost: number;
  price: number | null;
  mv: number;
  pnl: number;
  asset: "stock" | "etf" | string;
}

export interface PaperPlanItem {
  action: "BUY" | "SELL" | string;
  code: string;
  name: string;
  ref_price: number;
  est_shares: number;
  est_notional: number;
}

export interface BondInstructionView {
  active: boolean;
  side: "BUY" | "SELL" | "HOLD" | string;
  authorized?: boolean;
  blocked_reason?: string;
  code: string;
  name: string;
  ref_price: number;
  est_shares: number;
  est_notional: number;
  shares_held: number;
  note: string;
}

export interface CandidateStockRow {
  code: string;
  name: string;
}

export interface TradePlanView {
  signal_date: string;
  account_date: string;
  last_exec_signal_date: string;
  generated_at: string;
  stale: boolean;
  stale_reason: string;
  regime: string;
  regime_dist: number;
  in_market: boolean;
  band_exposure: number;
  action: string;
  small_index_vs_ma16: number;
  binary_in_market_shadow: boolean;
  base_in_market: boolean;
  executed: PaperTradeRow[];
  blocked: PaperBlockedRow[];
  plan: PaperPlanItem[];
  bond: BondInstructionView | null;
  positions: PaperPositionRow[];
  candidates: CandidateStockRow[];
  nav: number;
  cash: number;
  position_value: number;
  total_return: number;
  disclaimer: string;
}

export interface NavPoint {
  date: string;
  nav: number;
  cash: number;
  position_value: number;
  total_return: number;
}

export interface NavCurveView {
  points: NavPoint[];
  inception: string;
  init_capital: number;
  latest_nav_date: string;
  latest_nav: number;
  total_return: number;
  max_drawdown: number;
}

export interface PaperTradesView {
  trades: PaperTradeRow[];
  total: number;
}

// ── paper 多账户并行实测(WS-D 执行侧,R-PROD-001「不下单 ≠ 不实测」)────────
export interface PaperAccountNavPoint {
  date: string;
  nav: number;
  total_return: number;
}

// 判别联合:available=false 只有 reason;available=true 才有完整偏差字段。
export type PaperAccountBacktestDeviation =
  | { available: false; reason: string }
  | {
      available: true;
      window_start: string;
      window_end: string;
      paper_cumulative_return: number;
      backtest_cumulative_return: number;
      cumulative_deviation: number;
      tracking_error: number | null;
      common_days: number;
    };

export interface PaperAccountView {
  name: string;
  family: string;
  version: string;
  status: "active" | "frozen" | "blocked" | "degraded" | "unknown" | string;
  reason: string;
  opened_at: string;
  frozen_at: string;
  last_update_date: string;
  nav_points: PaperAccountNavPoint[];
  latest_nav: number;
  total_return: number;
  max_drawdown: number;
  backtest_deviation: PaperAccountBacktestDeviation;
}

export interface PaperAccountsListView {
  healthy: boolean;
  error: string;
  generated_at: string;
  accounts: PaperAccountView[];
}

export interface TradeReadinessView {
  allowed_to_trade: boolean;
  data_status: string;
  model_version: string;
  factor_health: string;
  portfolio_risk: string;
  cost_forecast: string;
  liquidity_status: string;
  regime_status: string;
  regime_confidence: number;
  kill_switch_status: string;
  human_approval_required: boolean;
  details: Record<string, any>;
}

export interface GovernanceView {
  model_cards: Record<string, any>[];
  validation_reports: Record<string, any>[];
  experiments_ledger: Record<string, any>[];
  committees: Record<string, any>[];
}

// 系统真相层:declared(清单声称在跑) ≠ verified(fail-closed 校验后真正可激活) ≠ production_allowed。
// 后端 /system/truth,前端只读呈现,不做任何"修正"。
export interface DeclaredLeg {
  family: string;
  version: string;
  spec_hash: string;
  role: string;
}

export interface LegEvidence {
  family: string;
  version: string;
  role: string;
  declared_spec_hash: string;
  registry_found: boolean;
  registry_status: string;
  registry_spec_hash: string;
  status_deployable: boolean;
  spec_hash_match: boolean;
  blocking_reason: string; // 空字符串 = 该腿无阻断
}

export interface SystemTruthView {
  as_of: string;
  production_allowed: boolean;
  declared_present: boolean;
  declared_deployment_id: string;
  declared_status: string;
  declared_legs: DeclaredLeg[];
  verified: boolean;
  verified_deployment_id: string;
  verified_legs: DeclaredLeg[];
  verify_error: string;
  blocking_reasons: string[];
  evidence_chain: LegEvidence[];
  truth_sources: Record<string, string>;
  readiness: Record<string, any>;
}

// Alpha 工厂「晋级就绪」驾驶舱(DECISION_COCKPITS 驾驶舱①)。
// 后端 /experiments/promotion-readiness;前端只读呈现,权威裁决在后端。
export interface GateDiag {
  gate: string;
  name: string;
  status: "passed" | "failed" | "unknown";
  actual: string;
  threshold: string;
  source_field: string;
}

export interface CandidateReadiness {
  family: string;
  version: string;
  stage: string;
  authoritative_verdict: string; // PASSED | FAILED | PENDING | RUN_FAILED
  audited: boolean;
  distance_to_register: number;
  single_blocker: string;
  marginal_action: string;
  gate_diag: GateDiag[];
  info_cluster: string;
  crowding: number | null; // null = 无相关数据(诚实未知)
  dsr_p: number | null;
  pbo: number | null;
  n_trials: number | null;
}

export interface PromotionReadinessView {
  as_of: string;
  lead_candidate: string;
  lead_blocker: string;
  research_steer: string;
  candidates: CandidateReadiness[];
  cluster_map: Record<string, any>;
  truth_sources: Record<string, string>;
}

// 验证闸门②:全注册表逐版本 9-Gate 裁决面。后端 /governance/gate-verdicts。
export interface GateVerdict {
  family: string;
  version: string;
  stage: string;
  verdict: string;        // PASSED | FAILED | PENDING | RUN_FAILED(权威)
  verdict_label: string;
  audited: boolean;
  register_blocker: string;
  gate_diag: GateDiag[];
  dsr_p: number | null;
  pbo: number | null;
  n_trials: number | null;
}

export interface GateVerdictsView {
  as_of: string;
  summary: Record<string, number>;
  verdicts: GateVerdict[];
  truth_sources: Record<string, string>;
}

// 信任校准首屏(over-trust 防护带)。后端 = services.read.trust_calibration。
// 前端只读呈现:banner_status 直接喂给 StatusBanner,禁止在展示层重算/上调裁绿。
export type TrustBannerStatus = "ready" | "attention" | "blocked" | "neutral";

export interface TrustSignal {
  key: string;                       // overfit_guard | oos_regime | audit_coverage | holdout | decay_watch
  label: string;
  status: "ok" | "attention" | "blocked" | "info";
  evidence: string;
  authority: string;
}

export interface TrustStrategyRow {
  family: string;
  version: string;
  stage: string;
  verdict: string;                   // PASSED | FAILED | PENDING | RUN_FAILED(权威)
  verdict_label: string;
  audited: boolean;
  dsr_p: number | null;
  dsr_significant: boolean | null;
  bull_sharpe: number | null;
  bear_sharpe: number | null;
  wf_sharpe: number | null;
  decay_thesis: string;              // §7.1 论点·非实时
  failure_thesis: string;            // §7.1 论点·非实时
  trust_note: string;
}

export interface TrustCalibrationView {
  as_of: string;
  banner_status: TrustBannerStatus;
  headline: string;
  detail: string;
  signals: TrustSignal[];
  strategies: TrustStrategyRow[];
  truth_sources: Record<string, string>;
  honesty: string;
}

// ── 决策收件箱 / 今日简报(产品主界面:「系统找人」)────────────────────
// 对应后端 contracts.views.DecisionInboxView / DailyBriefView(GET /inbox, /inbox/brief)。
// 前端只呈现:severity/headline 语义由后端 fail-closed 装配,UI 不得改写或补绿。

export type DecisionSeverity = "blocked" | "attention" | "info";

export interface DecisionAction {
  label: string;
  entrypoint: string;                // canonical 入口(命令/函数/API 路径),人执行
  allowed: boolean;                  // action_policy 裁决(advisory)
  reason: string;
}

export interface DecisionItem {
  key: string;
  kind: string;                      // registered_failed|deployment|review|decay|data|steer|source_error
  severity: DecisionSeverity;
  title: string;
  evidence: string[];
  consequence: string;
  actions: DecisionAction[];
  authority: string;
  drilldown: string;                 // 证据抽屉 API 路径
}

export interface DecisionInboxView {
  as_of: string;
  headline: string;
  pending_count: number;             // blocked+attention(info 不计)
  all_sources_readable: boolean;     // false = 收件箱不完整,禁称无事
  items: DecisionItem[];
  truth_sources: Record<string, string>;
  honesty: string;
}

export interface DailyBriefView {
  as_of: string;
  trust_banner_status: TrustBannerStatus;  // 原样透传 trust_calibration
  trust_headline: string;
  decision_count: number;            // -1 = 收件箱不可读(须显式呈现,非 0)
  decision_headline: string;
  top_decisions: DecisionItem[];
  system_activity: Record<string, unknown>;
  world_state: Record<string, { status: string; [k: string]: unknown }>;
  truth_sources: Record<string, string>;
  honesty: string;
}
