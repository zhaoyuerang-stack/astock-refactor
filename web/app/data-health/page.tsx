"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import DataTable from "@/components/ui/DataTable";
import { api, pct } from "@/lib/api";
import type { DataQualityView } from "@/lib/types";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import { latestDateFromRange } from "@/lib/freshness";

type PipelineStatusRow = {
  node: string;
  status: "success" | "running" | "lag" | "failed";
  completionTime: string;
  isDelayed: boolean;
};

type QualityCheckRow = {
  checkItem: string;
  status: "passed" | "warning" | "failed";
  anomalyCount: number;
  anomalyRatio: string;
  severity: "low" | "medium" | "high";
  comment: string;
};

type SourceHealthRow = {
  source: string;
  domain: string;
  latestDate: string;
  latency: string;
  failureRate: string;
  status: "active" | "warning" | "inactive";
};

type ActiveIssueRow = {
  code: string;
  type: string;
  severity: "low" | "medium" | "high";
};

export default function DataHealthPage() {
  const setContext = useAgent((s) => s.setContext);

  const [dq, setDq] = useState<DataQualityView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    api.dataQuality()
      .then((data) => {
        setDq(data);
        const latestDate = latestDateFromRange(data.duckdb?.date_range);

        setContext({
          page: "data-health",
          title: "數據健康中心",
          summary: `數據庫健康度判決：【${data.verdict}】。PIT 通過率: ${(data.clean_ratio * 100).toFixed(1)}%。覆蓋 A 股 ${data.total} 只。`,
          evidence: [
            `最新可交易日對齊: ${latestDate}`,
            `PIT 數據庫驗證率: ${(data.clean_ratio * 100).toFixed(1)}%`,
            `質量異常數: 嚴重問題 ${data.severe_count}只 · 正常跳變 ${data.jump_count}只`,
          ],
          risk: data.severe_count > 0 ? [`發現 ${data.severe_count} 只個股具有負價或 OHLC 一致性硬傷，可能影響今日操作信號`] : [],
          recommendation: [
            "對有嚴重一致性硬傷的證券實施交易禁入熔斷",
            "必要时启动补数据脚本重新拉取备用源",
          ],
          nextActions: [
            "運行本地 DuckDB 價量即席覆核查詢",
            "標記財務數據錯配問題為已修復",
          ],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [setContext]);

  useAutoRefresh(load);

  const latestDate = latestDateFromRange(dq?.duckdb?.date_range);
  const pipelines: PipelineStatusRow[] = [];
  const dataSources: SourceHealthRow[] = [];
  const activeIssues: ActiveIssueRow[] = (dq?.flagged_sample ?? []).map((item) => ({
    code: item.code,
    type: item.issues.join("、"),
    severity: item.issues.some((issue) => /negative|ohlc|非正|负价/i.test(issue)) ? "high" : "medium",
  }));
  const qualityChecks: QualityCheckRow[] = dq
    ? [
        {
          checkItem: "嚴重硬傷(severe_count)",
          status: dq.severe_count > 0 ? "failed" : "passed",
          anomalyCount: dq.severe_count,
          anomalyRatio: dq.total > 0 ? pct(dq.severe_count / dq.total, 2) : "—",
          severity: "high",
          comment: "来自 /data/quality severe_count",
        },
        {
          checkItem: "極端跳變標記(jump_count)",
          status: dq.jump_count > 0 ? "warning" : "passed",
          anomalyCount: dq.jump_count,
          anomalyRatio: dq.total > 0 ? pct(dq.jump_count / dq.total, 2) : "—",
          severity: "medium",
          comment: "来自 /data/quality jump_count",
        },
        ...Object.entries(dq.issue_breakdown ?? {}).map(([key, count]) => ({
          checkItem: key,
          status: Number(count) > 0 ? "warning" as const : "passed" as const,
          anomalyCount: Number(count),
          anomalyRatio: dq.total > 0 ? pct(Number(count) / dq.total, 2) : "—",
          severity: "medium" as const,
          comment: "来自 /data/quality issue_breakdown",
        })),
      ]
    : [];

  const getStatusBadge = (status: string) => {
    const styleMap = {
      success: "text-ok bg-[#35D06E]/10 border-[#35D06E]/20",
      running: "text-brand bg-[#3D7BFF]/10 border-[#3D7BFF]/20",
      lag: "text-warn bg-[#F6B73C]/10 border-[#F6B73C]/20",
      failed: "text-danger bg-[#FF5C5C]/10 border-[#FF5C5C]/20",
      active: "text-ok bg-[#35D06E]/10 border-[#35D06E]/20",
      warning: "text-warn bg-[#F6B73C]/10 border-[#F6B73C]/20",
      inactive: "text-danger bg-[#FF5C5C]/10 border-[#FF5C5C]/20",
      unknown: "text-subink bg-[#8E8E93]/10 border-line",
    };
    return (
      <span className={`px-2 py-0.5 rounded text-[10px] border font-bold font-mono ${styleMap[status as keyof typeof styleMap] || ""}`}>
        {status.toUpperCase()}
      </span>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="數據健康"
        desc="數據集版本覆蓋、時間軸 PIT (Point-In-Time) 數據完整度與管道運行延遲監控 (Data Quality Control)"
      />

      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}

      {/* 1. 數據健康總覽 */}
      <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">最新交易日對齊</div>
          <div className="text-2xl font-bold font-mono text-[#E6EDF7] mt-1.5">{latestDate}</div>
          <div className="text-[10px] text-[#5F728A] mt-2 flex items-center gap-1">
            来自 DuckDB date_range
          </div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">覆蓋股票數</div>
          <div className="text-2xl font-bold font-mono text-[#E6EDF7] mt-1.5">{dq ? `${dq.total} 只` : "—"}</div>
          <div className="text-[10px] text-[#5F728A] mt-2">A股全宇宙退市/停牌處理</div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">PIT 數據庫合規率</div>
          <div className="text-2xl font-bold font-mono text-ok mt-1.5">{dq ? pct(dq.clean_ratio, 1) : "—"}</div>
          <div className="text-[10px] text-[#5F728A] mt-2">防未來函數披露對齊率</div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">質量綜合得分</div>
          <div className="text-2xl font-bold font-mono text-subink mt-1.5">—</div>
          <div className="text-[10px] text-[#5F728A] mt-2">后端未提供综合评分</div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg">
          <div className="text-[12px] text-subink">最新管道延遲</div>
          <div className="text-2xl font-bold font-mono text-subink mt-1.5">—</div>
          <div className="text-[10px] text-[#5F728A] mt-2">后端未提供 ETL 延迟</div>
        </div>

        <div className="p-4 bg-navy border border-line rounded-lg text-center">
          <div className="text-[11px] text-subink uppercase font-bold tracking-wider">數據源狀態</div>
          <div className="mt-2.5">
            {getStatusBadge("unknown")}
          </div>
          <div className="text-[10px] text-[#5F728A] mt-2">后端未提供来源状态</div>
        </div>
      </div>

      {/* 2. 數據管道節點狀態 */}
      <Card title="數據管道調度流節點監控 (ETL Pipelines Status)">
        <div className="grid grid-cols-2 md:grid-cols-7 gap-4 text-center font-mono">
          {pipelines.length > 0 ? pipelines.map((p) => (
            <div key={p.node} className="p-3 bg-bg border border-line rounded-lg space-y-1">
              <div className="text-[10px] text-subink truncate" title={p.node}>{p.node.split(" ")[0]}</div>
              <div className="py-1">{getStatusBadge(p.status)}</div>
              <div className="text-[9px] text-[#5F728A]">完成: {p.completionTime}</div>
              {p.isDelayed && <div className="text-[8px] text-warn font-bold">⚠️ 延時警告</div>}
            </div>
          )) : (
            <div className="col-span-full py-8 text-sm text-subink border border-line rounded-lg bg-bg">
              暂无 ETL 管道节点状态接口数据。
            </div>
          )}
        </div>
      </Card>

      {/* 3. 數據質量檢查報告 (真問題 vs A股正常現象) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        <div className="lg:col-span-2">
          <Card title="數據質量審核檢查細則 (PIT Integrity Check Logs)">
            <div className="text-[11px] text-subink mb-2 leading-relaxed">
              根據量化鐵律#7：必須明確區分<strong>數據真問題</strong>（如負定價、開盤價高於收盤價等硬性邏輯錯）與<strong>A股正常交易現象</strong>（如新股首日無漲跌停、長期停牌成份股剔除、除權跳空等）。
            </div>
            <DataTable<QualityCheckRow>
	              rows={qualityChecks}
	              empty="暂无 /data/quality 明细"
              getRowKey={(r) => r.checkItem}
              columns={[
                { key: "checkItem", header: "審計項目", className: "text-[#E6EDF7] font-semibold", render: (r) => r.checkItem },
                {
                  key: "status",
                  header: "結果",
                  render: (r) => (
	                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
	                      r.status === "passed"
	                        ? "text-ok bg-[#35D06E]/10 border-[#35D06E]/20"
	                        : r.status === "failed"
	                        ? "text-danger bg-[#FF5C5C]/10 border-[#FF5C5C]/20"
	                        : "text-warn bg-[#F6B73C]/10 border-[#F6B73C]/20"
	                    }`}>
	                      {r.status === "passed" ? "PASS" : r.status === "failed" ? "FAIL" : "CHECK"}
                    </span>
                  ),
                },
                { key: "anomalyCount", header: "異常數", align: "right", className: "font-mono text-[#E6EDF7]", render: (r) => r.anomalyCount },
                { key: "anomalyRatio", header: "比例", align: "right", className: "font-mono text-subink", render: (r) => r.anomalyRatio },
                {
                  key: "severity",
                  header: "嚴重度",
                  render: (r) => (
                    <span className={`px-1 rounded text-[9px] font-mono ${
                      r.severity === "high" ? "text-danger" : r.severity === "medium" ? "text-warn" : "text-subink"
                    }`}>
                      {r.severity.toUpperCase()}
                    </span>
                  ),
                },
                { key: "comment", header: "判定依據 / 人工 triaging 建議", className: "text-subink text-[11px] max-w-[200px] truncate", render: (r) => r.comment },
              ]}
            />
          </Card>
        </div>

        {/* Multi source health */}
        <Card title="數據來源多源比對與延遲 (Multi-Source Latency)">
          <DataTable<SourceHealthRow>
            rows={dataSources}
            empty="暂无多源延迟/在线状态接口数据"
            getRowKey={(r) => r.source}
            columns={[
              { key: "source", header: "來源渠道", className: "text-[#E6EDF7] font-bold", render: (r) => r.source },
              { key: "latestDate", header: "最新日期", className: "font-mono text-subink", render: (r) => r.latestDate },
              { key: "latency", header: "拉取延遲", align: "right", className: "font-mono text-[#E6EDF7]", render: (r) => r.latency },
              {
                key: "status",
                header: "在線狀態",
                render: (r) => getStatusBadge(r.status),
              },
            ]}
          />
        </Card>
      </div>

      {/* 4. 異常問題登記冊 */}
      <Card title="異常數據問題跟蹤登記冊 (Active Issues Tracker)">
        <DataTable<ActiveIssueRow>
          rows={activeIssues}
          getRowKey={(r, i) => `${r.code}-${i}`}
          empty="當前無登記的活躍數據異常問題"
          columns={[
            { key: "code", header: "证券代码", className: "font-mono text-[#5F728A]", render: (r) => r.code },
            { key: "type", header: "異常類型與描述", className: "text-subink", render: (r) => r.type },
            {
              key: "severity",
              header: "嚴重度",
              render: (r) => (
                <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold border ${
                  r.severity === "high" ? "bg-[#FF5C5C]/10 border-[#FF5C5C]/20 text-danger" : "bg-[#F6B73C]/10 border-[#F6B73C]/20 text-warn"
                }`}>
                  {r.severity.toUpperCase()}
                </span>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
