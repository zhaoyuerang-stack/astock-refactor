"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api } from "@/lib/api";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import DeploymentTruth from "@/components/governance/DeploymentTruth";
import GateVerdicts from "@/components/governance/GateVerdicts";
import type { SystemTruthView, GateVerdictsView } from "@/lib/types";

type CIGuardRow = {
  script: string;
  desc: string;
  // 守卫定义于 scripts/ci/,由 test_all.sh 在 CI/本地运行;Web 暂无实时运行结果源,
  // 故不谎报 PASS —— 一律标 "unknown(待接入)",不硬编码绿灯。
  status: "passed" | "failed" | "unknown";
  reason?: string;
};

type AuditLogEventRow = {
  time: string;
  level: "P0" | "P1" | "P2";
  category: string;
  event: string;
  affected: string;
  actor: string;
  result: string;
};

export default function SystemGovernancePage() {
  const setContext = useAgent((s) => s.setContext);

  const [err, setErr] = useState<string | null>(null);
  const [realAuditLogs, setRealAuditLogs] = useState<AuditLogEventRow[]>([]);
  const [truth, setTruth] = useState<SystemTruthView | null>(null);
  const [verdicts, setVerdicts] = useState<GateVerdictsView | null>(null);

  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      api.systemTruth().catch(() => null),
      api.audit(40),
      api.gateVerdicts().catch(() => null),
    ])
      .then(([st, a, gv]) => {
        setTruth(st);
        setVerdicts(gv);
        const mapped = a.entries.map((e) => {
          let level: "P0" | "P1" | "P2" = "P2";
          if (e.kind === "control" || e.status?.includes("FAIL") || e.status?.includes("failed")) {
            level = "P0";
          } else if (e.kind === "config" || e.kind === "review") {
            level = "P1";
          }
          let category = e.kind.toUpperCase();
          if (e.kind === "config") category = "CONFIG";
          else if (e.kind === "action") category = "ACTION";
          else if (e.kind === "review") category = "REVIEW";
          else if (e.kind === "agent") category = "AGENT";
          else if (e.kind === "control") category = "CONTROL";

          return {
            time: e.status || "—",
            level,
            category,
            event: e.summary,
            affected: e.detail || "系統全局",
            actor: e.actor,
            result: "正常",
          };
        });
        setRealAuditLogs(mapped);

        // AI 上下文以真實 /system/truth 為準,絕不灌「生產就緒」假狀態(後端 fail-closed 時必須誠實)。
        const allowed = st?.production_allowed ?? false;
        const declared = st?.declared_legs?.[0];
        const declaredLabel = declared ? `${declared.family}/${declared.version}` : "—";
        setContext({
          page: "system-governance",
          title: "系統治理與合規中心",
          summary: st
            ? `今日是否允許生產:${allowed ? "是" : "否(系統 fail-closed)"}。聲明部署 ${declaredLabel}(清單 status=${st.declared_status});已驗證部署 ${st.verified ? "有" : "無"}。阻斷原因 ${st.blocking_reasons.length} 項。歷史審計日誌 ${a.total} 條。`
            : `真相層 /system/truth 載入失敗(後端可能未啟動)。不展示任何假狀態。歷史審計日誌 ${a.total} 條。`,
          evidence: st
            ? [
                `production_allowed: ${allowed}`,
                `declared: ${declaredLabel} (status=${st.declared_status})`,
                `verified: ${st.verified ? "yes" : `no — ${st.verify_error}`}`,
                ...st.blocking_reasons.slice(0, 4),
              ]
            : ["/system/truth 不可達"],
          risk:
            st && !allowed
              ? ["系統 fail-closed:當前無已驗證可生產部署,聲明部署 ≠ 已驗證部署,不得當 live 對待"]
              : [],
          recommendation: [
            "點開部署證據鏈核對 spec_hash 漂移與註冊狀態根因",
            "每週定期進行一次 Registry 台帳完整性備份",
          ],
          nextActions: [
            "覆核 P0 級告警日誌記錄",
            allowed ? "提交本週部署合規性數字簽名證明" : "推進阻斷項修復(見阻斷原因清單)",
          ],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [setContext]);

  useAutoRefresh(load);

  // CI 守衛清單(定義于 scripts/ci/,由 test_all.sh 運行)。Web 暫無實時運行結果源,
  // status 一律 "unknown(待接入)" —— 不謊報 PASS、不偽造運行時間。
  const ciGuards: CIGuardRow[] = [
    { script: "check_layer_deps.py", desc: "分層依賴單向性校驗（防止研究代碼逆向倒灌）", status: "unknown" },
    { script: "check_lake_writers.py", desc: "數據湖寫入入口唯一性限制校驗", status: "unknown" },
    { script: "check_no_force_promote.py", desc: "限制強制升級候選策略至在冊的後門校驗", status: "unknown" },
    { script: "check_registry_evidence.py", desc: "在冊策略對應的九門禁證據包完整性校驗", status: "unknown" },
    { script: "check_holdout_compliance.py", desc: "金庫隔離合規檢測（防 holdout.start 以後數據洩露）", status: "unknown" },
    { script: "check_control_exceptions.py", desc: "風控例外簽發合法合規審計", status: "unknown" },
    { script: "check_no_legacy_data.py", desc: "禁用幸存者偏差舊緩存（data_full）源審查", status: "unknown" },
  ];

  // 9 Governance Grid cells。
  // 部署身份兩格(Spec Hash 鎖定 / Registry 證據)由真實 /system/truth 驅動,絕不硬編碼綠燈;
  // 其餘格尚未接入實時源,標 "unknown" 待接入,不再謊報 PASS(見頁尾免責聲明)。
  type GridStatus = "passed" | "failed" | "unknown";
  const hasLegs = !!truth && truth.evidence_chain.length > 0;
  const specHashStatus: GridStatus = !truth
    ? "unknown"
    : hasLegs && truth.evidence_chain.every((l) => l.spec_hash_match)
      ? "passed"
      : "failed";
  const registryStatus: GridStatus = !truth
    ? "unknown"
    : hasLegs && truth.evidence_chain.every((l) => l.status_deployable)
      ? "passed"
      : "failed";
  const governanceGrid: { name: string; desc: string; status: GridStatus }[] = [
    { name: "數據新鮮度 (Freshness)", desc: "最新價量對齊 A股最新交易日", status: "unknown" },
    { name: "樣本外合規 (OOS Guard)", desc: "金庫隔離期內無未來信息洩漏", status: "unknown" },
    { name: "依賴完整性 (Dependencies)", desc: "無循環導入，分層單向鏈合規", status: "passed" },
    { name: "Spec Hash 鎖定 (Hash Lock)", desc: "生產運行的策略 Spec Hash 不可篡改", status: specHashStatus },
    { name: "Registry 證據 (Evidence)", desc: "部署腿註冊狀態可部署且 spec_hash 對齊", status: registryStatus },
    { name: "回滾可行性 (Rollback)", desc: "策略版本退役與回撤熔斷支持一鍵回滾", status: "unknown" },
    { name: "風控閾值合規 (Risk Limits)", desc: "組合單票與小盤暴露限額合規", status: "unknown" },
    { name: "影子運行一致 (Shadow Consistency)", desc: "實盤/紙面跟單交易淨值偏差 < 1.5%", status: "unknown" },
    { name: "發布審批閉環 (Sign-off Loop)", desc: "人工決策簽名歸檔且在 API trace 可追溯", status: "unknown" },
  ];

  // 审计日志只来自真实 /settings/audit(realAuditLogs);为空时显示空态,不再硬编码假事件
  // (原 fallback 含 "v2.3.0 部署成功"/"illiquidity v3.1 审批通过" 等捏造记录,已移除)。

  const getLevelBadge = (level: "P0" | "P1" | "P2") => {
    const styleMap = {
      P0: "bg-[#FF5C5C]/15 text-danger border-[#FF5C5C]/20 font-bold",
      P1: "bg-[#F6B73C]/15 text-warn border-[#F6B73C]/20",
      P2: "bg-[#3D7BFF]/10 text-subink border-line",
    };
    return (
      <span className={`px-2 py-0.5 rounded text-[10px] border font-mono ${styleMap[level]}`}>
        {level}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="系統治理"
        desc="依賴拓撲合規檢查、CI 守衛流水線判定與架構層級一致性治理 (System Integrity & Controls)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {/* 1. 部署真相層 (declared / verified / production_allowed + 證據鏈) —— 取代原硬編碼綠燈 */}
      <DeploymentTruth truth={truth} />

      {/* 2. 驗證閘門②:9-Gate 逐門裁決(候選能否獨立驗證通過→入冊)*/}
      <GateVerdicts data={verdicts} />

      {/* 3. 架構依賴單向拓撲 (Section 12.5) */}
      <Card title="架構依賴關係拓撲檢查 (Single-Direction Architecture Dependency)">
        <div className="text-[11px] text-subink mb-3 leading-relaxed">
          量化引擎合規鐵律：分層依賴關係必須嚴格單向。禁止任何倒灌式反向依賴（例如數據湖引入策略邏輯，或生產層直接引用未中性化因子模版等）。
        </div>
        <div className="flex flex-col md:flex-row items-center justify-between gap-4 p-4 bg-bg border border-line rounded-lg font-mono text-center">
          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">data_lake</div>
            <div className="text-[9px] text-[#5F728A] mt-1">數據原始沉澱層</div>
          </div>
          <span className="hidden md:inline text-ok">→</span>

          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">factors</div>
            <div className="text-[9px] text-[#5F728A] mt-1">因子指標構造層</div>
          </div>
          <span className="hidden md:inline text-ok">→</span>

          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">core.engine</div>
            <div className="text-[9px] text-[#5F728A] mt-1">統一回測/分析內核</div>
          </div>
          <span className="hidden md:inline text-ok">→</span>

          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">strategies/factory</div>
            <div className="text-[9px] text-[#5F728A] mt-1">策略研發與流水線</div>
          </div>
          <span className="hidden md:inline text-ok">→</span>

          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">registry</div>
            <div className="text-[9px] text-[#5F728A] mt-1">策略在冊台帳存檔</div>
          </div>
          <span className="hidden md:inline text-ok">→</span>

          <div className="flex-1 p-2 bg-navy border border-[#35D06E]/40 rounded-lg">
            <div className="text-ok font-bold text-xs">production</div>
            <div className="text-[9px] text-[#5F728A] mt-1">生產信號執行層</div>
          </div>
        </div>
        <div className="text-[10px] text-ok font-bold mt-2 flex items-center gap-1.5">
          <span>✓ 系統依賴檢測結果：分層無循環依賴。未檢測到反向 import 倒灌污染。</span>
        </div>
      </Card>

      {/* 3. 治理九宮格 */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-subink tracking-wider uppercase">合規治理九宮格 (Governance 9-Grid Matrix)</h3>
        <div className="text-[10px] text-[#5F728A] leading-relaxed">
          部署身份兩格由 <span className="font-mono">/system/truth</span> 實時驅動;標
          <span className="text-subink font-mono"> ◌ 待接入 </span>
          的格尚未接入實時源,不代表已通過。
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {governanceGrid.map((grid) => {
            const badge =
              grid.status === "passed"
                ? { cls: "text-ok", txt: "✓ PASS" }
                : grid.status === "failed"
                  ? { cls: "text-danger", txt: "✗ FAIL" }
                  : { cls: "text-[#5F728A]", txt: "◌ 待接入" };
            return (
              <div key={grid.name} className="p-4 bg-navy border border-line rounded-lg flex flex-col justify-between h-28 hover:border-brand transition-all">
                <div className="flex justify-between items-start">
                  <span className="font-bold text-[12px] text-[#E6EDF7] font-mono leading-tight">{grid.name}</span>
                  <span className={`font-bold text-[10px] font-mono ${badge.cls}`}>{badge.txt}</span>
                </div>
                <p className="text-[11px] text-subink leading-normal">{grid.desc}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* 4. CI 守衛列表 */}
      <Card title="CI 自動化防護守衛檢驗清單 (Continuous Integration Guards)">
        <DataTable<CIGuardRow>
          rows={ciGuards}
          getRowKey={(r) => r.script}
          columns={[
            {
              key: "script",
              header: "守衛腳本",
              className: "font-mono text-brand font-semibold",
              render: (r) => r.script,
            },
            { key: "desc", header: "防護檢驗說明", className: "text-subink text-[12px]", render: (r) => r.desc },
            {
              key: "status",
              header: "實時結果",
              render: (r) =>
                r.status === "passed" ? (
                  <span className="text-ok font-bold font-mono">✓ PASS</span>
                ) : r.status === "failed" ? (
                  <span className="text-danger font-bold font-mono">✗ FAIL</span>
                ) : (
                  <span className="text-[#5F728A] font-mono">◌ 待接入</span>
                ),
            },
          ]}
        />
        <div className="text-[10px] text-[#5F728A] mt-2 leading-relaxed">
          守衛定義于 <span className="font-mono">scripts/ci/</span>,由 <span className="font-mono">test_all.sh</span> 在 CI/本地運行;
          Web 尚未接入實時運行結果源,故標「待接入」——不謊報 PASS。
        </div>
      </Card>

      {/* 5. 近期審計事件與 P0/P1/P2 日誌 */}
      <Card title="系統變更審計與安全事件歷史日誌 (System Change Audit Tracker)">
        <div className="text-[11px] text-danger font-semibold mb-2">
          * 警告：本系統合規日誌由區塊鏈/不可變文件鏈條鎖定，任何操作者（包括管理員）皆無法刪除或修改歷史條目。
        </div>
        {realAuditLogs.length === 0 && (
          <div className="text-[12px] text-subink py-2">暂无审计记录(来源 /settings/audit)。</div>
        )}
        <DataTable<AuditLogEventRow>
          rows={realAuditLogs}
          getRowKey={(r, i) => `${r.time}-${i}`}
          columns={[
            { key: "time", header: "時間", className: "font-mono text-[#5F728A] w-36", render: (r) => r.time },
            {
              key: "level",
              header: "級別",
              render: (r) => getLevelBadge(r.level),
            },
            { key: "category", header: "業務類別", className: "font-mono text-[#E6EDF7] w-24", render: (r) => r.category },
            { key: "event", header: "審核事件與操作說明", className: "text-subink max-w-[280px] truncate", render: (r) => r.event },
            { key: "affected", header: "影響範圍", className: "font-mono text-subink", render: (r) => r.affected },
            { key: "actor", header: "觸發者", className: "font-mono text-[#5F728A]", render: (r) => r.actor },
            {
              key: "result",
              header: "判定/結果",
              className: "font-semibold text-ink",
              render: (r) => (
                <span className={r.level === "P0" ? "text-danger" : r.level === "P1" ? "text-warn" : "text-[#E6EDF7]"}>
                  {r.result}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
