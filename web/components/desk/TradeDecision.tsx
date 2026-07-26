"use client";

import Link from "next/link";
import StatusBanner from "@/components/ui/StatusBanner";
import Card from "@/components/ui/Card";
import type { TradeReadinessView } from "@/lib/types";

// PM 交易台答案先行(DECISION_COCKPITS 驾驶舱③):今天交易吗?用什么?多大风险?谁签字?
// 全部来自 /trade-readiness(后端聚合裁决);前端只读呈现,不造 P&L、不在 UI 改口径。

type Tone = "ok" | "warn" | "danger" | "muted";
const TONE_CLS: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  muted: "text-[#5F728A]",
};

const MODEL_LABEL: Record<string, { tone: Tone; txt: string }> = {
  approved: { tone: "ok", txt: "已批准" },
  not_registered: { tone: "danger", txt: "无在册可部署标的" },
  dsr_pending: { tone: "warn", txt: "DSR 待审计" },
  dsr_not_significant: { tone: "danger", txt: "DSR 多重检验不显著" },
  nine_gate_failed: { tone: "danger", txt: "9-Gate 运行失败" },
  deployment_not_ready: { tone: "danger", txt: "部署未就绪" },
};

function modelInfo(v: string): { tone: Tone; txt: string } {
  return MODEL_LABEL[v] || { tone: "warn", txt: v };
}

function healthInfo(v: string): { tone: Tone; txt: string } {
  if (v === "normal") return { tone: "ok", txt: "正常" };
  if (v === "watch") return { tone: "warn", txt: "观察" };
  if (v === "degraded") return { tone: "danger", txt: "衰减(decay)" };
  return { tone: "muted", txt: v || "未知" };
}

function GateChip({ label, tone, value }: { label: string; tone: Tone; value: string }) {
  return (
    <div className="p-3 bg-navy border border-line rounded-lg">
      <div className="text-[11px] text-subink">{label}</div>
      <div className={`text-sm font-bold font-mono mt-1 ${TONE_CLS[tone]}`}>{value}</div>
    </div>
  );
}

export default function TradeDecision({ readiness }: { readiness: TradeReadinessView | null }) {
  if (!readiness) {
    return (
      <Card title="今日交易决策 (Trade Decision)">
        <div className="text-[12px] text-subink leading-relaxed">
          正在從 <span className="font-mono">/trade-readiness</span> 載入……
          後端未啟動時此處為空,<span className="text-warn">不顯示任何假就緒態</span>。
        </div>
      </Card>
    );
  }

  const allowed = readiness.allowed_to_trade;
  const model = modelInfo(readiness.model_version);
  const health = healthInfo(readiness.factor_health);
  const riskOk = readiness.portfolio_risk === "within limit";
  const dataOk = readiness.data_status === "可用" || readiness.data_status === "关注";
  const killArmed = readiness.kill_switch_status === "armed";
  const benched = readiness.model_version === "not_registered";

  // 绑定原因:为什么不能交易(只列真正阻断项)
  const reasons: string[] = [];
  if (readiness.model_version !== "approved") reasons.push(`模型未就绪:${model.txt}`);
  if (readiness.factor_health !== "normal") reasons.push(`因子健康:${health.txt}`);
  if (!dataOk) reasons.push(`数据:${readiness.data_status}`);
  if (!riskOk) reasons.push(`组合风险:${readiness.portfolio_risk}`);
  if (!killArmed) reasons.push(`熔断:${readiness.kill_switch_status}`);
  if (readiness.human_approval_required) reasons.push("需人工签字放行");

  return (
    <div className="space-y-4">
      <StatusBanner
        status={allowed ? "ready" : "blocked"}
        title={allowed ? "今日可交易:是" : "今日可交易:否"}
        detail={
          allowed ? (
            <>市场制度 {readiness.regime_status} · 全部门禁通过,可生成正式信号。</>
          ) : (
            <>
              阻断 {reasons.length} 项:{reasons.join(" · ") || "—"}。市场制度 {readiness.regime_status}。
            </>
          )
        }
      />

      {/* 决策闸门一览(答案先行,逐项可读) */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <GateChip label="数据" tone={dataOk ? "ok" : "warn"} value={readiness.data_status} />
        <GateChip label="模型/入册" tone={model.tone} value={model.txt} />
        <GateChip label="因子健康/decay" tone={health.tone} value={health.txt} />
        <GateChip label="组合风险" tone={riskOk ? "ok" : "danger"} value={riskOk ? "限内" : "超限"} />
        <GateChip label="熔断" tone={killArmed ? "ok" : "danger"} value={readiness.kill_switch_status} />
        <GateChip label="人工签字" tone={readiness.human_approval_required ? "warn" : "ok"} value={readiness.human_approval_required ? "需要" : "不需要"} />
      </div>

      {/* 板凳态 + 解锁路径(无在册可部署标的时) */}
      {benched && (
        <Card title="资金在板凳上 (Capital on Bench)">
          <div className="text-[12px] text-subink leading-relaxed">
            当前 <span className="text-danger font-semibold">无在册可部署标的</span>(0 在册 + 部署 fail-closed),
            交易台诚实显示板凳态,<span className="text-warn">不造 live 收益曲线</span>。
          </div>
          <div className="text-[12px] text-subink mt-2">
            解锁路径:候选 →{" "}
            <Link href="/system-governance" className="text-brand hover:underline font-mono">验证闸门(9-Gate)</Link>{" "}
            → 入册 → 部署。研究投向见{" "}
            <Link href="/alpha-factory" className="text-brand hover:underline font-mono">Alpha 工厂·晋级就绪</Link>。
          </div>
        </Card>
      )}
    </div>
  );
}
