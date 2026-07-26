"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import DataTable from "@/components/ui/DataTable";
import { api, num, pct } from "@/lib/api";
import type { TradePlanView, TradeReadinessView, RiskReport, MarketStateView, SystemConfigView, StrategyDetailView, TrustCalibrationView, PaperAccountsListView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { GateCard, QuantMetricCard, HashCopy } from "@/components/ui/QuantComponents";
import { useAppStore } from "@/lib/appStore";
import TradeDecision from "@/components/desk/TradeDecision";
import TrustCalibration from "@/components/governance/TrustCalibration";
import PaperAccountsPanel from "@/components/paper/PaperAccountsPanel";

type SignalRow = {
  dir: string;
  name: string;
  code: string;
  refPrice: number;
  sharesText: string;
  notionalText: string;
  industry?: string;
  score?: number;
  advOccupancy?: string;
  stStatus?: string;
};

export default function DashboardPage() {
  const setContext = useAgent((s) => s.setContext);
  const { selectedStrategyId, selectedStrategyVersion } = useAppStore();

  const [market, setMarket] = useState<MarketStateView | null>(null);
  const [readiness, setReadiness] = useState<TradeReadinessView | null>(null);
  const [paperPlan, setPaperPlan] = useState<TradePlanView | null>(null);
  const [riskReport, setRiskReport] = useState<RiskReport | null>(null);
  const [systemConfig, setSystemConfig] = useState<SystemConfigView | null>(null);
  const [strategyDetail, setStrategyDetail] = useState<StrategyDetailView | null>(null);
  const [trust, setTrust] = useState<TrustCalibrationView | null>(null);
  const [paperAccounts, setPaperAccounts] = useState<PaperAccountsListView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"positions" | "top25" | "filters" | "execRisk">("positions");

  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      api.marketState(),
      api.tradeReadiness(),
      api.paperPlan(),
      api.risk(),
      api.systemConfig(),
      api.strategyDetail(selectedStrategyId, selectedStrategyVersion),
      // 信任校准是首屏补充信息,不得因其端点缺失/报错拖垮整个操作台(与 system-governance 一致的隔离)。
      api.trustCalibration().catch(() => null),
      // 多账户实测同理:名单缺失/后端未部署不得拖垮整个操作台,由 PaperAccountsPanel 自行诚实呈现。
      api.paperAccounts().catch(() => null)
    ])
      .then(([m, tr, pp, rk, sc, sd, tc, pa]) => {
        setMarket(m);
        setReadiness(tr);
        setPaperPlan(pp);
        setRiskReport(rk);
        setSystemConfig(sc);
        setStrategyDetail(sd);
        setTrust(tc);
        setPaperAccounts(pa);

        const activeFamily = (sc?.strategy?.family as string) || "illiquidity";
        const activeVersion = (sc?.strategy?.version as string) || "v3.1";
        const isProd = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;
        const hasCurrentExecutablePlan = isProd && !pp.stale && tr.allowed_to_trade;
        const blockedActionTip = pp.stale
          ? "纸面信号已过期,非现行可执行"
          : !tr.allowed_to_trade
          ? "生产门禁已拦截,无可执行交易指令"
          : "";
        const actionTip = blockedActionTip || (pp.bond
          ? (pp.bond.side === "HOLD" ? `继续持有 ${pp.bond.name}` : `${pp.bond.side} ${pp.bond.name}`)
          : pp.plan?.length > 0
          ? `${pp.plan[0].action} ${pp.plan[0].name}`
          : "今日无交易信号");

        setContext({
          page: "dashboard",
          title: "今日操作台",
          summary: isProd 
            ? `今日交易就绪度：${hasCurrentExecutablePlan ? "【已就绪】" : "【已拦截/非现行可执行】"}。建议决策动作：${actionTip}。大周期極性：${m.last_action}。`
            : `當前策略為非運行生產策略。今日操作台數據（實盤）僅對生產主策略 ${activeFamily} ${activeVersion} 開放。`,
          evidence: isProd ? [
            `前置就緒度門禁是否通過 (allowed_to_trade): ${tr.allowed_to_trade ? "YES" : "NO"}`,
            `纸面信号是否过期 (paperPlan.stale): ${pp.stale ? "YES" : "NO"}`,
            `大周期市場制度: ${m.last_action}`,
            `當前模擬盤 NAV: ${pp.nav.toFixed(2)} · 持倉市值: ${pp.position_value.toFixed(0)}元`,
            `Spec Hash: ${pp.stale_reason || "未提供"}`,
          ] : [
            `當前選定策略: ${selectedStrategyId} ${selectedStrategyVersion}`,
            `生產運行策略: ${activeFamily} ${activeVersion}`,
            `實盤運行數據已被安全隔离展示`,
          ],
          risk: isProd && !hasCurrentExecutablePlan ? ["生產門禁或纸面信号时效未通过，今日交易流程已自動攔截"] : [],
          recommendation: isProd 
            ? (hasCurrentExecutablePlan ? ["對齊目標倉位進行下單執行", "關注小盤流動性滑點衝擊"] : ["不要按当前 paper/signal 下单", "进入系统治理页排查部署、数据与信号时效"])
            : ["切換至生產主策略以查看實盤信號和交易決策"],
          nextActions: isProd 
            ? (hasCurrentExecutablePlan ? ["簽發今日交易指令單", "監控持倉組合敞口偏離"] : ["刷新数据并生成新信号", "覆核策略台帳在冊版本"])
            : ["前往「策略實驗室」查看選定策略的回測明細"],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [selectedStrategyId, selectedStrategyVersion, setContext]);

  useAutoRefresh(load);

  const activeFamily = (systemConfig?.strategy?.family as string) || "illiquidity";
  const activeVersion = (systemConfig?.strategy?.version as string) || "v3.1";
  const isProductionStrategy = selectedStrategyId === activeFamily && selectedStrategyVersion === activeVersion;
  const hasCurrentExecutablePlan =
    isProductionStrategy && !!paperPlan && !paperPlan.stale && !!readiness?.allowed_to_trade;

  // Normalize signals for Top-25 Candidates representation
  const signalRows: SignalRow[] = [];
  if (hasCurrentExecutablePlan) {
    if (paperPlan?.bond) {
      const b = paperPlan.bond;
      signalRows.push({
        dir: b.side,
        name: b.name,
        code: b.code,
        refPrice: b.ref_price,
        sharesText: b.side === "HOLD" ? "—" : String(b.est_shares),
        notionalText: b.side === "HOLD" ? "—" : num(b.est_notional, 0),
        industry: "债券",
        score: undefined,
        advOccupancy: "—",
        stStatus: "—",
      });
    }
    paperPlan?.plan?.forEach((item) => {
      signalRows.push({
        dir: item.action,
        name: item.name,
        code: item.code,
        refPrice: item.ref_price,
        sharesText: String(item.est_shares),
        notionalText: num(item.est_notional, 0),
        industry: undefined,
        score: undefined,
        advOccupancy: "—",
        stStatus: "—",
      });
    });
  }

  const positionRows = isProductionStrategy ? (paperPlan?.positions ?? []) : [];

  const specHash = strategyDetail?.strategy?.nine_gate?.config_hash || "—";
  const dataFingerprint = strategyDetail?.strategy?.nine_gate?.config_hash
    ? `lake_${strategyDetail.strategy.nine_gate.config_hash.slice(0, 12)}`
    : "—";
  const rawCandidateCount =
    hasCurrentExecutablePlan ? String(paperPlan.candidates.length) : "—";
  const executedSignalCount = hasCurrentExecutablePlan ? String(signalRows.length) : "—";
  const actionDetail =
    isProductionStrategy && paperPlan
      ? paperPlan.stale
        ? "非现行可执行(纸面信号已过期)"
        : readiness?.allowed_to_trade
        ? paperPlan.action
        : "生产门禁已拦截,无可执行交易指令"
      : "—";
  const statusFromReadiness = (value?: string): "passed" | "warning" | "failed" | "pending" => {
    if (!isProductionStrategy || !value) return "pending";
    if (/(不可用|失敗|失败|滯後|滞后|超限|異常|异常|blocked|fail)/i.test(value)) return "failed";
    if (/(預警|预警|警告|warning|待|unknown)/i.test(value)) return "warning";
    return "passed";
  };
  const riskGateStatus: "passed" | "warning" | "failed" | "pending" =
    !isProductionStrategy || !riskReport
      ? "pending"
      : riskReport.verdict === "超限"
      ? "failed"
      : riskReport.verdict === "预警"
      ? "warning"
      : "passed";

  return (
    <div className="space-y-6">
      <PageHeader
        title="今日操作台"
        desc="全天候交易信號、生產就緒度門禁與今日持倉狀態 (Today's Operations Desk)"
      />

      {/* 首屏 0. 信任校准 —— 看 KPI 前先看「该不该信这个池子」(over-trust 防护带,系统级) */}
      <TrustCalibration data={trust} />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}（請確認 FastAPI 後端已在 8011 埠運行）
        </div>
      )}

      {!isProductionStrategy && (
        <div className="p-4 bg-[#BF5AF2]/10 border border-[#BF5AF2]/20 rounded-lg text-sm text-[#BF5AF2] flex items-start gap-2.5">
          <span className="text-sm mt-0.5">⚠️</span>
          <div>
            <div className="font-bold text-[#F5F5F7] text-[13px]">即時運行監控受限</div>
            <div className="text-[#8E8E93] text-[12px] mt-1 leading-relaxed">
              當前選取的策略 <span className="font-mono text-[#0A84FF] font-semibold">{selectedStrategyId} {selectedStrategyVersion}</span> 非生產部署狀態。
              今日操作台（包含持倉、流水線門禁、NAV、Cash等實盤運行數據）僅對生產運行中的主策略（當前：<span className="font-mono text-[#F5F5F7] font-semibold">{activeFamily} {activeVersion}</span>）開放，以防多策略口徑實盤數據混淆。
            </div>
          </div>
        </div>
      )}

      {/* 0. PM 交易决策(答案先行,全台:今天能否交易 + 绑定原因 + 板凳/解锁路径)*/}
      <TradeDecision readiness={readiness} />

      {/* 1. 交易門禁就緒度總體横幅 */}
      <StatusBanner
        status={hasCurrentExecutablePlan ? "ready" : "blocked"}
        title={
          isProductionStrategy && readiness && paperPlan
            ? hasCurrentExecutablePlan
              ? "今日交易門禁：已就緒 (允許交易)"
              : paperPlan.stale
              ? "今日交易門禁：被攔截 (信号过期)"
              : "今日交易門禁：被攔截 (BLOCKED)"
            : isProductionStrategy
            ? "今日交易門禁：載入中…"
            : "非運行中生產策略 (交易門禁鎖定)"
        }
        detail={
          isProductionStrategy && market && paperPlan
            ? `市場制度：${market.last_action === "空仓观望" ? "熊市避險" : "牛市運行"} · 建議動作：${
                actionDetail
              } · 目標倉位：${(paperPlan.band_exposure * 100).toFixed(0)}%`
            : undefined
        }
      />

      {/* 2. 帳戶核心指標大卡 —— 模拟盘/影子,非 live 实盘 */}
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-subink tracking-wider uppercase">模拟盘账户 (Shadow)</h3>
        <span className="px-2 py-0.5 rounded text-[10px] border border-[#BF5AF2]/30 text-[#BF5AF2] font-mono">
          非 live 实盘 · paper
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <QuantMetricCard
          label="總淨資產 (NAV)"
          value={isProductionStrategy && paperPlan ? paperPlan.nav : "—"}
          unit={isProductionStrategy ? "CNY" : undefined}
          precision={isProductionStrategy ? 2 : undefined}
          intent="neutral"
        />
        <QuantMetricCard
          label="累計收益率 (Total Return)"
          value={isProductionStrategy && paperPlan ? `${(paperPlan.total_return * 100).toFixed(2)}%` : "—"}
          intent={isProductionStrategy && paperPlan && paperPlan.total_return >= 0 ? "positive" : "negative"}
        />
        <QuantMetricCard
          label="持倉市值 (Market Value)"
          value={isProductionStrategy && paperPlan ? paperPlan.position_value : "—"}
          unit={isProductionStrategy ? "CNY" : undefined}
          precision={0}
        />
        <QuantMetricCard
          label="可用資金 (Cash)"
          value={isProductionStrategy && paperPlan ? paperPlan.cash : "—"}
          unit={isProductionStrategy ? "CNY" : undefined}
          precision={0}
        />
      </div>

      {/* 2.5 排名靠前策略并排实测(WS-D 执行侧,R-PROD-001「不下单 ≠ 不实测」)——
          与上方单账户生产策略 paper 卡片解耦:这里展示组合再构成 top-N 候选各自
          独立的模拟盘账本,回答「该不该把某策略推向真仓」。 */}
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-subink tracking-wider uppercase">排名靠前策略并排实测</h3>
        <span className="px-2 py-0.5 rounded text-[10px] border border-[#BF5AF2]/30 text-[#BF5AF2] font-mono">
          多账户 · paper
        </span>
      </div>
      <PaperAccountsPanel data={paperAccounts} />

      {/* 3. 生產就緒度五項門禁檢查 */}
      <div className="space-y-3">
        <h3 className="text-sm font-bold text-subink tracking-wider uppercase">生產就緒度門禁檢查 (Ready-to-Trade Gates)</h3>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <GateCard
            name="1. Governance"
            status={isProductionStrategy && readiness ? (readiness.allowed_to_trade ? "passed" : "warning") : "pending"}
            summary={isProductionStrategy && readiness ? `trade_readiness.allowed_to_trade=${readiness.allowed_to_trade}` : "未接入生產門禁結果"}
            lastCheckedAt="—"
          />
          <GateCard
            name="2. Decay"
            status={statusFromReadiness(readiness?.factor_health)}
            summary={isProductionStrategy && readiness?.factor_health ? readiness.factor_health : "未接入因子健康度結果"}
            lastCheckedAt="—"
          />
          <GateCard
            name="3. Paper"
            status={isProductionStrategy && paperPlan ? (paperPlan.stale ? "warning" : "passed") : "pending"}
            summary={isProductionStrategy && paperPlan ? (paperPlan.stale ? paperPlan.stale_reason : `生成時間：${paperPlan.generated_at}`) : "未接入紙面交易計畫"}
            lastCheckedAt="—"
          />
          <GateCard
            name="4. Data"
            status={statusFromReadiness(readiness?.data_status)}
            summary={isProductionStrategy && readiness?.data_status ? readiness.data_status : "未接入數據可用性結果"}
            lastCheckedAt="—"
          />
          <GateCard
            name="5. Risk"
            status={riskGateStatus}
            summary={isProductionStrategy && riskReport ? `風控 verdict=${riskReport.verdict}` : "未接入風控評估結果"}
            lastCheckedAt="—"
          />
        </div>
      </div>

      {/* 4. 決策指紋細節卡 */}
      <Card title="決策身份指紋 (Spec Hash & Metadata)">
        <div className="flex flex-wrap gap-4 items-center justify-between text-[12px]">
          <div className="flex items-center gap-4">
            <span className="text-subink">
              發布狀態：
              <span className={`font-bold ${isProductionStrategy && readiness?.allowed_to_trade ? "text-ok" : "text-danger"}`}>
                {isProductionStrategy ? (readiness?.allowed_to_trade ? "正式發布" : "已阻塞") : "非生產部署"}
              </span>
            </span>
            <span className="text-[#5F728A]">部署ID: —</span>
            <span className="text-[#5F728A]">策略版本: {selectedStrategyId} {selectedStrategyVersion}</span>
          </div>
          <div className="flex items-center gap-3">
            <HashCopy label="Spec Hash" value={specHash} />
            <HashCopy label="數據指紋" value={dataFingerprint} />
          </div>
        </div>
      </Card>

      {/* 5. 詳情 Tab 區域 */}
      <div className="space-y-4">
        <div className="flex border-b border-line gap-2 select-none">
          <button
            onClick={() => setActiveTab("positions")}
            className={`px-4 py-2 text-[13px] font-bold transition-all border-b-2 ${
              activeTab === "positions"
                ? "border-[#3D7BFF] text-[#E6EDF7]"
                : "border-transparent text-subink hover:text-[#E6EDF7]"
            }`}
          >
            持倉與交易
          </button>
          <button
            onClick={() => setActiveTab("top25")}
            className={`px-4 py-2 text-[13px] font-bold transition-all border-b-2 ${
              activeTab === "top25"
                ? "border-[#3D7BFF] text-[#E6EDF7]"
                : "border-transparent text-subink hover:text-[#E6EDF7]"
            }`}
          >
            Top-25 候選
          </button>
          <button
            onClick={() => setActiveTab("filters")}
            className={`px-4 py-2 text-[13px] font-bold transition-all border-b-2 ${
              activeTab === "filters"
                ? "border-[#3D7BFF] text-[#E6EDF7]"
                : "border-transparent text-subink hover:text-[#E6EDF7]"
            }`}
          >
            否決器過濾
          </button>
          <button
            onClick={() => setActiveTab("execRisk")}
            className={`px-4 py-2 text-[13px] font-bold transition-all border-b-2 ${
              activeTab === "execRisk"
                ? "border-[#3D7BFF] text-[#E6EDF7]"
                : "border-transparent text-subink hover:text-[#E6EDF7]"
            }`}
          >
            執行風險
          </button>
        </div>

        {/* Tab 內容渲染 */}
        <div className="bg-navy border border-line rounded-lg p-4">
          {activeTab === "positions" && (
            <div className="space-y-4">
              <div className="flex justify-between items-center text-sm font-semibold text-[#E6EDF7]">
                <span>當前運行持倉明細 (Current Position Holdings)</span>
                <span className="text-xs text-subink font-mono">共 {positionRows.length} 只證券</span>
              </div>
              <DataTable<typeof positionRows[number]>
                rows={positionRows}
                getRowKey={(p) => p.code}
                empty={isProductionStrategy ? "當前無持倉記錄" : "非運行中生產策略無即時持倉數據。請切換至生產主策略。"}
                columns={[
                  {
                    key: "code",
                    header: "代碼",
                    className: "font-mono text-brand font-semibold",
                    render: (p) => p.code,
                  },
                  {
                    key: "name",
                    header: "名稱",
                    className: "text-[#E6EDF7] font-semibold",
                    render: (p) => p.name,
                  },
                  {
                    key: "shares",
                    header: "持有股數",
                    align: "right",
                    className: "font-mono text-subink",
                    render: (p) => p.shares,
                  },
                  {
                    key: "cost",
                    header: "持倉成本",
                    align: "right",
                    className: "font-mono text-subink",
                    render: (p) => p.cost.toFixed(3),
                  },
                  {
                    key: "price",
                    header: "當前市價",
                    align: "right",
                    className: "font-mono text-[#E6EDF7]",
                    render: (p) => p.price?.toFixed(3) ?? "—",
                  },
                  {
                    key: "mv",
                    header: "持倉市值",
                    align: "right",
                    className: "font-mono text-[#E6EDF7]",
                    render: (p) => num(p.mv, 0),
                  },
                  {
                    key: "pnl",
                    header: "持倉盈虧",
                    align: "right",
                    render: (p) => (
                      <span className={`font-mono font-bold ${p.pnl >= 0 ? "text-ok" : "text-danger"}`}>
                        {p.pnl >= 0 ? "+" : ""}
                        {num(p.pnl, 2)}
                      </span>
                    ),
                  },
                ]}
              />
            </div>
          )}

          {activeTab === "top25" && (
            <div className="space-y-4">
              <div className="flex justify-between items-center text-sm font-semibold text-[#E6EDF7]">
                <span>今日交易候選名單 (Top-25 Candidates)</span>
                <span className="text-xs text-subink">以因子綜合得分倒序排序</span>
              </div>
              <DataTable<SignalRow>
                rows={signalRows}
                getRowKey={(r, i) => `${r.code}-${i}`}
                empty={
                  isProductionStrategy && paperPlan?.stale
                    ? "纸面信号已过期,非现行可执行。"
                    : isProductionStrategy
                    ? "今日無現行可執行交易信號"
                    : "非運行中生產策略無即時交易信號。請切換至生產主策略。"
                }
                columns={[
                  {
                    key: "dir",
                    header: "方向",
                    render: (r) => (
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-bold border ${
                          r.dir === "BUY"
                            ? "bg-[#35D06E]/10 border-[#35D06E]/20 text-ok"
                            : r.dir === "SELL"
                            ? "bg-[#FF5C5C]/10 border-[#FF5C5C]/20 text-danger"
                            : "bg-[#9AA8BD]/10 border-[#9AA8BD]/20 text-subink"
                        }`}
                      >
                        {r.dir}
                      </span>
                    ),
                  },
                  {
                    key: "code",
                    header: "代碼",
                    className: "font-mono text-brand",
                    render: (r) => r.code,
                  },
                  { key: "name", header: "名稱", className: "text-[#E6EDF7]", render: (r) => r.name },
                  { key: "industry", header: "行業", className: "text-subink", render: (r) => r.industry || "—" },
                  {
                    key: "score",
                    header: "因子得分",
                    align: "right",
                    className: "font-mono text-subink",
                    render: (r) => r.score?.toFixed(4) ?? "—",
                  },
                  {
                    key: "advOccupancy",
                    header: "ADV 占用率",
                    align: "right",
                    className: "font-mono text-subink",
                    render: (r) => r.advOccupancy || "—",
                  },
                  {
                    key: "stStatus",
                    header: "ST 狀態",
                    render: (r) => (
                      <span className={r.stStatus === "ST" ? "text-danger" : r.stStatus && r.stStatus !== "—" ? "text-ok" : "text-subink"}>
                        {r.stStatus || "—"}
                      </span>
                    ),
                  },
                ]}
              />
            </div>
          )}

          {activeTab === "filters" && (
            <div className="space-y-4">
              <div className="text-sm font-semibold text-[#E6EDF7] mb-2">否決器過濾審計 (Veto Filtering Summary)</div>
              <div className="grid grid-cols-1 md:grid-cols-5 gap-4 py-2">
                <div className="p-3 bg-bg border border-line rounded-lg text-center">
                  <div className="text-2xl font-bold font-mono text-[#E6EDF7]">{rawCandidateCount}</div>
                  <div className="text-[11px] text-subink mt-1">原始候選股票數</div>
                </div>
                <div className="p-3 bg-bg border border-line rounded-lg text-center">
                  <div className="text-2xl font-bold font-mono text-subink">—</div>
                  <div className="text-[11px] text-subink mt-1">基本面否決 (ROE/扣非)</div>
                </div>
                <div className="p-3 bg-bg border border-line rounded-lg text-center">
                  <div className="text-2xl font-bold font-mono text-subink">—</div>
                  <div className="text-[11px] text-subink mt-1">流動性過濾 (成交額/ADV)</div>
                </div>
                <div className="p-3 bg-bg border border-line rounded-lg text-center">
                  <div className="text-2xl font-bold font-mono text-subink">—</div>
                  <div className="text-[11px] text-subink mt-1">風險特徵否決 (ST/高波動)</div>
                </div>
                <div className="p-3 bg-bg border border-line/80 rounded-lg text-center border-dashed">
                  <div className="text-2xl font-bold font-mono text-ok">{executedSignalCount}</div>
                  <div className="text-[11px] text-subink mt-1">通過後執行名單</div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "execRisk" && (
            <div className="space-y-4">
              <div className="text-sm font-semibold text-[#E6EDF7] mb-2">執行可行性與滑點衝擊風險評估</div>
              <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="p-4 bg-bg border border-line rounded-lg">
                  <div className="text-[11px] text-subink">預計單邊交易滑點</div>
                  <div className="text-xl font-bold text-[#E6EDF7] mt-1.5 font-mono">—</div>
                  <div className="text-[10px] text-[#5F728A] mt-1">後端未提供滑點估計</div>
                </div>
                <div className="p-4 bg-bg border border-line rounded-lg">
                  <div className="text-[11px] text-subink">預估市場衝擊成本</div>
                  <div className="text-xl font-bold text-[#E6EDF7] mt-1.5 font-mono">—</div>
                  <div className="text-[10px] text-[#5F728A] mt-1">後端未提供市場衝擊估計</div>
                </div>
                <div className="p-4 bg-bg border border-line rounded-lg">
                  <div className="text-[11px] text-subink">高擁擠度持仓數量</div>
                  <div className="text-xl font-bold text-warn mt-1.5 font-mono">—</div>
                  <div className="text-[10px] text-[#5F728A] mt-1">後端未提供擁擠度評估</div>
                </div>
                <div className="p-4 bg-bg border border-line rounded-lg">
                  <div className="text-[11px] text-subink">執行風險綜合評級</div>
                  <div className="text-xl font-bold text-ok mt-1.5 font-mono">—</div>
                  <div className="text-[10px] text-[#5F728A] mt-1">後端未提供綜合評級</div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
