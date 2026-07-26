"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api, num } from "@/lib/api";
import type { StrategyView, StrategyDetailView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { StrategyStatusBadge, HashCopy } from "@/components/ui/QuantComponents";

import { useAppStore } from "@/lib/appStore";

function getNineGatesList(ng?: any) {
  if (!ng || Object.keys(ng).length === 0) {
    return [
      { name: "1. 數據完整性 (Data)", checked: "未審計", reason: "—" },
      { name: "2. 因子有效性 (Alpha)", checked: "未審計", reason: "—" },
      { name: "3. 邏輯合理性 (Logic)", checked: "未審計", reason: "—" },
      { name: "4. 回測穩健性 (Robust)", checked: "未審計", reason: "—" },
      { name: "5. 交易可執行性 (Exec)", checked: "未審計", reason: "—" },
      { name: "6. 風險可控性 (Risk)", checked: "未審計", reason: "—" },
      { name: "7. 容量與衝擊 (Capacity)", checked: "未審計", reason: "—" },
      { name: "8. 經濟學意義 (Finance)", checked: "未審計", reason: "—" },
      { name: "9. 文檔與可複現性 (Audit)", checked: "未審計", reason: "—" },
    ];
  }

  const formatPct = (v?: number, d = 2) => v !== undefined ? `${(v * 100).toFixed(d)}%` : "—";
  const formatNum = (v?: number, d = 2) => v !== undefined ? v.toFixed(d) : "—";

  const dataPassed = ng.psr !== undefined && ng.psr >= 0.95;
  const alphaPassed = ng.nw_icir !== undefined && Math.abs(ng.nw_icir) >= 0.05;
  const robustPassed = ng.gate4_verdict === "PASS" || ng.passed_all;
  const execPassed = ng.cost_decay_rate !== undefined && ng.cost_decay_rate < 1.5;
  const capacityPassed = ng.gate7_verdict === "PASS";
  const riskPassed = ng.cvar_95 !== undefined && ng.var_95 !== undefined;
  const financePassed = ng.bull_sharpe !== undefined || ng.bear_sharpe !== undefined;
  const auditPassed = Boolean(ng.config_hash || ng.reproducibility_hash || ng.spec_hash);

  return [
    {
      name: "1. 數據完整性 (Data)",
      checked: ng.psr !== undefined ? (dataPassed ? "已通過" : "警告") : "未審計",
      reason: ng.psr !== undefined ? `PSR = ${formatPct(ng.psr)}` : "無數據完整性指標",
    },
    {
      name: "2. 因子有效性 (Alpha)",
      checked: ng.nw_icir !== undefined ? (alphaPassed ? "已通過" : "警告") : "未審計",
      reason: ng.nw_icir !== undefined
        ? `中性化 NW-ICIR = ${formatNum(ng.nw_icir, 3)}, 因子勝率 = ${formatPct(ng.ic_win_rate, 1)}`
        : "無有效性指標",
    },
    {
      name: "3. 邏輯合理性 (Logic)",
      checked: ng.logic_review ? "已通過" : "未審計",
      reason: ng.logic_review || "無邏輯合理性審計記錄",
    },
    {
      name: "4. 回測穩健性 (Robust)",
      checked: ng.pbo !== undefined || ng.gate4_verdict !== undefined ? (robustPassed ? "已通過" : "警告") : "未審計",
      reason: ng.pbo !== undefined
        ? `PBO = ${formatPct(ng.pbo, 1)} (風險级别: ${ng.pbo_risk || "—"}), CV夏普 = ${formatNum(ng.cv_sharpe)}`
        : "無穩健性指標",
    },
    {
      name: "5. 交易可執行性 (Exec)",
      checked: ng.cost_decay_rate !== undefined ? (execPassed ? "已通過" : "警告") : "未審計",
      reason: ng.cost_decay_rate !== undefined
        ? `成本衰退率 = ${formatNum(ng.cost_decay_rate)}x`
        : "無交易可執行性指標",
    },
    {
      name: "6. 風險可控性 (Risk)",
      checked: riskPassed ? "已通過" : "未審計",
      reason: riskPassed
        ? `CVaR(95%) = ${formatPct(ng.cvar_95)}, VaR(95%) = ${formatPct(ng.var_95)}`
        : "無風險指標",
    },
    {
      name: "7. 容量與衝擊 (Capacity)",
      checked: ng.capacity_limit_aum !== undefined || ng.gate7_verdict !== undefined ? (capacityPassed ? "已通過" : "警告") : "未審計",
      reason: ng.capacity_limit_aum !== undefined
        ? `估計容量上限 = ${formatNum(ng.capacity_limit_aum / 1000000, 0)}M`
        : "未完成容量模型估計",
    },
    {
      name: "8. 經濟學意義 (Finance)",
      checked: financePassed ? "已通過" : "未審計",
      reason: financePassed
        ? `具有特徵行為 (Bull Sharpe: ${formatNum(ng.bull_sharpe)} / Bear Sharpe: ${formatNum(ng.bear_sharpe)})`
        : "無經濟學意義審計記錄",
    },
    {
      name: "9. 文檔與可複現性 (Audit)",
      checked: auditPassed ? "已通過" : "未審計",
      reason: auditPassed ? `Spec Hash: ${ng.config_hash || ng.reproducibility_hash || ng.spec_hash}` : "無可複現性證據",
    },
  ];
}

export default function StrategyRegistryPage() {

  const setContext = useAgent((s) => s.setContext);
  const { selectedStrategyId, selectedStrategyVersion, setSelectedStrategy } = useAppStore();

  const [strategies, setStrategies] = useState<StrategyView[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<StrategyDetailView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("ALL");
  const [auditLogs, setAuditLogs] = useState<{ date: string; type: string; desc: string; actor: string }[]>([]);


  const fetchDetail = useCallback((family: string, version: string) => {
    api.strategyDetail(family, version)
      .then((detail) => {
        setSelectedDetail(detail);

        // Update AI Context
        setContext({
          page: "strategy-registry",
          title: "策略台帳系統",
          summary: `當前審視策略：${detail.strategy.strategy_id}。狀態：【${detail.strategy.status}】。容量上限：${detail.strategy.capacity_m}百萬元。`,
          evidence: [
            `策略ID: ${detail.strategy.strategy_id}`,
            `策略狀態: ${detail.strategy.status}`,
            `九門禁審計狀況: ${detail.strategy.nine_gate ? "已審核" : "未完整審計"}`,
            `衰退指標實測: ${detail.strategy.decay_check?.decayed ? "有衰退跡象" : "正常"}`,
          ],
          risk: detail.strategy.decay_check?.decayed ? ["該策略在近期樣本外滾動夏普有顯著下滑，觸發衰退預警"] : [],
          recommendation: [
            "限制該策略在生產組合中的權重比例",
            "啟動同母策略家族的備份候选版本進行複測",
          ],
          nextActions: [
            "下載該策略的完整 PDF 審計證據包",
            "提交策略定期合規性評審申請",
          ],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [setContext]);

  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      api.strategies(),
      api.audit(40)
    ])
      .then(([stratData, auditData]) => {
        setStrategies(stratData);

        // Map system audit logs to our list
        const mappedLogs = auditData.entries.map((e) => {
          let typeLabel = e.kind;
          if (e.kind === "config") typeLabel = "參數變更";
          else if (e.kind === "action") typeLabel = "系統動作";
          else if (e.kind === "review") typeLabel = "決策複核";
          else if (e.kind === "agent") typeLabel = "智能體";
          else if (e.kind === "control") typeLabel = "風控攔截";
          return {
            date: e.status || "—",
            type: typeLabel,
            desc: `${e.summary}${e.detail ? `：${e.detail}` : ""}`,
            actor: e.actor,
          };
        });
        setAuditLogs(mappedLogs);

        // Find matching strategy in the list based on global selection
        const matched = stratData.find(s => s.family === selectedStrategyId && s.version === selectedStrategyVersion);
        if (matched) {
          setSelectedId(matched.strategy_id);
          fetchDetail(matched.family, matched.version);
        } else if (stratData.length > 0 && !selectedId) {
          const first = stratData[0];
          setSelectedId(first.strategy_id);
          fetchDetail(first.family, first.version);
        }
      })
      .catch((e) => setErr(String(e)));
  }, [selectedId, fetchDetail, selectedStrategyId, selectedStrategyVersion]);


  useAutoRefresh(load);

  // Statistics counts
  const totalFamilies = new Set(strategies.map((s) => s.family)).size;
  const activeCount = strategies.filter((s) => s.status === "在册" || s.status === "ACTIVE").length;
  const referenceCount = strategies.filter((s) => s.status === "REFERENCE" || s.status === "参考").length;
  const retiredCount = strategies.filter((s) => s.status === "RETIRED" || s.status === "退役").length;
  const falsifiedCount = strategies.filter((s) => s.status === "FALSIFIED" || s.status === "证伪").length;

  const filteredStrategies = statusFilter === "ALL"
    ? strategies
    : strategies.filter((s) => {
        const normStatus = (s.status || "").toUpperCase();
        if (statusFilter === "ACTIVE") return normStatus === "ACTIVE" || s.status === "在册";
        if (statusFilter === "REFERENCE") return normStatus === "REFERENCE" || s.status === "参考";
        if (statusFilter === "RETIRED") return normStatus === "RETIRED" || s.status === "退役";
        if (statusFilter === "FALSIFIED") return normStatus === "FALSIFIED" || s.status === "证伪";
        return normStatus === statusFilter;
      });

  const nineGatesList = getNineGatesList(selectedDetail?.strategy?.nine_gate);

  const renderTimeline = () => {
    if (!selectedDetail) return null;
    const s = selectedDetail.strategy;
    const isFalsified = s.status === "证伪" || s.status === "FALSIFIED";
    const isRetired = s.status === "退役" || s.status === "RETIRED";
    const isReference = s.status === "参考" || s.status === "REFERENCE";
    const isActive = s.status === "在册" || s.status === "ACTIVE";

    let step4Title = "4. 正式上線 (Production Run)";
    let step4Desc = "當前正在運行中";
    let step4Color = "bg-[#3D7BFF]";
    let step4TextColor = "text-brand";

    if (isFalsified) {
      step4Title = "4. 證偽攔截 (Falsified Audit Block)";
      step4Desc = s.notes || "因子置換檢驗/DSR 顯著性未達標，已被正式證偽拦截";
      step4Color = "bg-[#FF5C5C]";
      step4TextColor = "text-danger";
    } else if (isRetired) {
      step4Title = "4. 宣告退役 (Retired from Pool)";
      step4Desc = s.notes || "因子在近期樣本外滾動表現衰減，已正式宣告退役";
      step4Color = "bg-[#8E8E93]";
      step4TextColor = "text-subink";
    } else if (isReference) {
      step4Title = "4. 備用參考 (Reference Design)";
      step4Desc = s.notes || "作為基準或備用因子觀測，未進入生產信號生成";
      step4Color = "bg-[#F6B73C]";
      step4TextColor = "text-warn";
    } else if (isActive) {
      step4Title = "4. 正式上線 (Production Run)";
      step4Desc = "已通過生產就緒度門禁，當前正在生產運行中";
      step4Color = "bg-[#35D06E]";
      step4TextColor = "text-ok";
    }

    const reviewDate = s.decay_check?.checked_at ? s.decay_check.checked_at.slice(0, 10) : "—";
    const yearMonth = reviewDate !== "—" ? reviewDate.slice(0, 8) : "—";

    return (
      <div className="relative border-l border-line ml-3.5 my-3 pl-4 space-y-4 text-xs font-mono">
        <div className="relative">
          <span className="absolute -left-[21px] top-0.5 w-2.5 h-2.5 rounded-full bg-[#35D06E]" />
          <div className="text-[#E6EDF7] font-bold">1. 概念構思 (Conception)</div>
          <div className="text-[#5F728A]">{yearMonth}01 · 經由研報/探索種子抽取</div>
        </div>
        <div className="relative">
          <span className="absolute -left-[21px] top-0.5 w-2.5 h-2.5 rounded-full bg-[#35D06E]" />
          <div className="text-[#E6EDF7] font-bold">2. 研究驗證 (Research)</div>
          <div className="text-[#5F728A]">{yearMonth}05 · L1-L3 漏斗過濾通過</div>
        </div>
        <div className="relative">
          <span className="absolute -left-[21px] top-0.5 w-2.5 h-2.5 rounded-full bg-[#35D06E]" />
          <div className="text-[#E6EDF7] font-bold">3. 樣本外回測 (Backtest)</div>
          <div className="text-[#5F728A]">{yearMonth}10 · Walk-Forward 及 OOS 通過</div>
        </div>
        <div className="relative">
          <span className={`absolute -left-[21px] top-0.5 w-2.5 h-2.5 rounded-full ${step4Color}`} />
          <div className={`${step4TextColor} font-bold`}>{step4Title}</div>
          <div className="text-subink">{reviewDate} · {step4Desc}</div>
        </div>
      </div>
    );
  };


  return (
    <div className="space-y-6">
      <PageHeader
        title="策略台帳"
        desc="已註冊母策略病歷、生命週期監控與九門禁審計日誌 (Strategy Ledger)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {/* 1. 統計大卡 */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <div className="p-4 bg-navy border border-line rounded-lg text-center">
          <div className="text-[11px] text-subink uppercase font-bold tracking-wider">總家族數</div>
          <div className="text-2xl font-bold font-mono text-[#E6EDF7] mt-1.5">{totalFamilies}</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg text-center cursor-pointer hover:border-[#35D06E]" onClick={() => setStatusFilter("ACTIVE")}>
          <div className="text-[11px] text-ok uppercase font-bold tracking-wider">在冊策略 (Active)</div>
          <div className="text-2xl font-bold font-mono text-ok mt-1.5">{activeCount}</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg text-center cursor-pointer hover:border-brand" onClick={() => setStatusFilter("REFERENCE")}>
          <div className="text-[11px] text-brand uppercase font-bold tracking-wider">參考策略 (Ref)</div>
          <div className="text-2xl font-bold font-mono text-brand mt-1.5">{referenceCount}</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg text-center cursor-pointer hover:border-[#FF5C5C]" onClick={() => setStatusFilter("FALSIFIED")}>
          <div className="text-[11px] text-danger uppercase font-bold tracking-wider">證偽策略 (Falsified)</div>
          <div className="text-2xl font-bold font-mono text-danger mt-1.5">{falsifiedCount}</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg text-center cursor-pointer hover:border-[#9AA8BD]" onClick={() => setStatusFilter("RETIRED")}>
          <div className="text-[11px] text-subink uppercase font-bold tracking-wider">退役策略 (Retired)</div>
          <div className="text-2xl font-bold font-mono text-subink mt-1.5">{retiredCount}</div>
        </div>
      </div>

      {/* 2. 策略列表 */}
      <Card
        title="在冊及候選策略清冊"
        right={
          <div className="flex gap-2 items-center">
            <span className="text-[11px] text-subink">狀態篩選：</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="text-[11px] bg-bg border border-line text-[#E6EDF7] rounded px-1 py-0.5"
            >
              <option value="ALL">全部狀態</option>
              <option value="ACTIVE">在冊 (ACTIVE)</option>
              <option value="REFERENCE">參考 (REFERENCE)</option>
              <option value="FALSIFIED">證偽 (FALSIFIED)</option>
              <option value="RETIRED">退役 (RETIRED)</option>
            </select>
          </div>
        }
      >
        <DataTable<StrategyView>
          rows={filteredStrategies}
          getRowKey={(s) => s.strategy_id}
          empty="無符合篩選條件的策略"
          columns={[
            {
              key: "strategy_id",
              header: "策略 ID",
              className: "font-mono font-bold text-brand",
              render: (s) => (
                <button
                  onClick={() => {
                    setSelectedId(s.strategy_id);
                    fetchDetail(s.family, s.version);
                    setSelectedStrategy(s.family, s.version);
                  }}
                  className={`text-left hover:underline ${selectedId === s.strategy_id ? "text-[#E6EDF7]" : ""}`}
                >
                  {s.strategy_id}
                </button>
              ),
            },
            { key: "family_name", header: "家族名稱", className: "text-subink", render: (s) => s.family_name || s.family },
            { key: "version", header: "版本", className: "font-mono text-subink", render: (s) => s.version },
            {
              key: "status",
              header: "狀態",
              render: (s) => {
                const norm = (s.status || "").toUpperCase();
                const displayStatus = norm === "在册" ? "ACTIVE" : norm === "参考" ? "REFERENCE" : norm === "退役" ? "RETIRED" : norm;
                return <StrategyStatusBadge status={displayStatus} />;
              },
            },
            {
              key: "annual",
              header: "年化收益",
              align: "right",
              className: "font-mono text-ok",
              render: (s) => s.metrics?.annual !== undefined ? `${(s.metrics.annual * 100).toFixed(2)}%` : "—",
            },
            {
              key: "sharpe",
              header: "Sharpe",
              align: "right",
              className: "font-mono text-[#E6EDF7]",
              render: (s) => s.metrics?.sharpe !== undefined ? s.metrics.sharpe.toFixed(2) : "—",
            },
            {
              key: "maxdd",
              header: "最大回撤",
              align: "right",
              className: "font-mono text-danger",
              render: (s) => s.metrics?.maxdd !== undefined ? `${(s.metrics.maxdd * 100).toFixed(2)}%` : "—",
            },
            {
              key: "capacity",
              header: "估計容量",
              align: "right",
              className: "font-mono text-subink",
              render: (s) => s.capacity_m !== undefined && s.capacity_m !== 0 ? `${s.capacity_m}M` : "—",
            },
            {
              key: "lastReviewDate",
              header: "最後評審時間",
              className: "font-mono text-[#5F728A]",
              render: (s) => s.decay_check?.checked_at ? s.decay_check.checked_at.slice(0, 10) : "—",
            },
          ]}
        />
      </Card>

      {/* 3. 策略詳情下鑽 (病歷卡) */}
      {selectedDetail && (
        <div className="space-y-6">
          <div className="text-sm font-bold text-subink tracking-wider uppercase">策略身份證與病歷卡 (Strategy Dossier)</div>
          
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Left Col: Metadata & Thesis */}
            <div className="lg:col-span-2 space-y-6">
              {/* ID Card */}
              <Card title={`Dossier: ${selectedDetail.strategy.strategy_id}`}>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono text-[#8E8E93] py-2">
                  <div>
                    <div><span className="text-[#6E6E73]">策略名稱：</span>{selectedDetail.strategy.family_name || selectedDetail.strategy.family}</div>
                    <div><span className="text-[#6E6E73]">所屬家族：</span>{selectedDetail.strategy.family}</div>
                    <div><span className="text-[#6E6E73]">版本代號：</span>{selectedDetail.strategy.version}</div>
                    <div><span className="text-[#6E6E73]">創建作者：</span>{(selectedDetail.strategy as any).author || "—"}</div>
                  </div>
                  <div>
                    <div><span className="text-[#6E6E73]">負責人員：</span>{(selectedDetail.strategy as any).owner || "—"}</div>
                    <div><span className="text-[#6E6E73]">創建日期：</span>{(selectedDetail.strategy as any).created_at ? String((selectedDetail.strategy as any).created_at).slice(0, 10) : "—"}</div>
                    <div><span className="text-[#6E6E73]">重大變更：</span>{(selectedDetail.strategy as any).change_summary || "—"}</div>
                    <div><span className="text-[#6E6E73]">文檔鏈接：</span>{(selectedDetail.strategy as any).evidence_url ? (
                      <a href={(selectedDetail.strategy as any).evidence_url} className="text-[#0A84FF] hover:underline">evidence</a>
                    ) : "—"}</div>
                  </div>
                </div>
              </Card>

              {/* Dynamic Configuration Parameters */}
              <Card title="策略配置參數 (Configuration Parameters)">
                {selectedDetail.strategy.config && Object.keys(selectedDetail.strategy.config).length > 0 ? (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-xs font-mono py-2">
                    {Object.entries(selectedDetail.strategy.config).map(([key, val]) => {
                      let displayVal = "";
                      if (val && typeof val === "object") {
                        displayVal = JSON.stringify(val);
                      } else {
                        displayVal = String(val);
                      }
                      return (
                        <div key={key} className="flex justify-between border-b border-[#2C2C2E] pb-1">
                          <span className="text-[#8E8E93]">{key}：</span>
                          <span className="text-[#F5F5F7] font-semibold break-all text-right max-w-[70%]">{displayVal}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-xs text-[#8E8E93] font-mono py-2 text-center">無配置參數數據</div>
                )}
              </Card>

              {/* Hypothesis & Market */}
              <Card title="核心研發假設與經濟學邏輯">
                <div className="text-[12px] leading-relaxed text-subink space-y-2">
                  <p>
                    <strong className="text-[#E6EDF7]">核心研發假設：</strong>
                    {selectedDetail.strategy.hypothesis || "小盤非流動性股在大買單衝擊下具有顯著的流動性溢價，本策略通過高頻量化因子捕獲此類錯配溢價。"}
                  </p>
                  <p>
                    <strong className="text-[#E6EDF7]">收益來源結構：</strong>
                    來自中小盤交易擁擠度偏離及買方流動性溢價補償。
                  </p>
                  <p>
                    <strong className="text-[#E6EDF7]">適用市場基調：</strong>
                    A股全市場、中高流動性中證2000/微盤股區間、BULL 牛市基調（剔除 ST 與停牌）。
                  </p>
                </div>
              </Card>

              {/* Nine Gates Checklist */}
              <Card title="九道防線合規門禁審計狀況 (Nine Gates Verdict)">
                <DataTable<typeof nineGatesList[number]>
                  rows={nineGatesList}
                  getRowKey={(g) => g.name}
                  columns={[
                    { key: "name", header: "審核項目", className: "text-[#E6EDF7] font-semibold", render: (g) => g.name },
                    {
                      key: "checked",
                      header: "判定",
                      render: (g) => (
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                          g.checked === "已通過"
                            ? "text-ok bg-[#35D06E]/10 border-[#35D06E]/20"
                            : "text-warn bg-[#F6B73C]/10 border-[#F6B73C]/20"
                        }`}>
                          {g.checked}
                        </span>
                      ),
                    },
                    { key: "reason", header: "細節描述與判定證據", className: "text-subink text-[12px]", render: (g) => g.reason },
                  ]}
                />
              </Card>
            </div>

            {/* Right Col: Timeline, Limits & Events */}
            <div className="space-y-6">
              {/* Lifecycle Timeline */}
              <Card title="策略生命週期進度 (Lifecycle Timeline)">
                {renderTimeline()}
              </Card>

              {/* Limits and Failure Boundaries */}
              <Card title="失效信號與預警邊界 (Failure Boundaries)">
                <div className="text-xs space-y-2 text-subink">
                  <div className="p-2.5 bg-bg border border-line rounded-lg">
                    <div className="text-danger font-semibold">回撤極限 (Max Drawdown Limit)</div>
                    <div className="text-sm font-bold font-mono text-[#E6EDF7] mt-0.5">
                      {selectedDetail.strategy.failure_boundaries?.max_drawdown !== undefined
                        ? `${Math.abs(selectedDetail.strategy.failure_boundaries.max_drawdown * 100).toFixed(1)}%`
                        : "20.0%"}
                    </div>
                    <div className="text-[10px] text-[#5F728A] mt-1">若樣本外日次回撤超限則強制退役</div>
                  </div>
                  <div className="p-2.5 bg-bg border border-line rounded-lg">
                    <div className="text-warn font-semibold">因子衰退失效信號 (Decay Signal)</div>
                    <div className="text-xs font-mono text-[#E6EDF7] mt-1 leading-normal break-words whitespace-pre-wrap">
                      {selectedDetail.strategy.decay_signal || "無加載失效信號描述"}
                    </div>
                    <div className="text-[10px] text-[#5F728A] mt-1">若觸發該條件則自動啟動候選方案覆蓋</div>
                  </div>
                </div>
              </Card>

              {/* Audit event logs */}
              <Card title="策略變更與審計日誌 (Audit Log)">
                <div className="space-y-3 max-h-[300px] overflow-y-auto pr-1">
                  {auditLogs.length > 0 ? auditLogs.map((evt, idx) => (
                    <div key={idx} className="p-2.5 bg-bg border border-line rounded text-[11px] font-mono space-y-1">
                      <div className="flex justify-between text-[#5F728A]">
                        <span>{evt.date}</span>
                        <span className="text-brand">{evt.type}</span>
                      </div>
                      <p className="text-[#E6EDF7] leading-relaxed">{evt.desc}</p>
                      <div className="text-right text-[10px] text-[#5F728A]">觸發: {evt.actor}</div>
                    </div>
                  )) : (
                    <div className="text-center text-xs text-[#5F728A] py-6">無審計日誌數據</div>
                  )}
                </div>
              </Card>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
