export interface AgentCitationLike {
  source_type?: string;
  title?: string;
  source_path?: string;
}

const SOURCE_LABELS: Record<string, string> = {
  system_manual: "系统手册",
  rules: "系统规则",
  runtime: "运行数据",
  research: "研究结论",
  ui_context: "页面上下文",
};

export function sourceTypeLabel(sourceType: string): string {
  return SOURCE_LABELS[sourceType] ?? sourceType;
}

export function citationLabel(citation: AgentCitationLike): string {
  return [
    sourceTypeLabel(citation.source_type ?? ""),
    citation.title,
    citation.source_path,
  ].filter(Boolean).join(" · ");
}
