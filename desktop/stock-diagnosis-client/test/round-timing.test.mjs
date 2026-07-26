/**
 * L0.1 诊断回合计时日志 — 对抗测试
 * 1) mock Pi 流 → 记录字段齐全且数值
 * 2) 不可写 .runtime → 诊断不阻断
 * 3) instrument 不改变业务返回值
 */
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const REQUIRED_NUMERIC = ["pi_ms", "tool_count", "tool_ms_sum", "model_est_ms"];
const REQUIRED_STRING = ["ts", "path"];
const ALLOWED_PATHS = new Set(["strategy", "stock", "agent_only", "unknown"]);

function makeRuntimeDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "round-timing-"));
}

function readLastTimingRecord(runtimeDir) {
  const logPath = path.join(runtimeDir, "round_timing.jsonl");
  assert.ok(fs.existsSync(logPath), `expected timing log at ${logPath}`);
  const lines = fs.readFileSync(logPath, "utf8").trim().split(/\r?\n/).filter(Boolean);
  assert.ok(lines.length >= 1, "expected at least one NDJSON line");
  return JSON.parse(lines[lines.length - 1]);
}

function mockPiStreamScript({ toolDelayMs = 30, capability = "strategy_idea_check", payload } = {}) {
  const resultPayload = payload ?? {
    can_claim_valid: false,
    trust: { banner_status: "attention", headline: "想法预检", detail: "未回测" },
    evidence: ["mock"],
    limits: [],
    forbidden_claims: [],
    parsed_hints: { missing_definition_fields: [] },
    system_facts: {},
  };
  // Emit retained Pi events; sleep between tool start/end so tool_ms_sum > 0.
  return [
    "const events = [",
    `  {type:'tool_execution_start',toolCallId:'t1',toolName:'astock_cli',args:{capability:${JSON.stringify(capability)},argumentsJson:'{}'}},`,
    `  {type:'tool_execution_end',toolCallId:'t1',toolName:'astock_cli',result:{details:{capability:${JSON.stringify(capability)},payload:${JSON.stringify(resultPayload)}}},isError:false},`,
    "  {type:'message_end',message:{role:'assistant',content:[{type:'text',text:'mock reply'}]}},",
    "  {type:'agent_end',messages:[]}",
    "];",
    `const delay = ${Number(toolDelayMs)};`,
    "const start = events[0];",
    "const rest = events.slice(1);",
    "process.stdout.write(JSON.stringify(start) + '\\n');",
    "const end = Date.now() + delay;",
    "while (Date.now() < end) {}",
    "for (const event of rest) process.stdout.write(JSON.stringify(event) + '\\n');",
  ].join("");
}

test("mock Pi stream diagnosis writes round timing with all required numeric fields", async () => {
  const runtimeDir = makeRuntimeDir();
  process.env.ASTOCK_ROUND_TIMING_DIR = runtimeDir;
  try {
    const { runPiJsonProcess, getAgentTurnTiming } = await import("../src/main/piBridge.cjs");
    const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");

    const agentTurn = await runPiJsonProcess(
      process.execPath,
      ["-e", mockPiStreamScript({ toolDelayMs: 40, capability: "strategy_idea_check" })],
      { timeout: 8000 },
    );
    assert.equal(agentTurn.ready, true);
    const streamTiming = getAgentTurnTiming(agentTurn);
    assert.ok(streamTiming, "runPiJsonProcess must attach timing via side-channel");
    assert.equal(typeof streamTiming.pi_ms, "number");
    assert.equal(typeof streamTiming.tool_count, "number");
    assert.equal(typeof streamTiming.tool_ms_sum, "number");

    const service = createDiagnosisService({
      readClient: {
        async resolveStockCode() {
          return null;
        },
        async getStockProfile() {
          throw new Error("should not fetch profile on strategy path");
        },
      },
      piBridge: {
        async runAgentTurn() {
          return agentTurn;
        },
      },
    });

    const diagnosis = await service.runDiagnosis("研究一下小市值动量策略想法");

    const rec = readLastTimingRecord(runtimeDir);
    for (const key of REQUIRED_STRING) {
      assert.ok(Object.prototype.hasOwnProperty.call(rec, key), `missing field ${key}`);
      assert.equal(typeof rec[key], "string", `${key} must be string`);
    }
    for (const key of REQUIRED_NUMERIC) {
      assert.ok(Object.prototype.hasOwnProperty.call(rec, key), `missing field ${key}`);
      assert.equal(typeof rec[key], "number", `${key} must be number`);
      assert.ok(Number.isFinite(rec[key]), `${key} must be finite`);
      assert.ok(rec[key] >= 0, `${key} must be >= 0`);
    }
    assert.ok(ALLOWED_PATHS.has(rec.path), `path must be one of ${[...ALLOWED_PATHS]}`);
    assert.equal(rec.path, "strategy");
    assert.equal(rec.tool_count, 1);
    assert.ok(rec.tool_ms_sum >= 20, `tool_ms_sum should reflect tool delay, got ${rec.tool_ms_sum}`);
    assert.ok(rec.pi_ms >= rec.tool_ms_sum, "pi_ms should cover tool wall time");
    assert.equal(rec.model_est_ms, Math.max(0, rec.pi_ms - rec.tool_ms_sum));
    assert.ok(Number.isFinite(Date.parse(rec.ts)), "ts must be ISO-parseable");
    assert.ok(diagnosis.decision, "diagnosis still returned");
  } finally {
    delete process.env.ASTOCK_ROUND_TIMING_DIR;
    fs.rmSync(runtimeDir, { recursive: true, force: true });
  }
});

test("unwritable timing path does not block diagnosis round", async () => {
  const tmp = makeRuntimeDir();
  // Use a regular file as parent so mkdir/append for .runtime fails.
  const blocker = path.join(tmp, "not-a-dir");
  fs.writeFileSync(blocker, "blocked");
  const unwritableRuntime = path.join(blocker, "runtime");
  process.env.ASTOCK_ROUND_TIMING_DIR = unwritableRuntime;

  try {
    const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
    const expectedSummary = "agent-only narrative";
    const agentTurn = {
      ready: true,
      text: expectedSummary,
      cliCalls: [],
      error: "",
    };
    const service = createDiagnosisService({
      readClient: {
        async resolveStockCode() {
          return null;
        },
        async getStockProfile() {
          throw new Error("unused");
        },
      },
      piBridge: {
        async runAgentTurn() {
          return agentTurn;
        },
      },
    });

    const diagnosis = await service.runDiagnosis("随便聊聊市场结构");
    assert.ok(diagnosis, "diagnosis must complete despite timing write failure");
    assert.equal(diagnosis.decision.verdict, "对话");
    assert.ok(String(diagnosis.piExplanation || diagnosis.decision.summary).length > 0);
    assert.ok(!fs.existsSync(path.join(unwritableRuntime, "round_timing.jsonl")));
  } finally {
    delete process.env.ASTOCK_ROUND_TIMING_DIR;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test("timing instrumentation does not change diagnosis return value bit-for-bit shape", async () => {
  const runtimeDir = makeRuntimeDir();
  process.env.ASTOCK_ROUND_TIMING_DIR = runtimeDir;

  try {
    const { createDiagnosisService } = await import("../src/main/diagnosisService.cjs");
    const profile = {
      code: "600519",
      name: "贵州茅台",
      price_cny: 1182.19,
      basic_date: "20260709",
      latest_price: { date: "2026-07-09", close: 8352.0053 },
      returns: { ret_20d: -0.041, ret_60d: -0.153 },
      daily_basic: { pe_ttm: 17.8, pb: 5.4, ps_ttm: 8.5, total_mv: 1e8 },
      moneyflow: { net_mf_amount: -100 },
      data_sources: ["price/daily/600519.parquet"],
      warnings: [],
    };
    const agentTurn = {
      ready: true,
      text: "已读取画像。",
      cliCalls: [
        {
          capability: "stock_profile",
          arguments: { code: "600519" },
          result: profile,
          isError: false,
        },
      ],
      error: "",
    };

    const service = createDiagnosisService({
      readClient: {
        async resolveStockCode() {
          return "600519";
        },
        async getStockProfile() {
          return profile;
        },
      },
      piBridge: {
        async runAgentTurn() {
          // Return a deep clone each call so WeakMap timing cannot leak into equality.
          return structuredClone(agentTurn);
        },
      },
    });

    const withTiming = await service.runDiagnosis("600519 怎么样");
    // Capture expected business fields (timing must not inject into diagnosis object).
    assert.equal(withTiming.thread.code, "600519");
    assert.equal(withTiming.decision.verdict, "观察");
    assert.ok(Array.isArray(withTiming.evidence));
    assert.ok(Array.isArray(withTiming.turns));
    assert.ok(!Object.prototype.hasOwnProperty.call(withTiming, "pi_ms"));
    assert.ok(!Object.prototype.hasOwnProperty.call(withTiming, "round_timing"));
    assert.ok(!Object.prototype.hasOwnProperty.call(withTiming, "model_est_ms"));

    // Second run: same inputs → same business snapshot (excluding time-based thread ids if any).
    const again = await service.runDiagnosis("600519 怎么样");
    assert.equal(again.thread.code, withTiming.thread.code);
    assert.equal(again.thread.name, withTiming.thread.name);
    assert.deepEqual(again.decision, withTiming.decision);
    assert.deepEqual(again.risks, withTiming.risks);
    assert.deepEqual(again.evidence, withTiming.evidence);
    assert.deepEqual(again.limits, withTiming.limits);
    assert.deepEqual(again.sourceChips, withTiming.sourceChips);
    assert.deepEqual(again.agentTrace?.cliCalls, withTiming.agentTrace?.cliCalls);

    const rec = readLastTimingRecord(runtimeDir);
    assert.equal(rec.path, "stock");
    for (const key of REQUIRED_NUMERIC) {
      assert.equal(typeof rec[key], "number");
    }
  } finally {
    delete process.env.ASTOCK_ROUND_TIMING_DIR;
    fs.rmSync(runtimeDir, { recursive: true, force: true });
  }
});

test("buildRoundTimingRecord clamps model_est_ms and rejects unknown path labels", async () => {
  const { buildRoundTimingRecord } = await import("../src/main/roundTiming.cjs");
  const rec = buildRoundTimingRecord({
    path: "strategy",
    piMs: 100,
    toolCount: 2,
    toolMsSum: 150,
  });
  assert.equal(rec.pi_ms, 100);
  assert.equal(rec.tool_count, 2);
  assert.equal(rec.tool_ms_sum, 150);
  assert.equal(rec.model_est_ms, 0);
  assert.equal(rec.path, "strategy");

  const unk = buildRoundTimingRecord({ path: "weird", piMs: 10, toolCount: 0, toolMsSum: 0 });
  assert.equal(unk.path, "unknown");
});
