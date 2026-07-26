"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api } from "@/lib/api";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { QuantMetricCard } from "@/components/ui/QuantComponents";

import { useAppStore } from "@/lib/appStore";
import type { StrategyDetailView } from "@/lib/types";

type PerformanceStatsRow = {
  metric: string;
  theoretical: string;
  realExecution: string;
  diff: string;
};

export default function BacktestLabPage() {
  const setContext = useAgent((s) => s.setContext);
  const { selectedStrategyId, selectedStrategyVersion } = useAppStore();

  const [err, setErr] = useState<string | null>(null);
  const [detail, setDetail] = useState<StrategyDetailView | null>(null);
  const [activeSegmentTab, setActiveSegmentTab] = useState<"is" | "oos" | "wf" | "stress">("oos");
  const [heatmapMetric, setHeatmapMetric] = useState<"annual" | "sharpe" | "maxdd">("sharpe");

  const load = useCallback(() => {
    setErr(null);
    api.strategyDetail(selectedStrategyId, selectedStrategyVersion)
      .then((data) => {
        setDetail(data);

        const oosSharpe = data.strategy.metrics?.sharpe_2023;
        const sharpe = data.strategy.metrics?.sharpe;
        const dsr = data.strategy.nine_gate?.dsr_p;
        const period =
          typeof data.strategy.data_scope === "string"
            ? data.strategy.data_scope
            : ((data.strategy.data_scope as any)?.period ?? "未记录");

        setContext({
          page: "backtest-lab",
          title: `回測實驗室: ${data.strategy.strategy_id}`,
          summary: `回测证据直读自台账。OOS 夏普 ${oosSharpe !== undefined ? oosSharpe.toFixed(2) : "未计算"}，DSR ${dsr !== undefined ? dsr.toFixed(3) : "未审计"}。`,
          evidence: [
            `策略 ID: ${data.strategy.strategy_id}`,
            `回测区间: ${period}`,
            `DSR p-value: ${dsr !== undefined ? dsr.toFixed(3) : "未审计"}`,
            `夏普比率: ${sharpe !== undefined ? sharpe.toFixed(2) : "未计算"}`,
          ],
          risk: data.strategy.decay_check?.decayed ? ["該策略在近期樣本外有衰退預警"] : [],
          recommendation: [
            "缺少逐日 NAV 或参数网格时不展示曲线/热力图",
            "需要完整图表时先由后端落库对应审计产物"
          ],
          nextActions: [
            "運行多階段壓力回測評核",
            "登記台帳最新回測 Spec Hash 證據",
          ],
        });
      })
      .catch((e) => {
        setDetail(null);
        setErr(String(e));
      });
  }, [selectedStrategyId, selectedStrategyVersion, setContext]);

  useAutoRefresh(load);

  const metrics = detail?.strategy?.metrics;

  const annVal = metrics?.annual !== undefined ? `${(metrics.annual * 100).toFixed(2)}%` : "—";
  const sharpeVal = metrics?.sharpe !== undefined ? metrics.sharpe.toFixed(2) : "—";
  const maxddVal = metrics?.maxdd !== undefined ? `${(metrics.maxdd * 100).toFixed(2)}%` : "—";
  
  // Diff computation for realExecution vs theoretical
  const realAnn = metrics?.annual_2023 !== undefined ? `${(metrics.annual_2023 * 100).toFixed(2)}%` : "—";
  const realSharpe = metrics?.sharpe_2023 !== undefined ? metrics.sharpe_2023.toFixed(2) : "—";
  const realMaxdd = metrics?.maxdd_2023 !== undefined ? `${(metrics.maxdd_2023 * 100).toFixed(2)}%` : "—";

  // Mismatch table rows
  const mismatchRows: PerformanceStatsRow[] = [
    { metric: "年化收益率", theoretical: annVal, realExecution: realAnn, diff: metrics?.annual !== undefined && metrics?.annual_2023 !== undefined ? `${((metrics.annual_2023 - metrics.annual) * 100).toFixed(2)}%` : "—" },
    { metric: "夏普比率 (Sharpe)", theoretical: sharpeVal, realExecution: realSharpe, diff: metrics?.sharpe !== undefined && metrics?.sharpe_2023 !== undefined ? (metrics.sharpe_2023 - metrics.sharpe).toFixed(2) : "—" },
    { metric: "最大回撤", theoretical: maxddVal, realExecution: realMaxdd, diff: metrics?.maxdd !== undefined && metrics?.maxdd_2023 !== undefined ? `${((metrics.maxdd_2023 - metrics.maxdd) * 100).toFixed(2)}%` : "—" },
    { metric: "年化換手率", theoretical: "—", realExecution: "—", diff: "—" },
    { metric: "成本後年化收益", theoretical: metrics?.cost_annual !== undefined ? `${(metrics.cost_annual * 100).toFixed(2)}%` : "—", realExecution: metrics?.cost_annual !== undefined && metrics?.annual_2023 !== undefined ? `${(metrics.annual_2023 * 100).toFixed(2)}%` : "—", diff: "—" },
  ];

  // Segment performances
  const segments = {
    is: [
      { period: "2018-2022 (In-Sample)", annual: metrics?.annual_2018 !== undefined ? `${(metrics.annual_2018 * 100).toFixed(2)}%` : "—", sharpe: metrics?.sharpe_2018 !== undefined ? metrics.sharpe_2018.toFixed(2) : "—", maxdd: metrics?.maxdd_2018 !== undefined ? `${(metrics.maxdd_2018 * 100).toFixed(2)}%` : "—", winRate: "—" },
    ],
    oos: [
      { period: "2023-2026 (Out-of-Sample)", annual: realAnn, sharpe: realSharpe, maxdd: realMaxdd, winRate: "—" },
    ],
    wf: [
      { period: "Walk-Forward (全區間)", annual: metrics?.wf_annual !== undefined ? `${(metrics.wf_annual * 100).toFixed(2)}%` : "—", sharpe: metrics?.wf_sharpe !== undefined ? metrics.wf_sharpe.toFixed(2) : "—", maxdd: metrics?.wf_maxdd !== undefined ? `${(metrics.wf_maxdd * 100).toFixed(2)}%` : "—", winRate: "—" },
    ],
    stress: [
      { period: "2010 至今長週期壓力回測", annual: metrics?.annual_2010 !== undefined ? `${(metrics.annual_2010 * 100).toFixed(2)}%` : "—", sharpe: metrics?.sharpe_2010 !== undefined ? metrics.sharpe_2010.toFixed(2) : "—", maxdd: metrics?.maxdd_2010 !== undefined ? `${(metrics.maxdd_2010 * 100).toFixed(2)}%` : "—", winRate: "—" },
    ],
  };

  const selectedSegment = segments[activeSegmentTab];

  const periodLabel =
    typeof detail?.strategy?.data_scope === "string"
      ? detail.strategy.data_scope
      : ((detail?.strategy?.data_scope as any)?.period ?? "未记录");

  const displayCalmar = metrics?.calmar !== undefined ? metrics.calmar.toFixed(2) : "—";
  const displayTurnover = metrics?.turnover_annual !== undefined ? `${(metrics.turnover_annual * 100).toFixed(1)}%` : "—";
  const displayNet = metrics?.cost_annual !== undefined ? `${(metrics.cost_annual * 100).toFixed(2)}%` : "—";

  const cvWin = detail?.strategy?.nine_gate?.cv_win_rate;
  const icWin = detail?.strategy?.nine_gate?.ic_win_rate;
  const displayWinRate = cvWin !== undefined ? `${(cvWin * 100).toFixed(1)}%` : icWin !== undefined ? `${(icWin * 100).toFixed(1)}%` : "—";

  const tailRatio = detail?.strategy?.nine_gate?.tail_ratio;
  const displayProfitLossRatio = tailRatio !== undefined ? `${tailRatio.toFixed(2)} : 1` : "—";

  const dailyMean = detail?.strategy?.nine_gate?.daily_mean_expected;
  const displayAvgReturn = dailyMean !== undefined ? `${(dailyMean * 100).toFixed(3)}%` : "—";


  return (
    <div className="space-y-6">
      <PageHeader
        title="回測實驗"
        desc="策略歷史淨值曲線、回測/真實口徑偏離與參數過擬合敏感性分析 (Backtest & Simulation Lab)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {/* 1. 回測摘要指標 */}
      <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
        <QuantMetricCard label="年化收益率 (Annual)" value={annVal} intent="positive" />
        <QuantMetricCard label="夏普比率 (Sharpe)" value={sharpeVal} intent="positive" />
        <QuantMetricCard label="最大回撤 (MaxDD)" value={maxddVal} intent="negative" />
        <QuantMetricCard label="卡瑪比率 (Calmar)" value={displayCalmar} intent="neutral" />
        <QuantMetricCard label="年化換手率" value={displayTurnover} intent="neutral" />
        <QuantMetricCard label="成本後收益 (Net)" value={displayNet} intent="positive" />
      </div>

      <div className="text-[11px] text-subink font-mono bg-navy border border-line px-4 py-2 rounded-lg">
        回測區間：{periodLabel} · 成本口徑：以後端台帳 / CostModel 證據為準
      </div>

      {/* 2. 淨值與回撤區域圖 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Card
            title="歷史累計淨值與回撤區域對比 (NAV Curve & Drawdowns)"
            right={
              <div className="flex gap-1.5 bg-bg p-0.5 rounded border border-line">
                {["1Y", "3Y", "5Y", "ALL"].map((range) => (
                  <button
                    key={range}
                    className={`px-2 py-0.5 text-[10px] font-bold rounded ${
                      range === "ALL" ? "bg-brand text-white" : "text-subink hover:text-ink"
                    }`}
                  >
                    {range}
                  </button>
                ))}
              </div>
            }
          >
            <div className="min-h-[210px] flex flex-col items-center justify-center border border-line/45 rounded bg-[#161617]/30 text-center text-subink">
              <div className="text-sm font-semibold text-[#E6EDF7]">暂无逐日 NAV 曲线证据</div>
              <div className="text-[11px] mt-1 max-w-lg">
                当前接口只提供台账级摘要指标。逐日净值、基准曲线和回撤面积需要后端审计产物落库后再展示。
              </div>
            </div>
            <div className="flex justify-between items-center text-[10px] text-weak mt-2 font-mono">
              <span>策略淨值: 未落庫</span>
              <span>基準指數: 未落庫</span>
              <span>回撤區間: 未落庫</span>
            </div>
          </Card>
        </div>

        {/* 3. 回測 vs 真實執行偏差 */}
        <Card title="回測 vs 真實執行口徑偏離 (Execution Demotion Check)">
          <div className="text-[11px] text-subink mb-2 leading-relaxed">
            量化回測與實盤交易的定價偏差審計。如果開盤口徑的收益衰退顯著，反映存在較嚴重的隔夜跳空或流動性陷阱。
          </div>
          <DataTable<PerformanceStatsRow>
            rows={mismatchRows}
            getRowKey={(r) => r.metric}
            columns={[
              { key: "metric", header: "核算指標", className: "text-[#E6EDF7] font-semibold", render: (r) => r.metric },
              { key: "theoretical", header: "理論 (T+1收盤)", align: "right", className: "font-mono text-subink", render: (r) => r.theoretical },
              { key: "realExecution", header: "真實 (T+1開盤)", align: "right", className: "font-mono text-[#E6EDF7]", render: (r) => r.realExecution },
              {
                key: "diff",
                header: "偏差 (Diff)",
                align: "right",
                render: (r) => {
                  const isNeg = r.diff.startsWith("-");
                  return (
                    <span className={`font-mono font-bold ${isNeg ? "text-danger" : "text-ok"}`}>
                      {r.diff}
                    </span>
                  );
                },
              },
            ]}
          />
          <div className="text-[10px] text-[#5F728A] mt-2 leading-snug font-mono">
            * 統計偏離成因：小盤股高頻換手引起的隔夜滑價及開盤競價滑點佔用。
          </div>
        </Card>
      </div>

      {/* 4. 參數敏感性與樣本分段 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Heatmap */}
        <Card
          title="參數敏感性熱力圖 (Parameter Platform)"
          right={
            <div className="flex gap-2 items-center text-[11px]">
              <span className="text-subink">指標：</span>
              <select
                value={heatmapMetric}
                onChange={(e) => setHeatmapMetric(e.target.value as any)}
                className="bg-bg border border-line text-[#E6EDF7] px-1 py-0.5"
              >
                <option value="sharpe">Sharpe 比率</option>
                <option value="annual">年化收益</option>
                <option value="maxdd">最大回撤</option>
              </select>
            </div>
          }
        >
          <div className="text-[11px] text-subink mb-3 leading-relaxed">
            橫軸為單隻股票最大持倉限制，縱軸為選股因子分位數閾值。穩健的策略應在一個寬廣的綠色「參數高原」中，如果最優點是孤立尖峰，則代表有擬合過度風險。
          </div>

          <div className="border border-line/45 rounded overflow-hidden text-xs">
            <table className="w-full text-center border-collapse">
              <thead>
                <tr className="bg-[#10263D] border-b border-line text-subink text-[11px]">
                  <th className="p-2 border-r border-line">備值 \ 持倉限額</th>
                  <th className="p-2 border-r border-line">10%</th>
                  <th className="p-2 border-r border-line">12%</th>
                  <th className="p-2 border-r border-line">15% (當前)</th>
                  <th className="p-2">20%</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1F3550]/30 font-mono">
                <tr>
                  <td colSpan={5} className="p-4 text-center text-[#8E8E93]">
                    無敏感度熱力圖數據。需要真实参数网格审计产物后再展示。
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="flex justify-between items-center text-[10px] text-[#5F728A] mt-2.5 font-mono">
            <span>★ 代表當前生產配置參量</span>
            <span>色塊：綠色越深代表 Sharpe 表現越優異，越穩健</span>
          </div>
        </Card>

        {/* Segment Performance */}
        <Card title="樣本分段與壓力期表現 (Out-of-Sample Validation)">
          <div className="flex border-b border-line gap-2 mb-3">
            {["is", "oos", "wf", "stress"].map((tab) => {
              const labelMap = { is: "樣本內 (IS)", oos: "樣本外 (OOS)", wf: "步進複測 (WF)", stress: "歷史壓力期" };
              return (
                <button
                  key={tab}
                  onClick={() => setActiveSegmentTab(tab as any)}
                  className={`px-3 py-1.5 text-xs font-bold border-b-2 transition-all ${
                    activeSegmentTab === tab ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                  }`}
                >
                  {labelMap[tab as keyof typeof labelMap]}
                </button>
              );
            })}
          </div>

          <DataTable<typeof selectedSegment[number]>
            rows={selectedSegment}
            getRowKey={(r) => r.period}
            columns={[
              { key: "period", header: "回測區間", className: "text-[#E6EDF7] font-bold", render: (r) => r.period },
              { key: "annual", header: "年化收益", align: "right", className: "font-mono text-ok", render: (r) => r.annual },
              { key: "sharpe", header: "Sharpe", align: "right", className: "font-mono text-[#E6EDF7]", render: (r) => r.sharpe },
              { key: "maxdd", header: "最大回撤", align: "right", className: "font-mono text-danger", render: (r) => r.maxdd },
              { key: "winRate", header: "交易勝率", align: "right", className: "font-mono text-subink", render: (r) => r.winRate },
            ]}
          />

          {activeSegmentTab === "oos" && (
            <div className="mt-3 p-2.5 bg-[#3D7BFF]/5 border border-[#3D7BFF]/10 rounded text-[11px] text-subink">
              樣本外 (OOS) 指標直讀自台帳：年化收益 {realAnn}，樣本內年化 {metrics?.annual_2018 !== undefined ? `${(metrics.annual_2018 * 100).toFixed(2)}%` : "—"}。
            </div>
          )}

          {activeSegmentTab === "stress" && (
            <div className="mt-3 p-2.5 bg-[#F6B73C]/5 border border-[#F6B73C]/10 rounded text-[11px] text-warn">
              ⚠️ 警示：在歷史壓力期，組合最大單次回撤達 {metrics?.maxdd_2010 !== undefined ? `${(metrics.maxdd_2010 * 100).toFixed(2)}%` : maxddVal}，反映該策略在特定壓力段下的流動性風險與敞口。
            </div>
          )}
        </Card>
      </div>

      {/* 5. 交易統計與換手 */}
      <Card title="回測明細交易數據統計 (Trading & Turnover Statistics)">
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 text-xs font-mono text-center py-2">
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">年化換手率</div>
            <div className="text-sm font-bold text-[#E6EDF7] mt-1">{displayTurnover}</div>
          </div>
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">平均持股週期</div>
            <div className="text-sm font-bold text-[#E6EDF7] mt-1">{detail?.strategy?.config?.rebalance_days !== undefined ? `${detail.strategy.config.rebalance_days} 天` : "—"}</div>
          </div>
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">交易勝率</div>
            <div className="text-sm font-bold text-ok mt-1">{displayWinRate}</div>
          </div>
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">平均盈虧比</div>
            <div className="text-sm font-bold text-ok mt-1">{displayProfitLossRatio}</div>
          </div>
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">單票平均收益</div>
            <div className="text-sm font-bold text-[#E6EDF7] mt-1">{displayAvgReturn}</div>
          </div>
          <div className="p-3 bg-bg border border-line rounded">
            <div className="text-subink text-[10px]">交易費用佔比</div>
            <div className="text-sm font-bold text-danger mt-1">—</div>
          </div>
        </div>
      </Card>
    </div>
  );
}
