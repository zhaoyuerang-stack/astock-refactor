"use client";

import type { NavCurveView } from "@/lib/types";

const fmt = (x: number) => x.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
const W = 640;
const H = 220;
const PAD = { l: 8, r: 8, t: 12, b: 22 };

export default function NavChart({ data }: { data: NavCurveView }) {
  const pts = data.points;
  if (pts.length < 2) {
    return (
      <div className="card text-sm text-subink">
        净值数据不足({pts.length} 个点),模拟盘起始日 {data.inception || "—"},累计后将显示曲线。
      </div>
    );
  }
  const navs = pts.map((p) => p.nav);
  const lo = Math.min(...navs, data.init_capital);
  const hi = Math.max(...navs, data.init_capital);
  const span = hi - lo || 1;
  const x = (i: number) => PAD.l + (i / (pts.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - lo) / span) * (H - PAD.t - PAD.b);
  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.nav).toFixed(1)}`).join(" ");
  const yBase = y(data.init_capital);
  const last = pts[pts.length - 1];

  return (
    <div className="card">
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-sm font-medium">模拟盘净值曲线</div>
        <div className="text-[12px] text-subink">
          最新{data.latest_nav_date ? ` ${data.latest_nav_date}` : ""} <span className="text-ink font-medium">{fmt(data.latest_nav)}</span>
          <span className={`ml-2 ${data.total_return >= 0 ? "text-ok" : "text-danger"}`}>
            {data.total_return >= 0 ? "+" : ""}{(data.total_return * 100).toFixed(2)}%
          </span>
          <span className="ml-2 text-danger">回撤 {(data.max_drawdown * 100).toFixed(2)}%</span>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="模拟盘净值曲线">
        {/* 本金基线 */}
        <line x1={PAD.l} y1={yBase} x2={W - PAD.r} y2={yBase}
              stroke="#94A3B8" strokeWidth="1" strokeDasharray="4 4" />
        <text x={W - PAD.r} y={yBase - 4} textAnchor="end" fontSize="10" fill="#64748B">
          本金 {fmt(data.init_capital)}
        </text>
        {/* 净值折线 */}
        <polyline points={line} fill="none" stroke="#3B82F6" strokeWidth="2"
                  strokeLinejoin="round" strokeLinecap="round" />
        {/* 数据点(hover 显示日期/净值) */}
        {pts.map((p, i) => (
          <circle key={p.date} cx={x(i)} cy={y(p.nav)} r="3" fill="#3B82F6" opacity="0.85">
            <title>{`${p.date}  净值 ${fmt(p.nav)}  (${(p.total_return * 100).toFixed(2)}%)`}</title>
          </circle>
        ))}
        {/* 首末日期 */}
        <text x={PAD.l} y={H - 6} fontSize="10" fill="#64748B">{pts[0].date}</text>
        <text x={W - PAD.r} y={H - 6} textAnchor="end" fontSize="10" fill="#64748B">{last.date}</text>
      </svg>
      <div className="text-[11px] text-subink mt-1">
        起始 {data.inception} · 本金 {fmt(data.init_capital)} · 真实盘 T+1 成交口径 · 模拟盘业绩不代表未来收益
      </div>
    </div>
  );
}
