import assert from "node:assert/strict";
import test from "node:test";

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
}

async function importFreshApi() {
  process.env.NEXT_PUBLIC_API_BASE = "http://api.test";
  return import(`./api.ts?case=${Date.now()}-${Math.random()}`);
}

test("protected AutoResearch posts include local action token and job polling returns result", async () => {
  const calls = [];
  const { api } = await importFreshApi();

  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("http://api.test", "");
    calls.push({ path, init });

    if (path === "/settings/action-token") {
      return jsonResponse({ header: "X-Action-Token", token: "secret-token", source: "file" });
    }

    if (path === "/experiments/autoresearch/run-seeds?limit=2&max_stage=l1") {
      assert.equal(init.method, "POST");
      assert.equal(init.headers["X-Action-Token"], "secret-token");
      return jsonResponse({
        job_id: "autoresearch-run-seeds-abc123",
        kind: "autoresearch.run_seeds",
        status: "queued",
        created_at: "2026-06-16T12:00:00Z",
      });
    }

    if (path === "/experiments/jobs/autoresearch-run-seeds-abc123") {
      return jsonResponse({
        job_id: "autoresearch-run-seeds-abc123",
        kind: "autoresearch.run_seeds",
        status: "succeeded",
        result: {
          vintage_id: "vintage-1",
          max_stage: "l1",
          results: [{ fingerprint: "fp1", status: "l1_passed", decision: "pass", reason: "ok", protocols: ["l0", "l1"] }],
        },
      });
    }

    throw new Error(`unexpected fetch ${path}`);
  };

  const job = await api.runAutoresearchSeeds({ limit: 2, max_stage: "l1" });
  assert.equal(job.job_id, "autoresearch-run-seeds-abc123");

  const result = await api.waitForExperimentJob(job.job_id, { intervalMs: 0, timeoutMs: 100 });
  assert.equal(result.vintage_id, "vintage-1");
  assert.equal(result.results[0].fingerprint, "fp1");
  assert.deepEqual(calls.map((c) => c.path), [
    "/settings/action-token",
    "/experiments/autoresearch/run-seeds?limit=2&max_stage=l1",
    "/experiments/jobs/autoresearch-run-seeds-abc123",
  ]);
});

test("protected settings posts use the action token header without exposing it in body", async () => {
  const calls = [];
  const { api } = await importFreshApi();

  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("http://api.test", "");
    calls.push({ path, init });

    if (path === "/settings/action-token") {
      return jsonResponse({ header: "X-Action-Token", token: "settings-token", source: "file" });
    }

    if (path === "/settings/llm") {
      assert.equal(init.method, "POST");
      assert.equal(init.headers["X-Action-Token"], "settings-token");
      assert.equal(JSON.parse(init.body).api_key, "sk-local");
      assert.equal(String(init.body).includes("settings-token"), false);
      return jsonResponse({
        provider: "openai_compatible",
        model: "deepseek-chat",
        base_url: "https://api.deepseek.com/v1",
        has_key: true,
        key_hint: "sk-…al",
        llm_ready: true,
      });
    }

    throw new Error(`unexpected fetch ${path}`);
  };

  const saved = await api.setLlmConfig({
    provider: "openai_compatible",
    model: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    api_key: "sk-local",
  });

  assert.equal(saved.llm_ready, true);
  assert.deepEqual(calls.map((c) => c.path), ["/settings/action-token", "/settings/llm"]);
});

test("production backtest client does not send fixed leverage", async () => {
  const calls = [];
  const { api } = await importFreshApi();

  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("http://api.test", "");
    calls.push({ path, init });
    assert.equal(path.includes("leverage="), false);
    return jsonResponse({
      annual: 0,
      vol: 0,
      sharpe: 0,
      maxdd: 0,
      calmar: 0,
      hit: false,
      n: 0,
      turnover_annual: 0,
      cost_annual: 0,
      yearly_returns: {},
      n_stocks: 0,
      n_days: 0,
      start: "2018-01-01",
      end: "2018-01-01",
      family: "illiquidity",
      version: "v3.1",
    });
  };

  await api.runBacktest({
    start: "2018-01-01",
    top_n: 25,
    rebalance_days: 20,
    factor_window: 20,
    timing_ma: 16,
    leverage: 9,
  });

  assert.equal(
    calls[0].path,
    "/backtest/run?start=2018-01-01&top_n=25&rebalance_days=20&factor_window=20&timing_ma=16",
  );
});

test("agentAsk sends prior messages for multi-turn context", async () => {
  const calls = [];
  const { api } = await importFreshApi();

  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("http://api.test", "");
    calls.push({ path, init });

    if (path === "/agent/ask") {
      assert.equal(init.method, "POST");
      const body = JSON.parse(init.body);
      assert.equal(body.request, "继续解释第二点");
      assert.deepEqual(body.context, { current_page: "overview" });
      assert.deepEqual(body.messages, [
        { role: "user", content: "这个系统怎么用" },
        { role: "assistant", content: "先看总览，再看风控。" },
      ]);
      return jsonResponse({
        output: {
          summary: "继续说明",
          evidence: [],
          risk: [],
          recommendation: [],
          next_actions: [],
          citations: [],
          source_types: [],
          suggested_navigation: [],
          confidence: 0.8,
          requires_human_confirmation: false,
        },
        task_id: "t-chat",
        tool: null,
        risk: null,
        llm_ready: true,
      });
    }

    throw new Error(`unexpected fetch ${path}`);
  };

  await api.agentAsk(
    "继续解释第二点",
    { current_page: "overview" },
    [
      { role: "user", content: "这个系统怎么用" },
      { role: "assistant", content: "先看总览，再看风控。" },
    ],
  );

  assert.equal(calls.length, 1);
});

test("agent session API creates a session and asks inside it", async () => {
  const calls = [];
  const { api } = await importFreshApi();

  globalThis.fetch = async (url, init = {}) => {
    const path = String(url).replace("http://api.test", "");
    calls.push({ path, init });

    if (path === "/agent/sessions") {
      assert.equal(init.method, "POST");
      assert.deepEqual(JSON.parse(init.body), {
        page_context: "overview",
        title: "AI 会话",
        user_id: "local",
      });
      return jsonResponse({
        session_id: "s-123",
        user_id: "local",
        title: "AI 会话",
        page_context: "overview",
        status: "active",
        created_at: "2026-06-17T09:00:00Z",
        updated_at: "2026-06-17T09:00:00Z",
        messages: [],
      });
    }

    if (path === "/agent/sessions/s-123/ask") {
      assert.equal(init.method, "POST");
      assert.deepEqual(JSON.parse(init.body), {
        request: "继续解释",
        context: { current_page: "overview" },
      });
      return jsonResponse({
        output: {
          summary: "继续说明",
          evidence: [],
          risk: [],
          recommendation: [],
          next_actions: [],
          citations: [],
          source_types: [],
          suggested_navigation: [],
          confidence: 0.8,
          requires_human_confirmation: false,
        },
        task_id: "t-session",
        tool: null,
        risk: null,
        llm_ready: true,
        session: {
          session_id: "s-123",
          user_id: "local",
          title: "AI 会话",
          page_context: "overview",
          status: "active",
          created_at: "2026-06-17T09:00:00Z",
          updated_at: "2026-06-17T09:01:00Z",
          messages: [
            { role: "user", content: "继续解释", created_at: "2026-06-17T09:01:00Z", metadata: {} },
            { role: "assistant", content: "继续说明", created_at: "2026-06-17T09:01:01Z", metadata: {} },
          ],
        },
      });
    }

    throw new Error(`unexpected fetch ${path}`);
  };

  const session = await api.createAgentSession({ page_context: "overview", title: "AI 会话", user_id: "local" });
  assert.equal(session.session_id, "s-123");
  const reply = await api.agentSessionAsk("s-123", "继续解释", { current_page: "overview" });
  assert.equal(reply.session.messages.length, 2);
  assert.deepEqual(calls.map((c) => c.path), ["/agent/sessions", "/agent/sessions/s-123/ask"]);
});
