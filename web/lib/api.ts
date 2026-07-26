// FastAPI 客户端(Phase 0 services 接缝)。前端不做任何量化计算,只调 API。
import type {
  AgentAskResponse,
  AgentMessage,
  AgentSession,
  AgentSessionAskResponse,
  ActionJobView,
  ActionTokenView,
  AuditView,
  AutoResearchCandidateView,
  AutoResearchFunnelView,
  AutoResearchReviewItemView,
  BacktestResult,
  DataQualityView,
  LLMConfigView,
  LLMTestResult,
  FactorHealthView,
  FactorView,
  FunnelView,
  HypothesisView,
  MarketStateView,
  NavCurveView,
  PaperAccountsListView,
  PaperTradesView,
  PortfolioView,
  RegisteredExperimentView,
  ResearchDraftView,
  ResearchRunIndexView,
  ResearchReviewView,
  ResearchWorkItemDetailView,
  ResearchWorkItemListView,
  TradePlanView,
  RiskReport,
  StrategyView,
  StrategyDetailView,
  SystemConfigView,
  TradeReadinessView,
  GovernanceView,
  SystemTruthView,
  PromotionReadinessView,
  GateVerdictsView,
  TrustCalibrationView,
  DecisionInboxView,
  DailyBriefView,
} from "./types";

const BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8011";

interface BacktestParams {
  start?: string;
  top_n?: number;
  rebalance_days?: number;
  factor_window?: number;
  timing_ma?: number;
}

interface WaitJobOptions {
  intervalMs?: number;
  timeoutMs?: number;
}

let actionTokenPromise: Promise<ActionTokenView> | null = null;

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} → ${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown, extraHeaders: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function patch<T>(path: string, body: unknown, extraHeaders: Record<string, string> = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API ${path} → ${res.status} ${res.statusText}`);
  return res.json() as Promise<T>;
}

async function actionToken(): Promise<ActionTokenView> {
  if (!actionTokenPromise) {
    actionTokenPromise = get<ActionTokenView>("/settings/action-token").catch((e) => {
      actionTokenPromise = null;
      throw e;
    });
  }
  return actionTokenPromise;
}

async function protectedPost<T>(path: string, body: unknown): Promise<T> {
  const token = await actionToken();
  return post<T>(path, body, { [token.header || "X-Action-Token"]: token.token });
}

async function protectedPatch<T>(path: string, body: unknown): Promise<T> {
  const token = await actionToken();
  return patch<T>(path, body, { [token.header || "X-Action-Token"]: token.token });
}

function sleep(ms: number): Promise<void> {
  return ms <= 0 ? Promise.resolve() : new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForExperimentJob<T>(jobId: string, options: WaitJobOptions = {}): Promise<T> {
  const intervalMs = options.intervalMs ?? 1500;
  const timeoutMs = options.timeoutMs ?? 15 * 60 * 1000;
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "queued";

  while (true) {
    const job = await get<ActionJobView>(`/experiments/jobs/${encodeURIComponent(jobId)}`);
    lastStatus = job.status;
    if (job.status === "succeeded") {
      if (job.result == null) throw new Error(`Job ${jobId} succeeded without a result`);
      return job.result as T;
    }
    if (job.status === "failed") {
      throw new Error(job.error || `Job ${jobId} failed`);
    }
    if (Date.now() >= deadline) {
      throw new Error(`Job ${jobId} timed out after ${timeoutMs}ms (last status: ${lastStatus})`);
    }
    await sleep(intervalMs);
  }
}

export const api = {
  base: BASE,
  health: () => get<{ status: string; phase: number }>("/health"),
  strategies: () => get<StrategyView[]>("/strategies"),
  strategyDetail: (family: string, version: string) =>
    get<StrategyDetailView>(`/strategies/${encodeURIComponent(family)}/${encodeURIComponent(version)}`),
  factors: () => get<FactorView[]>("/factors"),
  dataQuality: () => get<DataQualityView>("/data/quality"),
  strategyHealth: () => get<FactorHealthView[]>("/state/health"),
  marketState: () => get<MarketStateView>("/state/market"),
  portfolio: () => get<PortfolioView>("/portfolio"),
  paperPlan: () => get<TradePlanView>("/paper/plan"),
  paperTrades: (limit = 200) => get<PaperTradesView>(`/paper/trades?limit=${limit}`),
  paperNav: () => get<NavCurveView>("/paper/nav"),
  paperAccounts: () => get<PaperAccountsListView>("/paper-accounts"),
  risk: () => get<RiskReport>("/risk"),
  funnel: () => get<FunnelView>("/experiments/funnel"),
  hypotheses: (status?: string, limit = 60) =>
    get<HypothesisView[]>(`/experiments/hypotheses?${new URLSearchParams({ ...(status ? { status } : {}), limit: String(limit) })}`),
  registeredExperiments: () => get<RegisteredExperimentView[]>("/experiments/registered"),
  researchWorkItems: (params: { status?: string; kind?: string; action?: string; limit?: number } = {}) => {
    const query = new URLSearchParams(
      Object.entries(params)
        .filter(([, value]) => value !== undefined && value !== "")
        .map(([key, value]) => [key, String(value)]),
    );
    return get<ResearchWorkItemListView>(`/experiments/work-items${query.size ? `?${query}` : ""}`);
  },
  researchWorkItem: (kind: string, itemId: string) =>
    get<ResearchWorkItemDetailView>(`/experiments/work-items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}`),
  researchRuns: () => get<ResearchRunIndexView>("/experiments/research-runs"),
  researchJobs: () => get<ActionJobView[]>("/experiments/jobs"),
  createResearchDraft: (body: {
    title: string;
    source?: string;
    mechanism?: string;
    citation?: string;
    factor_fn_name?: string;
    factor_params?: Record<string, unknown>;
    timing_fn_name?: string | null;
    timing_params?: Record<string, unknown>;
    data_dependencies?: string[];
  }) => protectedPost<ResearchDraftView>("/experiments/drafts", body),
  updateResearchDraft: (draftId: string, body: Record<string, unknown>) =>
    protectedPatch<ResearchDraftView>(`/experiments/drafts/${encodeURIComponent(draftId)}`, body),
  reviewResearchWorkItem: (kind: string, itemId: string, action: "approve" | "reject", notes = "") =>
    protectedPost<ResearchReviewView>(
      `/experiments/work-items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}/reviews`,
      { action, notes, reviewer: "human" },
    ),
  runResearchAction: (
    kind: string,
    itemId: string,
    action: string,
    body: { start?: string; sample_dates?: number | null; version?: string; target_status?: string } = {},
  ) => protectedPost<ActionJobView>(
    `/experiments/work-items/${encodeURIComponent(kind)}/${encodeURIComponent(itemId)}/actions/${encodeURIComponent(action)}`,
    body,
  ),
  logicalChains: () => get<any[]>("/experiments/logical-chains"),
  industryKnowledgeGraph: () => get<any>("/experiments/industry-knowledge-graph"),
  autoresearchFunnel: () => get<AutoResearchFunnelView>("/experiments/autoresearch/funnel"),
  autoresearchCandidates: (limit = 40) =>
    get<AutoResearchCandidateView[]>(`/experiments/autoresearch/candidates?limit=${limit}`),
  autoresearchReviewQueue: (limit = 20) =>
    get<AutoResearchReviewItemView[]>(`/experiments/autoresearch/review-queue?limit=${limit}`),
  reviewAutoresearch: (fingerprint: string, action: "approve" | "reject", notes = "") =>
    protectedPost<AutoResearchReviewItemView>(`/experiments/autoresearch/review/${fingerprint}`, { action, notes }),
  promoteAutoresearch: (fingerprint: string, version = "v1.0") =>
    protectedPost<ActionJobView>(`/experiments/autoresearch/promote/${fingerprint}?version=${encodeURIComponent(version)}`, {}),
  runAutoresearchLLM: (params: { n?: number; theme?: string; max_stage?: string }) => {
    const q = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => [k, String(v)])
    );
    return protectedPost<ActionJobView>(`/experiments/autoresearch/run-llm?${q.toString()}`, {});
  },
  runIslandSearch: (params: { islands?: number; generations?: number; population?: number; final_stage?: string }) => {
    const q = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => [k, String(v)])
    );
    return protectedPost<ActionJobView>(`/experiments/autoresearch/island-search?${q.toString()}`, {});
  },
  runAutoresearchSeeds: (params: { limit?: number; max_stage?: string; start?: string; sample_dates?: number | null }) => {
    const q = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== null && v !== "")
        .map(([k, v]) => [k, String(v)])
    );
    return protectedPost<ActionJobView>(`/experiments/autoresearch/run-seeds?${q.toString()}`, {});
  },
  experimentJob: (jobId: string) => get<ActionJobView>(`/experiments/jobs/${encodeURIComponent(jobId)}`),
  waitForExperimentJob,
  agentAsk: (request: string, context: Record<string, unknown> = {}, messages: AgentMessage[] = []) =>
    post<AgentAskResponse>("/agent/ask", { request, context, messages }),
  createAgentSession: (body: { page_context?: string; title?: string; user_id?: string } = {}) =>
    post<AgentSession>("/agent/sessions", {
      page_context: body.page_context ?? "",
      title: body.title ?? "AI 会话",
      user_id: body.user_id ?? "local",
    }),
  getAgentSession: (sessionId: string) => get<AgentSession>(`/agent/sessions/${encodeURIComponent(sessionId)}`),
  agentSessionAsk: (sessionId: string, request: string, context: Record<string, unknown> = {}) =>
    post<AgentSessionAskResponse>(`/agent/sessions/${encodeURIComponent(sessionId)}/ask`, { request, context }),
  systemConfig: () => get<SystemConfigView>("/settings/config"),
  audit: (limit = 40) => get<AuditView>(`/settings/audit?limit=${limit}`),
  actionToken,
  getLlmConfig: () => get<LLMConfigView>("/settings/llm"),
  setLlmConfig: (body: { provider: string; model: string; base_url: string; api_key?: string | null }) =>
    protectedPost<LLMConfigView>("/settings/llm", body),
  testLlm: () => protectedPost<LLMTestResult>("/settings/llm/test", {}),
  runBacktest: (params: BacktestParams) => {
    const q = new URLSearchParams();
    const entries: [string, string | number | undefined][] = [
      ["start", params.start],
      ["top_n", params.top_n],
      ["rebalance_days", params.rebalance_days],
      ["factor_window", params.factor_window],
      ["timing_ma", params.timing_ma],
    ];
    for (const [key, value] of entries) {
      if (value !== undefined && value !== "") q.set(key, String(value));
    }
    return get<BacktestResult>(`/backtest/run?${q.toString()}`);
  },
  tradeReadiness: () => get<TradeReadinessView>("/trade-readiness"),
  governance: () => get<GovernanceView>("/governance"),
  systemTruth: () => get<SystemTruthView>("/system/truth"),
  promotionReadiness: () => get<PromotionReadinessView>("/experiments/promotion-readiness"),
  gateVerdicts: () => get<GateVerdictsView>("/governance/gate-verdicts"),
  trustCalibration: () => get<TrustCalibrationView>("/governance/trust-calibration"),
  decisionInbox: () => get<DecisionInboxView>("/inbox"),
  dailyBrief: () => get<DailyBriefView>("/inbox/brief"),
  shadowIncubation: () => get<any>("/experiments/shadow-incubation"),
  amountTimingValidation: () => get<any>("/experiments/amount-timing-validation"),
};

// 格式化助手
export const pct = (x: number, d = 2) => `${(x * 100).toFixed(d)}%`;
export const signedPct = (x: number, d = 2) =>
  `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
export const num = (x: number, d = 2) => x.toFixed(d);
