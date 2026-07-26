"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import ResearchNav from "@/components/research/ResearchNav";
import WorkItemBadge from "@/components/research/WorkItemBadge";
import { api } from "@/lib/api";
import { actionLabel, sourceLabel } from "@/lib/researchWorkspace.mjs";
import type { ResearchWorkItemView } from "@/lib/types";


export default function ResearchReviewsPage() {
  const [items, setItems] = useState<ResearchWorkItemView[]>([]);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | "review" | "promote">("all");

  const load = useCallback(() => api.researchWorkItems({ action: "review,promote", limit: 500 })
    .then((data) => setItems(data.items))
    .catch((e) => setError(String(e))), []);
  useEffect(() => { load(); }, [load]);

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const matchStatus = item.status === "review" || item.next_action === "promote";
      if (!matchStatus) return false;
      if (activeTab === "review") return item.status === "review";
      if (activeTab === "promote") return item.next_action === "promote";
      return true;
    });
  }, [items, activeTab]);

  async function review(item: ResearchWorkItemView, action: "approve" | "reject") {
    setBusy(item.work_id);
    setError("");
    try {
      await api.reviewResearchWorkItem(item.kind, item.item_id, action, notes[item.work_id] ?? "");
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  async function promote(item: ResearchWorkItemView) {
    setBusy(item.work_id);
    setError("");
    try {
      const job = await api.runResearchAction(item.kind, item.item_id, "promote", { target_status: "SHADOW" });
      const result = await api.waitForExperimentJob<any>(job.job_id, { timeoutMs: 15 * 60 * 1000 });
      if (result && result.registered === false) {
        setError(`晋级被审计拦截: ${result.detail || "未通过深度验证审计。"}`);
      } else {
        await load();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="研究实验室 · 人工复核"
        desc="在这里复核量化因子的经济学假设并决策是否晋级；严禁越过审核门直接部署。"
      />
      <ResearchNav />

      {error && (
        <div className="p-4 rounded-xl bg-danger/10 border border-danger/20 text-sm text-danger flex flex-col gap-1 font-medium">
          <span className="font-bold text-[15px]">⚠️ 执行错误/审计拦截:</span>
          <span>{error}</span>
        </div>
      )}

      {/* Tabs Switcher */}
      <div className="flex border-b border-line gap-6 text-sm font-medium">
        <button
          onClick={() => setActiveTab("all")}
          className={`pb-3 border-b-2 transition-all ${
            activeTab === "all" ? "border-brand text-brand font-bold" : "border-transparent text-subink hover:text-ink"
          }`}
        >
          全部工作 ({items.length})
        </button>
        <button
          onClick={() => setActiveTab("review")}
          className={`pb-3 border-b-2 transition-all ${
            activeTab === "review" ? "border-brand text-brand font-bold" : "border-transparent text-subink hover:text-ink"
          }`}
        >
          待人工审批 ({items.filter(i => i.status === "review").length})
        </button>
        <button
          onClick={() => setActiveTab("promote")}
          className={`pb-3 border-b-2 transition-all ${
            activeTab === "promote" ? "border-brand text-brand font-bold" : "border-transparent text-subink hover:text-ink"
          }`}
        >
          待正式晋级 ({items.filter(i => i.next_action === "promote").length})
        </button>
      </div>

      {/* Candidates List */}
      <div className="space-y-4">
        {filteredItems.map((item) => (
          <div
            key={item.work_id}
            className="card border border-line rounded-xl p-5 bg-white shadow-sm hover:border-brand/40 transition-all duration-200 grid grid-cols-1 lg:grid-cols-4 gap-6"
          >
            {/* Info Section */}
            <div className="lg:col-span-2 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Link
                  href={`/experiments/${item.kind}/${item.item_id}`}
                  className="font-bold text-ink text-base hover:text-brand hover:underline transition-colors"
                >
                  {item.title}
                </Link>
                <WorkItemBadge status={item.status} />
              </div>
              <div className="text-[11px] font-mono text-subink bg-neutral-50 px-2 py-0.5 rounded border border-line w-fit">
                {sourceLabel(item.source)} · {item.item_id}
              </div>

              {/* Economic Hypothesis */}
              <div className="bg-neutral-50/80 border-l-3 border-brand/50 rounded-r-lg p-3">
                <div className="text-[10px] uppercase tracking-wider text-subink font-bold">经济学假设机制</div>
                <p className="text-[13px] text-ink mt-1 font-medium italic">
                  “{item.mechanism || "未填写假设机制（请点击上方标题补齐）"}”
                </p>
                {item.citation && (
                  <div className="text-[11px] text-subink mt-2">
                    📖 引用支持: {item.citation}
                  </div>
                )}
              </div>
            </div>

            {/* Note & Feedback Area */}
            <div className="flex flex-col justify-between">
              {item.review ? (
                /* Display approved log history */
                <div className="bg-songshi/10 rounded-lg p-3 border border-songshi/20 space-y-1 my-auto">
                  <div className="text-[11px] font-bold text-songshi uppercase tracking-wider">✓ 人工已审批通过</div>
                  <p className="text-[12px] text-ink italic">“{item.review.notes || "无复核备注" }”</p>
                  <div className="text-[10px] text-subink">
                    操作人: {item.review.reviewer} · {item.review.reviewed_at}
                  </div>
                </div>
              ) : (
                /* Write active notes for review */
                <div className="space-y-2">
                  <label className="text-[11px] font-bold text-subink uppercase tracking-wider block">复核意见 / 备忘录</label>
                  <textarea
                    value={notes[item.work_id] ?? ""}
                    onChange={(e) => setNotes((prev) => ({ ...prev, [item.work_id]: e.target.value }))}
                    placeholder="输入复核要点、经济合理性审查备忘或拒绝原因..."
                    className="w-full h-24 rounded-lg border border-line p-2.5 text-[12px] placeholder:text-subink focus:border-brand focus:outline-none transition-colors"
                    disabled={item.next_action === "promote" || busy === item.work_id}
                  />
                </div>
              )}
            </div>

            {/* Actions Panel */}
            <div className="flex flex-col justify-center gap-3">
              {item.next_action === "promote" ? (
                <div className="space-y-2">
                  <button
                    onClick={() => promote(item)}
                    disabled={busy === item.work_id}
                    className="w-full py-2.5 rounded-lg bg-brand hover:bg-brand-dark text-white text-[12px] font-bold shadow transition-colors disabled:opacity-50"
                  >
                    {busy === item.work_id ? "9-Gate 审计与晋级中…" : "正式部署 (Promote)"}
                  </button>
                  <span className="text-[11px] text-subink text-center block">
                    晋级后将自动触发 15 轮多重检验与 9-Gate 审计。
                  </span>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <button
                    onClick={() => review(item, "approve")}
                    disabled={busy === item.work_id}
                    className="py-2 rounded-lg bg-songshi hover:bg-songshi-dark text-white text-[12px] font-bold shadow transition-colors disabled:opacity-50"
                  >
                    {busy === item.work_id && busy === item.work_id ? "审批中..." : "批准批准 (Approve)"}
                  </button>
                  <button
                    onClick={() => review(item, "reject")}
                    disabled={busy === item.work_id}
                    className="py-2 rounded-lg border border-danger/40 hover:bg-danger/5 text-danger text-[12px] font-bold transition-colors disabled:opacity-50"
                  >
                    拒绝 (Reject)
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {filteredItems.length === 0 && (
          <div className="card text-sm text-subink py-8 text-center bg-white border border-line rounded-xl">
            暂无当前分类的待审批或待部署对象。
          </div>
        )}
      </div>
    </div>
  );
}
