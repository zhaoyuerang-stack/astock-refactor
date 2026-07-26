/**
 * Pure-observation round timing for desktop diagnosis (L0.1).
 * Fail-open: write errors never throw into the diagnosis path.
 */
const fs = require("node:fs");
const path = require("node:path");

const CLIENT_ROOT = path.resolve(__dirname, "../..");
const DEFAULT_RUNTIME_DIR = path.join(CLIENT_ROOT, ".runtime");
const LOG_BASENAME = "round_timing.jsonl";
const ALLOWED_PATHS = new Set(["strategy", "stock", "agent_only"]);

function resolveRuntimeDir(options = {}) {
  return options.runtimeDir
    || process.env.ASTOCK_ROUND_TIMING_DIR
    || DEFAULT_RUNTIME_DIR;
}

function resolveLogPath(options = {}) {
  if (options.logPath) return options.logPath;
  return path.join(resolveRuntimeDir(options), LOG_BASENAME);
}

/**
 * @param {{ path?: string, piMs?: number, toolCount?: number, toolMsSum?: number, ts?: string }} input
 */
function buildRoundTimingRecord(input = {}) {
  const pi_ms = Math.max(0, Math.round(Number(input.piMs) || 0));
  const tool_count = Math.max(0, Math.round(Number(input.toolCount) || 0));
  const tool_ms_sum = Math.max(0, Math.round(Number(input.toolMsSum) || 0));
  const model_est_ms = Math.max(0, pi_ms - tool_ms_sum);
  const diagPath = ALLOWED_PATHS.has(input.path) ? input.path : "unknown";
  return {
    ts: typeof input.ts === "string" && input.ts ? input.ts : new Date().toISOString(),
    path: diagPath,
    pi_ms,
    tool_count,
    tool_ms_sum,
    model_est_ms,
  };
}

/**
 * Append one NDJSON line. Returns true on success, false on failure (stderr only).
 */
function appendRoundTimingRecord(record, options = {}) {
  try {
    const logPath = resolveLogPath(options);
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `${JSON.stringify(record)}\n`, "utf8");
    return true;
  } catch (error) {
    try {
      process.stderr.write(`[round_timing] write failed: ${error && error.message ? error.message : error}\n`);
    } catch (_stderrError) {
      // ignore
    }
    return false;
  }
}

/**
 * Build + append. Never throws.
 * @returns {object|null} the record written, or null on failure
 */
function recordDiagnosisRoundTiming(input = {}, options = {}) {
  try {
    const record = buildRoundTimingRecord(input);
    const ok = appendRoundTimingRecord(record, options);
    return ok ? record : null;
  } catch (error) {
    try {
      process.stderr.write(`[round_timing] record failed: ${error && error.message ? error.message : error}\n`);
    } catch (_stderrError) {
      // ignore
    }
    return null;
  }
}

module.exports = {
  ALLOWED_PATHS,
  DEFAULT_RUNTIME_DIR,
  LOG_BASENAME,
  buildRoundTimingRecord,
  appendRoundTimingRecord,
  recordDiagnosisRoundTiming,
  resolveRuntimeDir,
  resolveLogPath,
};
