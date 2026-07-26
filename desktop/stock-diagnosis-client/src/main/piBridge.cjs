const { execFile, spawn } = require("node:child_process");
const { mkdirSync } = require("node:fs");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);
const PI_CLI_TOOL = "astock_cli";
const CLIENT_ROOT = path.resolve(__dirname, "../..");
const REPO_ROOT = path.resolve(CLIENT_ROOT, "../..");
const DEFAULT_RESEARCH_ROOT = path.join(REPO_ROOT, "factor_research");
const DEFAULT_AGENT_CLI_PATH = path.join(DEFAULT_RESEARCH_ROOT, "apps", "agent_cli.py");
const DEFAULT_EXTENSION_PATH = path.join(__dirname, "piExtensions", "astockCli.ts");
const DEFAULT_TIMEOUT_MS = 60000;

/** Side-channel only — never mutates enumerable fields of agent-turn results. */
const agentTurnTiming = new WeakMap();

function setAgentTurnTiming(result, timing) {
  if (!result || typeof result !== "object") return;
  try {
    agentTurnTiming.set(result, {
      pi_ms: Math.max(0, Math.round(Number(timing?.pi_ms) || 0)),
      tool_count: Math.max(0, Math.round(Number(timing?.tool_count) || 0)),
      tool_ms_sum: Math.max(0, Math.round(Number(timing?.tool_ms_sum) || 0)),
    });
  } catch (_error) {
    // WeakMap set must never break Pi path.
  }
}

function getAgentTurnTiming(result) {
  if (!result || typeof result !== "object") return null;
  return agentTurnTiming.get(result) || null;
}

function hrtimeMsSince(startNs) {
  return Number(process.hrtime.bigint() - startNs) / 1e6;
}

const SYSTEM_PROMPT = [
  "你是 AStock Lens 的本地量化研究 Agent。你像 Codex 一样用工具读系统，而不是靠猜。",
  `唯一可信的系统事实入口是工具 ${PI_CLI_TOOL}。先按需 catalog/调用能力，再回答。`,
  "可用能力由 CLI catalog 动态给出（含 resolve_stock_code、stock_profile、strategy_idea_check、data_quality、factors、strategies、experiments 等 readonly 项）。",
  "判断问题类型后自己选工具：个股→resolve/stock_profile；策略/因子/回测想法→strategy_idea_check(idea=用户原话)，需要时再 factors/strategies/data_quality。",
  "不要在没有 stock_profile 证据时向用户索要股票代码；策略讨论不需要股票代码。",
  "禁止宣布策略有效；禁止编造年化/夏普/回撤/净值；数字只能复述 CLI 返回字段。",
  "内置 bash/write 的输出不是产品证据。用自然中文推进，缺证据就说尚未读取到系统事实。",
].join("\n");

function compactTurns(turns = [], limit = 20) {
  if (!Array.isArray(turns)) return [];
  return turns
    .filter((turn) => turn && ["user", "assistant"].includes(turn.role) && typeof turn.content === "string")
    .slice(-limit)
    .map((turn) => ({
      role: turn.role,
      content: turn.content.slice(0, 1200),
    }));
}

function buildPiAgentArgs(prompt, options = {}) {
  const args = [
    "--offline",
    "--no-session",
    "--no-prompt-templates",
    "--no-themes",
    "--no-extensions",
    "--extension",
    options.extensionPath || DEFAULT_EXTENSION_PATH,
    "--mode",
    "json",
    "--system-prompt",
    options.systemPrompt || SYSTEM_PROMPT,
  ];
  if (options.model) {
    args.push("--model", options.model);
  }
  if (options.thinking) {
    args.push("--thinking", options.thinking);
  }
  for (const skillPath of options.skillPaths || []) {
    args.push("--skill", skillPath);
  }
  args.push("-p", String(prompt || ""));
  return args;
}

async function detectPi(command = "pi") {
  try {
    const { stdout } = await execFileAsync("which", [command], { timeout: 1500 });
    return { available: true, command: stdout.trim() || command };
  } catch (error) {
    return { available: false, command, error: error.message };
  }
}

function publicSkillForPrompt(skill) {
  return {
    id: skill.id,
    name: skill.name,
    description: skill.description,
    boundary: skill.boundary,
    requiresStock: Boolean(skill.requiresStock),
    mode: skill.mode,
  };
}

function buildAgentTurnPrompt({ prompt, context = {}, skills = [], selectedSkill = null }) {
  return [
    "处理下面这次用户输入。沿用 currentThread 和 recentTurns，不要把追问当成新对象。",
    "你必须用 astock_cli 自己读取系统；客户端不会替你猜意图、也不会替你直调能力。",
    "先根据问题选择 capability；argumentsJson 必须是该 capability 要求的字段（例如 strategy_idea_check 用 idea，stock_profile 用 code）。",
    "回答中的数字只能复述 CLI 已返回字段；没有就写「CLI 未返回」。",
    JSON.stringify({
      currentUserPrompt: String(prompt || ""),
      currentThread: context.currentThread || context.thread || null,
      recentTurns: compactTurns(context.turns || context.messages),
      selectedSkill: selectedSkill ? publicSkillForPrompt(selectedSkill) : null,
      availableSkills: skills.map(publicSkillForPrompt),
    }),
  ].join("\n");
}

function resolveSkillPaths(skills = [], selectedSkill = null) {
  const candidates = selectedSkill ? [selectedSkill] : [];
  return candidates
    .map((skill) => skill?.piSkillFile)
    .filter(Boolean)
    .map((relativePath) => path.resolve(__dirname, relativePath));
}

function messageText(message) {
  if (!message || message.role !== "assistant") return "";
  if (typeof message.content === "string") return message.content.trim();
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("")
    .trim();
}

function parseToolPayload(result) {
  const details = result?.details;
  if (details && typeof details === "object" && !Array.isArray(details)) {
    if (Object.prototype.hasOwnProperty.call(details, "payload")) return details.payload;
    if (Object.prototype.hasOwnProperty.call(details, "result")) return details.result;
  }
  const text = Array.isArray(result?.content)
    ? result.content.find((item) => item?.type === "text")?.text
    : "";
  if (!text) return null;
  try {
    const parsed = JSON.parse(text);
    return Object.prototype.hasOwnProperty.call(parsed, "result") ? parsed.result : parsed;
  } catch (_error) {
    return null;
  }
}

function parsePiJsonEventStream(stdout) {
  const events = [];
  for (const line of String(stdout || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      events.push(JSON.parse(trimmed));
    } catch (_error) {
      // Pi may emit non-JSON startup warnings; they are not trusted as data.
    }
  }

  const starts = new Map();
  const cliCalls = [];
  let finalText = "";
  let completed = false;
  for (const event of events) {
    if (event.type === "tool_execution_start") {
      starts.set(event.toolCallId, event);
    }
    if (event.type === "tool_execution_end") {
      const start = starts.get(event.toolCallId) || {};
      if ((event.toolName || start.toolName) === PI_CLI_TOOL) {
        let toolArguments = start.args?.arguments || {};
        if (typeof start.args?.argumentsJson === "string") {
          try {
            toolArguments = JSON.parse(start.args.argumentsJson);
          } catch (_error) {
            toolArguments = {};
          }
        }
        cliCalls.push({
          capability: start.args?.capability || event.result?.details?.capability || "",
          arguments: toolArguments,
          result: parseToolPayload(event.result),
          isError: Boolean(event.isError),
        });
      }
    }
    if (event.type === "message_end") {
      finalText = messageText(event.message) || finalText;
    }
    if (event.type === "agent_end") {
      completed = true;
      const messages = Array.isArray(event.messages) ? event.messages : [];
      for (const message of messages) {
        finalText = messageText(message) || finalText;
      }
    }
  }

  return {
    ready: completed,
    text: finalText,
    cliCalls,
    error: completed ? "" : "Pi JSON event stream ended before agent_end",
  };
}

function parseCatalog(stdout) {
  const parsed = JSON.parse(String(stdout || "{}"));
  return Array.isArray(parsed.capabilities) ? parsed.capabilities : [];
}

function parseConfiguredModels(stdout) {
  const lines = String(stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.some((line) => line.startsWith("No models available"))) return [];
  return lines
    .slice(1)
    .map((line) => line.split(/\s{2,}/).slice(0, 2).join("/"))
    .filter((model) => model.includes("/"));
}

function runPiJsonProcess(command, args, options = {}) {
  const retainedTypes = new Set([
    "tool_execution_start",
    "tool_execution_end",
    "message_end",
    "agent_end",
  ]);

  return new Promise((resolve) => {
    const processStartNs = process.hrtime.bigint();
    const toolStarts = new Map();
    let toolCount = 0;
    let toolMsSum = 0;

    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const retainedEvents = [];
    let stdoutRemainder = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;

    function noteToolTiming(event) {
      if (!event || typeof event !== "object") return;
      if (event.type === "tool_execution_start" && event.toolCallId) {
        toolStarts.set(event.toolCallId, process.hrtime.bigint());
        return;
      }
      if (event.type === "tool_execution_end") {
        toolCount += 1;
        if (event.toolCallId && toolStarts.has(event.toolCallId)) {
          toolMsSum += hrtimeMsSince(toolStarts.get(event.toolCallId));
          toolStarts.delete(event.toolCallId);
        }
      }
    }

    function retainLine(line) {
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        const event = JSON.parse(trimmed);
        if (retainedTypes.has(event.type)) {
          retainedEvents.push(event);
          noteToolTiming(event);
        }
      } catch (_error) {
        // Ignore non-JSON startup output; it is never evidence.
      }
    }

    function settle(result) {
      setAgentTurnTiming(result, {
        pi_ms: hrtimeMsSince(processStartNs),
        tool_count: toolCount,
        tool_ms_sum: toolMsSum,
      });
      resolve(result);
    }

    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      const lines = `${stdoutRemainder}${chunk}`.split(/\r?\n/);
      stdoutRemainder = lines.pop() || "";
      for (const line of lines) retainLine(line);
    });
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (chunk) => {
      stderr = `${stderr}${chunk}`.slice(-64 * 1024);
    });

    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGKILL");
    }, options.timeout || DEFAULT_TIMEOUT_MS);

    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      settle({
        ...parsePiJsonEventStream(retainedEvents.map((event) => JSON.stringify(event)).join("\n")),
        ready: false,
        error: error.message,
      });
    });

    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      retainLine(stdoutRemainder);
      const result = parsePiJsonEventStream(retainedEvents.map((event) => JSON.stringify(event)).join("\n"));
      if (timedOut) {
        settle({ ...result, ready: false, error: `Pi agent timed out after ${options.timeout || DEFAULT_TIMEOUT_MS}ms` });
        return;
      }
      if (code !== 0) {
        settle({ ...result, ready: false, error: stderr.trim() || `Pi exited with code ${code}` });
        return;
      }
      settle(result);
    });
  });
}

function createPiBridge(options = {}) {
  const command = options.command || process.env.ASTOCK_PI_COMMAND || "pi";
  const model = options.model || process.env.ASTOCK_PI_MODEL || "";
  const thinking = options.thinking || process.env.ASTOCK_PI_THINKING || "low";
  const timeout = Number(options.timeout || process.env.ASTOCK_PI_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);
  const pythonCommand = options.pythonCommand || process.env.ASTOCK_PYTHON_COMMAND || process.env.PYTHON || "python3";
  const researchRoot = options.researchRoot || process.env.ASTOCK_RESEARCH_ROOT || DEFAULT_RESEARCH_ROOT;
  const agentCliPath = options.agentCliPath || process.env.ASTOCK_AGENT_CLI_PATH || DEFAULT_AGENT_CLI_PATH;
  const extensionPath = options.extensionPath || process.env.ASTOCK_PI_EXTENSION_PATH || DEFAULT_EXTENSION_PATH;
  const configDir = options.configDir
    || process.env.ASTOCK_PI_CONFIG_DIR
    || process.env.PI_CODING_AGENT_DIR
    || "";

  async function getCliCapabilities() {
    const { stdout } = await execFileAsync(pythonCommand, [agentCliPath, "catalog"], {
      cwd: researchRoot,
      timeout: Math.min(timeout, 10000),
      maxBuffer: 1024 * 256,
    });
    return parseCatalog(stdout);
  }

  async function getConfiguredModels(piCommand) {
    const { stdout, stderr } = await execFileAsync(piCommand, ["--offline", "--list-models"], {
      timeout: Math.min(timeout, 10000),
      maxBuffer: 1024 * 256,
    });
    return parseConfiguredModels(stdout || stderr);
  }

  return {
    async getStatus() {
      const pi = await detectPi(command);
      if (!pi.available) return pi;
      const [capabilityResult, modelResult] = await Promise.allSettled([
        getCliCapabilities(),
        getConfiguredModels(pi.command),
      ]);
      const capabilities = capabilityResult.status === "fulfilled" ? capabilityResult.value : [];
      const models = modelResult.status === "fulfilled" ? modelResult.value : [];
      return {
        ...pi,
        cliAvailable: capabilityResult.status === "fulfilled",
        capabilities: capabilities.map((capability) => capability.name),
        cliError: capabilityResult.status === "rejected" ? capabilityResult.reason.message : "",
        modelAvailable: models.length > 0,
        models,
        modelError: modelResult.status === "rejected" ? modelResult.reason.message : "",
      };
    },

    async runAgentTurn({ prompt, context = {}, skills = [], selectedSkill = null } = {}) {
      const turnStartNs = process.hrtime.bigint();
      const status = await detectPi(command);
      if (!status.available) {
        const result = { ready: false, text: "", cliCalls: [], error: status.error };
        setAgentTurnTiming(result, {
          pi_ms: hrtimeMsSince(turnStartNs),
          tool_count: 0,
          tool_ms_sum: 0,
        });
        return result;
      }

      const agentPrompt = buildAgentTurnPrompt({ prompt, context, skills, selectedSkill });
      try {
        if (configDir) mkdirSync(configDir, { recursive: true });
        const args = buildPiAgentArgs(agentPrompt, {
          extensionPath,
          model: model || undefined,
          thinking,
          skillPaths: resolveSkillPaths(skills, selectedSkill),
        });
        const piEnv = {
          ...process.env,
          PI_OFFLINE: "1",
          ASTOCK_AGENT_CLI_PATH: agentCliPath,
          ASTOCK_PYTHON_COMMAND: pythonCommand,
          ASTOCK_RESEARCH_ROOT: researchRoot,
        };
        if (configDir) piEnv.PI_CODING_AGENT_DIR = configDir;
        return await runPiJsonProcess(status.command, args, {
          cwd: researchRoot,
          timeout,
          env: piEnv,
        });
      } catch (error) {
        const result = {
          ready: false,
          text: "",
          cliCalls: [],
          error: error.killed
            ? `Pi agent timed out after ${timeout}ms`
            : error.stderr?.trim() || error.message,
        };
        setAgentTurnTiming(result, {
          pi_ms: hrtimeMsSince(turnStartNs),
          tool_count: 0,
          tool_ms_sum: 0,
        });
        return result;
      }
    },
  };
}

module.exports = {
  DEFAULT_AGENT_CLI_PATH,
  DEFAULT_EXTENSION_PATH,
  PI_CLI_TOOL,
  SYSTEM_PROMPT,
  buildAgentTurnPrompt,
  buildPiAgentArgs,
  compactTurns,
  createPiBridge,
  detectPi,
  getAgentTurnTiming,
  parseCatalog,
  parseConfiguredModels,
  parsePiJsonEventStream,
  runPiJsonProcess,
  setAgentTurnTiming,
};
