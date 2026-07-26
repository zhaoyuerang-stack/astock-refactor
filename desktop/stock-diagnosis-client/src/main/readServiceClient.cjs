const { execFile } = require("node:child_process");
const path = require("node:path");
const { promisify } = require("node:util");

const DEFAULT_BASE_URL = "http://127.0.0.1:8011";
const DEFAULT_TIMEOUT_MS = 4000;
const AGENT_RESOLVE_TIMEOUT_MS = 12000;
const PYTHON_RESOLVE_TIMEOUT_MS = 5000;
const execFileAsync = promisify(execFile);

const NAME_TO_CODE = new Map([
  ["贵州茅台", "600519"],
  ["茅台", "600519"],
  ["宁德时代", "300750"],
  ["宁德", "300750"],
  ["隆基绿能", "601012"],
  ["隆基", "601012"],
]);

function extractStockCode(query) {
  const text = String(query || "").trim();
  const match = text.match(/(?<!\d)(\d{6})(?!\d)/);
  if (match) return match[1];
  return null;
}

function resolveKnownStockAlias(query) {
  const text = String(query || "").trim();
  for (const [name, code] of NAME_TO_CODE.entries()) {
    if (text.includes(name)) return code;
  }
  return null;
}

function normalizeBaseUrl(baseUrl) {
  return String(baseUrl || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function extractStockCodeFromAgentResponse(result) {
  const output = result?.output || {};
  const evidence = Array.isArray(output.evidence) ? output.evidence : [];
  const text = [output.summary, ...evidence].filter(Boolean).join("\n");
  return extractStockCode(text) || resolveKnownStockAlias(text);
}

async function resolveStockCodeWithPython(query, options = {}) {
  const cwd = options.cwd || path.resolve(__dirname, "../../../../factor_research");
  const python = options.python || process.env.PYTHON || "python3";
  const script = "from services.read.stocks import resolve_stock_code; import sys; print(resolve_stock_code(sys.argv[1]) or '')";
  const { stdout } = await execFileAsync(python, ["-c", script, String(query || "")], {
    cwd,
    timeout: options.timeoutMs || PYTHON_RESOLVE_TIMEOUT_MS,
    maxBuffer: 1024 * 64,
  });
  return extractStockCode(stdout.trim());
}

function createReadServiceClient(options = {}) {
  const baseUrl = normalizeBaseUrl(options.baseUrl || process.env.ASTOCK_READ_SERVICE_URL);
  const fetchFn = options.fetchFn || globalThis.fetch;
  const timeoutMs = options.timeoutMs || DEFAULT_TIMEOUT_MS;
  const resolveStockCodeFn = options.resolveStockCodeFn || ((query) => resolveStockCodeWithPython(query, options.pythonResolver || {}));
  const enableAgentResolve = options.enableAgentResolve || process.env.ASTOCK_ENABLE_AGENT_RESOLVE === "1";
  if (typeof fetchFn !== "function") {
    throw new Error("fetch is required for LocalReadServiceClient");
  }

  async function requestJson(path, init = {}) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const effectiveTimeoutMs = init.timeoutMs || timeoutMs;
    const timer = controller
      ? setTimeout(() => controller.abort(), effectiveTimeoutMs)
      : null;
    const body = init.body === undefined ? undefined : JSON.stringify(init.body);
    const response = await fetchFn(`${baseUrl}${path}`, {
      method: init.method || "GET",
      headers: body ? { "content-type": "application/json", ...(init.headers || {}) } : init.headers,
      body,
      signal: controller?.signal,
    }).finally(() => {
      if (timer) clearTimeout(timer);
    });
    if (!response.ok) {
      const errorBody = typeof response.text === "function"
        ? await response.text().catch(() => "")
        : "";
      throw new Error(`local read service ${response.status}: ${errorBody || response.statusText}`);
    }
    return response.json();
  }

  async function resolveStockCode(query) {
    const direct = extractStockCode(query);
    if (direct) return direct;

    const viaPython = await resolveStockCodeFn(query).catch(() => null);
    if (viaPython) return viaPython;

    const viaAlias = resolveKnownStockAlias(query);
    if (viaAlias) return viaAlias;

    if (!enableAgentResolve) return null;

    const agentResult = await requestJson("/agent/ask", {
      method: "POST",
      timeoutMs: Math.max(timeoutMs, AGENT_RESOLVE_TIMEOUT_MS),
      body: {
        request: String(query || ""),
        context: { current_page: "desktop-stock-diagnosis" },
        messages: [],
      },
    });
    return extractStockCodeFromAgentResponse(agentResult);
  }

  return {
    baseUrl,
    extractStockCode,
    resolveStockCode,
    async getHealth() {
      try {
        const health = await requestJson("/health");
        return { available: true, ...health };
      } catch (error) {
        return { available: false, error: error.message };
      }
    },
    async getStockProfile(codeOrQuery) {
      const code = await resolveStockCode(codeOrQuery);
      if (!code) {
        throw new Error("stock code could not be resolved");
      }
      return requestJson(`/data/stocks/${code}`);
    },
  };
}

module.exports = {
  DEFAULT_BASE_URL,
  DEFAULT_TIMEOUT_MS,
  AGENT_RESOLVE_TIMEOUT_MS,
  PYTHON_RESOLVE_TIMEOUT_MS,
  extractStockCode,
  extractStockCodeFromAgentResponse,
  resolveKnownStockAlias,
  resolveStockCodeWithPython,
  createReadServiceClient,
};
