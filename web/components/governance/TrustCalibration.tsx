"use client";

import { useState } from "react";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import type { TrustCalibrationView, TrustSignal } from "@/lib/types";

// 信任校准首屏(over-trust 防护带)。决策:用户在看 KPI 前,当前策略池有多可信 /
// 哪里最可能是假 alpha 或已在失效。权威裁决在后端(decide_nine_gate);前端只读呈现,
// banner_status 直接透传给 StatusBanner,禁止在展示层重算或上调裁绿(fail-closed)。

const SIGNAL_TONE: Record<TrustSignal["status"], { dot: string; text: string }> = {
  ok: { dot: "bg-ok", text: "text-ok" },
  attention: { dot: "bg-warn", text: "text-warn" },
  blocked: { dot: "bg-danger", text: "text-danger" },
  info: { dot: "bg-subink/50", text: "text-subink" }, // info=仅陈述事实,不参与裁绿
};

const VERDICT_TONE: Record<string, string> = {
  PASSED: "text-ok",
  FAILED: "text-danger",
  PENDING: "text-warn",
  RUN_FAILED: "text-danger",
};

function fmt(x: number | null): string {
  return x === null || x === undefined ? "—" : x.toFixed(3);
}

export default function TrustCalibration({ data }: { data: TrustCalibrationView | null }) {
  const [showAll, setShowAll] = useState(false);

  if (!data) {
    return (
      <Card title="信任校准 · 首屏 (Trust Calibration)">
        <div className="text-[12px] text-subink leading-relaxed">
          正在从 <span className="font-mono">/governance/trust-calibration</span> 载入……
          后端未启动时此处为空,<span className="text-warn">不显示任何假裁决</span>。
        </div>
      </Card>
    );
  }

  const rows = showAll ? data.strategies : data.strategies.slice(0, 8);

  return (
    <div className="space-y-3">
      {/* 综合裁决:banner_status 直接透传,前端不重算 */}
      <StatusBanner
        status={data.banner_status}
        title={data.headline}
        detail={
          <>
            截至 {data.as_of || "—"}。{data.detail} 权威裁决 = 后端 decide_nine_gate(仅聚合,不重算)。
          </>
        }
      />

      {/* 逐维度信号 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
        {data.signals.map((s) => {
          const tone = SIGNAL_TONE[s.status];
          return (
            <div key={s.key} className="p-2.5 bg-bg border border-line rounded-lg" title={`权威:${s.authority}`}>
              <div className="flex items-center gap-2">
                <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${tone.dot}`} />
                <span className={`text-[12px] font-semibold ${tone.text}`}>{s.label}</span>
              </div>
              <div className="text-[11px] text-subink mt-1 leading-relaxed">{s.evidence}</div>
            </div>
          );
        })}
      </div>

      {/* 逐策略行(后端已风险优先排序) */}
      <Card title={`逐策略信任 · ${data.strategies.length} 版本(风险优先)`}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-[11px] text-subink border-b border-line text-left">
                <th className="py-2 pr-3 font-medium">阶段</th>
                <th className="py-2 pr-3 font-medium">版本</th>
                <th className="py-2 pr-3 font-medium">裁决</th>
                <th className="py-2 pr-3 font-medium text-center">已审计</th>
                <th className="py-2 pr-3 font-medium text-right">DSR p</th>
                <th className="py-2 pr-3 font-medium text-center">DSR 显著</th>
                <th className="py-2 pr-3 font-medium">over-trust 提示</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {rows.map((r) => (
                <tr key={`${r.family}/${r.version}`} className="border-b border-line/40 align-top">
                  <td className="py-2 pr-3 text-subink">{r.stage}</td>
                  <td className="py-2 pr-3 text-[#E6EDF7]">{r.family}/{r.version}</td>
                  <td className={`py-2 pr-3 font-bold ${VERDICT_TONE[r.verdict] || "text-subink"}`}>{r.verdict}</td>
                  <td className="py-2 pr-3 text-center">
                    {r.audited ? <span className="text-ok">✓</span> : <span className="text-warn">◌</span>}
                  </td>
                  <td className="py-2 pr-3 text-right text-subink">{fmt(r.dsr_p)}</td>
                  <td className="py-2 pr-3 text-center">
                    {r.dsr_significant === null ? (
                      <span className="text-subink">—</span>
                    ) : r.dsr_significant ? (
                      <span className="text-ok">✓</span>
                    ) : (
                      <span className="text-danger">✗</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-subink max-w-[280px] truncate" title={r.trust_note}>
                    {r.trust_note}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.strategies.length > 8 && (
          <button
            onClick={() => setShowAll((v) => !v)}
            className="mt-2 text-[11px] text-subink hover:text-ink underline"
          >
            {showAll ? "收起" : `展开全部 ${data.strategies.length} 行`}
          </button>
        )}
        <div className="text-[10px] text-[#5F728A] mt-2 leading-relaxed">{data.honesty}</div>
      </Card>
    </div>
  );
}
