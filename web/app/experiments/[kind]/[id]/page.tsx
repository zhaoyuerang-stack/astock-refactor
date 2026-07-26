"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import ResearchNav from "@/components/research/ResearchNav";
import WorkItemBadge from "@/components/research/WorkItemBadge";
import { api } from "@/lib/api";
import { actionLabel, sourceLabel } from "@/lib/researchWorkspace.mjs";
import type { ResearchWorkItemDetailView } from "@/lib/types";

export default function ResearchWorkItemPage({ params }: { params: { kind: string; id: string } }) {
  const [detail, setDetail] = useState<ResearchWorkItemDetailView | null>(null);
  const [notes, setNotes] = useState("");
  const [draftForm, setDraftForm] = useState({
    title: "",
    mechanism: "",
    citation: "",
    factor_fn_name: "",
    factor_params: "{}",
    data_dependencies: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => api.researchWorkItem(params.kind, params.id).then(setDetail).catch((e) => setError(String(e))), [params.kind, params.id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!detail || detail.item.kind !== "draft") return;
    setDraftForm({
      title: String(detail.raw.title ?? ""),
      mechanism: String(detail.raw.mechanism ?? ""),
      citation: String(detail.raw.citation ?? ""),
      factor_fn_name: String(detail.raw.factor_fn_name ?? ""),
      factor_params: JSON.stringify(detail.raw.factor_params ?? {}, null, 2),
      data_dependencies: Array.isArray(detail.raw.data_dependencies) ? detail.raw.data_dependencies.join(", ") : "",
    });
  }, [detail]);

  async function runAction() {
    if (!detail?.item.next_action) return;
    setBusy(true);
    try {
      const job = await api.runResearchAction(params.kind, params.id, detail.item.next_action, {});
      await api.waitForExperimentJob(job.job_id, { timeoutMs: 15 * 60 * 1000 });
      await load();
    } catch (e) {
      setError(String(e));
    } finally { setBusy(false); }
  }

  async function review(action: "approve" | "reject") {
    setBusy(true);
    try {
      await api.reviewResearchWorkItem(params.kind, params.id, action, notes);
      await load();
    } catch (e) {
      setError(String(e));
    } finally { setBusy(false); }
  }

  async function saveDraft() {
    setBusy(true);
    setError("");
    try {
      await api.updateResearchDraft(params.id, {
        title: draftForm.title,
        mechanism: draftForm.mechanism,
        citation: draftForm.citation,
        factor_fn_name: draftForm.factor_fn_name,
        factor_params: JSON.parse(draftForm.factor_params || "{}"),
        data_dependencies: draftForm.data_dependencies.split(",").map((value) => value.trim()).filter(Boolean),
      });
      await load();
    } catch (e) {
      setError(String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-5">
      <PageHeader title={detail?.item.title || "研究档案"} desc={detail?.item.work_id || `${params.kind}:${params.id}`} />
      <ResearchNav />
      <Link href="/experiments" className="text-[12px] text-brand hover:underline">← 返回工作队列</Link>
      {error && <div className="card text-sm text-danger">{error}</div>}
      {detail && (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
            <div className="lg:col-span-2 card space-y-4">
              <div className="flex justify-between gap-3"><h2 className="font-bold">研究概览</h2><WorkItemBadge status={detail.item.status} /></div>
              <div className="grid grid-cols-2 gap-3 text-[12px]">
                <div><span className="text-subink">来源</span><div>{sourceLabel(detail.item.source)}</div></div>
                <div><span className="text-subink">阶段</span><div>{detail.item.stage.toUpperCase()}</div></div>
                <div className="col-span-2"><span className="text-subink">经济假设</span><div>{detail.item.mechanism || "—"}</div></div>
                <div className="col-span-2"><span className="text-subink">证据</span><div>{detail.item.citation || "—"}</div></div>
              </div>
            </div>
            <div className="card space-y-3">
              <h2 className="font-bold">当前决策</h2>
              <div className="text-[12px]"><span className="text-subink">下一步：</span>{actionLabel(detail.item.next_action)}</div>
              <div className="text-[12px]"><span className="text-subink">阻塞：</span>{detail.item.blocked_reason || "无"}</div>
              {detail.item.next_action === "review" ? (
                <>
                  <textarea value={notes} onChange={(e) => setNotes(e.target.value)} className="w-full h-20 border border-line rounded-lg p-2 text-[12px]" placeholder="复核意见" />
                  <div className="grid grid-cols-2 gap-2"><button onClick={() => review("approve")} disabled={busy} className="bg-songshi text-white rounded-lg py-2 text-[12px]">批准</button><button onClick={() => review("reject")} disabled={busy} className="border border-danger text-danger rounded-lg py-2 text-[12px]">拒绝</button></div>
                </>
              ) : detail.item.next_action && detail.item.next_action !== "complete_draft" ? (
                <button onClick={runAction} disabled={busy} className="w-full bg-brand text-white rounded-lg py-2 text-[12px] font-semibold disabled:opacity-50">{busy ? "执行中…" : actionLabel(detail.item.next_action)}</button>
              ) : null}
            </div>
          </div>
          {detail.item.kind === "draft" && detail.raw.status === "active" && (
            <div className="card space-y-4">
              <div>
                <h2 className="font-bold">补全可执行研究草案</h2>
                <p className="text-[11px] text-subink mt-1">只有经济机制和可执行因子函数同时存在，草案才允许进入 L0。</p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="text-[11px] text-subink">标题<input value={draftForm.title} onChange={(e) => setDraftForm((prev) => ({ ...prev, title: e.target.value }))} className="mt-1 w-full border border-line rounded-lg p-2 text-[12px] text-ink" /></label>
                <label className="text-[11px] text-subink">因子函数路径<input value={draftForm.factor_fn_name} onChange={(e) => setDraftForm((prev) => ({ ...prev, factor_fn_name: e.target.value }))} placeholder="factors.module.function" className="mt-1 w-full border border-line rounded-lg p-2 text-[12px] text-ink font-mono" /></label>
                <label className="text-[11px] text-subink md:col-span-2">经济机制<textarea value={draftForm.mechanism} onChange={(e) => setDraftForm((prev) => ({ ...prev, mechanism: e.target.value }))} className="mt-1 w-full border border-line rounded-lg p-2 text-[12px] text-ink h-20" /></label>
                <label className="text-[11px] text-subink md:col-span-2">引用 / 证据<textarea value={draftForm.citation} onChange={(e) => setDraftForm((prev) => ({ ...prev, citation: e.target.value }))} className="mt-1 w-full border border-line rounded-lg p-2 text-[12px] text-ink h-16" /></label>
                <label className="text-[11px] text-subink">因子参数 JSON<textarea value={draftForm.factor_params} onChange={(e) => setDraftForm((prev) => ({ ...prev, factor_params: e.target.value }))} className="mt-1 w-full border border-line rounded-lg p-2 text-[11px] text-ink font-mono h-28" /></label>
                <label className="text-[11px] text-subink">数据依赖（逗号分隔）<textarea value={draftForm.data_dependencies} onChange={(e) => setDraftForm((prev) => ({ ...prev, data_dependencies: e.target.value }))} placeholder="price/close, price/amount" className="mt-1 w-full border border-line rounded-lg p-2 text-[11px] text-ink font-mono h-28" /></label>
              </div>
              <button onClick={saveDraft} disabled={busy} className="px-4 py-2 rounded-lg bg-brand text-white text-[12px] font-semibold disabled:opacity-50">{busy ? "保存中…" : "保存草案"}</button>
            </div>
          )}
          <div className="card overflow-x-auto">
            <h2 className="font-bold mb-3">验证历史</h2>
            <table className="w-full text-[12px] min-w-[680px]"><thead className="text-subink text-left"><tr><th className="py-2">协议</th><th>时间</th><th>决策</th><th>结果 / 失败原因</th><th>数据 vintage</th></tr></thead>
              <tbody>{detail.runs.map((run, index) => <tr key={index} className="border-t border-line/50"><td className="py-2 font-mono">{run.protocol || run.status || "—"}</td><td>{run.run_at || "—"}</td><td>{run.decision || "—"}</td><td>{run.notes || run.reason || run.result?.error || "—"}</td><td className="font-mono text-[10px]">{run.vintage_id || "—"}</td></tr>)}</tbody>
            </table>
            {detail.runs.length === 0 && <div className="text-sm text-subink">尚无验证运行。</div>}
          </div>
          <details className="card"><summary className="cursor-pointer text-sm font-semibold">原始定义与配置</summary><pre className="mt-3 text-[10px] overflow-auto max-h-96">{JSON.stringify(detail.raw, null, 2)}</pre></details>
        </>
      )}
    </div>
  );
}
