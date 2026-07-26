"use client";

import { useCallback, useMemo, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api } from "@/lib/api";
import type { FactorView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";

type ExperimentRow = {
  priority: string;
  hypothesis: string;
  desc: string;
  universe: string;
  stage: string;
  status: string;
  creator: string;
  date: string;
  reason?: string;
};

export default function FactorResearchPage() {
  const setContext = useAgent((s) => s.setContext);

  const [factors, setFactors] = useState<FactorView[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState<"performance" | "group" | "turnover" | "style" | "queue">("performance");

  const load = useCallback(() => {
    setErr(null);
    api.factors()
      .then((data) => {
        setFactors(data);
        if (!selectedId && data.length > 0) setSelectedId(data[0].name);

        setContext({
          page: "factor-research",
          title: "因子研究實驗室",
          summary: `当前展示 ${data.length} 个 registry 派生因子家族。IC、ICIR、风格相关和实验队列仅在后端产出证据后显示。`,
          evidence: [
            "数据来源: /factors registry 家族级只读视图",
            `因子家族数: ${data.length}`,
            `在册版本合计: ${data.reduce((sum, f) => sum + (f.n_registered ?? 0), 0)}`,
          ],
          risk: [],
          recommendation: [
            "需要 IC / ICIR 时先运行确定性因子审计",
            "需要风格相关时先落库对应研究证据",
          ],
          nextActions: [
            "查看策略台账中的家族版本生命周期",
            "从实验队列运行 L0-L3 后再回填证据",
          ],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [selectedId, setContext]);

  useAutoRefresh(load);



  const activeFactor = factors.find((f) => f.name === selectedId) ?? factors[0];

  const selectedFactor = useMemo(() => {
    if (activeFactor) {
      return {
        id: activeFactor.name,
        name: activeFactor.display_name || activeFactor.name,
        category: activeFactor.regime || "量價",
        ic: NaN,
        icir: NaN,
        neutralizedIcir: NaN,
        coverage: NaN,
        author: "registry",
        createdAt: "—",
        dataStart: "—",
        universe: activeFactor.regime || "A股全市場",
      };
    }
    return {
      id: "—",
      name: "未选择因子",
      category: "—",
      ic: NaN,
      icir: NaN,
      neutralizedIcir: NaN,
      coverage: NaN,
      author: "—",
      createdAt: "—",
      dataStart: "—",
      universe: "—",
    };
  }, [activeFactor]);


  const filteredFactors = factors.filter((f) => {
    const q = searchQuery.toLowerCase();
    return (
      f.name.toLowerCase().includes(q) ||
      (f.display_name && f.display_name.toLowerCase().includes(q)) ||
      f.regime.toLowerCase().includes(q)
    );
  });

  const styleCorrelation: { style: string; corr: number }[] = [];
  const experimentQueue: ExperimentRow[] = [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="因子研究"
        desc="底層量化因子庫管理、IC / ICIR 歸因分析與因子風格相關性熱力圖 (Factor Research Lab)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {/* 1. 因子總覽指標 */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">因子總數</div>
          <div className="text-2xl font-bold font-mono text-[#E6EDF7] mt-1.5">{factors.length} 個</div>
          <div className="text-[10px] text-[#5F728A] mt-2">已註冊獨立 alpha 家族</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">今日實驗數</div>
          <div className="text-2xl font-bold font-mono text-brand mt-1.5">
            {experimentQueue.length > 0 ? `${experimentQueue.length} 個` : "—"}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">由實驗隊列 API 落庫後顯示</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">平均 IC</div>
          <div className="text-2xl font-bold font-mono text-ok mt-1.5">
            {isNaN(selectedFactor.ic) ? "—" : selectedFactor.ic.toFixed(3)}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">市值與行業雙重中性化後</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">中性化 ICIR</div>
          <div className="text-2xl font-bold font-mono text-ok mt-1.5">
            {isNaN(selectedFactor.neutralizedIcir) ? "—" : selectedFactor.neutralizedIcir.toFixed(2)}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">Newey-West 延遲修正</div>
        </div>
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">數據覆蓋率</div>
          <div className="text-2xl font-bold font-mono text-ok mt-1.5">
            {isNaN(selectedFactor.coverage) ? "—" : `${(selectedFactor.coverage * 100).toFixed(1)}%`}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">若低於 95% 將觸發報警</div>
        </div>
      </div>

      {/* 2. 雙欄布局:左因子庫,右詳情 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        {/* Left Col: Factor Library list */}
        <div className="space-y-4">
          <Card title="因子庫 (Factor Library)">
            <div className="mb-3">
              <input
                type="text"
                placeholder="🔍 搜尋因子 (中/英)..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full text-xs px-2.5 py-1.5 bg-bg border border-line text-[#E6EDF7] rounded"
              />
            </div>

            <div className="space-y-1.5 max-h-[480px] overflow-y-auto pr-1">
              {filteredFactors.map((f) => {
                const isSelected = selectedId === f.name;
                return (
                  <div
                    key={f.name}
                    onClick={() => {
                      setSelectedId(f.name);
                    }}
                    className={`p-2.5 rounded border border-line cursor-pointer transition-colors ${
                      isSelected
                        ? "bg-[#3D7BFF]/10 border-[#3D7BFF] text-[#E6EDF7]"
                        : "bg-bg hover:bg-[#1F3550]/40 text-subink hover:text-[#E6EDF7]"
                    }`}
                  >
                    <div className="flex justify-between items-start">
                      <span className="font-mono font-semibold text-xs truncate max-w-[150px]">
                        {f.display_name || f.name}
                      </span>
                      <span className="text-[10px] px-1 bg-navy rounded text-subink scale-95">{f.regime}</span>
                    </div>
                    <div className="flex justify-between text-[10px] mt-2 font-mono text-[#5F728A]">
                      <span>IC: 未計算</span>
                      <span>ICIR: 未計算</span>
                    </div>
                  </div>
                );
              })}
              {filteredFactors.length === 0 && (
                <div className="text-center text-xs text-[#5F728A] py-6">找不到匹配的因子</div>
              )}
            </div>
          </Card>
        </div>

        {/* Right Col: Factor details & Tabs */}
        <div className="lg:col-span-2 space-y-6">
          {/* Factor Profile */}
          <Card title={`Dossier: ${selectedFactor.name}`}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs font-mono text-subink py-2">
              <div>
                <div><span className="text-[#5F728A]">因子代碼:</span> {selectedFactor.id}</div>
                <div><span className="text-[#5F728A]">因子作者:</span> {selectedFactor.author}</div>
              </div>
              <div>
                <div><span className="text-[#5F728A]">創建時間:</span> {selectedFactor.createdAt}</div>
                <div><span className="text-[#5F728A]">數據起點:</span> {selectedFactor.dataStart}</div>
              </div>
              <div>
                <div><span className="text-[#5F728A]">覆蓋範圍:</span> {selectedFactor.universe}</div>
                <div><span className="text-[#5F728A]">因子類型:</span> {selectedFactor.category}</div>
              </div>
              <div>
                <div><span className="text-[#5F728A]">覆蓋比率:</span> {isNaN(selectedFactor.coverage) ? "—" : `${(selectedFactor.coverage * 100).toFixed(1)}%`}</div>
                <div><span className="text-[#5F728A]">有效性判定:</span> <span className="text-ok font-bold">{activeFactor?.status || "PASS"}</span></div>
              </div>
            </div>
          </Card>

          {/* Details tab menu */}
          <div className="space-y-4">
            <div className="flex border-b border-line gap-2 select-none">
              <button
                onClick={() => setActiveTab("performance")}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-b-2 ${
                  activeTab === "performance" ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                }`}
              >
                因子表現 (IC 時序)
              </button>
              <button
                onClick={() => setActiveTab("group")}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-b-2 ${
                  activeTab === "group" ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                }`}
              >
                分組收益 (Monotonicity)
              </button>
              <button
                onClick={() => setActiveTab("turnover")}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-b-2 ${
                  activeTab === "turnover" ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                }`}
              >
                換手與成本敏感性
              </button>
              <button
                onClick={() => setActiveTab("style")}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-b-2 ${
                  activeTab === "style" ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                }`}
              >
                風格相關性
              </button>
              <button
                onClick={() => setActiveTab("queue")}
                className={`px-3 py-1.5 text-xs font-bold transition-all border-b-2 ${
                  activeTab === "queue" ? "border-[#3D7BFF] text-[#E6EDF7]" : "border-transparent text-subink"
                }`}
              >
                實驗隊列 (Funnel)
              </button>
            </div>

            <div className="bg-navy border border-line rounded-lg p-4 text-xs">
              {activeTab === "performance" && (
                <div className="space-y-4">
                  <div className="flex justify-between items-center font-bold text-[#E6EDF7]">
                    <span>IC 歷史滾動時序 (IC Time Series Curve)</span>
                    <span className="text-[11px] text-subink font-mono">待接入審計結果</span>
                  </div>
                  
                  <div className="flex flex-col items-center justify-center h-40 text-subink font-mono border border-line/45 rounded bg-[#06111F]/30">
                    <span className="text-[13px] font-semibold text-[#8E8E93]">📊 暫無該因子的歷史 IC 時序數據</span>
                    <span className="text-[10px] text-[#5F728A] mt-1">需在策略實驗室運行 Phase 1 審計生成時序</span>
                  </div>
                </div>
              )}

              {activeTab === "group" && (
                <div className="space-y-4">
                  <div className="font-bold text-[#E6EDF7]">等權分組累計超額收益 (Group Monotonicity)</div>
                  
                  <div className="flex flex-col items-center justify-center h-40 text-subink font-mono border border-line/45 rounded bg-[#06111F]/30">
                    <span className="text-[13px] font-semibold text-[#8E8E93]">📊 暫無該因子的等權分組收益數據</span>
                  </div>
                </div>
              )}

              {activeTab === "turnover" && (
                <div className="space-y-4">
                  <div className="font-bold text-[#E6EDF7] mb-2">年化換手與扣除交易成本敏感性 (Cost Sensitivity Matrix)</div>
                  <div className="flex flex-col items-center justify-center h-40 text-subink font-mono border border-line/45 rounded bg-[#06111F]/30">
                    <span className="text-[13px] font-semibold text-[#8E8E93]">📊 暫無該因子的換手與成本敏感性數據</span>
                  </div>
                </div>
              )}

              {activeTab === "style" && (
                <div className="space-y-4">
                  <div className="font-bold text-[#E6EDF7] mb-2">Barra 風格暴露相關性矩陣 (Style Beta Correlation Heatmap)</div>
                  <div className="border border-line/40 rounded overflow-hidden">
                    <table className="w-full text-left font-mono">
                      <thead>
                        <tr className="bg-[#10263D] border-b border-line text-subink">
                          <th className="p-2 font-medium">風格特徵</th>
                          <th className="p-2 font-medium text-right">對應相關性</th>
                          <th className="p-2 font-medium">風險評級 / 中性化建議</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-[#1F3550]/30 text-[#E6EDF7]">
                        {styleCorrelation.map((item) => {
                          const isHigh = item.corr >= 0.7;
                          return (
                            <tr key={item.style}>
                              <td className="p-2">{item.style}</td>
                              <td className={`p-2 text-right font-bold ${isHigh ? "text-danger" : "text-subink"}`}>
                                {item.corr.toFixed(2)}
                              </td>
                              <td className="p-2">
                                {isHigh ? (
                                  <span className="text-danger font-bold">⚠️ 風格污染 (相關 &gt; 0.70)，強制中性化</span>
                                ) : item.corr > 0.3 ? (
                                  <span className="text-warn">建议中性化</span>
                                ) : (
                                  <span className="text-[#5F728A]">暴露安全</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {activeTab === "queue" && (
                <div className="space-y-4">
                  <div className="font-bold text-[#E6EDF7] mb-2">研發漏斗實驗隊列 (R&D Experiment Pipeline)</div>
                  <DataTable<ExperimentRow>
                    rows={experimentQueue}
                    getRowKey={(r) => r.hypothesis}
                    columns={[
                      { key: "priority", header: "級別", className: "font-mono font-bold text-danger w-12", render: (r) => r.priority },
                      { key: "hypothesis", header: "實驗代碼", className: "font-mono text-brand font-semibold", render: (r) => r.hypothesis },
                      { key: "desc", header: "假設描述", className: "text-subink truncate max-w-[200px]", render: (r) => r.desc },
                      { key: "universe", header: "範圍", className: "text-subink", render: (r) => r.universe },
                      {
                        key: "stage",
                        header: "階段",
                        className: "font-bold",
                        render: (r) => (
                          <span className={r.stage === "L3" ? "text-ok" : "text-subink"}>
                            {r.stage}
                          </span>
                        ),
                      },
                      {
                        key: "status",
                        header: "狀態",
                        render: (r) => (
                          <span className={r.status === "運行中" ? "text-brand animate-pulse" : r.status === "已拒絕" ? "text-danger" : "text-subink"}>
                            {r.status}
                          </span>
                        ),
                      },
                      { key: "reason", header: "備註/拒絕原因", className: "text-danger text-[11px] max-w-[200px] truncate", render: (r) => r.reason || "—" },
                      { key: "creator", header: "創建者", className: "text-[#5F728A] font-mono", render: (r) => r.creator },
                    ]}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
