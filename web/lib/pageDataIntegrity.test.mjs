import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const ROOT = process.cwd();

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), "utf8");
}

// 假数据守卫只针对会真正渲染/执行的代码;诚实性注释(如"不硬编码绿灯")不算脚手架。
// 只剥离块注释与整行 // 注释,避免误伤字符串里的 "https://..."。
function stripComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

test("current product routes are the only route contracts under test", () => {
  for (const relative of [
    "app/dashboard/page.tsx",
    "app/portfolio-risk/page.tsx",
    "app/signal-audit/page.tsx",
    "app/strategy-registry/page.tsx",
    "app/factor-research/page.tsx",
    "app/backtest-lab/page.tsx",
    "app/data-health/page.tsx",
    "app/system-governance/page.tsx",
  ]) {
    assert.equal(fs.existsSync(path.join(ROOT, relative)), true, `${relative} must exist`);
  }

  assert.equal(fs.existsSync(path.join(ROOT, "app/portfolio/page.tsx")), false);
  assert.equal(fs.existsSync(path.join(ROOT, "app/factors/page.tsx")), false);
});

test("frontend pages do not keep obvious fake-data scaffolds", () => {
  const pages = [
    "app/factor-research/page.tsx",
    "app/backtest-lab/page.tsx",
    "app/data-health/page.tsx",
    "app/system-governance/page.tsx",
    "app/dashboard/page.tsx",
    "app/signal-audit/page.tsx",
    "app/strategy-registry/page.tsx",
  ];

  for (const relative of pages) {
    // 剥离注释后再匹配:守卫的是真正会渲染的代码,诚实性注释里的"硬编码/fallback"字样不是脚手架。
    const source = stripComments(read(relative));
    assert.doesNotMatch(source, /Mock|mock keys|fallback|硬编码|all PASS|全部 PASS/);
    assert.doesNotMatch(source, /\|\|\s*['"]0\.012['"]|\|\|\s*1\.58|\|\|\s*1\.85/);
  }
});

test("ops pages do not ship static operational facts", () => {
  const dataHealth = read("app/data-health/page.tsx");
  assert.doesNotMatch(dataHealth, /Wind|萬得|同花順|中證指數公司|2026-06-2[34]|07:0[0-9]|0\.02%|0\.45%/);

  const dashboard = read("app/dashboard/page.tsx");
  assert.doesNotMatch(dashboard, /18\.5 bps|0\.082%|325|"-67"|"-42"|"-191"|LOW \(低\)|0\.45%|信息技术/);
});

test("shell and registry pages do not ship static environment metadata", () => {
  const sources = [
    read("components/shell/TopBar.tsx"),
    read("components/shell/Sidebar.tsx"),
    read("lib/appStore.ts"),
    read("app/signal-audit/page.tsx"),
    read("app/strategy-registry/page.tsx"),
  ].join("\n");

  assert.doesNotMatch(
    sources,
    /2026-06-2[345]|deploy_20260624|v2\.3\.0|researcher|Fallback static|模擬刷新|還有 12 個交易日|evidence_\{/
  );
});

test("factor research page does not ship static evidence charts", () => {
  const factorResearch = read("app/factor-research/page.tsx");
  assert.doesNotMatch(
    factorResearch,
    /324\.5%|12\.5 天|18\.5 bps|22\.40%|-8\.65%|42\.1%|Newey-West 校正已啟用/
  );
});

test("dashboard wires the trust-calibration banner from the backend (no fake)", () => {
  const dashboard = read("app/dashboard/page.tsx");
  // 首屏必须实际拉取并渲染信任校准视图,而非硬编码一个横幅。
  assert.match(dashboard, /api\.trustCalibration\(\)/);
  assert.match(dashboard, /<TrustCalibration\s+data=\{trust\}/);

  const component = read("components/governance/TrustCalibration.tsx");
  // banner_status 必须透传自后端,禁止在展示层重算/硬编码 status。
  assert.match(component, /status=\{data\.banner_status\}/);
  assert.doesNotMatch(component, /status="(ready|blocked|attention)"/);
});
