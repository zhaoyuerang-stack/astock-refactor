// 右侧常驻 Agent 的上下文总线(Phase 1:规则式解读;Phase 5 接入真 Agent)。
// 页面把当前上下文/结论 push 进来,AgentPanel 渲染。结构对齐 contracts.AgentOutput。
import { create } from "zustand";

export interface AgentContext {
  page: string;
  title: string;
  summary: string;
  evidence: string[];
  risk: string[];
  recommendation: string[];
  nextActions: string[];
  citations: {
    source_id: string;
    source_type: string;
    title: string;
    source_path: string;
    excerpt: string;
  }[];
  sourceTypes: string[];
  suggestedNavigation: string[];
}

const empty: AgentContext = {
  page: "",
  title: "研究副驾驶",
  summary: "在左侧选择模块开始研究。Agent 会随当前页面上下文给出解读与建议。",
  evidence: [],
  risk: [],
  recommendation: [],
  nextActions: [],
  citations: [],
  sourceTypes: [],
  suggestedNavigation: [],
};

interface AgentState {
  ctx: AgentContext;
  setContext: (ctx: Partial<AgentContext> & { page: string }) => void;
}

export const useAgent = create<AgentState>((set) => ({
  ctx: empty,
  setContext: (ctx) => set({ ctx: { ...empty, ...ctx } }),
}));
