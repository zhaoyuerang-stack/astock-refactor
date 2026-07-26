import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { actionLabel, artifactSection, displayRegistryStatus, sourceLabel, statusLabel } from "./researchWorkspace.mjs";

const ROOT = process.cwd();

test("research workspace labels expose lifecycle semantics", () => {
  assert.equal(statusLabel("review"), "待人工复核");
  assert.equal(actionLabel("run_l2"), "运行 L2");
  assert.equal(sourceLabel("llm_paper"), "研报 / LLM");
});

test("registry candidate and shadow statuses display as observation versions", () => {
  for (const raw of ["候选", "SHADOW", "shadow", "观察"]) {
    assert.equal(displayRegistryStatus(raw), "观察版本");
  }
  assert.equal(displayRegistryStatus("在册"), "在册");
});

test("specialized artifacts are routed to their owning factor detail section", () => {
  assert.equal(artifactSection("amount_timing_validation"), "performance");
  assert.equal(artifactSection("shadow_ontology_performance"), "monitoring");
  assert.equal(artifactSection("shadow_incubation_log"), "monitoring");
  assert.equal(artifactSection("ontology_predictions"), "monitoring");
});

test("experiments home is a work queue without legacy dual funnels or registered leaderboard", () => {
  const source = fs.readFileSync(path.join(ROOT, "app/experiments/page.tsx"), "utf8");
  assert.match(source, /api\.researchWorkItems/);
  assert.doesNotMatch(source, /AutoResearchLab/);
  assert.doesNotMatch(source, /registeredExperiments/);
  assert.doesNotMatch(source, /假设池漏斗/);
});

test("research lifecycle subpages and detail routes exist", () => {
  for (const relative of [
    "app/experiments/evidence/page.tsx",
    "app/experiments/runs/page.tsx",
    "app/experiments/reviews/page.tsx",
    "app/experiments/[kind]/[id]/page.tsx",
    "app/factor-research/page.tsx",
    "app/strategy-registry/page.tsx",
  ]) {
    assert.equal(fs.existsSync(path.join(ROOT, relative)), true, `${relative} must exist`);
  }
});

test("factor research page does not load legacy factor noise pool", () => {
  const source = fs.readFileSync(path.join(ROOT, "app/factor-research/page.tsx"), "utf8");
  assert.doesNotMatch(source, /noisePool/);
  assert.match(source, /api\.factors/);
});
