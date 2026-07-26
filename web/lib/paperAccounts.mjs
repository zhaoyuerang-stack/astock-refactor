// paper 多账户并排展示的纯逻辑(T5,PLAN_paper_multiaccount_loop.md)。
// 顺序=后端产物顺序(R-PROD-001,前端不得重排名)——这里只做「保序透传」+
// 「三态判别」,不含任何排序/排名逻辑,避免把排名判断悄悄挪回前端。

// 顶层展示态判别:区分「有账户」/「名单健康但空」/「源不可读」三态,
// 供页面选择渲染分支,不在这里编排具体 UI。
export function accountsDisplayState(view) {
  if (!view) return "loading";
  if (!view.healthy) return "error";
  if (!view.accounts || view.accounts.length === 0) return "empty";
  return "ok";
}

// 保序透传:直接返回后端顺序,不做任何 sort/reverse——存在这个函数本身就是
// 立一个可测的契约点:任何未来改动一旦引入排序,下面的测试会先红。
export function orderedAccounts(view) {
  if (!view || !view.accounts) return [];
  return view.accounts;
}

// 账户状态徽章文案 + 语气(供 StatusBanner/badge 复用色板语义,不新造一套配色)。
const STATUS_BADGE = {
  active: { label: "实测中", tone: "ok" },
  frozen: { label: "已冻结(历史保留)", tone: "neutral" },
  blocked: { label: "无可执行规格", tone: "danger" },
  degraded: { label: "数据降级", tone: "warn" },
  unknown: { label: "台账缺失", tone: "danger" },
};

export function accountStatusBadge(status) {
  return STATUS_BADGE[status] || { label: status || "未知", tone: "neutral" };
}

// 综合裁决(StatusBanner):按「实测覆盖是否健康」给一句话结论,不算任何数值。
export function overallVerdict(view) {
  const state = accountsDisplayState(view);
  if (state === "loading") {
    return { status: "neutral", title: "多账户实测加载中…" };
  }
  if (state === "error") {
    return {
      status: "blocked",
      title: "多账户实测名单不可读",
      detail: view.error || "候选名单来源不可信,已 fail-closed 拒绝展示",
    };
  }
  if (state === "empty") {
    return {
      status: "neutral",
      title: "当前无可实测策略",
      detail: "组合再构成提案为空或未选出候选,不代表故障",
    };
  }
  const accounts = orderedAccounts(view);
  const blockedCount = accounts.filter((a) => a.status === "blocked" || a.status === "unknown").length;
  const activeCount = accounts.filter((a) => a.status === "active").length;
  if (activeCount === 0) {
    return {
      status: "attention",
      title: `${accounts.length} 个候选无一实测中`,
      detail: `${blockedCount} 个无可执行规格或台账缺失`,
    };
  }
  return {
    status: blockedCount > 0 ? "attention" : "ready",
    title: `${activeCount}/${accounts.length} 个候选实测中`,
    detail: blockedCount > 0 ? `另有 ${blockedCount} 个无可执行规格或台账缺失` : undefined,
  };
}
