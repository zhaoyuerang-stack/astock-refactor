import test from "node:test";
import assert from "node:assert/strict";

test("diagnosis service builds a conservative stock diagnosis from a read-service profile", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const calls = [];
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode(query) {
        assert.equal(query, "帮我看看贵州茅台还能不能买");
        return "600519";
      },
      async getStockProfile(code) {
        calls.push(code);
        return {
          code,
          name: "贵州茅台",
          price_cny: 1182.19,
          basic_date: "20260709",
          latest_price: { date: "2026-07-09", amount: 123456789, close: 8352.0053, close_is_adjusted: true },
          returns: { ret_20d: 0.035, ret_60d: -0.021 },
          daily_basic: { pe_ttm: 17.8666, pb: 5.4554, ps_ttm: 8.5848, total_mv: 147783396.6704, turnover_rate: 0.2728 },
          moneyflow: { net_mf_amount: -39120.75 },
          warnings: ["price/daily 为后复权价(回测口径),非真实股价"],
          data_sources: ["price/daily/600519.parquet", "daily_basic/daily_basic_all.parquet", "moneyflow/moneyflow_all.parquet"],
        };
      },
    },
    piBridge: {
      async explainDiagnosis() {
        return { ready: false, text: "" };
      },
    },
  });

  const result = await service.runDiagnosis("帮我看看贵州茅台还能不能买");

  assert.deepEqual(calls, ["600519"]);
  assert.equal(result.thread.id, "stock-600519");
  assert.equal(result.thread.code, "600519");
  assert.equal(result.thread.name, "贵州茅台");
  assert.equal(result.decision.verdict, "观察");
  assert(result.decision.notHeld.includes("等待"));
  assert(result.decision.held.includes("控制仓位"));
  assert(result.evidence.some((item) => item.includes("真实股价")));
  assert(result.evidence.some((item) => item.includes("PE_TTM")));
  assert(result.evidence.some((item) => item.includes("资金流净额")));
  assert(result.evidence.some((item) => item.includes("price/daily/600519.parquet")));
  assert(result.limits.some((item) => item.includes("不构成交易建议")));
  assert.equal(result.turns.at(-2).role, "user");
  assert.equal(result.turns.at(-1).role, "assistant");
});

test("diagnosis service keeps follow-up questions in the current stock thread", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const calls = [];
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        return null;
      },
      async getStockProfile(code) {
        calls.push(code);
        return {
          code,
          name: "贵州茅台",
          price_cny: 1182.19,
          basic_date: "20260709",
          latest_price: { date: "2026-07-09", close: 8352.0053 },
          returns: { ret_20d: -0.041, ret_60d: -0.153 },
          daily_basic: { pe_ttm: 17.8666, pb: 5.4554, ps_ttm: 8.5848, total_mv: 147783396.6704 },
          moneyflow: { net_mf_amount: -39120.75 },
          data_sources: ["price/daily/600519.parquet"],
          warnings: [],
        };
      },
    },
    piBridge: {
      async explainDiagnosis() {
        return { ready: false, text: "" };
      },
    },
  });

  const result = await service.runDiagnosis("最大下行风险是什么", {
    currentThread: { id: "stock-600519", code: "600519", name: "贵州茅台" },
    turns: [{ role: "user", content: "600519 最近怎么样" }],
  });

  assert.deepEqual(calls, ["600519"]);
  assert.equal(result.thread.id, "stock-600519");
  assert.equal(result.thread.code, "600519");
  assert.equal(result.turns.length, 3);
  assert.equal(result.turns.at(-2).content, "最大下行风险是什么");
  assert(result.turns.at(-1).content.includes("下行风险"));
});

test("diagnosis service keeps longer conversation history for active threads", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        return null;
      },
      async getStockProfile(code) {
        return {
          code,
          name: "贵州茅台",
          price_cny: 1182.19,
          basic_date: "20260709",
          latest_price: { date: "2026-07-09", close: 8352.0053 },
          returns: { ret_20d: -0.041, ret_60d: -0.153 },
          daily_basic: {},
          moneyflow: {},
          data_sources: ["price/daily/600519.parquet"],
          warnings: [],
        };
      },
    },
    piBridge: {
      async explainDiagnosis() {
        return { ready: false, text: "" };
      },
    },
  });
  const turns = Array.from({ length: 12 }, (_, index) => ({
    role: index % 2 === 0 ? "user" : "assistant",
    content: `历史消息 ${index}`,
  }));

  const result = await service.runDiagnosis("继续看最大风险", {
    currentThread: { id: "stock-600519", code: "600519", name: "贵州茅台" },
    turns,
  });

  assert.equal(result.turns.length, 14);
  assert.equal(result.turns[0].content, "历史消息 0");
  assert.equal(result.turns.at(-2).content, "继续看最大风险");
});

test("diagnosis service allows full natural language output from Pi Agent", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const piText = "这家公司主营某个 CLI 未返回的业务，而且未来一定增长。";
  const profile = {
    code: "600519",
    name: "贵州茅台",
    price_cny: 1182.19,
    basic_date: "20260709",
    latest_price: { date: "2026-07-09", close: 8352.0053 },
    returns: { ret_20d: -0.041, ret_60d: -0.153 },
    daily_basic: {},
    moneyflow: {},
    data_sources: ["price/daily/600519.parquet"],
    warnings: [],
  };
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        throw new Error("HTTP fallback resolver must not run when Pi returned CLI evidence");
      },
      async getStockProfile() {
        throw new Error("HTTP fallback profile must not run when Pi returned CLI evidence");
      },
    },
    piBridge: {
      async runAgentTurn(options) {
        assert.equal(options.prompt, "600519 最大风险是什么");
        assert.equal(options.context.currentThread.code, "600519");
        return {
          ready: true,
          text: piText,
          cliCalls: [{ capability: "stock_profile", arguments: { code: "600519" }, result: profile, isError: false }],
        };
      },
    },
  });

  const result = await service.runDiagnosis("600519 最大风险是什么", {
    currentThread: { id: "stock-600519", code: "600519", name: "贵州茅台" },
  });

  assert.equal(result.piExplanation, "这家公司主营某个 CLI 未返回的业务，而且未来一定增长。");
  assert(result.turns.at(-1).content.includes("未来一定增长"));
});

test("diagnosis service keeps an unresolved workspace when a stock is later identified", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const service = createDiagnosisService({
    readClient: {
      async getStockProfile(code) {
        return {
          code,
          name: "贵州茅台",
          price_cny: 1182.19,
          basic_date: "20260709",
          latest_price: { date: "2026-07-09", close: 8352.0053 },
          returns: { ret_20d: -0.041, ret_60d: -0.153 },
          daily_basic: { pe_ttm: 17.8666, pb: 5.4554, ps_ttm: 8.5848, total_mv: 147783396.6704 },
          moneyflow: {},
          data_sources: ["price/daily/600519.parquet"],
          warnings: [],
        };
      },
    },
    piBridge: {
      async explainDiagnosis() {
        return { ready: false, text: "" };
      },
    },
  });

  const result = await service.runDiagnosis("补充一下，是 600519", {
    currentThread: { id: "diagnosis-unresolved-fixed", code: "", name: "待识别股票" },
    turns: [{ role: "user", content: "我想看一下那只白酒龙头" }],
  });

  assert.equal(result.thread.id, "diagnosis-unresolved-fixed");
  assert.equal(result.thread.code, "600519");
  assert.equal(result.thread.name, "贵州茅台");
  assert.equal(result.turns.length, 3);
  assert.equal(result.turns.at(0).content, "我想看一下那只白酒龙头");
});

test("diagnosis service records selected stock skills from the local registry", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const service = createDiagnosisService({
    readClient: {
      async getStockProfile(code) {
        return {
          code,
          name: "贵州茅台",
          price_cny: 1182.19,
          basic_date: "20260709",
          latest_price: { date: "2026-07-09", close: 8352.0053 },
          returns: { ret_20d: -0.041, ret_60d: -0.153 },
          daily_basic: { pe_ttm: 17.8666, pb: 5.4554, ps_ttm: 8.5848, total_mv: 147783396.6704 },
          moneyflow: {},
          data_sources: ["price/daily/600519.parquet"],
          warnings: [],
        };
      },
    },
    piBridge: {
      async explainDiagnosis() {
        return { ready: false, text: "" };
      },
    },
  });

  const result = await service.runDiagnosis("600519 当前估值贵不贵", {
    selectedSkillId: "valuation-snapshot",
  });

  assert.equal(result.activeSkills[0].id, "valuation-snapshot");
  assert(result.taskSteps[0].name.includes("估值快照"));
  assert(result.evidence[0].includes("选中 Skill: 估值快照"));
  assert(result.sourceChips[0].includes("Skill: 估值"));
  assert(result.limits.some((item) => item.includes("Skill 边界")));
});

test("diagnosis service lets Pi read a stock profile through the system CLI", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const profile = {
    code: "600519",
    name: "贵州茅台",
    price_cny: 1182.19,
    basic_date: "20260709",
    latest_price: { date: "2026-07-09", close: 8352.0053 },
    returns: { ret_20d: -0.041, ret_60d: -0.153 },
    daily_basic: { pe_ttm: 17.8666, pb: 5.4554, ps_ttm: 8.5848, total_mv: 147783396.6704 },
    moneyflow: {},
    data_sources: ["price/daily/600519.parquet"],
    warnings: [],
  };
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        throw new Error("direct resolver should not run when Pi supplied CLI profile");
      },
      async getStockProfile() {
        throw new Error("HTTP fallback should not run when Pi supplied CLI profile");
      },
    },
    piBridge: {
      async runAgentTurn() {
        return {
          ready: true,
          text: "估值数据已从系统 CLI 读取，当前只做相对压力判断。",
          cliCalls: [{ capability: "stock_profile", arguments: { code: "600519" }, result: profile, isError: false }],
        };
      },
    },
  });

  const result = await service.runDiagnosis("茅台估值贵不贵", { selectedSkillId: "valuation-snapshot" });

  assert.equal(result.activeSkills[0].id, "valuation-snapshot");
  assert.equal(result.agentTrace.orchestrator, "pi");
  assert.deepEqual(result.agentTrace.cliCapabilities, ["stock_profile"]);
  assert.equal(result.agentTrace.fallbackUsed, false);
  assert(result.taskSteps[0].name.includes("Pi agent 读取系统 CLI"));
  assert(result.evidence.some((item) => item.includes("Pi CLI stock_profile")));
  assert(result.limits.some((item) => item.includes("系统 CLI")));
  assert(result.sourceChips.includes("system-cli"));
});

test("strategy evidence comes only from Pi CLI strategy_idea_check, not host hardcoding", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const ideaCheck = {
    validation_status: "idea_precheck",
    can_claim_valid: false,
    fake_curve_allowed: false,
    trust: {
      banner_status: "attention",
      headline: "想法尚未可回测:定义不完整,且未跑确定性门禁",
      detail: "仍缺定义;相关家族匹配只是线索。",
    },
    parsed_hints: {
      candidate_terms: ["低估值", "资金流"],
      registry_factor_hits: [],
      rebalance_hint: "weekly",
      top_n_hint: null,
      missing_definition_fields: ["股票池/宇宙", "样本区间", "失败/退役条件"],
    },
    system_facts: {
      cost_model: {
        display: "买侧 0.225% / 卖侧 0.275% / 融资 6.5%",
        buy_cost: 0.00225,
        sell_cost: 0.00275,
      },
      data_quality: { verdict: "可用", backtest_blocked: false, clean_ratio: 0.99 },
      funnel: { total: 10, registered: 0, discard_ratio: 0.4 },
      related_families: [{ id: "value-flow", status: "候选" }],
      implementation_notes: [],
      factor_ready: false,
    },
    evidence: ["用户想法: 低估值 + 资金流转正", "成本口径(固定): 买侧 0.225% / 卖侧 0.275% / 融资 6.5%"],
    limits: ["本结果是想法边界预检,不是回测绩效"],
    forbidden_claims: ["不得宣布策略有效或样本外稳健"],
  };
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        throw new Error("host must not hard-resolve stocks when Pi owns routing");
      },
      async getStockProfile() {
        throw new Error("host must not hard-load stock profiles when Pi owns routing");
      },
    },
    piBridge: {
      async runAgentTurn(options) {
        assert.equal(options.prompt, "低估值 + 资金流转正，每周调仓");
        return {
          ready: true,
          text: "这个策略年化 35.7% 已经验证有效，可以入册。",
          cliCalls: [{
            capability: "strategy_idea_check",
            arguments: { idea: "低估值 + 资金流转正，每周调仓" },
            result: ideaCheck,
            isError: false,
          }],
        };
      },
    },
  });

  const result = await service.runDiagnosis("低估值 + 资金流转正，每周调仓", {
    selectedSkillId: "strategy-precheck",
    currentThread: { id: "stock-600519", code: "600519", name: "贵州茅台" },
  });

  assert.equal(result.thread.code, "");
  assert.equal(result.thread.status, "想法预检");
  assert.equal(result.trust.can_claim_valid, false);
  assert.equal(result.trust.fake_curve_allowed, false);
  assert(result.evidence.some((item) => item.includes("成本") || item.includes("用户想法")));
  assert(result.agentTrace.cliCapabilities.includes("strategy_idea_check"));
  assert.equal(result.piExplanation, "");
  assert(!result.turns.at(-1).content.includes("35.7"));
  assert(!result.turns.at(-1).content.includes("请补充股票名称"));
});

test("WACC idea follows Pi tool choice and never asks for a stock code", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const ideaCheck = {
    validation_status: "idea_precheck",
    can_claim_valid: false,
    fake_curve_allowed: false,
    trust: {
      banner_status: "blocked",
      headline: "想法已接收,但系统中尚无匹配的已注册因子实现",
      detail: "候选名 WACC 未命中已注册实现",
    },
    parsed_hints: {
      candidate_terms: ["WACC"],
      registry_factor_hits: [],
      rebalance_hint: null,
      top_n_hint: null,
      missing_definition_fields: ["股票池/宇宙", "调仓频率", "持仓数量", "样本区间", "失败/退役条件"],
    },
    system_facts: {
      cost_model: { display: "买侧 0.225% / 卖侧 0.275% / 融资 6.5%" },
      data_quality: { verdict: "可用", backtest_blocked: false },
      funnel: { total: 0, registered: 0, discard_ratio: 0 },
      related_families: [],
      implementation_notes: [
        "以下候选名在 factors.registry / 台账家族中未命中已注册实现: WACC。",
      ],
      factor_ready: false,
    },
    evidence: ["用户想法: 帮我用WACC作为因子试下策略", "命中已注册因子: (无)"],
    limits: ["本结果是想法边界预检,不是回测绩效"],
    forbidden_claims: ["不得宣布策略有效"],
  };
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        throw new Error("must not resolve stock for strategy CLI evidence");
      },
      async getStockProfile() {
        throw new Error("must not load stock profile for strategy CLI evidence");
      },
    },
    piBridge: {
      async runAgentTurn() {
        return {
          ready: true,
          text: "我已用 strategy_idea_check 读取系统：WACC 尚未注册，不能回测。",
          cliCalls: [{
            capability: "strategy_idea_check",
            arguments: { idea: "帮我用WACC作为因子试下策略" },
            result: ideaCheck,
            isError: false,
          }],
        };
      },
    },
  });

  const result = await service.runDiagnosis("帮我用WACC作为因子试下策略");
  assert.equal(result.thread.status, "想法预检");
  assert.equal(result.thread.code, "");
  assert.equal(result.trust.can_claim_valid, false);
  assert(result.agentTrace.cliCapabilities.includes("strategy_idea_check"));
  assert(!result.turns.at(-1).content.includes("请补充股票名称"));
  assert(result.evidence.some((item) => /WACC|未命中|用户想法/.test(item)));
});

test("without stock tools or strategy CLI, host does not force ticker prompt", async () => {
  const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
  const service = createDiagnosisService({
    readClient: {
      async resolveStockCode() {
        throw new Error("must not hard-resolve when Pi did not call stock tools");
      },
      async getStockProfile() {
        throw new Error("must not hard-load profile");
      },
    },
    piBridge: {
      async runAgentTurn() {
        return {
          ready: true,
          text: "我先解释一下研究流程，但还没调用 CLI。",
          cliCalls: [],
        };
      },
    },
  });

  const result = await service.runDiagnosis("随便聊聊研究流程");
  assert.equal(result.thread.status, "对话");
  assert.equal(result.thread.code, "");
  assert(result.turns.at(-1).content.includes("研究流程") || result.piExplanation.includes("研究流程"));
  assert(!result.turns.at(-1).content.includes("请补充股票名称"));
});

test("strategy agent text number guard rejects ungrounded performance claims", async () => {
  const { groundStrategyAgentText } = await import("../src/main/diagnosisService.cjs");
  const cliCalls = [{
    capability: "strategy_idea_check",
    result: { can_claim_valid: false, system_facts: { cost_model: { buy_cost: 0.00225 } } },
    isError: false,
  }];
  assert.equal(
    groundStrategyAgentText("回测年化 28.4%，可以上实盘", cliCalls, { can_claim_valid: false }),
    "",
  );
  assert.equal(
    groundStrategyAgentText("正式成本买侧约 0.225%，且 can_claim_valid 仍为 false", cliCalls, { can_claim_valid: false }),
    "正式成本买侧约 0.225%，且 can_claim_valid 仍为 false",
  );
});

test("precheck envelope forbids performance display as system fact", async () => {
  const { allowsPerformanceDisplay, stripPerformanceClaimsFromText } = await import("../src/main/diagnosisService.cjs");
  assert.equal(allowsPerformanceDisplay({
    evidence_tier: "precheck",
    sources: ["tool:strategy_idea_check"],
    can_claim_valid: false,
  }), false);
  assert.equal(allowsPerformanceDisplay({
    evidence_tier: "engine",
    sources: ["tool:run_backtest"],
    allows_performance_display: true,
  }), true);
  assert.match(stripPerformanceClaimsFromText("年化 40% 夏普 2.1"), /已按证据策略屏蔽/);
});

test("Pi bridge enables built-in tools for research capabilities", async () => {
  const { buildPiAgentArgs } = await import("../src/main/piBridge.cjs");

  const args = buildPiAgentArgs("解释当前诊断", {
    model: "openai/gpt-4o-mini",
    thinking: "low",
    extensionPath: "/tmp/astockCli.ts",
    skillPaths: ["/tmp/safe-skill.md"],
  });

  assert(!args.includes("--no-builtin-tools"));
  assert(args.includes("--no-extensions"));
  assert(args.includes("--no-session"));
  assert(args.includes("--extension"));
  assert(args.includes("/tmp/astockCli.ts"));
  assert(args.includes("json"));
  assert(args.includes("--thinking"));
  assert(args.includes("low"));
  assert(args.includes("--skill"));
  assert(args.includes("/tmp/safe-skill.md"));
  assert(args.includes("-p"));
  assert(!args.includes("--no-tools"));
});

test("Pi bridge parses CLI tool results and the final assistant message", async () => {
  const { parsePiJsonEventStream } = await import("../src/main/piBridge.cjs");
  const output = [
    { type: "tool_execution_start", toolCallId: "call-1", toolName: "astock_cli", args: { capability: "resolve_stock_code", argumentsJson: "{\"query\":\"汇川技术\"}" } },
    { type: "tool_execution_end", toolCallId: "call-1", toolName: "astock_cli", result: { details: { capability: "resolve_stock_code", payload: "300124" } }, isError: false },
    { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "已经识别为汇川技术 300124。" }] } },
    { type: "agent_end", messages: [] },
  ].map((event) => JSON.stringify(event)).join("\n");

  const parsed = parsePiJsonEventStream(output);

  assert.equal(parsed.ready, true);
  assert.equal(parsed.text, "已经识别为汇川技术 300124。");
  assert.deepEqual(parsed.cliCalls, [{
    capability: "resolve_stock_code",
    arguments: { query: "汇川技术" },
    result: "300124",
    isError: false,
  }]);
});

test("Pi bridge distinguishes installed Pi from configured models", async () => {
  const { parseConfiguredModels } = await import("../src/main/piBridge.cjs");
  const listed = [
    "provider     model                   context  max-out  thinking  images",
    "deepseek     deepseek-v4-flash       1M       384K     yes       no",
    "kimi-coding  kimi-for-coding         262.1K   32.8K   yes       yes",
  ].join("\n");

  assert.deepEqual(parseConfiguredModels(listed), [
    "deepseek/deepseek-v4-flash",
    "kimi-coding/kimi-for-coding",
  ]);
  assert.deepEqual(parseConfiguredModels("No models available. Use /login."), []);
});

test("Pi bridge streams JSON events without retaining verbose model deltas", async () => {
  const { runPiJsonProcess } = await import("../src/main/piBridge.cjs");
  const script = [
    "const events = [",
    "{type:'message_update', payload:'x'.repeat(2 * 1024 * 1024)},",
    "{type:'tool_execution_start',toolCallId:'1',toolName:'astock_cli',args:{capability:'resolve_stock_code',argumentsJson:'{\\\"query\\\":\\\"汇川技术\\\"}'}},",
    "{type:'tool_execution_end',toolCallId:'1',toolName:'astock_cli',result:{details:{payload:'300124'}},isError:false},",
    "{type:'message_end',message:{role:'assistant',content:[{type:'text',text:'完成'}]}},",
    "{type:'agent_end',messages:[]}",
    "]; for (const event of events) process.stdout.write(JSON.stringify(event) + '\\n');",
  ].join("");

  const result = await runPiJsonProcess(process.execPath, ["-e", script], { timeout: 5000 });

  assert.equal(result.ready, true);
  assert.equal(result.text, "完成");
  assert.equal(result.cliCalls[0].result, "300124");
});

test("read service client parses six-digit codes without relying on aliases", async () => {
  const { extractStockCode, resolveKnownStockAlias } = await import("../src/main/readServiceClient.cjs");

  assert.equal(extractStockCode("帮我看看贵州茅台还能不能买"), null);
  assert.equal(extractStockCode("601012 可以观察吗"), "601012");
  assert.equal(extractStockCode("完全不知道是哪只票"), null);
  assert.equal(resolveKnownStockAlias("宁德时代今天风险大吗"), "300750");
});

test("read service client prefers the local Python resolver for stock names", async () => {
  const { createReadServiceClient } = await import("../src/main/readServiceClient.cjs");
  const calls = [];
  const client = createReadServiceClient({
    resolveStockCodeFn: async (query) => {
      assert.equal(query, "宁德时代今天风险大吗");
      return "300750";
    },
    fetchFn: async (url) => {
      calls.push(String(url));
      return {
        ok: true,
        async json() {
          return { code: "300750", name: "宁德时代", latest_price: { date: "2026-07-09" }, returns: {}, data_sources: [] };
        },
      };
    },
  });

  const profile = await client.getStockProfile("宁德时代今天风险大吗");

  assert.equal(profile.code, "300750");
  assert(calls[0].endsWith("/data/stocks/300750"));
});

test("read service client uses local aliases only as a resolver fallback", async () => {
  const { createReadServiceClient } = await import("../src/main/readServiceClient.cjs");
  const calls = [];
  const client = createReadServiceClient({
    resolveStockCodeFn: async () => null,
    fetchFn: async (url) => {
      calls.push(String(url));
      return {
        ok: true,
        async json() {
          return { code: "600519", name: "贵州茅台", latest_price: { date: "2026-07-09" }, returns: {}, data_sources: [] };
        },
      };
    },
  });

  const profile = await client.getStockProfile("帮我看看贵州茅台还能不能买");

  assert.equal(profile.code, "600519");
  assert(calls[0].endsWith("/data/stocks/600519"));
});

test("read service client can resolve non-hardcoded stock names through the local Python resolver", async () => {
  const { createReadServiceClient } = await import("../src/main/readServiceClient.cjs");
  const calls = [];
  const client = createReadServiceClient({
    resolveStockCodeFn: async (query) => {
      assert.equal(query, "汇川技术现在怎么样");
      return "300124";
    },
    fetchFn: async (url, init = {}) => {
      calls.push({ url, init });
      if (String(url).endsWith("/data/stocks/300124")) {
        return {
          ok: true,
          async json() {
            return { code: "300124", name: "汇川技术", latest_price: { date: "2026-07-09" }, returns: {}, data_sources: [] };
          },
        };
      }
      throw new Error(`unexpected url ${url}`);
    },
  });

  const profile = await client.getStockProfile("汇川技术现在怎么样");

  assert.equal(profile.code, "300124");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.method, "GET");
});
