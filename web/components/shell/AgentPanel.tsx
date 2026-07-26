"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useAgent } from "@/lib/agentStore";
import { useWorkspaceStore } from "@/lib/workspaceStore";
import { useLayoutStore } from "@/lib/layoutStore";
import { api } from "@/lib/api";
import { citationLabel, sourceTypeLabel } from "@/lib/agentDisplay";
import type { AgentMessage } from "@/lib/types";

const SESSION_KEY = "quant_agent_session_id";

function Section({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items?.length) return null;
  return (
    <div className="mt-3">
      <div className="text-[11px] font-medium text-subink uppercase tracking-wide">{title}</div>
      <ul className="mt-1 space-y-1">
        {items.map((t, i) => (
          <li key={i} className={`text-[13px] leading-snug ${tone ?? "text-ink"}`}>• {t}</li>
        ))}
      </ul>
    </div>
  );
}

export default function AgentPanel() {
  const ctx = useAgent((s) => s.ctx);
  const setContext = useAgent((s) => s.setContext);
  const pathname = usePathname();
  const { mode } = useWorkspaceStore();
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [llmReady, setLlmReady] = useState(false);
  const [confirmTip, setConfirmTip] = useState<string | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);

  async function startSession(page: string) {
    const s = await api.createAgentSession({ page_context: page, title: "AI 会话", user_id: "local" });
    localStorage.setItem(SESSION_KEY, s.session_id);
    setSessionId(s.session_id);
    setMessages([]);
    return s.session_id;
  }

  useEffect(() => {
    const page = pathname.replace(/^\//, "") || "overview";
    const saved = localStorage.getItem(SESSION_KEY);
    if (!saved) {
      startSession(page).catch(() => undefined);
      return;
    }
    api.getAgentSession(saved)
      .then((s) => {
        setSessionId(s.session_id);
        setMessages(
          s.messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }))
            .slice(-10),
        );
      })
      .catch(() => {
        localStorage.removeItem(SESSION_KEY);
        startSession(page).catch(() => undefined);
      });
  }, [pathname]);

  async function ask() {
    const request = q.trim();
    if (!request || loading) return;
    setLoading(true);
    setConfirmTip(null);
    try {
      const page = pathname.replace(/^\//, "") || "overview";
      const userMessage: AgentMessage = { role: "user", content: request };
      setMessages((prev) => [...prev, userMessage]);
      const sid = sessionId ?? await startSession(page);
      const r = await api.agentSessionAsk(sid, request, { current_page: page });
      setLlmReady(r.llm_ready);
      const o = r.output;
      const assistantMessage = o.summary || "已处理。";
      const replyMessage: AgentMessage = { role: "assistant", content: assistantMessage };
      setMessages(
        r.session.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }))
          .slice(-10),
      );
      setContext({
        page,
        title: mode === "ops" ? "交易审计助航" : "学术探索研究",
        summary: o.summary,
        evidence: o.evidence,
        risk: o.risk,
        recommendation: o.recommendation,
        nextActions: o.next_actions,
        citations: o.citations ?? [],
        sourceTypes: o.source_types ?? [],
        suggestedNavigation: o.suggested_navigation ?? [],
      });
      if (o.requires_human_confirmation) {
        setConfirmTip(`「${r.tool}」为${r.risk}风险动作,Agent 不自动执行,需你在对应页面手动确认。`);
      }
      setQ("");
    } catch (e) {
      setContext({ page: "", title: mode === "ops" ? "交易审计助航" : "学术探索研究", summary: `调用失败:${String(e)}(确认后端 :8011)` });
      const errorMessage: AgentMessage = { role: "assistant", content: `调用失败:${String(e)}` };
      setMessages((prev) => [...prev, errorMessage].slice(-10));
    } finally {
      setLoading(false);
    }
  }

  // Dual-mode UI copy definitions
  const isOps = mode === "ops";
  const agentTitle = isOps ? "AI 交易审计员" : "AI 量化科学家";
  const placeholderText = isOps
    ? "问:交易就绪度 / 信号审计 / 风控偏离…"
    : "问:因子IC衰退 / 回测参数 / Pareto前沿…";
  const disclaimerText = "AI 僅供研究與審計參考，不替代有效性裁決，不構成交易建議。";

  const width = useLayoutStore((s) => s.agentWidth);
  const collapsed = useLayoutStore((s) => s.agentCollapsed);
  const toggle = useLayoutStore((s) => s.toggleAgent);

  // 折叠态:仅保留浮动复原按钮
  if (collapsed) {
    return (
      <button
        onClick={toggle}
        aria-label="展开 AI 助手栏"
        title="展开 AI 助手栏"
        className="fixed right-2 top-3 z-50 w-8 h-8 flex items-center justify-center rounded-md bg-jilan border border-line/40 text-subink hover:text-ink hover:border-brand/40 transition-colors"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 10h8M8 14h5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </button>
    );
  }

  return (
    <aside
      style={{ width }}
      className="shrink-0 border-l border-line bg-[#161617] h-screen sticky top-0 flex flex-col text-ink shadow-sm"
    >
      <div className="px-4 py-4 border-b border-line/30">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 min-w-0">
            <button
              onClick={toggle}
              aria-label="折叠 AI 助手栏"
              title="折叠导航栏"
              className="shrink-0 w-6 h-6 flex items-center justify-center rounded-md text-subink/70 hover:text-ink hover:bg-line/45 transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5l7 7-7 7" />
              </svg>
            </button>
            <div className="text-sm font-bold text-ink font-quant truncate">{agentTitle}</div>
          </div>
          {isOps ? (
            <span className="text-[9px] px-1.5 py-0.5 rounded border border-songshi/30 text-songshi font-quant bg-songshi/5">
              风控审计
            </span>
          ) : (
            <span className="text-[9px] px-1.5 py-0.5 rounded border border-brand/30 text-brand font-quant bg-brand/5">
              学术探索
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <div className="text-[11px] text-subink truncate">{ctx.title}</div>
          <button
            onClick={() => startSession(pathname.replace(/^\//, "") || "overview").catch(() => undefined)}
            className="text-[10px] px-1.5 py-0.5 rounded border border-line/50 text-brand hover:bg-brand/10"
            title={sessionId ? `当前会话 ${sessionId}` : "新建会话"}
          >
            新会话
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {messages.length > 0 && (
          <div className="space-y-2">
            {messages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={`text-[12px] leading-relaxed rounded-lg px-3 py-2 whitespace-pre-wrap ${
                  m.role === "user"
                    ? "ml-5 bg-brand/10 text-ink border border-brand/20"
                    : "mr-5 bg-jilan/25 text-ink border border-line/25"
                }`}
              >
                <div className="text-[9px] uppercase tracking-wide text-subink mb-1">
                  {m.role === "user" ? "你" : "AI"}
                </div>
                {m.content}
              </div>
            ))}
          </div>
        )}
        <div className="text-[13px] text-ink leading-relaxed whitespace-pre-wrap">{loading ? "思考中…" : ctx.summary}</div>
        <div className="space-y-3">
          <Section title="证据" items={ctx.evidence} tone="text-subink" />
          <Section title="风险" items={ctx.risk} tone="text-danger" />
          <Section title="建议" items={ctx.recommendation} />
          <Section title="下一步" items={ctx.nextActions} tone="text-brand" />
        </div>
        {ctx.sourceTypes.length > 0 && (
          <div className="mt-3">
            <div className="text-[11px] font-medium text-subink uppercase tracking-wide">来源层</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {ctx.sourceTypes.map((t) => (
                <span key={t} className="text-[10px] px-1.5 py-0.5 rounded border border-line text-brand bg-brand/5">
                  {sourceTypeLabel(t)}
                </span>
              ))}
            </div>
          </div>
        )}
        {ctx.citations.length > 0 && (
          <div className="mt-3">
            <div className="text-[11px] font-medium text-subink uppercase tracking-wide">引用</div>
            <ul className="mt-1 space-y-2">
              {ctx.citations.slice(0, 4).map((c) => (
                <li key={c.source_id} className="text-[11px] leading-snug text-subink border-l border-line pl-2">
                  <div className="text-brand">{citationLabel(c)}</div>
                  <div className="mt-0.5 line-clamp-2">{c.excerpt}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
        {ctx.suggestedNavigation.length > 0 && (
          <div className="mt-3">
            <div className="text-[11px] font-medium text-subink uppercase tracking-wide">导航建议</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {ctx.suggestedNavigation.map((path) => (
                <a key={path} href={path} className="text-[11px] px-2 py-1 rounded border border-brand/25 text-brand hover:bg-brand/10">
                  {path}
                </a>
              ))}
            </div>
          </div>
        )}
        {confirmTip && <div className="mt-3 text-[12px] text-warn border border-warn/20 bg-warn/10 rounded p-2">{confirmTip}</div>}
      </div>
      <div className="px-4 py-3 border-t border-line/25 bg-jilan/10">
        <div className="text-[9px] text-subink mb-1.5 opacity-80 leading-snug">
          {disclaimerText}
        </div>
        <div className="flex gap-1.5">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder={placeholderText}
            className="flex-1 text-[12px] bg-white border border-line/50 rounded-lg px-3 py-1.5 outline-none focus:border-brand text-ink placeholder-subink/45"
          />
          <button onClick={ask} disabled={loading} className="text-sm bg-brand text-white font-medium rounded-lg px-3 disabled:opacity-50 hover:bg-brand/90 transition-colors">
            问
          </button>
        </div>
      </div>
    </aside>
  );
}
