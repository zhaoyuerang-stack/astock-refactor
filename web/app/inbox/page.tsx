"use client";

// 今日简报 + 决策收件箱 —— 产品主界面(「系统找人」,非「人巡视看板」)。
// 三问一屏:能不能信当前池(trust 透传)/ 今天要裁决几件事(收件箱)/ 系统与世界背景。
// 诚实纪律:severity/headline 全部由后端 fail-closed 装配,本页只呈现,不改写不补绿;
// 空收件箱是功能(系统健康),不是待填充的空态;decision_count=-1(收件箱不可读)须显式报错。

import { useCallback, useState } from "react";
import PageHeader from "@/components/ui/PageHeader";
import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import { api } from "@/lib/api";
import type { DailyBriefView, DecisionInboxView, DecisionItem, DecisionSeverity } from "@/lib/types";
import { useAutoRefresh } from "@/lib/useAutoRefresh";

const SEV_META: Record<DecisionSeverity, { label: string; chip: string; border: string }> = {
  blocked:   { label: "必须裁决", chip: "bg-danger/15 text-danger", border: "border-danger/40" },
  attention: { label: "待裁决",   chip: "bg-warn/15 text-warn",     border: "border-warn/40" },
  info:      { label: "常设建议", chip: "bg-subink/10 text-subink", border: "border-line/50" },
};

function DecisionCard({ item }: { item: DecisionItem }) {
  const [open, setOpen] = useState(false);
  const meta = SEV_META[item.severity] ?? SEV_META.info;
  return (
    <div className={`rounded-lg border ${meta.border} bg-bg px-4 py-3`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left flex items-start gap-3"
      >
        <span className={`shrink-0 mt-0.5 text-[11px] px-2 py-0.5 rounded ${meta.chip}`}>
          {meta.label}
        </span>
        <span className="text-sm text-ink font-medium leading-relaxed flex-1">{item.title}</span>
        <span className="text-subink text-xs mt-1">{open ? "收起" : "证据"}</span>
      </button>
      {open && (
        <div className="mt-3 pl-1 space-y-3 text-[12px] leading-relaxed">
          <div>
            <div className="text-subink mb-1">机械证据</div>
            <ul className="list-disc pl-5 text-ink/90 space-y-0.5">
              {item.evidence.map((e, i) => <li key={i} className="break-all">{e}</li>)}
            </ul>
          </div>
          {item.consequence && (
            <div><span className="text-subink">不裁决的后果:</span><span className="text-ink/90">{item.consequence}</span></div>
          )}
          {item.actions.length > 0 && (
            <div>
              <div className="text-subink mb-1">动作(advisory,经 canonical 入口人工执行)</div>
              <div className="space-y-1">
                {item.actions.map((a, i) => (
                  <div key={i} className="flex flex-wrap items-baseline gap-2">
                    <span className={a.allowed ? "text-ink" : "text-subink line-through"}>{a.label}</span>
                    <code className="text-[11px] text-subink bg-panel px-1.5 py-0.5 rounded break-all">{a.entrypoint}</code>
                    {!a.allowed && <span className="text-danger text-[11px]">{a.reason}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="text-subink">
            权威:{item.authority}
            {item.drilldown && <span className="ml-2">溯源:<code className="text-[11px]">{item.drilldown}</code></span>}
          </div>
        </div>
      )}
    </div>
  );
}

function WorldChip({ name, section }: { name: string; section?: { status: string; [k: string]: unknown } }) {
  if (!section) return null;
  const ok = section.status === "ok";
  const tone = ok ? "text-ok" : section.status === "unknown" ? "text-danger" : "text-warn";
  const detail = Object.entries(section)
    .filter(([k]) => k !== "status")
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(" · ");
  return (
    <div className="flex items-baseline gap-2 text-[12px]">
      <span className="text-subink w-14 shrink-0">{name}</span>
      <span className={`${tone} font-medium`}>{section.status}</span>
      <span className="text-subink break-all">{detail}</span>
    </div>
  );
}

export default function InboxPage() {
  const [brief, setBrief] = useState<DailyBriefView | null>(null);
  const [inbox, setInbox] = useState<DecisionInboxView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.dailyBrief().then(setBrief).catch((e) => setError(String(e)));
    api.decisionInbox().then(setInbox).catch((e) => setError(String(e)));
  }, []);
  useAutoRefresh(load, 60000);

  const activity = brief?.system_activity as
    | { status?: string; candidates_total?: number; candidates_recent_7d?: number; review_pending?: number; error?: string }
    | undefined;

  return (
    <div className="max-w-4xl">
      <PageHeader
        title="今日简报 · 决策收件箱"
        desc="系统找人:只有需要人裁决的事项才出现在这里;其余全自动、可溯源。空收件箱 = 系统健康。"
      />

      {error && (
        <StatusBanner status="blocked" title="后端不可达——本页所有结论失效,不得视为无事。" detail={error} />
      )}

      {/* 第一问:能不能信当前池(原样透传 trust-calibration,禁改写) */}
      {brief && (
        <div className="mb-4">
          <StatusBanner status={brief.trust_banner_status} title={brief.trust_headline} />
        </div>
      )}

      {/* 第二问:今天要裁决几件事 */}
      {inbox && (
        <Card
          title={inbox.pending_count > 0 ? `待你裁决(${inbox.pending_count})` : "待你裁决"}
          subtitle={inbox.headline}
          tone={inbox.items.some((i) => i.severity === "blocked") ? "danger"
            : inbox.pending_count > 0 || !inbox.all_sources_readable ? "warn" : "ok"}
          className="mb-4"
        >
          {inbox.items.length === 0 ? (
            <p className="text-sm text-subink py-2">
              收件箱为空且所有事实源已确认——今天系统不需要你。这是完整的决策支持,不是缺数据。
            </p>
          ) : (
            <div className="space-y-2">
              {inbox.items.map((item) => <DecisionCard key={item.key} item={item} />)}
            </div>
          )}
        </Card>
      )}
      {brief && brief.decision_count === -1 && (
        <StatusBanner status="blocked" title="决策收件箱不可读——不得视为无事。" detail={brief.decision_headline} />
      )}

      {/* 第三问:背景(系统干了什么 / 世界变化)。放最后:背景不该抢裁决的注意力 */}
      <div className="grid md:grid-cols-2 gap-4">
        <Card title="系统自己干了什么" subtitle="autoresearch 漏斗(机械计数,非叙述)">
          {activity?.status === "ok" ? (
            <div className="text-[12px] space-y-1 text-ink/90">
              <div>候选总数 {activity.candidates_total} · 近 7 天新增 {activity.candidates_recent_7d}</div>
              <div>待人工复核 {activity.review_pending}</div>
            </div>
          ) : (
            <p className="text-[12px] text-warn">活动面不可读:{activity?.error ?? "加载中…"}</p>
          )}
        </Card>
        <Card title="世界有什么变化" subtitle="数据 / 衰减 / paper 实测(逐源诚实降级)">
          <div className="space-y-1.5">
            <WorldChip name="数据" section={brief?.world_state?.data} />
            <WorldChip name="衰减" section={brief?.world_state?.decay} />
            <WorldChip name="模拟盘" section={brief?.world_state?.paper} />
          </div>
        </Card>
      </div>

      {inbox && (
        <p className="text-[11px] text-subink mt-4 leading-relaxed">{inbox.honesty}</p>
      )}
    </div>
  );
}
