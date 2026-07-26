"use client";

import { useState } from "react";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import { HashCopy } from "@/components/ui/QuantComponents";
import type { SystemTruthView, LegEvidence } from "@/lib/types";

// 系统真相层呈现:把「聲明的部署 / 已驗證的部署 / 是否允許生產」三態並排,
// 並讓每條腿/每個阻斷原因可點開看證據鏈(spec_hash 對比、註冊狀態、根因)。
// 純讀取:所有判定來自後端 /system/truth,前端不做任何「修正」(web/CLAUDE.md §3.2)。

function shortHash(h: string): string {
  return h ? `${h.slice(0, 12)}…` : "—";
}

function EvidenceDrawer({ leg }: { leg: LegEvidence }) {
  return (
    <div className="mt-2 p-3 bg-bg border border-line rounded-lg space-y-2 font-mono text-[11px]">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1.5">
        <div className="flex justify-between gap-3">
          <span className="text-subink">註冊表狀態</span>
          <span className={leg.status_deployable ? "text-ok" : "text-danger"}>
            {leg.registry_found ? leg.registry_status || "—" : "不在註冊表"}
            {leg.status_deployable ? " ✓可部署" : " ✗非可部署"}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-subink">spec_hash 一致</span>
          <span className={leg.spec_hash_match ? "text-ok" : "text-danger"}>
            {leg.spec_hash_match ? "✓ 匹配" : "✗ 漂移"}
          </span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-subink">清單聲明 hash</span>
          <span className="text-[#E6EDF7]">{shortHash(leg.declared_spec_hash)}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-subink">註冊表 hash</span>
          <span className="text-[#E6EDF7]">{shortHash(leg.registry_spec_hash)}</span>
        </div>
      </div>
      {leg.blocking_reason ? (
        <div className="pt-2 border-t border-line text-danger leading-relaxed">
          ⛔ {leg.blocking_reason}
        </div>
      ) : (
        <div className="pt-2 border-t border-line text-ok">✓ 該腿無阻斷</div>
      )}
    </div>
  );
}

export default function DeploymentTruth({ truth }: { truth: SystemTruthView | null }) {
  const [openLeg, setOpenLeg] = useState<number | null>(0);

  if (!truth) {
    return (
      <Card title="部署真相 (Deployment Truth)">
        <div className="text-[12px] text-subink leading-relaxed">
          正在從 <span className="font-mono">/system/truth</span> 載入真相層……
          後端未啟動時此處保持為空,<span className="text-warn">不顯示任何假綠燈</span>。
        </div>
      </Card>
    );
  }

  const allowed = truth.production_allowed;
  const declaredLeg = truth.declared_legs[0];

  return (
    <div className="space-y-4">
      <StatusBanner
        status={allowed ? "ready" : "blocked"}
        title={
          allowed
            ? "今日允許生產 — 部署已驗證且就緒"
            : "今日不允許生產 — 系統 fail-closed"
        }
        detail={
          <>
            截至 {truth.as_of || "—"}。
            {allowed
              ? "已驗證部署通過 registry / spec_hash / decay / paper 全部閘門。"
              : `阻斷原因 ${truth.blocking_reasons.length} 項(見下方證據鏈)。declared ≠ verified ≠ production_allowed。`}
          </>
        }
      />

      {/* 三態 KPI:declared / verified / production_allowed 並排,杜絕把清單聲明誤讀成 live */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">聲明部署 (declared)</div>
          <div className="text-base font-bold font-mono text-[#E6EDF7] mt-1.5">
            {declaredLeg ? `${declaredLeg.family}/${declaredLeg.version}` : "—"}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">
            清單 status: <span className="font-mono">{truth.declared_status || "—"}</span>
            {declaredLeg ? <span className="ml-2">role: {declaredLeg.role}</span> : null}
          </div>
          <div className="text-[10px] text-warn mt-1">清單聲稱在跑什麼 ≠ 真的在跑</div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">已驗證部署 (verified)</div>
          <div
            className={`text-base font-bold font-mono mt-1.5 ${
              truth.verified ? "text-ok" : "text-danger"
            }`}
          >
            {truth.verified
              ? truth.verified_legs.map((l) => `${l.family}/${l.version}`).join(", ") || truth.verified_deployment_id
              : "無 (none)"}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2 leading-relaxed">
            {truth.verified
              ? "通過 fail-closed 校驗後真正可激活"
              : truth.verify_error || "未通過部署身份校驗"}
          </div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">今日允許生產 (production_allowed)</div>
          <div
            className={`text-2xl font-bold font-mono mt-1.5 ${
              allowed ? "text-ok" : "text-danger"
            }`}
          >
            {allowed ? "🟢 是" : "🔴 否"}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">
            數據/治理/decay/paper 全過才放行
          </div>
        </div>
      </div>

      {/* 證據鏈:逐腿可點開 */}
      <Card title="部署身份證據鏈 (Deployment Identity Evidence)">
        <div className="text-[11px] text-subink mb-3 leading-relaxed">
          每條聲明腿與註冊表逐項對照。點擊展開可見 spec_hash 對比、註冊狀態與阻斷根因——
          所有判定來自後端,前端不改口徑。
        </div>
        {truth.evidence_chain.length === 0 ? (
          <div className="text-[12px] text-subink font-mono">清單無任何腿。</div>
        ) : (
          <div className="space-y-2">
            {truth.evidence_chain.map((leg, i) => {
              const blocked = !!leg.blocking_reason;
              const open = openLeg === i;
              return (
                <div key={`${leg.family}-${leg.version}-${i}`}>
                  <button
                    type="button"
                    onClick={() => setOpenLeg(open ? null : i)}
                    className="w-full flex items-center justify-between gap-3 p-3 bg-navy border border-line rounded-lg hover:border-brand transition-all text-left"
                  >
                    <span className="font-mono text-[12px] text-[#E6EDF7]">
                      {leg.family}/{leg.version}
                      <span className="text-[#5F728A] ml-2">({leg.role})</span>
                    </span>
                    <span className="flex items-center gap-3">
                      <span
                        className={`font-mono text-[11px] font-bold ${
                          blocked ? "text-danger" : "text-ok"
                        }`}
                      >
                        {blocked ? "⛔ 阻斷" : "✓ 通過"}
                      </span>
                      <span className="text-subink text-[10px]">{open ? "▲" : "▼"}</span>
                    </span>
                  </button>
                  {open && <EvidenceDrawer leg={leg} />}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* readiness 閘門全部阻斷原因 */}
      {truth.blocking_reasons.length > 0 && (
        <Card title="生產閘門阻斷原因 (Readiness Blocking Reasons)">
          <ul className="space-y-1.5">
            {truth.blocking_reasons.map((r, i) => (
              <li key={i} className="font-mono text-[11px] text-danger flex gap-2">
                <span className="shrink-0">⛔</span>
                <span className="leading-relaxed break-all">{r}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* 真相源:文件路徑可追溯 */}
      <div className="text-[10px] text-[#5F728A] font-mono leading-relaxed">
        真相源:
        {Object.entries(truth.truth_sources).map(([k, v]) => (
          <span key={k} className="ml-2">
            {k}=<span className="text-subink">{v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
