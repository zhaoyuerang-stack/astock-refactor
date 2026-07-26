"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import MetricCard from "@/components/ui/MetricCard";
import StatusBanner from "@/components/ui/StatusBanner";
import ResearchNav from "@/components/research/ResearchNav";
import WorkItemBadge from "@/components/research/WorkItemBadge";
import { api } from "@/lib/api";
import { actionLabel, sourceLabel } from "@/lib/researchWorkspace.mjs";
import type { ResearchWorkItemListView, ResearchWorkItemView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";

const JOB_TIMEOUT_MS = 15 * 60 * 1000;

export default function ExperimentsPage() {
  const [data, setData] = useState<ResearchWorkItemListView | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [showArchive, setShowArchive] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const setContext = useAgent((s) => s.setContext);

  const load = useCallback(async () => {
    try {
      const next = await api.researchWorkItems(
        showArchive
          ? { limit: 2000 }
          : { status: "review,blocked,ready,running", limit: 200 },
      );
      setData(next);
      setSelectedId((current) => current || next.items.find((item) => !["completed", "archived"].includes(item.status))?.work_id || next.items[0]?.work_id || "");
      setError("");
      setContext({
        page: "experiments",
        title: "研究工作队列",
        summary: `待复核 ${next.counts.review ?? 0}，阻塞 ${next.counts.blocked ?? 0}，可执行 ${next.counts.ready ?? 0}，运行中 ${next.counts.running ?? 0}。`,
        evidence: next.items.slice(0, 5).map((item) => `${item.title}: ${actionLabel(item.next_action)}`),
        recommendation: ["先处理人工复核与阻塞项", "所有正式晋级必须留下批准记录"],
        nextActions: ["处理队列中的唯一下一步"],
      });
    } catch (e) {
      setError(String(e));
    }
  }, [setContext, showArchive]);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(
    () => (data?.items ?? []).filter((item) => showArchive || !["completed", "archived"].includes(item.status)),
    [data, showArchive],
  );
  const selected = (data?.items ?? []).find((item) => item.work_id === selectedId) ?? visible[0] ?? null;

  async function execute(item: ResearchWorkItemView) {
    if (!item.next_action || ["review", "complete_draft"].includes(item.next_action)) return;
    setBusy(item.work_id);
    setError("");
    try {
      const job = await api.runResearchAction(item.kind, item.item_id, item.next_action, {});
      await api.waitForExperimentJob(job.job_id, { timeoutMs: JOB_TIMEOUT_MS });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function launch(mode: "seed" | "llm" | "island") {
    setBusy(`create:${mode}`);
    setError("");
    try {
      const job = mode === "seed"
        ? await api.runAutoresearchSeeds({ limit: 5, max_stage: "l1" })
        : mode === "llm"
          ? await api.runAutoresearchLLM({ n: 5, max_stage: "l1" })
          : await api.runIslandSearch({ islands: 4, generations: 3, population: 8 });
      await api.waitForExperimentJob(job.job_id, { timeoutMs: JOB_TIMEOUT_MS });
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  const counts = data?.counts;
  const review = counts?.review ?? 0;
  const blocked = counts?.blocked ?? 0;
  const ready = counts?.ready ?? 0;
  const running = counts?.running ?? 0;
  const queueStatus = review > 0 || blocked > 0 ? "attention" : ready > 0 || running > 0 ? "ready" : "neutral";
  const queueTitle =
    review > 0
      ? `研究队列:${review} 项待人工复核(最高优先)`
      : blocked > 0
      ? `研究队列:${blocked} 项阻塞待排查失败原因`
      : ready > 0
      ? `研究队列:${ready} 项可执行`
      : running > 0
      ? `研究队列:${running} 项运行中`
      : "研究队列:空闲,无待办";

  return (
    <div className="space-y-6">
      <PageHeader
        title="研究实验室"
        desc="量化因子孕育工作台：从学术证据出发，经过 L0（预测力）、L1（交易成本）、L2/L3 等深度风险验证，最后完成人工审批与正式晋级。"
      />
      <ResearchNav />

      {error && (
        <div className="p-4 rounded-xl bg-danger/10 border border-danger/20 text-sm text-danger flex flex-col gap-1 font-medium">
          <span className="font-bold text-[15px]">⚠️ 执行错误或阻塞拦截:</span>
          <span>{error}</span>
        </div>
      )}

      {data && (
        <StatusBanner
          status={queueStatus}
          title={queueTitle}
          detail="审计处理顺序: 待人工复核 → 解决阻塞/失败项 → 运行就绪队列。正式策略晋级在册必须含有批准记录。"
        />
      )}

      {/* Metrics Dashboard */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard label="待人工复核" value={String(review)} tone="warn" sub="需要明确批准或拒绝" />
        <MetricCard label="失败 / 阻塞" value={String(blocked)} tone="danger" sub="优先处理验证失败原因" />
        <MetricCard label="就绪可执行" value={String(ready)} tone="ok" sub="含有唯一下一步" />
        <MetricCard label="运行中任务" value={String(running)} sub="正在进行异步回测审计" />
      </div>

      {/* Actions Bar */}
      <div className="flex items-center justify-between gap-3">
        <label className="text-[12px] text-subink flex items-center gap-2 cursor-pointer font-medium hover:text-ink">
          <input
            type="checkbox"
            checked={showArchive}
            onChange={(e) => setShowArchive(e.target.checked)}
            className="rounded border-line text-brand focus:ring-brand"
          />
          显示已登记 / 已归档的研究历史
        </label>
        <details className="relative">
          <summary className="list-none cursor-pointer px-4 py-2 rounded-lg bg-brand hover:bg-brand-dark text-white text-[12px] font-bold shadow transition-colors">
            {busy.startsWith("create:") ? "任务启动中..." : "启动自主探索任务"}
          </summary>
          <div className="absolute right-0 mt-2 z-20 w-56 bg-white border border-line rounded-xl shadow-lg p-2 space-y-1">
            <Link href="/experiments/evidence" className="block px-3 py-2 rounded hover:bg-jilan text-[12px] font-medium text-ink">
              📖 从研报证据创建草案
            </Link>
            <button
              onClick={() => launch("seed")}
              className="w-full text-left px-3 py-2 rounded hover:bg-jilan text-[12px] font-medium text-ink disabled:opacity-50"
              disabled={!!busy}
            >
              🌱 运行确定性种子演化
            </button>
            <button
              onClick={() => launch("llm")}
              className="w-full text-left px-3 py-2 rounded hover:bg-jilan text-[12px] font-medium text-ink disabled:opacity-50"
              disabled={!!busy}
            >
              🤖 LLM 探索新因子候选
            </button>
            <button
              onClick={() => launch("island")}
              className="w-full text-left px-3 py-2 rounded hover:bg-jilan text-[12px] font-medium text-ink disabled:opacity-50"
              disabled={!!busy}
            >
              🏝️ 启动多数据岛屿搜索
            </button>
          </div>
        </details>
      </div>

      {/* Workspace Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Table Queue (Left) */}
        <div className="lg:col-span-3 card overflow-hidden p-0 border border-line rounded-xl shadow-sm bg-white">
          <div className="px-4 py-3 border-b border-line text-sm font-bold text-ink">工作队列</div>
          <div className="overflow-x-auto max-h-[620px]">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-neutral-50 text-subink text-left border-b border-line">
                <tr>
                  <th className="px-4 py-2.5">研究对象</th>
                  <th className="px-4 py-2.5">来源</th>
                  <th className="px-4 py-2.5">阶段</th>
                  <th className="px-4 py-2.5">下一步行动</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/40">
                {visible.map((item) => {
                  const isSelected = selected?.work_id === item.work_id;
                  const isItemRunning = item.status === "running" || busy === item.work_id;
                  return (
                    <tr
                      key={item.work_id}
                      onClick={() => setSelectedId(item.work_id)}
                      className={`cursor-pointer transition-colors ${
                        isSelected ? "bg-brand/5 font-medium" : "hover:bg-neutral-50"
                      }`}
                    >
                      <td className="px-4 py-3.5">
                        <div className="font-bold text-ink flex items-center gap-2">
                          {item.title}
                          {isItemRunning && (
                            <span className="flex h-2.5 w-2.5 relative">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-75"></span>
                              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-brand"></span>
                            </span>
                          )}
                        </div>
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <WorkItemBadge status={item.status} />
                          {item.status === "running" && <span className="text-[10px] text-brand font-medium">执行中...</span>}
                        </div>
                      </td>
                      <td className="px-4 py-3.5 text-subink font-medium">{sourceLabel(item.source)}</td>
                      <td className="px-4 py-3.5 font-mono text-subink uppercase">{item.stage || "DRAFT"}</td>
                      <td className="px-4 py-3.5 text-brand font-bold">{actionLabel(item.next_action) || "等待中"}</td>
                    </tr>
                  );
                })}
                {visible.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-12 text-center text-subink font-medium">
                      当前队列无待办事项。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Detail Panel (Right Sidebar) */}
        <div className="lg:col-span-2 card border border-line rounded-xl p-5 bg-white shadow-sm h-fit lg:sticky lg:top-5 space-y-5">
          {selected ? (
            <div className="space-y-4">
              <div className="flex items-start justify-between gap-3 border-b border-line pb-3">
                <div>
                  <h2 className="font-bold text-ink text-base">{selected.title}</h2>
                  <div className="text-[11px] font-mono text-subink mt-1 bg-neutral-100 px-1.5 py-0.5 rounded border border-line w-fit">
                    {selected.work_id}
                  </div>
                </div>
                <WorkItemBadge status={selected.status} />
              </div>

              {/* Information Table */}
              <div className="space-y-3 text-[12px]">
                {/* Hypothesis Quote Box */}
                <div className="bg-neutral-50 border-l-3 border-brand/50 rounded-r-lg p-3 space-y-1">
                  <div className="text-[10px] text-subink font-bold uppercase tracking-wider">经济合理性假说</div>
                  <p className="text-[13px] text-ink font-medium italic">
                    “{selected.mechanism || "未填写机制假设" }”
                  </p>
                  {selected.citation && (
                    <div className="text-[11px] text-subink mt-2">
                      📖 引用依据: {selected.citation}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-2 gap-y-2 gap-x-4 pt-1 border-t border-line/50">
                  <div>
                    <span className="text-subink block">当前审计阶段:</span>
                    <span className="font-mono text-ink uppercase font-bold">{selected.stage.toUpperCase() || "草案"}</span>
                  </div>
                  <div>
                    <span className="text-subink block">来源维度:</span>
                    <span className="font-semibold text-ink">{sourceLabel(selected.source)}</span>
                  </div>
                </div>

                {/* Risk / Block Alert Box */}
                {selected.status === "blocked" && (
                  <div className="bg-danger/10 border border-danger/20 rounded-lg p-3 text-[12px] text-danger font-medium">
                    <span className="font-bold block">❌ 验证被拦截 / 阻塞风险:</span>
                    <span>{selected.blocked_reason || "该阶段的指标审计未达到设定值门槛。"}</span>
                  </div>
                )}

                {/* Latest Result / Details Metrics */}
                {selected.latest_result && Object.keys(selected.latest_result).length > 0 && (
                  <div className="bg-neutral-50 border border-line rounded-lg p-3 text-[11px] space-y-1.5 font-mono">
                    <div className="font-bold text-subink text-[10px] uppercase font-sans tracking-wide">最近运行统计 / 属性</div>
                    <div className="divide-y divide-line/40 max-h-40 overflow-y-auto pr-1">
                      {Object.entries(selected.latest_result).map(([key, val]) => (
                        <div key={key} className="flex justify-between py-1 gap-2">
                          <span className="text-subink font-medium">{key}</span>
                          <span className="text-ink font-semibold text-right break-all">
                            {typeof val === "object" ? JSON.stringify(val) : String(val)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="pt-2 border-t border-line/50 flex flex-col gap-1">
                  <span className="text-subink">下一步推荐行动:</span>
                  <strong className="text-brand text-[13px]">{actionLabel(selected.next_action) || "已完成全部流程"}</strong>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="pt-2 space-y-2">
                {selected.next_action === "review" ? (
                  <Link
                    href="/experiments/reviews"
                    className="block text-center px-4 py-2.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm font-bold shadow transition-colors"
                  >
                    进入人工复核
                  </Link>
                ) : selected.next_action === "complete_draft" ? (
                  <Link
                    href={`/experiments/${selected.kind}/${selected.item_id}`}
                    className="block text-center px-4 py-2.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm font-bold shadow transition-colors"
                  >
                    补全研究草案
                  </Link>
                ) : selected.next_action ? (
                  <button
                    onClick={() => execute(selected)}
                    disabled={!!busy}
                    className="w-full px-4 py-2.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-sm font-bold shadow transition-colors disabled:opacity-50"
                  >
                    {busy === selected.work_id ? "异步审计计算中…" : `启动 ${actionLabel(selected.next_action)}`}
                  </button>
                ) : null}

                <Link
                  href={`/experiments/${selected.kind}/${selected.item_id}`}
                  className="block text-center text-[12px] text-brand hover:underline font-semibold"
                >
                  查看完整研究档案与代码
                </Link>
              </div>
            </div>
          ) : (
            <div className="text-sm text-subink text-center py-10">
              👈 选择左侧队列中的研究对象以查看详情。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
