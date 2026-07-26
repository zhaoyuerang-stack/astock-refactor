"use client";

import { Fragment, useState } from "react";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import type { GateDiag, GateVerdict, GateVerdictsView } from "@/lib/types";

// 验证闸门②:全注册表逐版本 9-Gate R2P 裁决面。决策:能否独立验证通过→入册。
// 权威裁决在后端 decide_nine_gate;逐门诊断仅定位卡点(诊断·非裁决)。前端只读呈现。

const VERDICT_TONE: Record<string, string> = {
  PASSED: "text-ok",
  FAILED: "text-danger",
  PENDING: "text-warn",
  RUN_FAILED: "text-danger",
};

function mark(status: GateDiag["status"]): { cls: string; txt: string } {
  if (status === "passed") return { cls: "text-ok", txt: "✓" };
  if (status === "failed") return { cls: "text-danger", txt: "✗" };
  return { cls: "text-[#5F728A]", txt: "◌" };
}

function GateStrip({ gates }: { gates: GateDiag[] }) {
  return (
    <span className="font-mono text-[11px] tracking-wider" title="G1…G9 逐门诊断">
      {gates.map((g) => {
        const m = mark(g.status);
        return (
          <span key={g.gate} className={m.cls} title={`${g.gate} ${g.name}: ${g.status}`}>
            {m.txt}
          </span>
        );
      })}
    </span>
  );
}

function GateDrawer({ gv }: { gv: GateVerdict }) {
  return (
    <div className="mt-1 mb-2 mx-2 p-3 bg-bg border border-line rounded-lg">
      <div className="text-[10px] text-subink mb-2">
        逐门诊断(<span className="text-warn">诊断·非裁决</span>,权威裁决 ={" "}
        <span className="font-mono">{gv.verdict}</span> · {gv.verdict_label})
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2 font-mono text-[11px]">
        {gv.gate_diag.map((g) => {
          const m = mark(g.status);
          return (
            <div key={g.gate} className="p-2 bg-navy border border-line rounded">
              <div className="text-[#E6EDF7]">
                <span className={`${m.cls} font-bold mr-1`}>{m.txt}</span>
                {g.gate} {g.name}
              </div>
              <div className="text-[#5F728A] text-[10px] mt-0.5 truncate">
                {g.threshold} · 实际 {g.actual || "—"}
              </div>
              <div className="text-[#5F728A] text-[10px] truncate">来源 {g.source_field}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function GateVerdicts({ data }: { data: GateVerdictsView | null }) {
  const [open, setOpen] = useState<string | null>(null);

  if (!data) {
    return (
      <Card title="验证闸门 · 9-Gate 逐门裁决 (Validation Gate)">
        <div className="text-[12px] text-subink leading-relaxed">
          正在從 <span className="font-mono">/governance/gate-verdicts</span> 載入……
          後端未啟動時此處為空,<span className="text-warn">不顯示任何假裁決</span>。
        </div>
      </Card>
    );
  }

  const s = data.summary || {};
  const passed = s.PASSED ?? 0;

  return (
    <div className="space-y-4">
      <StatusBanner
        status={passed > 0 ? "ready" : "attention"}
        title={
          passed > 0
            ? `${passed} 个版本通过完整 9-Gate 验证`
            : "无任何版本通过完整 9-Gate 验证(与 0 在册一致)"
        }
        detail={
          <>
            截至 {data.as_of || "—"}。共 {s.total ?? 0} 版本 · 已审计 {s.audited ?? 0} ·
            通过 {passed} · 未通过 {s.FAILED ?? 0} · 待完整审计 {s.PENDING ?? 0}。
            权威裁决 = 后端 decide_nine_gate(只认 passed_all)。
          </>
        }
      />

      <Card title={`9-Gate 逐门裁决 · ${data.verdicts.length} 版本(通过→待审→失败)`}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[11px] text-subink border-b border-line text-left">
                <th className="py-2 pr-3 font-medium">版本</th>
                <th className="py-2 pr-3 font-medium">阶段</th>
                <th className="py-2 pr-3 font-medium">裁决</th>
                <th className="py-2 pr-3 font-medium">入册卡点</th>
                <th className="py-2 pr-3 font-medium">G1…G9</th>
                <th className="py-2 pr-3 font-medium text-right">DSR p</th>
                <th className="py-2 pr-3 font-medium text-right">n_trials</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {data.verdicts.map((gv) => {
                const key = `${gv.family}/${gv.version}`;
                const isOpen = open === key;
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
                      <td className="py-2 pr-3 text-subink">{gv.stage}</td>
                      <td className={`py-2 pr-3 font-bold ${VERDICT_TONE[gv.verdict] || "text-subink"}`}>
                        {gv.verdict}
                      </td>
                      <td className="py-2 pr-3 text-danger max-w-[220px] truncate" title={gv.register_blocker}>
                        {gv.register_blocker || "—"}
                      </td>
                      <td className="py-2 pr-3">
                        <GateStrip gates={gv.gate_diag} />
                      </td>
                      <td className="py-2 pr-3 text-right text-subink">
                        {gv.dsr_p === null ? "—" : gv.dsr_p.toFixed(3)}
                      </td>
                      <td className="py-2 pr-3 text-right text-subink">{gv.n_trials ?? "—"}</td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={7} className="p-0">
                          <GateDrawer gv={gv} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="text-[10px] text-[#5F728A] mt-2 font-mono">
          真相源:{Object.entries(data.truth_sources).map(([k, v]) => (
            <span key={k} className="ml-2">{k}=<span className="text-subink">{v}</span></span>
          ))}
        </div>
      </Card>
    </div>
  );
}
