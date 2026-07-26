const STATUS_LABELS = {
  review: "待人工复核",
  blocked: "失败 / 阻塞",
  ready: "可执行",
  running: "运行中",
  completed: "已登记",
  archived: "已归档",
};

const ACTION_LABELS = {
  complete_draft: "补全研究草案",
  queue: "送入 L0 队列",
  run_l0: "运行 L0",
  run_l1: "运行 L1",
  run_l2: "运行 L2",
  run_l3: "运行 L3",
  review: "人工复核",
  promote: "正式晋级",
};

const SOURCE_LABELS = {
  manual: "人工",
  report: "研报",
  llm_paper: "研报 / LLM",
  autoresearch: "AutoResearch",
  agent: "Agent",
  mutation: "变异搜索",
  island: "岛屿搜索",
};

export function statusLabel(value) {
  return STATUS_LABELS[value] ?? value ?? "—";
}

export function actionLabel(value) {
  return ACTION_LABELS[value] ?? value ?? "—";
}

export function sourceLabel(value) {
  return SOURCE_LABELS[value] ?? value ?? "未知来源";
}

export function displayRegistryStatus(value) {
  return ["候选", "SHADOW", "shadow", "观察"].includes(value) ? "观察版本" : value;
}

export function artifactSection(name) {
  const normalized = String(name ?? "").toLowerCase();
  return /(shadow|decay|incubation|prediction|monitor)/.test(normalized)
    ? "monitoring"
    : "performance";
}

export function splitWorkId(value) {
  const index = String(value).indexOf(":");
  return index < 0 ? ["", String(value)] : [String(value).slice(0, index), String(value).slice(index + 1)];
}
