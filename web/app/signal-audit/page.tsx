"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api, num } from "@/lib/api";
import type { TradePlanView, TradeReadinessView, StrategyDetailView, SystemConfigView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { HashCopy, PipelineStepper, RiskBadge } from "@/components/ui/QuantComponents";
import { useAppStore } from "@/lib/appStore";

type CandidateRow = {
  rank: number;
  code: string;
  name: string;
  score: number | null;
  amount: number;
  industry: string | null;
  isSt: boolean;
  reason: string;
  sizeExposure: number | null;
  valueExposure: number | null;
  momentumExposure: number | null;
};

export default function SignalAuditPage() {
  const setContext = useAgent((s) => s.setContext);
  const { selectedStrategyId, selectedStrategyVersion } = useAppStore();

  const [paperPlan, setPaperPlan] = useState<TradePlanView | null>(null);
  const [readiness, setReadiness] = useState<TradeReadinessView | null>(null);
  const [strategyDetail, setStrategyDetail] = useState<StrategyDetailView | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfigView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      api.paperPlan(),
      api.tradeReadiness(),
      api.strategyDetail(selectedStrategyId, selectedStrategyVersion),
      api.systemConfig()
    ])
      .then(([pp, tr, sd, sc]) => {
        setPaperPlan(pp);
        setReadiness(tr);
        setStrategyDetail(sd);
        setSystemConfig(sc);

        const dataScope = sd?.strategy?.data_scope;
        const reproducibility = typeof dataScope === "object" && dataScope !== null 
          ? (dataScope as any).reproducibility 
          : null;
        const specHash = sd?.strategy?.nine_gate?.config_hash || reproducibility?.git_commit || "—";

        const activeFamily = (sc?.strategy?.family as string) || "illiquidity";
        const activeVersion = (sc?.strategy?.version as string) || "v3.1";
        const isProd = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;
        const hasCurrentExecutableSignal = isProd && !pp.stale && tr.allowed_to_trade;

        setContext({
          page: "signal-audit",
          title: "信號審計系統",
          summary: isProd 
            ? `當前信號審計：${hasCurrentExecutableSignal ? "【通過】" : "【非现行可执行】"}。已鎖定 Spec Hash: ${specHash}。`
            : `當前策略為非運行生產策略。生產運行中主策略為: ${activeFamily} ${activeVersion}。`,
          evidence: isProd ? [
            `信號發布狀態: ${hasCurrentExecutableSignal ? "正式發布" : pp.stale ? `stale/阻塞: ${pp.stale_reason}` : "草稿/阻塞"}`,
            `選股管道漏斗: ${pp.candidates?.length ?? 0} 只候選 -> ${pp.plan?.length ?? 0} 只交易項目`,
            `大週期狀態: ${pp.regime || "—"}`,
            `數據集指紋: ${reproducibility?.data_snapshot ? "git_" + reproducibility.git_commit : "—"}`,
          ] : [
            `當前選定策略: ${selectedStrategyId} ${selectedStrategyVersion}`,
            `生產運行策略: ${activeFamily} ${activeVersion}`,
            `此非生產策略無每日實盤信號生成`,
          ],
          risk: isProd ? (hasCurrentExecutableSignal ? [] : [pp.stale ? "纸面信号已过期,非现行可执行" : "信號生成被生產門禁攔截"]) : [],
          recommendation: isProd 
            ? (hasCurrentExecutableSignal ? ["核對在冊代碼版本的 Git commit 標籤", "執行信號重新生成校驗"] : ["不要按此信號下單", "先解除 stale/門禁阻塞再恢復展示執行清單"])
            : ["如需查看實盤信號與審計，請在下拉選單切換至生產主策略"],
          nextActions: isProd 
            ? (hasCurrentExecutableSignal ? ["前往「系統治理」下載本次審計證據包", "比對回測口徑與真實執行偏差"] : ["前往「今日操作台」查看阻塞原因", "等待新信號或調整部署清單"])
            : ["前往「策略實驗室」或「因子研究」查看該策略的歷史回測與九門禁審計指標"],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [selectedStrategyId, selectedStrategyVersion, setContext]);

  useAutoRefresh(load);

  const activeFamily = (systemConfig?.strategy?.family as string) || "illiquidity";
  const activeVersion = (systemConfig?.strategy?.version as string) || "v3.1";
  const isProductionStrategy = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;
  const hasCurrentExecutableSignal =
    isProductionStrategy && !!paperPlan && !paperPlan.stale && !!readiness?.allowed_to_trade;
  const nonExecutableSignalReason =
    isProductionStrategy && paperPlan?.stale
      ? "信号已过期,非现行可执行"
      : isProductionStrategy && readiness && !readiness.allowed_to_trade
      ? "生产门禁已拦截,非现行可执行"
      : "当前无现行可执行信号";
  const suggestedActionText = isProductionStrategy
    ? paperPlan?.stale
      ? "信号已过期,非现行可执行"
      : readiness?.allowed_to_trade
      ? (paperPlan?.action || "—")
      : "生产门禁已拦截,非现行可执行"
    : "—";
  const releaseStatusText = paperPlan?.stale ? "已阻塞 (stale)" : readiness?.allowed_to_trade ? "正式發布" : "已阻塞 (草稿)";

  // Steps for PipelineStepper derived dynamically
  const candidatesCount = paperPlan?.candidates?.length ?? 0;
  const planCount = paperPlan?.plan?.length ?? 0;

  const pipelineSteps = hasCurrentExecutableSignal ? [
    { name: "1. 原始候選池", count: "—", desc: "A股全市場覆蓋", status: "completed" as const },
    { name: "2. 否決器過濾", count: "—", desc: "排除 ST / 高風險", status: "completed" as const },
    { name: "3. 因子打分排序", count: candidatesCount > 0 ? `${candidatesCount} 只` : "—", desc: "策略選股成員", status: "active" as const },
    { name: "4. 執行調倉計劃", count: planCount > 0 ? `${planCount} 只` : "—", desc: "當前調倉執行項", status: planCount > 0 ? ("completed" as const) : ("pending" as const) },
  ] : isProductionStrategy ? [
    { name: "1. 原始候選池", count: "—", desc: nonExecutableSignalReason, status: "warning" as const },
    { name: "2. 否決器過濾", count: "—", desc: nonExecutableSignalReason, status: "warning" as const },
    { name: "3. 因子打分排序", count: "—", desc: nonExecutableSignalReason, status: "warning" as const },
    { name: "4. 執行調倉計劃", count: "—", desc: nonExecutableSignalReason, status: "warning" as const },
  ] : [
    { name: "1. 原始候選池", count: "—", desc: "A股全市場覆蓋", status: "pending" as const },
    { name: "2. 否決器過濾", count: "—", desc: "排除 ST / 高風險", status: "pending" as const },
    { name: "3. 因子打分排序", count: "—", desc: "策略選股成員", status: "pending" as const },
    { name: "4. 執行調倉計劃", count: "—", desc: "當前調倉執行項", status: "pending" as const },
  ];

  // Candidates list mapping dynamically
  const candidateRows: CandidateRow[] = [];
  if (hasCurrentExecutableSignal) {
    if (paperPlan?.plan && paperPlan.plan.length > 0) {
      paperPlan.plan.forEach((item, index) => {
        const isStAsset = item.name.includes("ST") || item.name.includes("*ST");
        candidateRows.push({
          rank: index + 1,
          code: item.code,
          name: item.name,
          score: null,
          amount: item.est_notional,
          industry: null,
          isSt: isStAsset,
          reason: item.action === "BUY" ? "策略買入信號" : "策略賣出信號",
          sizeExposure: null,
          valueExposure: null,
          momentumExposure: null,
        });
      });
    } else if (paperPlan?.candidates) {
      paperPlan.candidates.forEach((c, index) => {
        candidateRows.push({
          rank: index + 1,
          code: c.code,
          name: c.name,
          score: null,
          amount: 0,
          industry: null,
          isSt: c.name.includes("ST") || c.name.includes("*ST"),
          reason: "策略候選",
          sizeExposure: null,
          valueExposure: null,
          momentumExposure: null,
        });
      });
    }
  }

  // Execution risks data calculated dynamically
  const blockedList = paperPlan?.blocked || [];
  const getRiskCount = (keyword: string) => blockedList.filter(b => b.reason.includes(keyword)).length;
  const totalCandidates = paperPlan?.candidates?.length || 25;
  const getRiskRatioStr = (count: number) => {
    if (totalCandidates === 0) return "0.0%";
    return `${((count / totalCandidates) * 100).toFixed(1)}%`;
  };

  const executionRisks = hasCurrentExecutableSignal ? [
    { 
      riskType: "漲停買不進", 
      count: getRiskCount("涨停"), 
      ratio: getRiskRatioStr(getRiskCount("涨停")), 
      amount: getRiskCount("涨停") > 0 ? "¥" + (getRiskCount("涨停") * 10000).toLocaleString() : "¥0", 
      action: getRiskCount("涨停") > 0 ? "觸發人工覆核，轉入影子防守債" : "正常" 
    },
    { 
      riskType: "跌停賣不出", 
      count: getRiskCount("跌停"), 
      ratio: getRiskRatioStr(getRiskCount("跌停")), 
      amount: "¥0", 
      action: "正常" 
    },
    { 
      riskType: "停牌 / 臨時停牌", 
      count: getRiskCount("停牌"), 
      ratio: getRiskRatioStr(getRiskCount("停牌")), 
      amount: getRiskCount("停牌") > 0 ? "¥" + (getRiskCount("停牌") * 9500).toLocaleString() : "¥0", 
      action: getRiskCount("停牌") > 0 ? "扣除今日換手額度，ffill 填補" : "正常" 
    },
    { 
      riskType: "一字板限制", 
      count: getRiskCount("一字"), 
      ratio: getRiskRatioStr(getRiskCount("一字")), 
      amount: "¥0", 
      action: "正常" 
    },
  ] : [
    { riskType: "漲停買不進", count: "—", ratio: "—", amount: "—", action: isProductionStrategy ? nonExecutableSignalReason : "非生產運行策略，不執行評估" },
    { riskType: "跌停賣不出", count: "—", ratio: "—", amount: "—", action: isProductionStrategy ? nonExecutableSignalReason : "非生產運行策略，不執行評估" },
    { riskType: "停牌 / 臨時停牌", count: "—", ratio: "—", amount: "—", action: isProductionStrategy ? nonExecutableSignalReason : "非生產運行策略，不執行評估" },
    { riskType: "一字板限制", count: "—", ratio: "—", amount: "—", action: isProductionStrategy ? nonExecutableSignalReason : "非生產運行策略，不執行評估" },
  ];

  const dataScope = strategyDetail?.strategy?.data_scope;
  const reproducibility = typeof dataScope === "object" && dataScope !== null 
    ? (dataScope as any).reproducibility 
    : null;
  const specHash = strategyDetail?.strategy?.nine_gate?.config_hash || reproducibility?.git_commit || "—";
  const dataFingerprint = reproducibility?.data_snapshot ? "lake_" + reproducibility.git_commit : "—";

  return (
    <div className="space-y-6">
      <PageHeader
        title="信號審計"
        desc="選股流水線下鑽、否決原因回溯與信號發布 Spec 鎖定 (Signal Generation Audit Log)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-[#FF453A]">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {!isProductionStrategy && (
        <div className="p-4 bg-[#BF5AF2]/10 border border-[#BF5AF2]/20 rounded-lg text-sm text-[#BF5AF2] flex items-start gap-2.5">
          <span className="text-sm mt-0.5">⚠️</span>
          <div>
            <div className="font-bold text-[#F5F5F7] text-[13px]">即時信號審計受限</div>
            <div className="text-[#8E8E93] text-[12px] mt-1 leading-relaxed">
              當前選取的策略 <span className="font-mono text-[#0A84FF] font-semibold">{selectedStrategyId} {selectedStrategyVersion}</span> 非生產部署狀態。
              即時信號審計與交易執行清單僅對生產運行中的主策略（當前：<span className="font-mono text-[#F5F5F7] font-semibold">{activeFamily} {activeVersion}</span>）開放，以防止多策略口徑信號數據混淆。
            </div>
          </div>
        </div>
      )}

      {/* 1. 信號身份卡 */}
      <Card title="信號身份與部署元數據 (Signal Identity Fingerprint)">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-[12px] py-2">
          <div className="space-y-2">
            <div>
              <span className="text-[#8E8E93]">信號日期：</span>
              <span className="font-bold text-[#F5F5F7] font-mono">{isProductionStrategy ? (paperPlan?.signal_date || "—") : "—"}</span>
            </div>
            <div>
              <span className="text-[#8E8E93]">大周期狀態：</span>
              <span className="font-bold text-[#F5F5F7]">
                {isProductionStrategy
                  ? paperPlan?.stale
                    ? `历史状态 ${paperPlan?.regime === "bear" ? "BEAR" : "BULL"}(非现行可执行)`
                    : (paperPlan?.regime === "bear" ? "🔴 BEAR (避險)" : "🟢 BULL (運行)")
                  : "—"}
              </span>
            </div>
            <div>
              <span className="text-[#8E8E93]">建議動作：</span>
              <span className="text-[#0A84FF] font-bold">{suggestedActionText}</span>
            </div>
          </div>

          <div className="space-y-2">
            <div>
              <span className="text-[#8E8E93]">發布狀態：</span>
              {isProductionStrategy ? (
                <span className={`px-2 py-0.5 rounded text-[10px] border font-bold ${
                  hasCurrentExecutableSignal
                    ? "text-[#30D158] bg-[#30D158]/10 border-[#30D158]/20"
                    : "text-[#FF453A] bg-[#FF453A]/10 border-[#FF453A]/20"
                }`}>
                  {releaseStatusText}
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded text-[10px] border font-bold text-[#8E8E93] bg-[#8E8E93]/10 border-[#8E8E93]/20">
                  非生產部署
                </span>
              )}
            </div>
            <div>
              <span className="text-[#8E8E93]">部署 ID：</span>
              <span className="text-[#F5F5F7] font-mono">
                {isProductionStrategy ? `deploy_${paperPlan?.signal_date ? paperPlan.signal_date.replace(/-/g, "") : "unknown"}_${selectedStrategyVersion}` : "—"}
              </span>
            </div>
            <div>
              <span className="text-[#8E8E93]">策略版本：</span>
              <span className="text-[#F5F5F7] font-mono">{selectedStrategyId} {selectedStrategyVersion}</span>
            </div>
          </div>

          <div className="space-y-3 flex flex-col items-start justify-center">
            <HashCopy label="Spec Hash" value={specHash} />
            <HashCopy label="數據指纹" value={dataFingerprint} />
          </div>
        </div>
      </Card>

      {/* 2. 信號生成流水線 */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-subink tracking-wider uppercase">信號生成流水線 (Funnel Pipeline)</h3>
        <div className="bg-navy border border-line rounded-lg p-4">
          <PipelineStepper steps={pipelineSteps} />
        </div>
      </div>

      {/* 3. Top-25 執行清單表 */}
      <Card
        title="Top-25 策略執行清單 (Top-25 Members)"
        right={
          <button
            onClick={() => alert("開始匯出今日審計報告...")}
            className="px-2.5 py-1 text-[11px] bg-[#3D7BFF] hover:bg-[#3D7BFF]/80 text-white rounded font-bold cursor-pointer"
          >
            📥 匯出審計報告
          </button>
        }
      >
        <div className="text-[11px] text-subink mb-2">點擊行可展開查看詳細因子貢獻分解 (Factor Betas Attribution)</div>
        <DataTable<CandidateRow>
          rows={candidateRows}
          getRowKey={(r) => r.code}
          empty={isProductionStrategy ? nonExecutableSignalReason : "非運行中生產策略無即時交易信號數據。請切換至生產主策略以查看信號審計。"}
          columns={[
            {
              key: "rank",
              header: "排名",
              className: "font-mono font-bold text-subink w-12",
              render: (r) => r.rank,
            },
            {
              key: "code",
              header: "代碼",
              className: "font-mono text-brand",
              render: (r) => r.code,
            },
            {
              key: "name",
              header: "名稱",
              className: "text-[#E6EDF7] font-semibold",
              render: (r) => r.name,
            },
            {
              key: "score",
              header: "綜合得分",
              align: "right",
              className: "font-mono text-subink",
              render: (r) => r.score !== null ? r.score.toFixed(4) : "—",
            },
            {
              key: "amount",
              header: "建議交易金額 (RMB)",
              align: "right",
              className: "font-mono text-[#E6EDF7]",
              render: (r) => `¥${r.amount.toLocaleString()}`,
            },
            {
              key: "industry",
              header: "行業",
              className: "text-subink",
              render: (r) => r.industry || "—",
            },
            {
              key: "isSt",
              header: "ST 狀態",
              render: (r) => (
                <span className={r.isSt ? "text-[#FF453A]" : "text-[#30D158]"}>
                  {r.isSt ? "ST" : "正常"}
                </span>
              ),
            },
            {
              key: "reason",
              header: "核心理由 (Contribution)",
              className: "text-[#30D158] font-medium",
              render: (r) => r.reason,
            },
            {
              key: "actions",
              header: "操作",
              render: (r) => (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedRow(expandedRow === r.code ? null : r.code);
                  }}
                  className="text-xs text-[#0A84FF] hover:underline"
                >
                  {expandedRow === r.code ? "收起" : "下鑽"}
                </button>
              ),
            },
          ]}
        />

        {/* Selected row details (下鑽) */}
        {expandedRow && (() => {
          const matched = candidateRows.find((c) => c.code === expandedRow);
          if (!matched) return null;
          return (
            <div className="mt-4 p-4 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg text-xs space-y-3 font-mono animate-fadeIn">
              <div className="text-sm font-semibold text-[#F5F5F7] border-b border-[#2C2C2E] pb-1.5 flex justify-between">
                <span>📊 因子分解歸因 — {matched.name} ({matched.code})</span>
                <button onClick={() => setExpandedRow(null)} className="text-[#FF453A] hover:underline text-[10px]">✕ 關閉</button>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className="space-y-1">
                  <div className="text-[#8E8E93]">市值暴露 (Size Beta):</div>
                  <div className={`text-sm font-bold ${matched.sizeExposure !== null ? (matched.sizeExposure < 0 ? "text-[#30D158]" : "text-[#FF453A]") : "text-[#8E8E93]"}`}>
                    {matched.sizeExposure !== null ? `${matched.sizeExposure} std (${matched.sizeExposure < 0 ? "偏向小盤，溢價高" : "偏向大盤"})` : "—"}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-[#8E8E93]">估值暴露 (Value Beta):</div>
                  <div className="text-sm font-bold text-[#0A84FF]">
                    {matched.valueExposure !== null ? `${matched.valueExposure} std (低估值溢價)` : "—"}
                  </div>
                </div>
                <div className="space-y-1">
                  <div className="text-[#8E8E93]">動量暴露 (Momentum Beta):</div>
                  <div className={`text-sm font-bold ${matched.momentumExposure !== null ? (matched.momentumExposure >= 0 ? "text-[#30D158]" : "text-[#FF453A]") : "text-[#8E8E93]"}`}>
                    {matched.momentumExposure !== null ? `${matched.momentumExposure} std` : "—"}
                  </div>
                </div>
              </div>
            </div>
          );
        })()}
      </Card>

      {/* 4. 執行風險與可行性 */}
      <Card title="今日交易執行可行性評估 (Execution Feasibility & Constraints)">
        <DataTable<typeof executionRisks[number]>
          rows={executionRisks}
          getRowKey={(r) => r.riskType}
          columns={[
            {
              key: "riskType",
              header: "風險特徵",
              className: "text-[#E6EDF7] font-bold",
              render: (r) => r.riskType,
            },
            {
              key: "count",
              header: "異常證券數",
              align: "right",
              className: "font-mono text-warn",
              render: (r) => r.count,
            },
            {
              key: "ratio",
              header: "占比",
              align: "right",
              className: "font-mono text-subink",
              render: (r) => r.ratio,
            },
            {
              key: "amount",
              header: "預估影響金額",
              align: "right",
              className: "font-mono text-[#E6EDF7]",
              render: (r) => r.amount,
            },
            {
              key: "action",
              header: "執行備份動作 (Mitigation Action)",
              className: "text-subink text-[12px]",
              render: (r) => r.action,
            },
          ]}
        />
      </Card>
    </div>
  );
}
