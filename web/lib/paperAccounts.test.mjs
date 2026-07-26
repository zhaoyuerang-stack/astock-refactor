import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {
  accountsDisplayState,
  accountStatusBadge,
  orderedAccounts,
  overallVerdict,
} from "./paperAccounts.mjs";

const ROOT = process.cwd();

function read(relative) {
  return fs.readFileSync(path.join(ROOT, relative), "utf8");
}

const ACCOUNT_A = {
  name: "zzz-fam.v1.0", family: "zzz-fam", version: "v1.0", status: "active",
  reason: "", opened_at: "", frozen_at: "", last_update_date: "2026-07-09",
  nav_points: [{ date: "2026-07-09", nav: 1010000, total_return: 0.01 }],
  latest_nav: 1010000, total_return: 0.01, max_drawdown: -0.02,
  backtest_deviation: { available: true, window_start: "2026-01-01", window_end: "2026-07-09",
    paper_cumulative_return: 0.01, backtest_cumulative_return: 0.012,
    cumulative_deviation: -0.002, tracking_error: 0.03, common_days: 120 },
};
const ACCOUNT_B = {
  name: "aaa-fam.v1.0", family: "aaa-fam", version: "v1.0", status: "blocked",
  reason: "no_executable_spec: registry 版本无 executable_spec.spec 字段",
  opened_at: "", frozen_at: "", last_update_date: "",
  nav_points: [], latest_nav: 0, total_return: 0, max_drawdown: 0,
  backtest_deviation: { available: false, reason: "无 NAV 记录(账户尚未产生任何估值)" },
};

// ─────────────────────────── 顺序:保序透传,禁止前端重排名 ───────────────────────────

test("orderedAccounts passes through backend order verbatim (no client-side re-ranking)", () => {
  const view = { healthy: true, error: "", generated_at: "2026-07-10T00:00:00+08:00",
    accounts: [ACCOUNT_A, ACCOUNT_B] };
  const result = orderedAccounts(view);
  assert.deepEqual(result.map((a) => a.name), ["zzz-fam.v1.0", "aaa-fam.v1.0"],
    "顺序必须与后端 accounts 数组顺序逐一相同(zzz 在前),不得按字母/名称重排");
});

test("a client-side re-sort implementation would be caught by this order assertion", () => {
  // 反证:若有人在 orderedAccounts 里偷偷加一行 .sort((a,b)=>a.name.localeCompare(b.name)),
  // 这条断言必须失败——用同样的输入手算"错误实现"的输出,证明测试有区分力。
  const view = { healthy: true, error: "", generated_at: "", accounts: [ACCOUNT_A, ACCOUNT_B] };
  const wrongImplementationOutput = [...view.accounts].sort((a, b) => a.name.localeCompare(b.name));
  const correctOutput = orderedAccounts(view);
  assert.notDeepEqual(
    correctOutput.map((a) => a.name),
    wrongImplementationOutput.map((a) => a.name),
    "若 orderedAccounts 退化成按名称排序,会与保序透传的正确输出相同,本用例应能分辨两者不同"
  );
});

test("orderedAccounts returns empty array for missing/null view without throwing", () => {
  assert.deepEqual(orderedAccounts(null), []);
  assert.deepEqual(orderedAccounts({ healthy: true, accounts: null }), []);
});

// ─────────────────────────── 三态判别 ───────────────────────────

test("accountsDisplayState distinguishes loading / error / empty / ok", () => {
  assert.equal(accountsDisplayState(null), "loading");
  assert.equal(accountsDisplayState({ healthy: false, error: "过期", accounts: [] }), "error");
  assert.equal(accountsDisplayState({ healthy: true, error: "", accounts: [] }), "empty");
  assert.equal(accountsDisplayState({ healthy: true, error: "", accounts: [ACCOUNT_A] }), "ok");
});

test("healthy=false with a non-empty error is never collapsed into empty state", () => {
  // 防止"源不可读"被误判为"名单健康但空"这类静默假绿。
  const view = { healthy: false, error: "recompose 产物已过期", accounts: [] };
  assert.notEqual(accountsDisplayState(view), "empty");
  assert.equal(accountsDisplayState(view), "error");
});

// ─────────────────────────── 状态徽章 ───────────────────────────

test("accountStatusBadge maps every known status to a distinct label/tone", () => {
  assert.deepEqual(accountStatusBadge("active"), { label: "实测中", tone: "ok" });
  assert.deepEqual(accountStatusBadge("frozen"), { label: "已冻结(历史保留)", tone: "neutral" });
  assert.deepEqual(accountStatusBadge("blocked"), { label: "无可执行规格", tone: "danger" });
  assert.deepEqual(accountStatusBadge("degraded"), { label: "数据降级", tone: "warn" });
  assert.deepEqual(accountStatusBadge("unknown"), { label: "台账缺失", tone: "danger" });
});

test("accountStatusBadge falls back honestly for unrecognized status strings", () => {
  const badge = accountStatusBadge("something_new");
  assert.equal(badge.label, "something_new");
  assert.equal(badge.tone, "neutral");
});

// ─────────────────────────── 综合裁决(StatusBanner) ───────────────────────────

test("overallVerdict reports blocked tone with the backend error when unhealthy", () => {
  const verdict = overallVerdict({ healthy: false, error: "recompose 产物已过期(20 天前)", accounts: [] });
  assert.equal(verdict.status, "blocked");
  assert.match(verdict.detail, /过期/);
});

test("overallVerdict reports neutral (not blocked) for a healthy-but-empty candidate list", () => {
  const verdict = overallVerdict({ healthy: true, error: "", accounts: [] });
  assert.equal(verdict.status, "neutral");
  assert.doesNotMatch(verdict.title, /不可读|失败|錯誤|error/i);
});

test("overallVerdict flags attention when some accounts are blocked/unknown", () => {
  const verdict = overallVerdict({ healthy: true, error: "", accounts: [ACCOUNT_A, ACCOUNT_B] });
  assert.equal(verdict.status, "attention");
  assert.match(verdict.title, /1\/2/);
});

test("overallVerdict flags attention (not ready) when zero accounts are active", () => {
  const allBlocked = { healthy: true, error: "", accounts: [ACCOUNT_B] };
  const verdict = overallVerdict(allBlocked);
  assert.equal(verdict.status, "attention");
  assert.notEqual(verdict.status, "ready");
});

test("overallVerdict is ready (no attention) when every account is active", () => {
  const allActive = { healthy: true, error: "",
    accounts: [{ ...ACCOUNT_A }, { ...ACCOUNT_A, name: "other.v1.0", family: "other" }] };
  const verdict = overallVerdict(allActive);
  assert.equal(verdict.status, "ready");
});

// ─────────────────────────── 源码扫描:页面必须真接线,不得假绿/硬编码 ───────────────────────────

test("dashboard wires the real /paper-accounts endpoint for the multi-account panel", () => {
  const dashboard = read("app/dashboard/page.tsx");
  assert.match(dashboard, /api\.paperAccounts\(\)/);
});

test("PaperAccountsPanel renders honest empty/error states, not fake cards", () => {
  const panel = read("components/paper/PaperAccountsPanel.tsx");
  assert.match(panel, /accountsDisplayState/);
  assert.match(panel, /orderedAccounts/);
  // 空/错误态必须有对应的诚实文案分支,不是无条件渲染卡片列表。
  assert.match(panel, /当前无可实测策略|无可实测策略/);
});

test("PaperAccountsPanel does not re-sort accounts client-side", () => {
  const panel = read("components/paper/PaperAccountsPanel.tsx");
  assert.doesNotMatch(panel, /\.sort\(/, "多账户面板不得在客户端重新排序账户(R-PROD-001)");
});
