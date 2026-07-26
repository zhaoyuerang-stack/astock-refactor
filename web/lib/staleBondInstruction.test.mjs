import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = process.cwd();

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), "utf8");
}

test("stale bond rotation is displayed as non-actionable", () => {
  const planCard = read("components/paper/PlanCard.tsx");
  assert.match(planCard, /非现行可执行/);
  assert.match(planCard, /plan\.stale/);
  assert.match(planCard, /冻结历史信号/);
  assert.match(planCard, /authorized === false/);
  assert.match(planCard, /未授权 defensive overlay/);
});

test("ops dashboard suppresses stale bond instructions from action rows", () => {
  const dashboard = read("app/dashboard/page.tsx");
  assert.match(dashboard, /hasCurrentExecutablePlan/);
  assert.match(dashboard, /!paperPlan\.stale/);
  assert.match(dashboard, /生产门禁已拦截/);
});

test("signal audit does not label stale signals as current suggested actions", () => {
  const signalAudit = read("app/signal-audit/page.tsx");
  assert.match(signalAudit, /hasCurrentExecutableSignal/);
  assert.match(signalAudit, /paperPlan\?\.stale/);
  assert.match(signalAudit, /非现行可执行/);
});
