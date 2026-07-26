import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

import { Type } from "@mariozechner/pi-ai";
import { defineTool, type ExtensionAPI } from "@mariozechner/pi-coding-agent";

const execFileAsync = promisify(execFile);
const MAX_BUFFER = 1024 * 512;
const TIMEOUT_MS = 15000;

type Capability = {
  name: string;
  description: string;
  risk: string;
  arguments: string[];
};

function runtimeConfig() {
  const cliPath = process.env.ASTOCK_AGENT_CLI_PATH;
  const researchRoot = process.env.ASTOCK_RESEARCH_ROOT;
  if (!cliPath || !researchRoot) {
    throw new Error("AStock CLI environment is incomplete");
  }
  return {
    cliPath: path.resolve(cliPath),
    researchRoot: path.resolve(researchRoot),
    python: process.env.ASTOCK_PYTHON_COMMAND || "python3",
  };
}

async function runCli(args: string[]) {
  const config = runtimeConfig();
  const { stdout } = await execFileAsync(config.python, [config.cliPath, ...args], {
    cwd: config.researchRoot,
    timeout: TIMEOUT_MS,
    maxBuffer: MAX_BUFFER,
    env: process.env,
  });
  return JSON.parse(stdout);
}

async function loadCatalog(): Promise<Capability[]> {
  const payload = await runCli(["catalog"]);
  if (!Array.isArray(payload.capabilities)) {
    throw new Error("AStock CLI returned an invalid capability catalog");
  }
  // Product surface: only readonly capabilities are auto-exposed to Pi.
  return payload.capabilities.filter((item: Capability) => item?.risk === "readonly");
}

const CORE_CAPABILITY_DESCRIPTION = [
  "Call one capability from the live readonly AStock system CLI catalog.",
  "Common capabilities:",
  "- resolve_stock_code(query): resolve a stock name or user sentence to a six-digit code.",
  "- stock_profile(code): read dated price, return, valuation, money-flow, source, and warning fields.",
  "- strategy_idea_check(idea): deterministic strategy-idea precheck; returns boundaries, cost model, data quality, funnel, related families; never claims validity or fake equity curves.",
  "- data_quality / factors / strategies / experiments: system readonly facts.",
  "The extension loads the machine-readable catalog before execution and rejects names not registered as readonly.",
].join("\n");

export default function (pi: ExtensionAPI) {
  let catalogPromise: Promise<Capability[]> | null = null;
  const currentCatalog = () => {
    catalogPromise ||= loadCatalog();
    return catalogPromise;
  };

  pi.registerTool(defineTool({
    name: "astock_cli",
    label: "AStock CLI",
    description: CORE_CAPABILITY_DESCRIPTION,
    parameters: Type.Object({
      capability: Type.String({ description: "Exact capability name from the catalog" }),
      argumentsJson: Type.Optional(Type.String({
        description: "JSON object containing the exact named arguments required by the capability",
      })),
    }),

    async execute(_toolCallId, params) {
      const capabilities = await currentCatalog();
      const allowed = new Set(capabilities.map((item) => item.name));
      if (!allowed.has(params.capability)) {
        throw new Error(`Capability is not in the readonly catalog: ${params.capability}`);
      }
      const argumentsValue = JSON.parse(params.argumentsJson || "{}");
      if (!argumentsValue || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
        throw new Error("Capability arguments must decode to a JSON object");
      }
      // Desktop Pi stays on readonly Strict rail by default (mid needs host confirm token).
      const payload = await runCli([
        "call",
        "--tool",
        params.capability,
        "--args-json",
        JSON.stringify(argumentsValue),
        "--readonly-only",
      ]);
      return {
        content: [{ type: "text", text: JSON.stringify(payload) }],
        details: {
          capability: params.capability,
          payload: payload.result,
        },
      };
    },
  }));
}
