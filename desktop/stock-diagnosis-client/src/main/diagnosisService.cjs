const { extractStockCode } = require("./readServiceClient.cjs");
const { PI_CLI_TOOL, getAgentTurnTiming } = require("./piBridge.cjs");
const { recordDiagnosisRoundTiming } = require("./roundTiming.cjs");
const skillDefinitions = require("../shared/skills.json");

const TASK_STEPS = [
  { name: "识别股票", desc: "解析名称、代码和市场。", status: "done" },
  { name: "检查数据新鲜度", desc: "确认可用交易日、PIT 对齐和缺口。", status: "done" },
  { name: "读取风险快照", desc: "汇总流动性、波动、行业和估值风险。", status: "done" },
  { name: "生成保守诊断", desc: "拆分未持有与已持有两种动作语境。", status: "done" },
];
const MAX_CONTEXT_TURNS = 40;
const MAX_TURN_CHARS = 1200;

function pct(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
  return `${(value * 100).toFixed(2)}%`;
}

function number(value, digits = 2) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
  return value.toFixed(digits);
}

function tradeDate(value) {
  const text = String(value || "");
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  }
  return text || "未知";
}

function marketCapYi(totalMvWan) {
  if (typeof totalMvWan !== "number" || !Number.isFinite(totalMvWan)) return "未知";
  return `${(totalMvWan / 10000).toFixed(0)} 亿元`;
}

function moneyWan(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未知";
  return `${value.toFixed(2)} 万元`;
}

function chooseVerdict(profile) {
  const returns = profile.returns || {};
  const ret20 = returns.ret_20d;
  const ret60 = returns.ret_60d;
  if (!profile.latest_price?.date) return "数据不足";
  if (typeof ret20 === "number" && typeof ret60 === "number" && ret20 > 0 && ret60 > 0) {
    return "谨慎持有";
  }
  return "观察";
}

function publicSkill(skill) {
  if (!skill) return null;
  return {
    id: skill.id,
    name: skill.name,
    shortName: skill.shortName,
    category: skill.category,
    description: skill.description,
    boundary: skill.boundary,
    requiresStock: Boolean(skill.requiresStock),
    mode: skill.mode,
  };
}

function normalizeSelectedSkill(context = {}) {
  const requestedId = typeof context.selectedSkillId === "string"
    ? context.selectedSkillId
    : typeof context.selectedSkill?.id === "string"
      ? context.selectedSkill.id
      : "";
  return skillDefinitions.find((skill) => skill.id === requestedId) || null;
}

function skillTaskStep(skill) {
  return {
    name: `启用 Skill: ${skill.name}`,
    desc: skill.description,
    status: "done",
  };
}

function withSkillContext(diagnosis, skill) {
  if (!skill) {
    return { ...diagnosis, activeSkills: diagnosis.activeSkills || [] };
  }
  const skillInfo = publicSkill(skill);
  return {
    ...diagnosis,
    activeSkills: [skillInfo],
    taskSteps: [skillTaskStep(skill), ...(diagnosis.taskSteps || [])],
    evidence: [`选中 Skill: ${skill.name}`, ...(diagnosis.evidence || [])],
    limits: [...(diagnosis.limits || []), `Skill 边界: ${skill.boundary}`],
    sourceChips: [`Skill: ${skill.shortName || skill.name}`, ...(diagnosis.sourceChips || [])],
  };
}

function cliCallEvidence(call) {
  if (call.isError) return `Pi CLI ${call.capability || "unknown"}: 调用失败`;
  if (call.capability === "resolve_stock_code") {
    return `Pi CLI resolve_stock_code: ${call.result || "未识别股票代码"}`;
  }
  if (call.capability === "stock_profile") {
    return `Pi CLI stock_profile: ${call.result?.name || call.result?.code || "已读取"} ${call.result?.code || ""}`.trim();
  }
  if (call.capability === "strategy_idea_check") {
    const trust = call.result?.trust?.headline || "想法预检已返回";
    return `Pi CLI strategy_idea_check: ${trust}`;
  }
  return `Pi CLI ${call.capability}: 已读取系统只读能力`;
}

function floatsFromText(text) {
  const out = [];
  for (const token of String(text || "").match(/\d+(?:\.\d+)?/g) || []) {
    const value = Number(token);
    if (Number.isFinite(value)) out.push(value);
  }
  return out;
}

function isSalientNumber(value) {
  return value !== Math.trunc(value) || Math.abs(value) >= 100;
}

function numberGrounded(n, reals, relTol = 0.005) {
  for (const r of reals) {
    for (const cand of [r, r * 100, r / 100]) {
      if (Math.abs(n - cand) <= relTol * Math.max(Math.abs(cand), 1)) return true;
    }
  }
  return false;
}

function collectCliFactNumbers(cliCalls = []) {
  const reals = [];
  for (const call of cliCalls) {
    if (call?.isError) continue;
    reals.push(...floatsFromText(JSON.stringify(call.result ?? "")));
  }
  return reals;
}

function agentTextClaimsValidity(text) {
  return /策略有效|已经验证|回测证明|年化\s*\d|夏普\s*\d|最大回撤\s*[-+]?\d|可入册|可实盘|伪造|稳赚/.test(String(text || ""));
}

function envelopeFromIdeaCheck(ideaCheck) {
  if (!ideaCheck || typeof ideaCheck !== "object") return null;
  return ideaCheck.evidence_envelope && typeof ideaCheck.evidence_envelope === "object"
    ? ideaCheck.evidence_envelope
    : null;
}

function allowsPerformanceDisplay(envelope) {
  if (!envelope || typeof envelope !== "object") return false;
  if (envelope.allows_performance_display === true) return true;
  const tier = envelope.evidence_tier;
  return (tier === "engine" || tier === "gated")
    && Array.isArray(envelope.sources)
    && envelope.sources.length > 0
    && envelope.fake_curve_allowed !== true;
}

function stripPerformanceClaimsFromText(text) {
  // Remove common performance claim patterns when envelope forbids display.
  return String(text || "")
    .replace(/年化\s*[-+]?\d+(?:\.\d+)?%?/g, "年化[已按证据策略屏蔽]")
    .replace(/夏普\s*[-+]?\d+(?:\.\d+)?/g, "夏普[已按证据策略屏蔽]")
    .replace(/最大回撤\s*[-+]?\d+(?:\.\d+)?%?/g, "最大回撤[已按证据策略屏蔽]");
}

function groundStrategyAgentText(text, cliCalls = [], ideaCheck = null) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  if (agentTextClaimsValidity(raw)) return "";
  if (ideaCheck && ideaCheck.can_claim_valid !== false && ideaCheck.can_claim_valid !== undefined) {
    return "";
  }
  const envelope = envelopeFromIdeaCheck(ideaCheck);
  if (envelope && envelope.can_claim_valid === true && envelope.evidence_tier !== "gated") {
    return "";
  }
  let candidate = raw;
  if (!allowsPerformanceDisplay(envelope)) {
    // Without engine/gated envelope, strip performance numbers from model prose.
    if (/(年化|夏普|最大回撤|calmar|sharpe)/i.test(candidate) && floatsFromText(candidate).some(isSalientNumber)) {
      candidate = stripPerformanceClaimsFromText(candidate);
    }
  }
  const reals = collectCliFactNumbers(cliCalls);
  if (!reals.length) {
    return floatsFromText(candidate).some(isSalientNumber) ? "" : candidate;
  }
  const salient = floatsFromText(candidate).filter(isSalientNumber);
  if (salient.some((n) => !numberGrounded(n, reals))) return "";
  return candidate;
}

function ideaCheckFromAgentTurn(agentTurn) {
  const call = successfulCliCall(agentTurn, "strategy_idea_check");
  if (!call?.result || typeof call.result !== "object" || Array.isArray(call.result)) return null;
  return call.result;
}

function attachPiAgentTurn(diagnosis, agentTurn, options = {}) {
  const cliCalls = Array.isArray(agentTurn?.cliCalls) ? agentTurn.cliCalls : [];
  if (!agentTurn?.ready && cliCalls.length === 0) return diagnosis;
  const successfulCalls = cliCalls.filter((call) => !call.isError);
  return {
    ...diagnosis,
    agentTrace: {
      orchestrator: "pi",
      selectedSkillId: diagnosis.activeSkills?.[0]?.id || "",
      toolWhitelist: [PI_CLI_TOOL],
      cliCapabilities: successfulCalls.map((call) => call.capability),
      cliCalls,
      fallbackUsed: Boolean(options.fallbackUsed),
    },
    taskSteps: [
      {
        name: "Pi agent 读取系统 CLI",
        desc: options.fallbackUsed
          ? "Pi 未返回完整 CLI 证据，已降级到本地 read service。"
          : `Pi 已通过唯一工具 ${PI_CLI_TOOL} 读取 readonly 能力。`,
        status: "done",
      },
      ...(diagnosis.taskSteps || []),
    ],
    evidence: [
      ...cliCalls.map(cliCallEvidence),
      ...(diagnosis.evidence || []),
    ],
    limits: [
      ...(diagnosis.limits || []),
      `Pi agent 只能调用 ${PI_CLI_TOOL}；系统 CLI 只执行登记为 readonly 的能力。`,
      ...(options.fallbackUsed ? ["本轮使用 HTTP read service 兜底，未把该路径伪装成 Pi CLI 调用。"] : []),
    ],
    sourceChips: ["Pi agent", "system-cli", ...(diagnosis.sourceChips || [])],
    piExplanation: options.useText && agentTurn.text ? agentTurn.text : diagnosis.piExplanation,
  };
}

function buildDecision(profile) {
  const verdict = chooseVerdict(profile);
  if (verdict === "谨慎持有") {
    return {
      verdict,
      note: "趋势证据尚可，但需要控制单一股票暴露。",
      summary: "当前数据支持继续跟踪持仓，但不支持把诊断结果升级为主动加仓指令。",
      notHeld: "等待更好的风险补偿，不追逐短期强势。",
      held: "控制仓位，保留风险边界，观察趋势能否延续。",
    };
  }
  if (verdict === "数据不足") {
    return {
      verdict,
      note: "关键证据缺失，不能给出稳定判断。",
      summary: "当前诊断缺少足够数据支持，应先补齐证据再讨论操作。",
      notHeld: "暂不进入候选，等待数据补齐。",
      held: "降低对模型结论的信任，优先人工复核风险。",
    };
  }
  return {
    verdict,
    note: "有证据支撑继续跟踪，但缺少足够安全边际。",
    summary: "估值、流动性和趋势状态未给出明确进场信号。当前更适合作为观察对象，而不是交易动作。",
    notHeld: "等待更清晰的风险补偿；不要因为品牌确定性替代买入条件。",
    held: "控制仓位，继续观察趋势和估值修复，不把诊断卡当作加仓指令。",
  };
}

function buildRisks(profile) {
  const basic = profile.daily_basic || {};
  const moneyflow = profile.moneyflow || {};
  const risks = [
    `20 日收益: ${pct(profile.returns?.ret_20d)}；60 日收益: ${pct(profile.returns?.ret_60d)}。`,
  ];
  if (typeof basic.pe_ttm === "number" || typeof basic.pb === "number") {
    risks.push(`估值快照: PE_TTM ${number(basic.pe_ttm)}；PB ${number(basic.pb)}；PS_TTM ${number(basic.ps_ttm)}。`);
  }
  if (typeof moneyflow.net_mf_amount === "number") {
    risks.push(`最新资金流净额: ${moneyWan(moneyflow.net_mf_amount)}。`);
  }
  if (profile.warnings?.length) {
    risks.push(...profile.warnings);
  }
  if (!profile.moneyflow || Object.keys(profile.moneyflow).length === 0) {
    risks.push("资金流证据缺失或不可用。");
  }
  return risks;
}

function buildEvidence(profile) {
  const sources = (profile.data_sources || []).filter(Boolean);
  const basic = profile.daily_basic || {};
  const moneyflow = profile.moneyflow || {};
  const evidence = [
    `股票画像: ${profile.name || profile.code} ${profile.code}`,
    `最新价格数据日期: ${profile.latest_price?.date || "未知"}`,
  ];
  if (typeof profile.price_cny === "number") {
    evidence.push(`真实股价(不复权): ${number(profile.price_cny)} 元；对应估值日期: ${tradeDate(profile.basic_date)}。`);
  }
  if (profile.latest_price?.close !== undefined) {
    evidence.push(`后复权收盘价(仅用于收益计算): ${number(profile.latest_price.close, 4)}；不可当真实股价展示。`);
  }
  if (Object.keys(basic).length) {
    evidence.push(`估值: PE_TTM ${number(basic.pe_ttm)}；PB ${number(basic.pb)}；PS_TTM ${number(basic.ps_ttm)}；总市值 ${marketCapYi(basic.total_mv)}。`);
  }
  if (typeof moneyflow.net_mf_amount === "number") {
    evidence.push(`资金流净额: ${moneyWan(moneyflow.net_mf_amount)}。`);
  }
  return [
    ...evidence,
    ...sources.map((source) => `来源: ${source}`),
  ];
}

function existingThread(context = {}) {
  return context.currentThread || context.thread || {};
}

function reusableThreadId(context = {}) {
  const thread = existingThread(context);
  return typeof thread.id === "string" && thread.id && thread.id !== "empty" ? thread.id : "";
}

function strategyPrecheckDiagnosis(prompt, skill, context = {}, ideaCheck = null) {
  const thread = existingThread(context);
  const skillInfo = skill ? publicSkill(skill) : {
    id: "strategy-precheck",
    name: "策略预检",
    shortName: "策略",
    category: "策略",
    description: "策略想法边界预检",
    boundary: "只做想法边界预检；禁止伪造收益曲线。",
    requiresStock: false,
    mode: "strategy_precheck",
  };
  const trust = ideaCheck?.trust || {
    banner_status: "attention",
    headline: "想法尚未经确定性回测与 9-Gate",
    detail: "当前只做边界预检；不会生成伪收益曲线。",
  };
  const missing = ideaCheck?.parsed_hints?.missing_definition_fields || [];
  const themes = ideaCheck?.parsed_hints?.candidate_terms
    || ideaCheck?.parsed_hints?.matched_themes
    || [];
  const registryHits = ideaCheck?.parsed_hints?.registry_factor_hits || [];
  const cost = ideaCheck?.system_facts?.cost_model;
  const quality = ideaCheck?.system_facts?.data_quality;
  const funnel = ideaCheck?.system_facts?.funnel;
  const related = ideaCheck?.system_facts?.related_families || [];
  const hasCli = Boolean(ideaCheck);

  const risks = [
    ...(missing.length ? [`定义缺口: ${missing.join("、")}`] : ["定义线索较完整，但仍未跑回测与门禁。"]),
    ...(quality?.backtest_blocked ? ["数据质量 backtest_blocked：禁止用半截数据假装已验证。"] : []),
    "Agent 叙述不是有效性裁决；有效性只认 BacktestEngine + 9-Gate。",
  ];

  const evidence = [
    ...(skill ? [`选中 Skill: ${skill.name}`] : ["自动进入策略想法预检"]),
    `用户输入: ${prompt}`,
    ...(Array.isArray(ideaCheck?.evidence) ? ideaCheck.evidence : []),
    ...(cost ? [`固定成本: ${cost.display}`] : []),
    ...(themes.length ? [`候选词: ${themes.join("、")}`] : []),
    ...(registryHits.length ? [`已注册因子命中: ${registryHits.join(", ")}`] : []),
    ...(related.length
      ? [`相关家族线索: ${related.slice(0, 3).map((item) => item.id).join(", ")}`]
      : []),
  ];

  const status = quality?.backtest_blocked ? "数据阻断" : "想法预检";
  return {
    thread: {
      id: reusableThreadId(context) || `skill-strategy-precheck-${Date.now()}`,
      name: thread.name && thread.name !== "等待输入" ? thread.name : "策略想法预检",
      code: thread.code || "",
      status,
    },
    taskSteps: [
      skillTaskStep(skillInfo),
      {
        name: hasCli ? "CLI 策略想法预检" : "记录策略想法",
        desc: hasCli
          ? "已通过 strategy_idea_check 读取成本、数据质量、漏斗与边界。"
          : "已把用户输入作为待验证假设保存；本轮未拿到 strategy_idea_check 证据。",
        status: "done",
      },
      {
        name: "拆解验证口径",
        desc: missing.length
          ? `仍缺: ${missing.slice(0, 4).join("、")}`
          : "定义线索较完整；下一步才是确定性回测。",
        status: missing.length ? "pending" : "done",
      },
      {
        name: "确定性回测与 9-Gate",
        desc: "未执行。Agent 不得跳过门禁宣布有效。",
        status: "pending",
      },
    ],
    decision: {
      verdict: quality?.backtest_blocked ? "数据阻断" : "想法预检",
      note: trust.headline,
      summary: hasCli
        ? `${trust.detail} 系统强制 can_claim_valid=false，不会生成伪收益曲线。`
        : "当前只能做策略预检：拆清楚假设、数据需求和验证边界；不会生成伪收益曲线。",
      notHeld: "不要把该策略直接作为候选或交易依据，先补齐可验证定义并经门禁。",
      held: "已有持仓不要按该策略想法调整仓位。",
    },
    risks,
    evidence,
    limits: [
      "本结果不构成交易建议。",
      "当前只做策略想法预检，不执行正式回测，不生成收益曲线。",
      `Skill 边界: ${skillInfo.boundary}`,
      ...(Array.isArray(ideaCheck?.limits) ? ideaCheck.limits : []),
      ...(Array.isArray(ideaCheck?.forbidden_claims) ? ideaCheck.forbidden_claims : []),
    ],
    sourceChips: [
      `Skill: ${skillInfo.shortName || skillInfo.name}`,
      "strategy-precheck",
      "no-fake-curve",
      "read-only",
      hasCli ? "cli-grounded" : "awaiting-cli",
      cost ? "fixed-cost" : null,
      funnel ? `funnel-reg=${funnel.registered ?? "?"}` : null,
    ].filter(Boolean),
    activeSkills: [skillInfo],
    trust: {
      banner_status: trust.banner_status || "attention",
      headline: trust.headline,
      detail: trust.detail,
      can_claim_valid: false,
      fake_curve_allowed: false,
      validation_status: ideaCheck?.validation_status || "idea_precheck_local",
      cost_display: cost?.display || "",
      missing_fields: missing,
      candidate_terms: themes,
      registry_factor_hits: registryHits,
      related_families: related.slice(0, 5),
      funnel,
      data_quality: quality || null,
    },
    ideaCheck,
    piExplanation: "",
  };
}

function unresolvedDiagnosis(prompt, skill = null, context = {}) {
  const thread = existingThread(context);
  const diagnosis = {
    thread: {
      id: reusableThreadId(context) || `diagnosis-unresolved-${Date.now()}`,
      name: thread.name && thread.name !== "等待输入" ? thread.name : "待识别股票",
      code: thread.code || "",
      status: "数据不足",
    },
    taskSteps: TASK_STEPS.map((step, index) => (index === 0 ? { ...step, status: "blocked" } : { ...step, status: "pending" })),
    decision: {
      verdict: "数据不足",
      note: "未能识别股票代码。",
      summary: "请补充股票名称或 6 位代码。若输入的是简称，本地数据湖必须能在 codes.parquet 中匹配到它。",
      notHeld: "先不要进入候选。",
      held: "先不要按该问题调整仓位。",
    },
    risks: ["无法解析股票代码。"],
    evidence: [`用户输入: ${prompt}`],
    limits: ["本结果不构成交易建议。", "未读取本地 Python read service 的股票画像。"],
    sourceChips: ["待澄清", "read-only"],
    activeSkills: [],
    piExplanation: "",
  };
  return withSkillContext(diagnosis, skill);
}

function clipTurnText(value, limit = MAX_TURN_CHARS) {
  return String(value || "").slice(0, limit);
}

function normalizeTurns(turns) {
  if (!Array.isArray(turns)) return [];
  return turns
    .filter((turn) => turn && typeof turn.content === "string" && ["user", "assistant"].includes(turn.role))
    .map((turn) => ({ role: turn.role, content: clipTurnText(turn.content) }))
    .slice(-MAX_CONTEXT_TURNS);
}

function contextStockCode(context) {
  const thread = existingThread(context);
  return extractStockCode(thread.code || "");
}

function stableThreadId(profile, context) {
  const currentId = reusableThreadId(context);
  if (currentId) return currentId;
  return `stock-${profile.code}`;
}

function stockLabel(diagnosis) {
  const thread = diagnosis.thread || {};
  if (thread.code) return `${thread.name || "当前股票"} ${thread.code}`;
  return thread.name || "当前对象";
}

function compactEvidence(items = [], limit = 3) {
  return items
    .filter(Boolean)
    .slice(0, limit)
    .map((item) => `- ${item}`)
    .join("\n");
}

function promptIntent(prompt) {
  // Only used for stock follow-up reply shaping; routing is decided by Pi CLI evidence.
  const text = String(prompt || "");
  if (/风险|下行|回撤|减仓|止损|亏|跌/.test(text)) return "risk";
  if (/估值|贵|便宜|PE|PB|PS|市盈|市净|市销/.test(text)) return "valuation";
  if (/持有|仓位|卖|买|候选|加仓|进入/.test(text)) return "position";
  return "general";
}

function strategySkillOrDefault(selectedSkill = null) {
  if (selectedSkill?.mode === "strategy_precheck") return selectedSkill;
  return skillDefinitions.find((item) => item.id === "strategy-precheck") || selectedSkill || null;
}

function piCalledStockTools(agentTurn) {
  const calls = Array.isArray(agentTurn?.cliCalls) ? agentTurn.cliCalls : [];
  return calls.some((call) => !call.isError && ["resolve_stock_code", "stock_profile"].includes(call.capability));
}

function buildAssistantReply(prompt, diagnosis) {
  if (diagnosis.piExplanation) return clipTurnText(diagnosis.piExplanation, 1800);

  const decision = diagnosis.decision || {};
  const risks = compactEvidence(diagnosis.risks || [], 3);
  const evidence = compactEvidence(diagnosis.evidence || [], 3);
  const label = stockLabel(diagnosis);
  const boundary = "我不会把这段诊断当作交易指令，只按已读取证据推进。";

  if (decision.verdict === "待模拟盘" || decision.verdict === "想法预检" || decision.verdict === "数据阻断") {
    const trustLine = diagnosis.trust?.headline ? `\n信任状态: ${diagnosis.trust.headline}` : "";
    const missing = (diagnosis.trust?.missing_fields || []).slice(0, 4);
    const missingLine = missing.length ? `\n仍缺定义: ${missing.join("、")}` : "";
    const impl = (diagnosis.ideaCheck?.system_facts?.implementation_notes || []).slice(0, 2);
    const implLine = impl.length ? `\n系统读取:\n${impl.map((item) => `- ${item}`).join("\n")}` : "";
    return `${decision.summary}${trustLine}${missingLine}${implLine}\n\n以上边界来自 Pi 经系统 CLI 返回的结构化证据；未跑可审计回测前不会宣布策略有效，也不展示伪收益曲线。`;
  }

  if (decision.verdict === "对话" || decision.verdict === "Agent 无证据") {
    return decision.summary || "本轮未从系统 CLI 读到结构化证据。";
  }

  if (!diagnosis.thread?.code) {
    return `${decision.summary || "还没有识别到股票代码。"}\n\n${boundary}`;
  }

  const intent = promptIntent(prompt);
  if (intent === "risk") {
    return `${label} 的追问我先按“下行风险”处理。\n\n已检查的主要风险:\n${risks || "- 当前风险证据不足。"}\n\n保守结论: ${decision.held || decision.summary}\n${boundary}`;
  }

  if (intent === "valuation") {
    return `${label} 的估值问题只能基于本地 read service 已返回的快照讨论。\n\n已检查证据:\n${evidence || "- 当前估值证据不足。"}\n\n保守结论: ${decision.summary}\n${boundary}`;
  }

  if (intent === "position") {
    return `${label} 现在不能直接简化成买或卖。\n\n如果未持有: ${decision.notHeld || "先等待证据补齐。"}\n如果已持有: ${decision.held || "先控制仓位并复核风险。"}\n\n依据:\n${risks || evidence || "- 当前证据不足。"}\n${boundary}`;
  }

  return `${decision.summary}\n\n已检查证据:\n${evidence || risks || "- 当前证据不足。"}\n\n如果未持有: ${decision.notHeld || "先等待证据补齐。"}\n如果已持有: ${decision.held || "先控制仓位并复核风险。"}\n${boundary}`;
}

function appendTurns(context, prompt, diagnosis) {
  return [
    ...normalizeTurns(context?.turns || context?.messages),
    { role: "user", content: clipTurnText(prompt) },
    { role: "assistant", content: buildAssistantReply(prompt, diagnosis) },
  ];
}

async function requestPiAgentTurn(piBridge, prompt, context, selectedSkill) {
  if (!piBridge?.runAgentTurn) {
    return { ready: false, text: "", cliCalls: [], error: "Pi agent turn is unavailable" };
  }
  try {
    return await piBridge.runAgentTurn({
      prompt,
      context,
      skills: skillDefinitions,
      selectedSkill,
    });
  } catch (error) {
    return { ready: false, text: "", cliCalls: [], error: error.message };
  }
}

function successfulCliCall(agentTurn, capability) {
  const calls = Array.isArray(agentTurn?.cliCalls) ? agentTurn.cliCalls : [];
  return [...calls].reverse().find((call) => call.capability === capability && !call.isError) || null;
}

function profileFromAgentTurn(agentTurn) {
  const result = successfulCliCall(agentTurn, "stock_profile")?.result;
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  return result;
}

function codeFromAgentTurn(agentTurn) {
  const profile = profileFromAgentTurn(agentTurn);
  if (profile?.code) return extractStockCode(profile.code);
  return extractStockCode(successfulCliCall(agentTurn, "resolve_stock_code")?.result || "");
}

function buildStrategyDiagnosisFromPi(prompt, context, selectedSkill, agentTurn, ideaCheck) {
  const skill = strategySkillOrDefault(selectedSkill);
  // Do not inherit a stock code into a strategy evidence thread.
  const strategyContext = {
    ...context,
    currentThread: {
      id: reusableThreadId(context) || `skill-strategy-precheck-${Date.now()}`,
      name: "策略想法预检",
      code: "",
      status: "想法预检",
    },
  };
  let diagnosis = strategyPrecheckDiagnosis(prompt, skill, strategyContext, ideaCheck);
  const grounded = groundStrategyAgentText(agentTurn?.text, agentTurn?.cliCalls, ideaCheck);
  diagnosis = attachPiAgentTurn(diagnosis, agentTurn, {
    useText: Boolean(grounded),
    fallbackUsed: false,
  });
  diagnosis.piExplanation = grounded || "";
  const envelope = envelopeFromIdeaCheck(ideaCheck);
  diagnosis.trust = {
    ...(diagnosis.trust || {}),
    can_claim_valid: false,
    fake_curve_allowed: false,
    evidence_tier: envelope?.evidence_tier || diagnosis.trust?.validation_status || "precheck",
    protocol_id: envelope?.protocol_id || "idea_precheck",
    sources: envelope?.sources || [],
    allows_performance_display: allowsPerformanceDisplay(envelope),
  };
  diagnosis.evidenceEnvelope = envelope;
  diagnosis.turns = appendTurns(strategyContext, prompt, diagnosis);
  return diagnosis;
}

function buildAgentOnlyDiagnosis(prompt, context, selectedSkill, agentTurn) {
  const thread = existingThread(context);
  const hasText = Boolean(String(agentTurn?.text || "").trim());
  const diagnosis = {
    thread: {
      id: reusableThreadId(context) || `agent-${Date.now()}`,
      name: thread.name && thread.name !== "等待输入" ? thread.name : "研究对话",
      code: "",
      status: hasText ? "对话" : "Agent 无证据",
    },
    taskSteps: [
      {
        name: "Pi agent",
        desc: hasText
          ? "Pi 返回了自然语言，但本轮没有可用的结构化 CLI 证据。"
          : (agentTurn?.error || "Pi 未完成系统 CLI 读取。"),
        status: hasText ? "done" : "blocked",
      },
    ],
    decision: {
      verdict: hasText ? "对话" : "Agent 无证据",
      note: "本轮未形成股票画像或策略预检结构化结果。",
      summary: hasText
        ? "以下内容来自 Pi 叙述；未绑定 stock_profile / strategy_idea_check 时，不能当作已验证证据。"
        : `Pi 未能通过系统 CLI 读到结构化证据。${agentTurn?.error ? ` 错误: ${agentTurn.error}` : ""}`,
      notHeld: "先不要当作候选或交易依据。",
      held: "先不要按本轮结果调整仓位。",
    },
    risks: hasText
      ? ["自然语言未与 CLI 结构化字段对齐，可信度有限。"]
      : ["系统 CLI 证据缺失。"],
    evidence: [
      `用户输入: ${prompt}`,
      ...(Array.isArray(agentTurn?.cliCalls) ? agentTurn.cliCalls.map(cliCallEvidence) : []),
    ],
    limits: [
      "客户端不替 Agent 硬编码意图路由。",
      "结构化证据只认 Pi 调用的 readonly CLI。",
    ],
    sourceChips: ["pi-agent", hasText ? "narrative-only" : "no-cli-evidence"],
    activeSkills: selectedSkill ? [publicSkill(selectedSkill)] : [],
    piExplanation: hasText ? String(agentTurn.text).trim() : "",
    trust: {
      banner_status: "attention",
      headline: hasText ? "仅有 Agent 叙述，缺少系统 CLI 结构化证据" : "Agent 未读到系统证据",
      detail: "请让 Agent 重试并调用 astock_cli 能力。",
      can_claim_valid: false,
      fake_curve_allowed: false,
    },
  };
  return attachPiAgentTurn(diagnosis, agentTurn, { useText: hasText });
}

function emitRoundTiming(pathLabel, agentTurn) {
  try {
    const timing = getAgentTurnTiming(agentTurn) || {};
    recordDiagnosisRoundTiming({
      path: pathLabel,
      piMs: timing.pi_ms,
      toolCount: timing.tool_count,
      toolMsSum: timing.tool_ms_sum,
    });
  } catch (_error) {
    // Pure observation: never surface timing failures to callers.
  }
}

function createDiagnosisService({ readClient, piBridge } = {}) {
  if (!readClient) throw new Error("readClient is required");

  return {
    async runDiagnosis(prompt, context = {}) {
      const selectedSkill = normalizeSelectedSkill(context);

      // Single entry: Pi agent chooses tools and reads the system via astock_cli.
      const agentTurn = await requestPiAgentTurn(piBridge, prompt, context, selectedSkill);
      const ideaCheck = ideaCheckFromAgentTurn(agentTurn);

      // Product shape follows what Pi actually called — not keyword hardcoding.
      if (ideaCheck || selectedSkill?.mode === "strategy_precheck") {
        const diagnosis = buildStrategyDiagnosisFromPi(prompt, context, selectedSkill, agentTurn, ideaCheck);
        emitRoundTiming("strategy", agentTurn);
        return diagnosis;
      }

      const cliProfile = profileFromAgentTurn(agentTurn);
      let resolveError;
      // Stock HTTP fallback only when:
      // - stock skill / context already has a code, or
      // - Pi already called stock tools, or
      // - Pi returned no CLI calls (offline/unavailable) so local read service may still help.
      // Never used to force a ticker prompt for strategy CLI evidence paths.
      const piReturnedNoCli = !Array.isArray(agentTurn?.cliCalls) || agentTurn.cliCalls.length === 0;
      const piOfflineOrFailed = piReturnedNoCli && !agentTurn?.ready;
      const allowStockFallback = Boolean(
        selectedSkill?.requiresStock
        || piCalledStockTools(agentTurn)
        || contextStockCode(context)
        || extractStockCode(prompt)
        // Only when Pi truly did not run: keep legacy local stock diagnosis available.
        || (piOfflineOrFailed && selectedSkill?.mode !== "strategy_precheck")
      );
      const promptCode = codeFromAgentTurn(agentTurn) || await (async () => {
        if (!allowStockFallback) return null;
        try {
          if (extractStockCode(prompt)) return extractStockCode(prompt);
          return readClient.resolveStockCode
            ? await readClient.resolveStockCode(prompt)
            : null;
        } catch (error) {
          resolveError = error;
          return null;
        }
      })();
      const code = promptCode || (allowStockFallback ? contextStockCode(context) : null);

      if (!code) {
        if (resolveError && allowStockFallback) throw resolveError;
        // Not a forced stock diagnosis: surface Pi conversation instead of "补股票代码".
        if (!selectedSkill?.requiresStock && !piCalledStockTools(agentTurn)) {
          const diagnosis = buildAgentOnlyDiagnosis(prompt, context, selectedSkill, agentTurn);
          diagnosis.turns = appendTurns(context, prompt, diagnosis);
          emitRoundTiming("agent_only", agentTurn);
          return diagnosis;
        }
        let diagnosis = unresolvedDiagnosis(prompt, selectedSkill, context);
        diagnosis = attachPiAgentTurn(diagnosis, agentTurn, {
          fallbackUsed: Boolean(agentTurn?.ready),
          useText: true,
        });
        diagnosis.turns = appendTurns(context, prompt, diagnosis);
        // Stock-intent path without a resolved code (skill/tools asked for ticker).
        emitRoundTiming("stock", agentTurn);
        return diagnosis;
      }

      const profile = cliProfile || await readClient.getStockProfile(code);
      const fallbackUsed = !cliProfile;
      const decision = buildDecision(profile);
      let diagnosis = {
        thread: {
          id: stableThreadId(profile, context),
          name: profile.name || profile.code,
          code: profile.code,
          status: decision.verdict,
        },
        taskSteps: TASK_STEPS,
        decision,
        risks: buildRisks(profile),
        evidence: buildEvidence(profile),
        limits: [
          "本结果不构成交易建议。",
          "当前版本只读本地数据，不连接交易执行。",
          "Agent 只能解释证据，不能替代确定性读模型。",
        ],
        sourceChips: [
          profile.latest_price?.date ? `数据截至 ${profile.latest_price.date}` : "数据日期未知",
          typeof profile.price_cny === "number" ? `真实股价 ${number(profile.price_cny)} 元` : "真实股价未知",
          "PIT 检查",
          "风险快照",
          "read-only",
        ],
        activeSkills: [],
        piExplanation: "",
      };
      diagnosis = withSkillContext(diagnosis, selectedSkill);
      diagnosis = attachPiAgentTurn(diagnosis, agentTurn, {
        fallbackUsed,
        useText: true,
      });
      diagnosis.turns = appendTurns(context, prompt, diagnosis);
      emitRoundTiming("stock", agentTurn);
      return diagnosis;
    },
  };
}

module.exports = {
  TASK_STEPS,
  createDiagnosisService,
  buildDecision,
  chooseVerdict,
  normalizeSelectedSkill,
  buildAssistantReply,
  codeFromAgentTurn,
  profileFromAgentTurn,
  groundStrategyAgentText,
  ideaCheckFromAgentTurn,
  strategyPrecheckDiagnosis,
  promptIntent,
  piCalledStockTools,
  allowsPerformanceDisplay,
  stripPerformanceClaimsFromText,
  envelopeFromIdeaCheck,
};
