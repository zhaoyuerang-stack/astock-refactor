/**
 * Lab-rail launcher (ADR-037 Dual-Rail Evidence).
 *
 * Strict rail = formal evidence via tools/CLI.
 * Lab rail = free exploration sandbox under scratch/lab/; default non-evidence.
 * Write isolation is OS-enforced by macOS Seatbelt (sandbox-exec + lab.sb).
 *
 * Pure helpers (buildLabSpawnArgs / buildLabPiArgs / ensureLabDir) are unit-testable
 * without electron or third-party packages.
 */
const { spawn } = require("node:child_process");
const { mkdirSync, realpathSync } = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const SANDBOX_EXEC = "/usr/bin/sandbox-exec";
const LAB_PROFILE_PATH = path.join(__dirname, "lab.sb");
const CLIENT_ROOT = path.resolve(__dirname, "../../..");
const REPO_ROOT = path.resolve(CLIENT_ROOT, "../..");
const DEFAULT_RESEARCH_ROOT = path.join(REPO_ROOT, "factor_research");
const DEFAULT_TIMEOUT_MS = 60000;
const SESSION_ID_RE = /^[A-Za-z0-9_-]+$/;
const RAIL = "lab";

const LAB_SYSTEM_PROMPT = [
  "你在 AStock Lens 的 Lab 草稿沙箱中运行（ADR-037 Lab 轨）。",
  "这里的全部输出永远不是产品证据，不得被展示为「已验证 / 可入册 / 可实盘」。",
  "文件只能写入本会话的 LAB_DIR；不得写入仓库其他路径。",
  "你可以自由使用 Pi 内建 bash/write 等工具做探索与草稿实验。",
  "禁止宣布策略有效；禁止编造年化/夏普/回撤/净值作为系统事实。",
  "正式证据只来自 Strict 轨 tool/CLI；Lab 结果默认 can_claim_valid=false。",
].join("\n");

/**
 * Build Pi CLI args for Lab rail.
 * Unlike Strict (buildPiAgentArgs): does NOT pass --no-extensions / --extension
 * so built-in bash/write tools stay available for sandbox exploration.
 */
function buildLabPiArgs(prompt, options = {}) {
  const args = [
    "--offline",
    "--no-session",
    "--no-prompt-templates",
    "--no-themes",
    "--mode",
    "json",
    "--system-prompt",
    options.systemPrompt || LAB_SYSTEM_PROMPT,
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

/**
 * Full argv for sandbox-exec (not including the binary itself).
 * Caller spawns: spawn(SANDBOX_EXEC, buildLabSpawnArgs(...), ...)
 */
function buildLabSpawnArgs({ labDir, tmpDir, piArgs }) {
  if (!labDir || !tmpDir) {
    throw new Error("buildLabSpawnArgs requires labDir and tmpDir");
  }
  const args = [
    "-D",
    `LAB_DIR=${labDir}`,
    "-D",
    `TMP_DIR=${tmpDir}`,
    "-f",
    LAB_PROFILE_PATH,
    "pi",
  ];
  if (Array.isArray(piArgs) && piArgs.length) {
    args.push(...piArgs);
  }
  return args;
}

/**
 * Create factor_research/scratch/lab/<sessionId>/ and return absolute path.
 * sessionId must match [A-Za-z0-9_-] only (path-escape prevention).
 * Side effect: mkdir only.
 */
function ensureLabDir(sessionId) {
  if (typeof sessionId !== "string" || !SESSION_ID_RE.test(sessionId)) {
    throw new Error(
      `invalid lab sessionId (must match [A-Za-z0-9_-]): ${JSON.stringify(sessionId)}`
    );
  }
  const labDir = path.join(DEFAULT_RESEARCH_ROOT, "scratch", "lab", sessionId);
  mkdirSync(labDir, { recursive: true });
  return labDir;
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

/** Tag every event with rail:"lab" at the bridge layer (never trust the model). */
function tagLabEvent(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return { type: "unknown", raw: event, rail: RAIL };
  }
  return { ...event, rail: RAIL };
}

function parseLabJsonEventStream(stdout) {
  const events = [];
  for (const line of String(stdout || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      events.push(tagLabEvent(JSON.parse(trimmed)));
    } catch (_error) {
      // Non-JSON startup noise is never evidence.
    }
  }

  const starts = new Map();
  const toolCalls = [];
  let finalText = "";
  let completed = false;
  for (const event of events) {
    if (event.type === "tool_execution_start") {
      starts.set(event.toolCallId, event);
    }
    if (event.type === "tool_execution_end") {
      const start = starts.get(event.toolCallId) || {};
      let toolArguments = start.args?.arguments || {};
      if (typeof start.args?.argumentsJson === "string") {
        try {
          toolArguments = JSON.parse(start.args.argumentsJson);
        } catch (_error) {
          toolArguments = {};
        }
      }
      toolCalls.push({
        toolName: event.toolName || start.toolName || "",
        arguments: toolArguments,
        result: parseToolPayload(event.result),
        isError: Boolean(event.isError),
        rail: RAIL,
      });
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
    toolCalls,
    events,
    rail: RAIL,
    error: completed ? "" : "Lab Pi JSON event stream ended before agent_end",
  };
}

function runLabSandboxedProcess(spawnArgs, options = {}) {
  const retainedTypes = new Set([
    "tool_execution_start",
    "tool_execution_end",
    "message_end",
    "agent_end",
  ]);

  return new Promise((resolve) => {
    const child = spawn(SANDBOX_EXEC, spawnArgs, {
      cwd: options.cwd,
      env: options.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const retainedEvents = [];
    let stdoutRemainder = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;

    function retainLine(line) {
      const trimmed = line.trim();
      if (!trimmed) return;
      try {
        const event = JSON.parse(trimmed);
        if (retainedTypes.has(event.type)) {
          retainedEvents.push(tagLabEvent(event));
        }
      } catch (_error) {
        // Ignore non-JSON startup output; never evidence.
      }
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
      resolve({
        ...parseLabJsonEventStream(
          retainedEvents.map((event) => JSON.stringify(event)).join("\n")
        ),
        ready: false,
        rail: RAIL,
        error: error.message,
      });
    });

    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      retainLine(stdoutRemainder);
      const result = parseLabJsonEventStream(
        retainedEvents.map((event) => JSON.stringify(event)).join("\n")
      );
      if (timedOut) {
        resolve({
          ...result,
          ready: false,
          rail: RAIL,
          error: `Lab agent timed out after ${options.timeout || DEFAULT_TIMEOUT_MS}ms`,
        });
        return;
      }
      if (code !== 0) {
        resolve({
          ...result,
          ready: false,
          rail: RAIL,
          error: stderr.trim() || `Lab sandbox-exec exited with code ${code}`,
        });
        return;
      }
      resolve({ ...result, rail: RAIL });
    });
  });
}

/**
 * Lab-rail bridge: spawn pi under Seatbelt write sandbox.
 * cwd = labDir; env merges PYTHONDONTWRITEBYTECODE=1; all events tagged rail:"lab".
 */
function createLabBridge(options = {}) {
  const model = options.model || process.env.ASTOCK_PI_MODEL || "";
  const thinking = options.thinking || process.env.ASTOCK_PI_THINKING || "low";
  const timeout = Number(options.timeout || process.env.ASTOCK_PI_TIMEOUT_MS || DEFAULT_TIMEOUT_MS);

  return {
    rail: RAIL,

    /**
     * Run one Lab turn inside the write sandbox for sessionId.
     * Returns { ready, text, toolCalls, events, rail, error }.
     */
    async runLabTurn({ prompt, sessionId, systemPrompt } = {}) {
      if (!sessionId) {
        return {
          ready: false,
          text: "",
          toolCalls: [],
          events: [],
          rail: RAIL,
          error: "sessionId is required for Lab rail",
        };
      }

      let labDir;
      try {
        labDir = ensureLabDir(sessionId);
      } catch (error) {
        return {
          ready: false,
          text: "",
          toolCalls: [],
          events: [],
          rail: RAIL,
          error: error.message,
        };
      }

      // Seatbelt param must be the real path of TMPDIR (not a symlink).
      const tmpDir = realpathSync(os.tmpdir());
      const piArgs = buildLabPiArgs(prompt, {
        systemPrompt,
        model: model || undefined,
        thinking,
      });
      const spawnArgs = buildLabSpawnArgs({ labDir, tmpDir, piArgs });
      const env = {
        ...process.env,
        PI_OFFLINE: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        TMPDIR: tmpDir,
        TMP: tmpDir,
        TEMP: tmpDir,
      };

      return runLabSandboxedProcess(spawnArgs, {
        cwd: labDir,
        timeout,
        env,
      });
    },
  };
}

module.exports = {
  SANDBOX_EXEC,
  LAB_PROFILE_PATH,
  LAB_SYSTEM_PROMPT,
  REPO_ROOT,
  RAIL,
  buildLabSpawnArgs,
  buildLabPiArgs,
  ensureLabDir,
  createLabBridge,
  parseLabJsonEventStream,
  tagLabEvent,
};
