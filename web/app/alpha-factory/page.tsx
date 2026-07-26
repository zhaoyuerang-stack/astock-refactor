"use client";

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import PromotionReadiness from "@/components/factory/PromotionReadiness";
import { api } from "@/lib/api";
import { useAgent } from "@/lib/agentStore";
import { useAutoRefresh } from "@/lib/useAutoRefresh";
import type { PromotionReadinessView } from "@/lib/types";

// Alpha 工厂 · 晋级就绪驾驶舱(DECISION_COCKPITS 驾驶舱①)。
// 0 在册时系统唯一能动的价值决策:下一个推进哪个候选、卡它的那一个约束是什么。
export default function AlphaFactoryPage() {
  const setContext = useAgent((s) => s.setContext);
  const [err, setErr] = useState<string | null>(null);
  const [data, setData] = useState<PromotionReadinessView | null>(null);

  const load = useCallback(() => {
    setErr(null);
    api
      .promotionReadiness()
      .then((d) => {
        setData(d);
        const lead = d.candidates[0];
        setContext({
          page: "alpha-factory",
          title: "Alpha 工厂 · 晋级就绪",
          summary: d.lead_candidate
            ? `最接近入册:${d.lead_candidate}(卡在 ${d.lead_blocker})。${d.research_steer}。候选 ${d.candidates.length} 个,按距入册排序。`
            : "暂无候选。",
          evidence: [
            `候选数: ${d.candidates.length}`,
            lead ? `lead: ${d.lead_candidate} 距入册 ${lead.distance_to_register} 门 · 裁决 ${lead.authoritative_verdict}` : "—",
            `最拥挤家族对: ${
              d.cluster_map?.most_crowded
                ? `${d.cluster_map.most_crowded.a}↔${d.cluster_map.most_crowded.b}=${d.cluster_map.most_crowded.corr}`
                : "—"
            }`,
            "排序按距入册,非按收益(避诱导过拟合)",
          ],
          risk: lead && lead.crowding !== null && lead.crowding > 0.7
            ? [`最接近入册者所在信息簇已拥挤(corr ${lead.crowding.toFixed(2)}),继续微调边际≈0`]
            : [],
          recommendation: [
            lead ? lead.marginal_action : "—",
            "逐门诊断仅定位卡点;权威裁决在后端 decide_nine_gate,AI 不得自动晋级",
          ],
          nextActions: [
            "点开候选行查看逐门诊断证据",
            "对拥挤簇,优先寻找新信息源而非簇内微调",
          ],
        });
      })
      .catch((e) => setErr(String(e)));
  }, [setContext]);

  useAutoRefresh(load);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Alpha 工厂 · 晋级就绪"
        desc="候选按「距入册」排序 + 唯一卡点门 + 边际动作 + 信息簇拥挤度(Promotion Readiness)"
      />
      {err && (
        <div className="p-4 bg-[#FF5C5C]/10 border border-[#FF5C5C]/20 rounded-lg text-sm text-danger">
          ⚠️ API 載入出錯: {err}
        </div>
      )}
      <PromotionReadiness data={data} />
    </div>
  );
}
