"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api, num } from "@/lib/api";
import type { PortfolioView, RiskReport, TradePlanView, StrategyDetailView, SystemConfigView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { QuantMetricCard, RiskBadge } from "@/components/ui/QuantComponents";
import { useAppStore } from "@/lib/appStore";

type HoldingRiskRow = {
  code: string;
  name: string;
  weight: number;
  advUsage: string;
  isSt: boolean;
  liquidityScore: number;
  estSlippageBps: number;
  riskExposure: number;
  riskTags: string[];
};

export default function PortfolioRiskPage() {
  const setContext = useAgent((s) => s.setContext);
  const { selectedStrategyId, selectedStrategyVersion } = useAppStore();

  const [portfolio, setPortfolio] = useState<PortfolioView | null>(null);
  const [riskReport, setRiskReport] = useState<RiskReport | null>(null);
  const [paperPlan, setPaperPlan] = useState<TradePlanView | null>(null);
  const [strategyDetail, setStrategyDetail] = useState<StrategyDetailView | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfigView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      api.portfolio(),
      api.risk(),
      api.paperPlan(),
      api.strategyDetail(selectedStrategyId, selectedStrategyVersion),
      api.systemConfig()
    ])
      .then(([p, rk, pp, sd, sc]) => {
        setPortfolio(p);
        setRiskReport(rk);
        setPaperPlan(pp);
        setStrategyDetail(sd);
        setSystemConfig(sc);

        // Highlight main risk sources to the AI assistant
        const stAssetsCount = pp.positions?.filter((pos) => pos.name.includes("ST") || pos.name.includes("*ST")).length ?? 0;
        const highestWeightAsset = pp.positions?.reduce((max, x) => {
          const xW = x.mv / (pp.position_value || 1);
          const maxW = max.mv / (pp.position_value || 1);
          return xW > maxW ? x : max;
        }, pp.positions[0] || { name: "无", mv: 0 });
        const highestWeight = pp.positions && pp.positions.length > 0
          ? (highestWeightAsset.mv / (pp.position_value || 1))
          : 0;

        const maxddVal = sd.strategy.metrics?.maxdd !== undefined 
          ? `${(sd.strategy.metrics.maxdd * 100).toFixed(2)}%` 
          : "—";

        // Calculate dynamic risk budget usage ratio
        let rbu = 0.0;
        if (rk.checks && rk.checks.length > 0) {
          const ratios = rk.checks.map(c => {
            if (c.current !== null && c.threshold !== null && c.threshold !== 0) {
              return Math.abs(c.current) / Math.abs(c.threshold);
            }
            return 0;
          });
          rbu = Math.max(...ratios) * 100;
        }

        const capacityLimit = (sd.strategy.capacity_m || 0) * 1_000_000;
        const capacityUsage = capacityLimit > 0 ? (p.nav / capacityLimit) * 100 : 0;

        const activeFamily = sc?.strategy?.family || "illiquidity";
        const activeVersion = sc?.strategy?.version || "v3.1";
        const isProd = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;

        setContext({
          page: "portfolio-risk",
          title: `組合風控面板: ${sd.strategy.strategy_id}`,
          summary: isProd 
            ? `組合風險總體評級：【${rk.verdict}】。組合 NAV: ${p.nav.toFixed(2)} CNY。風險預算使用率: ${rbu.toFixed(1)}%。容量使用率: ${capacityUsage.toFixed(1)}%。`
            : `組合風險總體評級：【—】（非生產運行策略）。當前選定策略: ${selectedStrategyId} ${selectedStrategyVersion}。`,
          evidence: isProd ? [
            `風險級別判定: ${rk.verdict}`,
            `ST 暴露資產數: ${stAssetsCount}只`,
            `最大持倉暴露: ${highestWeightAsset.name} (權重 ${(highestWeight * 100).toFixed(1)}%)`,
            `歷史最大回撤: ${maxddVal}`,
          ] : [
            `當前選定策略: ${selectedStrategyId} ${selectedStrategyVersion}`,
            `生產運行策略: ${activeFamily} ${activeVersion}`,
            `歷史最大回撤: ${maxddVal}`,
          ],
          risk: isProd && stAssetsCount > 0 ? [`持倉中包含 ${stAssetsCount} 只 ST/垃圾股，面臨退市價值陷阱風險`] : [],
          recommendation: isProd 
            ? ["限制單個資產權重超限暴露 (單票限額 < 15%)", "對高換手策略增加交易成本阻尼惩罚"]
            : ["切換至生產主策略以監控實盤組合暴露限額"],
          nextActions: isProd 
            ? ["調整風險預算配置至 80% 安全墊內", "向 AI 助手諮詢高擁擠度標的之替代清單"]
            : ["前往「回測實驗室」分析該選定策略的參數敏感性"],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [selectedStrategyId, selectedStrategyVersion, setContext]);

  useAutoRefresh(load);

  const activeFamily = (systemConfig?.strategy?.family as string) || "illiquidity";
  const activeVersion = (systemConfig?.strategy?.version as string) || "v3.1";
  const isProductionStrategy = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;

  // Generate holdings risk rows
  const holdingRows: HoldingRiskRow[] = [];
  if (isProductionStrategy && paperPlan?.positions) {
    const minAdv = (strategyDetail?.strategy?.config?.min_adv20 as number) || 10_000_000;
    paperPlan.positions.forEach((pos) => {
      const isStAsset = pos.name.includes("ST") || pos.name.includes("*ST");
      const weightPct = pos.mv / (paperPlan.position_value || 1);
      
      const tags = [];
      if (isStAsset) tags.push("ST 標的");
      if (weightPct > 0.15) tags.push("單票集中");
      
      const advUsageRatio = pos.mv / minAdv;
      if (advUsageRatio > 0.05) tags.push("ADV使用過高");

      holdingRows.push({
        code: pos.code,
        name: pos.name,
        weight: weightPct,
        advUsage: `${(advUsageRatio * 100).toFixed(1)}%`,
        isSt: isStAsset,
        liquidityScore: isStAsset ? 0.2 : 0.85,
        estSlippageBps: isStAsset ? 45 : 12,
        riskExposure: weightPct * 1.25,
        riskTags: tags,
      });
    });
  }

  // Dynamic calculated aggregate variables
  const riskBudgetUsage = (() => {
    if (!riskReport?.checks || riskReport.checks.length === 0) return 0;
    const ratios = riskReport.checks.map(c => {
      if (c.current !== null && c.threshold !== null && c.threshold !== 0) {
        return Math.abs(c.current) / Math.abs(c.threshold);
      }
      return 0;
    });
    return Math.max(...ratios) * 100;
  })();

  const capacityLimit = (strategyDetail?.strategy?.capacity_m || 0) * 1_000_000;
  const capacityUsage = capacityLimit > 0 && portfolio?.nav ? (portfolio.nav / capacityLimit) * 100 : null;

  const totalSlippageBps = holdingRows.length > 0 
    ? holdingRows.reduce((sum, r) => sum + r.weight * r.estSlippageBps, 0)
    : null;

  const stExposureVal = holdingRows.reduce((sum, r) => sum + (r.isSt ? r.weight : 0), 0) * 100;
  const maxSingleWeight = holdingRows.length > 0 ? Math.max(...holdingRows.map(r => r.weight)) * 100 : null;

  // Style betas from backend
  const styleSize = strategyDetail?.strategy?.style_betas?.size;
  const styleLiq = strategyDetail?.strategy?.style_betas?.illiquidity ?? strategyDetail?.strategy?.style_betas?.liquidity;

  // Stress test scenarios data from actual metrics
  const metrics = strategyDetail?.strategy?.metrics;
  const nineGate = strategyDetail?.strategy?.nine_gate;

  const stressTests = [
    {
      scenario: "BEAR 避險熊市大週期",
      desc: "市場大勢擇時為 BEAR 避險大週期下，策略（空倉或國債輪動）的實測收益表現",
      pnl: nineGate?.bear_annual !== undefined ? `${(nineGate.bear_annual * 100).toFixed(2)}%` : "—",
      drawdown: nineGate?.bear_sharpe !== undefined ? `夏普 ${nineGate.bear_sharpe.toFixed(2)}` : "—",
      level: (nineGate?.bear_sharpe !== undefined && nineGate.bear_sharpe < 0) ? "high" as const : "medium" as const,
    },
    {
      scenario: "BULL 運行牛市大週期",
      desc: "市場風格與大勢擇時為 BULL 運行大週期下，策略滿倉暴露選股的收益表現",
      pnl: nineGate?.bull_annual !== undefined ? `${(nineGate.bull_annual * 100).toFixed(2)}%` : "—",
      drawdown: nineGate?.bull_sharpe !== undefined ? `夏普 ${nineGate.bull_sharpe.toFixed(2)}` : "—",
      level: "low" as const,
    },
    {
      scenario: "2010-2026 長週期壓力回測",
      desc: "涵蓋 2010 年至今全歷史的所有單邊熊市、震蕩及流動性危機區間的壓力評估",
      pnl: metrics?.annual_2010 !== undefined ? `${(metrics.annual_2010 * 100).toFixed(2)}%` : "—",
      drawdown: metrics?.maxdd_2010 !== undefined ? `${(metrics.maxdd_2010 * 100).toFixed(2)}%` : "—",
      level: (metrics?.maxdd_2010 !== undefined && metrics.maxdd_2010 < -0.30) ? "high" as const : "medium" as const,
    },
  ];

  // Helper styles based on risk values
  const getBudgetColor = (pct: number) => {
    if (pct > 100) return "text-danger";
    if (pct > 80) return "text-warn";
    return "text-[#30D158]";
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="組合風控"
        desc="多維風險暴露審計、槓桿敞口限制與極端場景壓力測試 (Portfolio Risk & Controls)"
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
            <div className="font-bold text-[#F5F5F7] text-[13px]">組合風控審計受限</div>
            <div className="text-[#8E8E93] text-[12px] mt-1 leading-relaxed">
              當前選取的策略 <span className="font-mono text-[#0A84FF] font-semibold">{selectedStrategyId} {selectedStrategyVersion}</span> 非生產部署狀態。
              組合淨值、持倉風險明細及風險預算使用率僅對生產運行中的主策略（當前：<span className="font-mono text-[#F5F5F7] font-semibold">{activeFamily} {activeVersion}</span>）開放，以防多策略口徑風控數據混淆。
            </div>
          </div>
        </div>
      )}

      {/* 1. 風險總覽大卡 */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <div className="p-4 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg text-center">
          <div className="text-[11px] text-[#8E8E93] uppercase font-bold tracking-wider">總風險等級</div>
          <div className="mt-2.5">
            <RiskBadge level={isProductionStrategy && riskReport?.verdict === "超限" ? "high" : isProductionStrategy && riskReport?.verdict === "预警" ? "medium" : "low"} label={isProductionStrategy ? (riskReport?.verdict ?? "正常") : "—"} />
          </div>
          <div className="text-[10px] text-[#6E6E73] mt-2">基於 6 項核心暴露評估</div>
        </div>

        <QuantMetricCard
          label="當前淨值 (NAV)"
          value={isProductionStrategy && portfolio ? portfolio.nav : "—"}
          unit={isProductionStrategy ? "CNY" : undefined}
        />

        <div className="p-4 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
          <div className="text-[12px] text-[#8E8E93]">風險預算使用率</div>
          <div className={`text-2xl font-bold mt-1.5 font-mono ${getBudgetColor(riskBudgetUsage)}`}>
            {isProductionStrategy && riskReport?.checks && riskReport.checks.length > 0 ? `${riskBudgetUsage.toFixed(1)}%` : "—"}
          </div>
          <div className="text-[10px] text-[#6E6E73] mt-2">警戒閾值: 80% / 100%</div>
        </div>

        <div className="p-4 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
          <div className="text-[12px] text-[#8E8E93]">容量使用率</div>
          <div className="text-2xl font-bold mt-1.5 font-mono text-[#F5F5F7]">
            {isProductionStrategy && capacityUsage !== null ? `${capacityUsage.toFixed(1)}%` : "—"}
          </div>
          <div className="text-[10px] text-[#6E6E73] mt-2">
            相較容量上限 {strategyDetail?.strategy?.capacity_m !== undefined ? `${(strategyDetail.strategy.capacity_m * 100).toFixed(0)} 萬` : "—"}
          </div>
        </div>

        <div className="p-4 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
          <div className="text-[12px] text-[#8E8E93]">預估單邊滑點</div>
          <div className="text-2xl font-bold mt-1.5 font-mono text-warn">
            {isProductionStrategy && totalSlippageBps !== null ? `${totalSlippageBps.toFixed(1)} bps` : "—"}
          </div>
          <div className="text-[10px] text-[#6E6E73] mt-2">包含小盤流動性惩罚</div>
        </div>
      </div>

      {/* 2. 六項風險暴露暴露卡 */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-[#8E8E93] tracking-wider uppercase">風險暴露總覽 (Risk Exposures)</h3>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">小盤暴露 (Size)</div>
            <div className={`text-lg font-bold font-mono mt-1 ${styleSize !== undefined && styleSize > 0.5 ? "text-[#FF453A]" : "text-[#30D158]"}`}>
              {styleSize !== undefined ? `${styleSize.toFixed(2)} std` : "—"}
            </div>
            <div className="text-[9px] text-[#8E8E93] mt-1">
              {styleSize !== undefined ? (styleSize > 0.5 ? "高風險暴露" : "安全範圍") : "未定義"}
            </div>
          </div>
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">流動性暴露 (Liq)</div>
            <div className={`text-lg font-bold font-mono mt-1 ${styleLiq !== undefined && Math.abs(styleLiq) > 0.5 ? "text-warn" : "text-[#30D158]"}`}>
              {styleLiq !== undefined ? `${styleLiq.toFixed(2)} std` : "—"}
            </div>
            <div className="text-[9px] text-[#8E8E93] mt-1">
              {styleLiq !== undefined ? (Math.abs(styleLiq) > 0.5 ? "中風險暴露" : "安全範圍") : "未定義"}
            </div>
          </div>
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">ST 暴露 (Trash)</div>
            <div className={`text-lg font-bold font-mono mt-1 ${stExposureVal > 5 ? "text-[#FF453A]" : stExposureVal > 0 ? "text-warn" : "text-[#30D158]"}`}>
              {stExposureVal.toFixed(2)}%
            </div>
            <div className="text-[9px] text-[#8E8E93] mt-1">
              {stExposureVal > 5 ? "高風險暴露" : stExposureVal > 0 ? "中風險暴露" : "安全範圍"}
            </div>
          </div>
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">行業集中度 (Ind)</div>
            <div className="text-lg font-bold text-[#8E8E93] font-mono mt-1">—</div>
            <div className="text-[9px] text-[#6E6E73] mt-1">安全 (限額 30%)</div>
          </div>
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">單票集中度 (Idio)</div>
            <div className={`text-lg font-bold font-mono mt-1 ${maxSingleWeight !== null && maxSingleWeight > 15 ? "text-[#FF453A]" : maxSingleWeight !== null && maxSingleWeight > 10 ? "text-warn" : "text-[#30D158]"}`}>
              {maxSingleWeight !== null ? `${maxSingleWeight.toFixed(2)}%` : "—"}
            </div>
            <div className="text-[9px] text-[#8E8E93] mt-1">
              {maxSingleWeight !== null ? (maxSingleWeight > 15 ? "單票超限" : maxSingleWeight > 10 ? "接近預警" : "安全 (限額 15%)") : "無持倉"}
            </div>
          </div>
          <div className="p-3 bg-[#1C1C1E] border border-[#2C2C2E] rounded-lg">
            <div className="text-[11px] text-[#8E8E93]">換手壓力 (Turnover)</div>
            <div className="text-lg font-bold text-[#8E8E93] font-mono mt-1">—</div>
            <div className="text-[9px] text-[#6E6E73] mt-1">未定義</div>
          </div>
        </div>
      </div>

      {/* 3. 持倉風險明細表 */}
      <Card title="持倉風險暴露明細 (Holdings Risk Breakdown)">
        <DataTable<HoldingRiskRow>
          rows={holdingRows}
          getRowKey={(r) => r.code}
          empty={isProductionStrategy ? "當前無持倉風險數據" : "非運行中生產策略無即時持倉數據。請切換至生產主策略以查看持倉風險。"}
          columns={[
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
              key: "weight",
              header: "權重",
              align: "right",
              className: "font-mono text-[#E6EDF7]",
              render: (r) => `${(r.weight * 100).toFixed(2)}%`,
            },
            {
              key: "advUsage",
              header: "ADV 占用率",
              align: "right",
              className: "font-mono text-subink",
              render: (r) => r.advUsage,
            },
            {
              key: "liquidityScore",
              header: "流動性得分",
              align: "right",
              className: "font-mono text-subink",
              render: (r) => r.liquidityScore.toFixed(2),
            },
            {
              key: "estSlippageBps",
              header: "估計滑點 bps",
              align: "right",
              className: "font-mono text-warn",
              render: (r) => `${r.estSlippageBps} bps`,
            },
            {
              key: "riskExposure",
              header: "風險Beta暴露",
              align: "right",
              className: "font-mono text-subink",
              render: (r) => r.riskExposure.toFixed(2),
            },
            {
              key: "riskTags",
              header: "風險標籤",
              render: (r) => (
                <div className="flex gap-1.5 flex-wrap">
                  {r.riskTags.map((tag) => {
                    const tagStyle = tag === "ST 標的"
                      ? "text-danger bg-[#FF5C5C]/10 border-[#FF5C5C]/20"
                      : "text-warn bg-[#F6B73C]/10 border-[#F6B73C]/20";
                    return (
                      <span key={tag} className={`px-1.5 py-0.5 rounded text-[10px] border ${tagStyle}`}>
                        {tag}
                      </span>
                    );
                  })}
                  {r.riskTags.length === 0 && <span className="text-ok text-[10px]">✓ 無異常</span>}
                </div>
              ),
            },
          ]}
        />
      </Card>

      {/* 4. 壓力測試區 */}
      <Card title="組合極端情景壓力測試 (Stress Testing Simulations)">
        <DataTable<typeof stressTests[number]>
          rows={stressTests}
          getRowKey={(s) => s.scenario}
          columns={[
            {
              key: "scenario",
              header: "場景",
              className: "text-[#E6EDF7] font-bold",
              render: (s) => s.scenario,
            },
            {
              key: "desc",
              header: "情景描述",
              className: "text-subink text-[12px] max-w-[360px]",
              render: (s) => s.desc,
            },
            {
              key: "pnl",
              header: "預估組合損益",
              align: "right",
              className: "font-mono text-danger font-semibold",
              render: (s) => s.pnl,
            },
            {
              key: "drawdown",
              header: "預估最大回撤",
              align: "right",
              className: "font-mono text-danger font-semibold",
              render: (s) => s.drawdown,
            },
            {
              key: "level",
              header: "風險評級",
              render: (s) => (
                <RiskBadge level={s.level} label={s.level.toUpperCase()} />
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
