"use client";

import Card from "@/components/ui/Card";
import StatusBanner from "@/components/ui/StatusBanner";
import type { PaperAccountsListView, PaperAccountView } from "@/lib/types";
import {
  accountsDisplayState,
  accountStatusBadge,
  orderedAccounts,
  overallVerdict,
} from "@/lib/paperAccounts.mjs";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-ok/10 border-ok/25 text-ok",
  danger: "bg-danger/10 border-danger/25 text-danger",
  warn: "bg-warn/10 border-warn/25 text-warn",
  neutral: "bg-line/10 border-line/40 text-subink",
};

function StatusBadge({ status }: { status: string }) {
  const badge = accountStatusBadge(status);
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-bold border ${TONE_CLASS[badge.tone] ?? TONE_CLASS.neutral}`}>
      {badge.label}
    </span>
  );
}

// 极简账户内 mini NAV 走势(SVG polyline,无第三方图表库依赖,和既有 NavChart 同风格但缩小)。
function MiniNavSpark({ points }: { points: PaperAccountView["nav_points"] }) {
  const W = 220;
  const H = 56;
  if (points.length < 2) {
    return <div className="h-14 flex items-center text-[11px] text-subink">净值点数不足({points.length}),暂无曲线</div>;
  }
  const navs = points.map((p) => p.nav);
  const lo = Math.min(...navs);
  const hi = Math.max(...navs);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / span) * H;
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.nav).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14" role="img" aria-label="账户净值走势">
      <polyline points={line} fill="none" stroke="#3D7BFF" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function DeviationCell({ deviation }: { deviation: PaperAccountView["backtest_deviation"] }) {
  if (!deviation.available) {
    return <div className="text-[11px] text-subink">回测偏差:{deviation.reason}</div>;
  }
  const dev = deviation.cumulative_deviation;
  return (
    <div className="text-[11px] text-subink">
      回测偏差:
      <span className={`font-mono font-semibold ml-1 ${dev >= 0 ? "text-ok" : "text-danger"}`}>
        {dev >= 0 ? "+" : ""}
        {(dev * 100).toFixed(2)}%
      </span>
      {deviation.tracking_error != null && (
        <span className="ml-2">跟踪误差 {(deviation.tracking_error * 100).toFixed(2)}%</span>
      )}
      <span className="ml-2 text-[10px]">
        窗口 {deviation.window_start}~{deviation.window_end}
      </span>
    </div>
  );
}

function AccountCard({ account }: { account: PaperAccountView }) {
  return (
    <div className="p-3 bg-bg border border-line rounded-lg space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="font-mono text-[12px] font-semibold text-ink truncate" title={account.name}>
          {account.name}
        </div>
        <StatusBadge status={account.status} />
      </div>
      {account.reason && (
        <div className="text-[10px] text-subink leading-relaxed">{account.reason}</div>
      )}
      <MiniNavSpark points={account.nav_points} />
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-subink">
          NAV <span className="font-mono text-ink">{account.latest_nav ? account.latest_nav.toLocaleString("zh-CN", { maximumFractionDigits: 0 }) : "—"}</span>
        </span>
        <span className={`font-mono ${account.total_return >= 0 ? "text-ok" : "text-danger"}`}>
          {account.total_return >= 0 ? "+" : ""}
          {(account.total_return * 100).toFixed(2)}%
        </span>
        <span className="text-danger font-mono">回撤 {(account.max_drawdown * 100).toFixed(2)}%</span>
      </div>
      <DeviationCell deviation={account.backtest_deviation} />
    </div>
  );
}

export default function PaperAccountsPanel({ data }: { data: PaperAccountsListView | null }) {
  const state = accountsDisplayState(data);
  const verdict = overallVerdict(data);

  return (
    <div className="space-y-3">
      <StatusBanner status={verdict.status} title={verdict.title} detail={verdict.detail} />

      {state === "error" && (
        <Card title="排名靠前策略并排实测">
          <div className="text-[12px] text-danger">{data?.error}</div>
        </Card>
      )}

      {state === "empty" && (
        <Card title="排名靠前策略并排实测">
          <div className="text-[12px] text-subink">当前无可实测策略(组合再构成名单为空,非故障)。</div>
        </Card>
      )}

      {state === "ok" && data && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {orderedAccounts(data).map((a) => (
            <AccountCard key={a.name} account={a} />
          ))}
        </div>
      )}

      {data?.generated_at && (
        <div className="text-[10px] text-subink">
          名单生成于 {data.generated_at} · 顺序=组合再构成排名产物顺序,非前端重排
        </div>
      )}
    </div>
  );
}
