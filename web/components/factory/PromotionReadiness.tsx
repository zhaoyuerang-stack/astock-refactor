"use client";

import { Fragment, useState } from "react";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import type { CandidateReadiness, GateDiag, PromotionReadinessView } from "@/lib/types";

// Alpha 工厂「晋级就绪」驾驶舱:候选按"距入册"排序(非按收益)+ 唯一卡点 + 边际动作 + 拥挤度。
// 纯读取后端 /experiments/promotion-readiness;权威裁决在后端,前端不改口径(web/CLAUDE.md §3.2)。

const VERDICT_TONE: Record<string, string> = {
  PASSED: "text-ok",
  FAILED: "text-danger",
  PENDING: "text-warn",
  RUN_FAILED: "text-danger",
};

function gateMark(status: GateDiag["status"]): { cls: string; txt: string } {
  if (status === "passed") return { cls: "text-ok", txt: "✓" };
  if (status === "failed") return { cls: "text-danger", txt: "✗" };
  return { cls: "text-[#5F728A]", txt: "◌" };
}

function crowdCell(c: number | null): { cls: string; txt: string } {
  if (c === null) return { cls: "text-[#5F728A]", txt: "未知" };
  const crowded = c > 0.7;
  return { cls: crowded ? "text-danger" : "text-subink", txt: `${c.toFixed(2)}${crowded ? "↑" : ""}` };
}

function GateDiagDrawer({ c }: { c: CandidateReadiness }) {
  return (
    <div className="mt-1 mb-2 mx-2 p-3 bg-bg border border-line rounded-lg">
      <div className="text-[10px] text-subink mb-2">
        逐门诊断(<span className="text-warn">诊断·非裁决</span>,权威裁决 ={" "}
        <span className="font-mono">{c.authoritative_verdict}</span>)
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 font-mono text-[11px]">
        {c.gate_diag.map((g) => {
          const m = gateMark(g.status);
          return (
            <div key={g.gate} className="flex items-start justify-between gap-2 p-2 bg-navy border border-line rounded">
              <div className="min-w-0">
                <div className="text-[#E6EDF7]">
                  <span className={`${m.cls} font-bold mr-1`}>{m.txt}</span>
                  {g.gate} {g.name}
                </div>
                <div className="text-[#5F728A] text-[10px] mt-0.5 truncate">
                  {g.threshold} · 实际 {g.actual || "—"}
                </div>
                <div className="text-[#5F728A] text-[10px] truncate">来源 {g.source_field}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function PromotionReadiness({ data }: { data: PromotionReadinessView | null }) {
  const [open, setOpen] = useState<string | null>(null);

  if (!data) {
    return (
      <Card title="晋级就绪 (Promotion Readiness)">
        <div className="text-[12px] text-subink leading-relaxed">
          正在從 <span className="font-mono">/experiments/promotion-readiness</span> 載入……
          後端未啟動時此處為空,<span className="text-warn">不顯示任何假數據</span>。
        </div>
      </Card>
    );
  }

  const lead = data.candidates[0];
  const cm = data.cluster_map || {};
  const mostCrowded = cm.most_crowded as { a: string; b: string; corr: number } | null | undefined;

  return (
    <div className="space-y-4">
      {/* 顶部裁决条:全台唯一瓶颈 + 研究重心 */}
      <StatusBanner
        status={lead && lead.distance_to_register === 0 ? "ready" : "attention"}
        title={
          data.lead_candidate
            ? `最接近入册:${data.lead_candidate} · 卡在 ${data.lead_blocker || "—"}`
            : "暫無候選"
        }
        detail={
          <>
            截至 {data.as_of || "—"}。{data.research_steer || "—"}
            <span className="text-[#5F728A]"> · 排序按「距入冊」而非收益(避諸誘導過擬合)</span>
          </>
        }
      />

      {/* 晋级就绪榜 */}
      <Card title={`晋级就绪榜 · ${data.candidates.length} 个候选(按距入册升序)`}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[11px] text-subink border-b border-line text-left">
                <th className="py-2 pr-3 font-medium">候选</th>
                <th className="py-2 pr-3 font-medium">阶段</th>
                <th className="py-2 pr-3 font-medium">距入册</th>
                <th className="py-2 pr-3 font-medium">裁决</th>
                <th className="py-2 pr-3 font-medium">唯一卡点</th>
                <th className="py-2 pr-3 font-medium">边际动作</th>
                <th className="py-2 pr-3 font-medium">信息簇</th>
                <th className="py-2 pr-3 font-medium text-right">拥挤度</th>
                <th className="py-2 pr-3 font-medium text-right">DSR p</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {data.candidates.map((c) => {
                const key = `${c.family}/${c.version}`;
                const isOpen = open === key;
                const crowd = crowdCell(c.crowding);
                return (
                  <Fragment key={key}>
                    <tr
                      onClick={() => setOpen(isOpen ? null : key)}
                      className="border-b border-line/40 hover:bg-navy cursor-pointer align-top"
                    >
                      <td className="py-2 pr-3 text-[#E6EDF7]">
                        <span className="text-subink mr-1">{isOpen ? "▲" : "▼"}</span>
                        {key}
                      </td>
                      <td className="py-2 pr-3 text-subink">{c.stage}</td>
                      <td className="py-2 pr-3 font-bold">{c.distance_to_register} 门</td>
                      <td className={`py-2 pr-3 font-bold ${VERDICT_TONE[c.authoritative_verdict] || "text-subink"}`}>
                        {c.authoritative_verdict}
                      </td>
                      <td className="py-2 pr-3 text-danger max-w-[180px] truncate" title={c.single_blocker}>
                        {c.single_blocker || "—"}
                      </td>
                      <td className="py-2 pr-3 text-subink max-w-[220px] truncate" title={c.marginal_action}>
                        {c.marginal_action}
                      </td>
                      <td className="py-2 pr-3 text-[#5F728A] max-w-[160px] truncate" title={c.info_cluster}>
                        {c.info_cluster}
                      </td>
                      <td className={`py-2 pr-3 text-right ${crowd.cls}`}>{crowd.txt}</td>
                      <td className="py-2 pr-3 text-right text-subink">
                        {c.dsr_p === null ? "—" : c.dsr_p.toFixed(3)}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={9} className="p-0">
                          <GateDiagDrawer c={c} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 信息地图 / 拥挤度 */}
      <Card title="信息地图 / 拥挤度 (Crowding)">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-3">
          <div className="p-3 bg-navy border border-line rounded-lg">
            <div className="text-[11px] text-subink">家族数</div>
            <div className="text-xl font-bold font-mono text-[#E6EDF7] mt-1">{cm.n_families ?? "—"}</div>
          </div>
          <div className="p-3 bg-navy border border-line rounded-lg">
            <div className="text-[11px] text-subink">冗余率(corr&gt;0.7)</div>
            <div className="text-xl font-bold font-mono text-warn mt-1">
              {cm.redundancy_rate === null || cm.redundancy_rate === undefined
                ? "—"
                : `${(cm.redundancy_rate * 100).toFixed(0)}%`}
            </div>
          </div>
          <div className="col-span-2 p-3 bg-navy border border-line rounded-lg">
            <div className="text-[11px] text-subink">最拥挤家族对</div>
            <div className="text-sm font-bold font-mono text-danger mt-1">
              {mostCrowded ? `${mostCrowded.a} ↔ ${mostCrowded.b} = ${mostCrowded.corr}` : "—"}
            </div>
          </div>
        </div>
        {cm.note ? <div className="text-[10px] text-[#5F728A] leading-relaxed">{cm.note}</div> : null}
      </Card>

      {/* 真相源 */}
      <div className="text-[10px] text-[#5F728A] font-mono leading-relaxed">
        真相源:
        {Object.entries(data.truth_sources).map(([k, v]) => (
          <span key={k} className="ml-2">
            {k}=<span className="text-subink">{v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
