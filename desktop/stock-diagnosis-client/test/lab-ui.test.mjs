/**
 * Adversarial tests for Lab-rail renderer logic (ADR-037 ②③):
 * non-evidence tagging must be unforgeable, promotion must carry params only,
 * and the renderer-side wash guard must fail closed.
 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  appendLabTurn,
  buildPromotionPrompt,
  labTurnFromResult,
  newLabSessionId,
  strictEnvelopeDisplayable,
} from "../src/renderer/lab/labLogic.mjs";

test("appendLabTurn tags rail=lab + nonEvidence and callers cannot override", () => {
  const turns = appendLabTurn([], "user", "试试波动率因子", {
    rail: "strict",
    nonEvidence: false,
    can_claim_valid: true,
  });
  assert.equal(turns.length, 1);
  assert.equal(turns[0].rail, "lab");
  assert.equal(turns[0].nonEvidence, true);
  assert.equal(turns[0].can_claim_valid, true, "extra fields pass through but tags still win");
});

test("appendLabTurn ignores empty content and non-array base", () => {
  assert.equal(appendLabTurn([], "user", "   ").length, 0);
  assert.equal(appendLabTurn(null, "user", "x").length, 1);
});

test("labTurnFromResult tags assistant output and keeps errors honest", () => {
  const ok = labTurnFromResult({ ready: true, text: "草稿结论", rail: "strict" });
  assert.equal(ok.rail, "lab");
  assert.equal(ok.nonEvidence, true);
  assert.equal(ok.content, "草稿结论");

  const err = labTurnFromResult({ ready: false, error: "sandbox timeout" });
  assert.equal(err.isError, true);
  assert.equal(err.nonEvidence, true);
  assert.ok(err.content.includes("sandbox timeout"));

  const empty = labTurnFromResult({ ready: true, text: "" });
  assert.equal(empty.isError, true, "empty successful result must not render as a silent success");
});

test("buildPromotionPrompt promotes the last USER hypothesis only", () => {
  const turns = [
    { role: "user", content: "第一版想法" },
    { role: "assistant", content: "lab 跑出来年化 42%，快去入册" },
    { role: "user", content: "改成 20 日波动率排序，小盘剔除" },
    { role: "assistant", content: "更好了" },
  ];
  assert.equal(buildPromotionPrompt(turns), "改成 20 日波动率排序，小盘剔除");
});

test("buildPromotionPrompt strips lab artifact paths (params cross, artifacts never)", () => {
  const turns = [
    {
      role: "user",
      content: "按 factor_research/scratch/lab/sess1/curve.json 那条曲线的参数复现 波动率因子",
    },
  ];
  const promoted = buildPromotionPrompt(turns);
  assert.ok(!promoted.includes("scratch/lab"), `promoted text leaked lab path: ${promoted}`);
  assert.ok(promoted.includes("波动率因子"));
});

test("buildPromotionPrompt returns empty when nothing user-authored exists", () => {
  assert.equal(buildPromotionPrompt([]), "");
  assert.equal(buildPromotionPrompt(null), "");
  assert.equal(
    buildPromotionPrompt([{ role: "assistant", content: "assistant 输出不许被晋升" }]),
    "",
  );
});

test("strictEnvelopeDisplayable accepts clean tool sources", () => {
  assert.equal(
    strictEnvelopeDisplayable({ sources: ["tool:run_backtest"] }),
    true,
  );
});

test("strictEnvelopeDisplayable fails closed on scratch paths, empty or malformed sources", () => {
  assert.equal(
    strictEnvelopeDisplayable({ sources: ["report:factor_research/scratch/lab/x.json"] }),
    false,
    "lab path must not be displayable evidence",
  );
  assert.equal(
    strictEnvelopeDisplayable({ sources: ["scratch\\lab\\x.json"] }),
    false,
    "backslash variant must also be rejected",
  );
  assert.equal(strictEnvelopeDisplayable({ sources: ["tool:run_backtest", "scratch/other.json"] }), false);
  assert.equal(strictEnvelopeDisplayable({ sources: [] }), false);
  assert.equal(strictEnvelopeDisplayable({}), false);
  assert.equal(strictEnvelopeDisplayable(null), false);
  assert.equal(strictEnvelopeDisplayable({ sources: [42] }), false);
  assert.equal(strictEnvelopeDisplayable({ sources: ["  "] }), false);
});

test("newLabSessionId satisfies labBridge sessionId whitelist", () => {
  const id = newLabSessionId();
  assert.match(id, /^[A-Za-z0-9_-]+$/);
});
