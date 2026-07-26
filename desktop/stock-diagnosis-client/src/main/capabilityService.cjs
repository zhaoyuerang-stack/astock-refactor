/**
 * Strict-rail capability runner for desktop (ADR-037 P4.3).
 * Mid-risk tools require explicit confirm → one-time token → agent_cli.
 */
const { execFile } = require("node:child_process");
const crypto = require("node:crypto");
const path = require("node:path");
const { promisify } = require("node:util");

const execFileAsync = promisify(execFile);

const CLIENT_ROOT = path.resolve(__dirname, "../..");
const REPO_ROOT = path.resolve(CLIENT_ROOT, "../..");
const DEFAULT_RESEARCH_ROOT = path.join(REPO_ROOT, "factor_research");
const DEFAULT_AGENT_CLI = path.join(DEFAULT_RESEARCH_ROOT, "apps", "agent_cli.py");

const MID_RISK_TOOLS = new Set(["run_backtest"]);

function createCapabilityService(options = {}) {
  const python = options.python || process.env.ASTOCK_PYTHON_COMMAND || process.env.PYTHON || "python3";
  const researchRoot = options.researchRoot || process.env.ASTOCK_RESEARCH_ROOT || DEFAULT_RESEARCH_ROOT;
  const agentCliPath = options.agentCliPath || process.env.ASTOCK_AGENT_CLI_PATH || DEFAULT_AGENT_CLI;
  const timeoutMs = Number(options.timeoutMs || process.env.ASTOCK_CAPABILITY_TIMEOUT_MS || 600000);
  const runCliFn = options.runCli || null;

  async function runCli(args, envExtra = {}) {
    if (runCliFn) return runCliFn(args, envExtra);
    const { stdout, stderr } = await execFileAsync(python, [agentCliPath, ...args], {
      cwd: researchRoot,
      timeout: timeoutMs,
      maxBuffer: 1024 * 1024 * 4,
      env: { ...process.env, ...envExtra },
    });
    const text = String(stdout || "").trim();
    if (!text) {
      const err = new Error(stderr?.trim() || "agent_cli returned empty stdout");
      err.stderr = stderr;
      throw err;
    }
    return JSON.parse(text);
  }

  return {
    async catalog() {
      const payload = await runCli(["catalog"]);
      return Array.isArray(payload.capabilities) ? payload.capabilities : [];
    },

    /**
     * @param {{ tool: string, args?: object, confirmed?: boolean }} request
     */
    async runCapability(request = {}) {
      const tool = String(request.tool || "");
      const args = request.args && typeof request.args === "object" && !Array.isArray(request.args)
        ? request.args
        : {};
      if (!tool) {
        return { ok: false, error: "tool is required", needs_confirmation: false };
      }

      const isMid = MID_RISK_TOOLS.has(tool);
      if (isMid && !request.confirmed) {
        return {
          ok: false,
          needs_confirmation: true,
          tool,
          risk: "mid",
          confirm_prompt: [
            "将调用正式 BacktestEngine，扣固定真实成本",
            "（买 0.225% / 卖 0.275% / 融资 6.5%）。",
            "结果仅作 engine 级证据，不是入册裁决。确认执行？",
          ].join(""),
          args,
          can_claim_valid: false,
        };
      }

      const cliArgs = [
        "call",
        "--tool",
        tool,
        "--args-json",
        JSON.stringify(args),
      ];
      const envExtra = {};
      if (isMid) {
        const token = crypto.randomBytes(16).toString("hex");
        envExtra.ASTOCK_MID_CONFIRM_TOKEN = token;
        cliArgs.push("--confirm-token", token);
      } else {
        cliArgs.push("--readonly-only");
      }

      try {
        const payload = await runCli(cliArgs, envExtra);
        const result = payload.result;
        const envelope = result && typeof result === "object" ? result.evidence_envelope : null;
        return {
          ok: true,
          needs_confirmation: false,
          tool: payload.capability || tool,
          result,
          evidence_envelope: envelope,
          can_claim_valid: false,
        };
      } catch (error) {
        let message = error.message || String(error);
        const stderr = String(error.stderr || "");
        for (const line of stderr.split(/\r?\n/).reverse()) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          try {
            const errPayload = JSON.parse(trimmed);
            if (errPayload?.error) {
              message = errPayload.error;
              break;
            }
          } catch (_e) {
            // continue
          }
        }
        return {
          ok: false,
          needs_confirmation: false,
          tool,
          error: message,
          can_claim_valid: false,
        };
      }
    },
  };
}

module.exports = {
  createCapabilityService,
  MID_RISK_TOOLS,
};
