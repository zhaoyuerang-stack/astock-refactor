import assert from "node:assert/strict";
import test from "node:test";

async function importFreshDisplay() {
  return import(`./agentDisplay.ts?case=${Date.now()}-${Math.random()}`);
}

test("agent citation labels expose source type and path", async () => {
  const { sourceTypeLabel, citationLabel } = await importFreshDisplay();

  assert.equal(sourceTypeLabel("rules"), "系统规则");
  assert.equal(sourceTypeLabel("runtime"), "运行数据");
  assert.equal(sourceTypeLabel("unknown"), "unknown");
  assert.equal(
    citationLabel({ source_type: "rules", title: "操作宪法", source_path: "CLAUDE.md" }),
    "系统规则 · 操作宪法 · CLAUDE.md",
  );
});
