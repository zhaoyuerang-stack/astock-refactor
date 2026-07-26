import test from "node:test";
import assert from "node:assert/strict";
import { createCapabilityService } from "../src/main/capabilityService.cjs";

test("mid-risk run_backtest without confirm returns needs_confirmation", async () => {
  const service = createCapabilityService({
    async runCli() {
      throw new Error("CLI must not be called before confirmation");
    },
  });
  const result = await service.runCapability({ tool: "run_backtest", args: {}, confirmed: false });
  assert.equal(result.needs_confirmation, true);
  assert.equal(result.ok, false);
  assert.equal(result.tool, "run_backtest");
  assert.equal(result.can_claim_valid, false);
  assert.match(result.confirm_prompt, /BacktestEngine|真实成本/);
});

test("mid-risk confirmed path injects confirm token into CLI", async () => {
  const calls = [];
  const service = createCapabilityService({
    async runCli(args, envExtra = {}) {
      calls.push({ args, envExtra });
      return {
        capability: "run_backtest",
        result: {
          annual: 0.12,
          sharpe: 0.8,
          maxdd: -0.15,
          evidence_envelope: {
            evidence_tier: "engine",
            can_claim_valid: false,
            sources: ["tool:run_backtest"],
            protocol_id: "engine_backtest",
          },
        },
      };
    },
  });
  const result = await service.runCapability({ tool: "run_backtest", args: {}, confirmed: true });
  assert.equal(result.ok, true);
  assert.equal(result.can_claim_valid, false);
  assert.equal(result.evidence_envelope.evidence_tier, "engine");
  assert.equal(calls.length, 1);
  assert.ok(calls[0].args.includes("--confirm-token"));
  assert.ok(calls[0].envExtra.ASTOCK_MID_CONFIRM_TOKEN);
  assert.equal(
    calls[0].args[calls[0].args.indexOf("--confirm-token") + 1],
    calls[0].envExtra.ASTOCK_MID_CONFIRM_TOKEN,
  );
  assert.ok(!calls[0].args.includes("--readonly-only"));
});

test("readonly capability uses --readonly-only and never needs confirm", async () => {
  const calls = [];
  const service = createCapabilityService({
    async runCli(args) {
      calls.push(args);
      return {
        capability: "data_gap_audit",
        result: { missing: [], evidence_envelope: { evidence_tier: "precheck", can_claim_valid: false } },
      };
    },
  });
  const result = await service.runCapability({
    tool: "data_gap_audit",
    args: { idea: "WACC" },
    confirmed: false,
  });
  assert.equal(result.ok, true);
  assert.equal(result.needs_confirmation, false);
  assert.ok(calls[0].includes("--readonly-only"));
});
