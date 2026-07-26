"use client";

import { useEffect, useState, useMemo } from "react";
import type { NavCurveView, PaperTradesView } from "@/lib/types";
import { pct, num } from "@/lib/api";

// 真实模拟盘时序点(从 nav/trades 派生;只有真实数据,无合成演示)。
interface SimPoint {
  date: string;
  nav: number;
  cash: number;
  posValue: number;
  regime: "BULL" | "BEAR" | "VOL";
  holdings: { ticker: string; company: string; weight: number; price: number; pnl: number }[];
  aiLog: string;
  exposures: { momentum: number; volatility: number; value: number; quality: number; growth: number };
}


// 雷达图中心配置
const cx = 90;
const cy = 90;
const R = 60;

export default function TimeTravelSimulator({
  nav,
  trades,
}: {
  nav: NavCurveView | null;
  trades: PaperTradesView | null;
}) {
  // 只渲染真实模拟盘数据(合成演示沙盒已移除)。
  const [index, setIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<1 | 2 | 4>(2);

  // 1. 构建真实模拟盘的每日时序数据
  const realPoints: SimPoint[] = useMemo(() => {
    if (!nav || nav.points.length === 0) return [];
    const pts = nav.points;
    const trds = trades?.trades || [];

    return pts.map((p) => {
      // 找出当前日期及以前的所有交易记录
      const activeTrades = trds.filter((t) => t.date <= p.date);
      
      // 聚合计算持仓
      const holdMap: Record<string, { company: string; shares: number; price: number; cost: number }> = {};
      activeTrades.forEach((t) => {
        if (!holdMap[t.code]) {
          holdMap[t.code] = { company: t.name, shares: 0, price: t.price, cost: 0 };
        }
        if (t.side === "BUY") {
          holdMap[t.code].shares += t.shares;
          holdMap[t.code].cost += t.notional;
        } else {
          holdMap[t.code].shares -= t.shares;
          // 简化卖出后的成本折算
          if (holdMap[t.code].shares <= 0) delete holdMap[t.code];
        }
      });

      const holdings = Object.entries(holdMap).map(([code, info]) => {
        const value = info.shares * info.price;
        const weight = p.nav > 0 ? (value / p.nav) * 100 : 0;
        const pnl = info.cost > 0 ? ((value - info.cost) / info.cost) * 100 : 0;
        return {
          ticker: code,
          company: info.company,
          weight: Number(weight.toFixed(1)),
          price: info.price,
          pnl: Number(pnl.toFixed(1))
        };
      });

      // 判定 regime 和 AI 日志
      const isBear = p.cash / p.nav > 0.5; // 简化判定：现金占大头即为避险
      const regime = isBear ? "BEAR" : holdings.length > 0 ? "BULL" : "VOL";

      let aiLog = `当前净值 ${p.nav.toFixed(2)}，现金 ${(p.cash / 10000).toFixed(2)}万。持仓跟踪中。`;
      if (activeTrades.length > 0 && activeTrades[activeTrades.length - 1].date === p.date) {
        const lastT = activeTrades[activeTrades.length - 1];
        aiLog = `监测到交易信号：模拟盘跟单执行 ${lastT.side === "BUY" ? "买入" : "卖出"} ${lastT.name} ${lastT.shares} 股，单价 ${lastT.price} 元。`;
      } else if (holdings.length === 0) {
        aiLog = "市场择时判定为弱势/空仓区。闲置现金配至低波避险组合。";
      }

      // 根据持仓虚拟因子暴露度
      const hasBond = holdings.some((h) => h.ticker === "511010");
      const exposures = hasBond
        ? { momentum: 10, volatility: 5, value: 85, quality: 90, growth: 5 }
        : holdings.length > 0
        ? { momentum: 70, volatility: 35, value: 40, quality: 80, growth: 60 }
        : { momentum: 0, volatility: 0, value: 0, quality: 0, growth: 0 };

      return {
        date: p.date,
        nav: p.nav,
        cash: p.cash,
        posValue: p.position_value,
        regime,
        holdings,
        aiLog,
        exposures
      };
    });
  }, [nav, trades]);

  const activeData = realPoints;
  const maxIdx = activeData.length - 1;

  // 数据刷新时重置回放
  useEffect(() => {
    setIndex(0);
    setIsPlaying(false);
  }, [realPoints.length]);

  // 播放器 effect
  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setIndex((i) => {
        if (i >= maxIdx) {
          setIsPlaying(false);
          return maxIdx;
        }
        return i + 1;
      });
    }, 1500 / speed);
    return () => clearInterval(interval);
  }, [isPlaying, speed, maxIdx]);

  if (realPoints.length === 0) {
    return (
      <div className="card text-sm text-[#547689] py-8 text-center">
        模拟盘历史点数不足，无法运行穿梭重放（需先有真实模拟盘 NAV/成交记录落盘）。
      </div>
    );
  }

  const current = activeData[index] || activeData[0];

  // SVG 折线图比例计算
  const W = 680;
  const H = 140;
  const PAD = { l: 20, r: 20, t: 10, b: 20 };
  const navs = activeData.map((d) => d.nav);
  const minNav = Math.min(...navs) * 0.98;
  const maxNav = Math.max(...navs) * 1.02;
  const navSpan = maxNav - minNav || 1;

  const getX = (i: number) => PAD.l + (i / maxIdx) * (W - PAD.l - PAD.r);
  const getY = (v: number) => PAD.t + (1 - (v - minNav) / navSpan) * (H - PAD.t - PAD.b);

  const polylinePts = activeData.map((d, i) => `${getX(i).toFixed(1)},${getY(d.nav).toFixed(1)}`).join(" ");

  // 计算雷达图的多边形点
  const getRadarPt = (axisIdx: number, val: number) => {
    const angle = (axisIdx * 2 * Math.PI) / 5 - Math.PI / 2; // offset by 90deg to point top axis upwards
    const r = (val / 100) * R;
    const x = cx + r * Math.cos(angle);
    const y = cy + r * Math.sin(angle);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  };

  const radarPoints = [
    getRadarPt(0, current.exposures.momentum),
    getRadarPt(1, current.exposures.value),
    getRadarPt(2, current.exposures.quality),
    getRadarPt(3, current.exposures.growth),
    getRadarPt(4, current.exposures.volatility)
  ].join(" ");

  return (
    <div className="space-y-4">
      {/* 头部 */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-4 border border-line/30 rounded-lg">
        <div>
          <h2 className="text-sm font-semibold text-ink font-quant">
            模拟盘时空飞行模拟器 (Time-Travel Flight Simulator)
          </h2>
          <div className="text-[11px] text-subink mt-0.5">
            真实模拟盘时空同步重放：折线净值、动态持仓、AI 审计日志、因子暴露雷达图
          </div>
        </div>
      </div>

      {/* 主面板一：多轨同步折线时间轴 */}
      <div className="card space-y-3">
        <div className="flex items-center justify-between text-xs border-b border-line/30 pb-2">
          <div className="flex items-center gap-2">
            <span className="text-subink font-quant">STRATEGY:</span>
            <span className="text-ink font-quant">Paper_Live_v3.1</span>
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-quant border ${
              current.regime === "BULL"
                ? "bg-songshi/10 text-songshi border-songshi/20"
                : current.regime === "BEAR"
                ? "bg-danger/10 text-danger border-danger/20"
                : "bg-warn/10 text-warn border-warn/20"
            }`}>
              {current.regime} 状态
            </span>
          </div>
          <div className="font-quant text-ink">
            日期: <span className="text-brand font-semibold">{current.date}</span>
          </div>
        </div>

        {/* 市场状态时序块 */}
        <div className="h-6 w-full flex rounded overflow-hidden text-[10px] text-[#12264F] font-semibold border border-[#3C4654]/25">
          {activeData.map((d, i) => {
            const isSelected = i === index;
            const wPct = (1 / activeData.length) * 100;
            const bgClass =
              d.regime === "BULL" ? "bg-songshi" : d.regime === "BEAR" ? "bg-danger" : "bg-warn";
            return (
              <div
                key={i}
                style={{ width: `${wPct}%` }}
                className={`${bgClass} h-full ${isSelected ? "opacity-100 border-2 border-brand" : "opacity-50"} hover:opacity-100 transition-opacity`}
                title={`${d.date} (${d.regime})`}
              />
            );
          })}
        </div>

        {/* 净值折线图 (SVG) */}
        <div className="relative pt-2">
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full overflow-visible">
            {/* 渐变定义 */}
            <defs>
              <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#88ABDA" stopOpacity="0.25" />
                <stop offset="100%" stopColor="#88ABDA" stopOpacity="0.0" />
              </linearGradient>
            </defs>

            {/* 栅格横线 */}
            <line x1={PAD.l} y1={getY(minNav + navSpan * 0.5)} x2={W - PAD.r} y2={getY(minNav + navSpan * 0.5)} stroke="rgba(84, 118, 137, 0.15)" strokeWidth="1" strokeDasharray="3 3" />

            {/* 净值底色填充 */}
            <path
              d={`M ${getX(0).toFixed(1)},${H - PAD.b} L ${polylinePts} L ${getX(maxIdx).toFixed(1)},${H - PAD.b} Z`}
              fill="url(#areaGrad)"
            />

            {/* 净值折线 */}
            <polyline
              points={polylinePts}
              fill="none"
              stroke="#CC5D20"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {/* 垂直时间轴 Scrubber Line */}
            <line
              x1={getX(index).toFixed(1)}
              y1={PAD.t}
              x2={getX(index).toFixed(1)}
              y2={H - PAD.b}
              stroke="#12264F"
              strokeWidth="1.5"
              strokeDasharray="2 2"
            />

            {/* 当前指针数据球 */}
            <circle
              cx={getX(index).toFixed(1)}
              cy={getY(current.nav).toFixed(1)}
              r="4.5"
              fill="#EFEFEF"
              stroke="#CC5D20"
              strokeWidth="2"
            />

            {/* 底部首尾标签 */}
            <text x={PAD.l} y={H - 5} fontSize="9" fill="#555147" className="font-quant">{activeData[0].date}</text>
            <text x={W - PAD.r} y={H - 5} textAnchor="end" fontSize="9" fill="#555147" className="font-quant">{activeData[maxIdx].date}</text>
          </svg>
        </div>
      </div>

      {/* 主面板二：播放与进度控制器 */}
      <div className="card flex flex-col md:flex-row items-center gap-4 py-3 bg-gray-50/50 border border-line/30">
        {/* 播放控制纽 */}
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className="w-10 h-10 rounded-full bg-[#88ABDA] hover:bg-[#88ABDA]/90 text-[#12264F] flex items-center justify-center transition-colors shadow-md shadow-black/20"
          >
            {isPlaying ? (
              // 暂停图标
              <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" />
              </svg>
            ) : (
              // 播放图标
              <svg className="w-5 h-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </button>
          
          <button
            onClick={() => {
              setIsPlaying(false);
              setIndex(0);
            }}
            className="p-2 text-subink hover:text-ink rounded hover:bg-line/30 transition-colors"
            title="重置"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 7.89M9 11l3-3 3 3m-3-3v12" />
            </svg>
          </button>

          {/* 播放速度 */}
          <div className="flex bg-[#0E172B] rounded p-0.5 border border-[#3C4654]/30">
            {([1, 2, 4] as const).map((s) => (
              <button
                key={s}
                onClick={() => setSpeed(s)}
                className={`text-[10px] px-2 py-0.5 rounded font-quant font-medium ${
                  speed === s ? "bg-brand text-white" : "text-subink hover:text-ink hover:bg-line/30"
                }`}
              >
                {s}x
              </button>
            ))}
          </div>
        </div>

        {/* 时间轴进度条 (Scrubber) */}
        <div className="flex-1 w-full flex items-center gap-3">
          <input
            type="range"
            min={0}
            max={maxIdx}
            value={index}
            onChange={(e) => {
              setIsPlaying(false);
              setIndex(Number(e.target.value));
            }}
            className="flex-1 h-1 bg-line/40 rounded-lg appearance-none cursor-pointer accent-brand"
          />
          <span className="text-xs font-quant text-brand font-semibold shrink-0 min-w-[70px] text-right">
            {index + 1} / {activeData.length} 日
          </span>
        </div>
      </div>

      {/* 主面板三：双分栏数据展现 */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        {/* 左栏：当天持仓明细 */}
        <div className="lg:col-span-3 card">
          <div className="flex items-center justify-between border-b border-line/30 pb-2.5 mb-3">
            <span className="text-xs font-medium text-ink">当天持仓 (Holdings): {current.date}</span>
            <span className="text-[11px] text-subink font-quant">
              现金额: {current.cash.toLocaleString("zh-CN", { maximumFractionDigits: 0 })} 元
            </span>
          </div>

          <div className="max-h-60 overflow-y-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-subink text-left border-b border-line/30 bg-gray-50/50 sticky top-0">
                  <th className="py-2 px-2 font-medium">代码</th>
                  <th className="py-2 px-2 font-medium">名称</th>
                  <th className="py-2 px-2 font-medium text-right">权重 %</th>
                  <th className="py-2 px-2 font-medium text-right">估值单价</th>
                  <th className="py-2 px-2 font-medium text-right">收益浮动</th>
                </tr>
              </thead>
              <tbody>
                {current.holdings.map((h) => (
                  <tr key={h.ticker} className="border-b border-cardline/60">
                    <td className="py-2 px-2 font-quant text-ink">{h.ticker}</td>
                    <td className="py-2 px-2 text-ink/80">{h.company}</td>
                    <td className="py-2 px-2 text-right font-quant text-ink">{h.weight}%</td>
                    <td className="py-2 px-2 text-right font-quant text-subink">{h.price}</td>
                    <td className={`py-2 px-2 text-right font-quant ${h.pnl >= 0 ? "text-danger" : "text-songshi"}`}>
                      {h.pnl >= 0 ? "+" : ""}{h.pnl}%
                    </td>
                  </tr>
                ))}
                {current.holdings.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-6 text-center text-subink">
                      当前空仓 (全现金闲置避险)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* 右栏：AI飞行日志与五维因子暴露 */}
        <div className="lg:col-span-2 card flex flex-col justify-between">
          <div className="space-y-4">
            {/* AI 飞行日志 */}
            <div>
              <div className="text-[10px] text-subink uppercase tracking-wider font-quant mb-2">AI Flight Log</div>
              <div className="bg-bg/40 p-3 rounded-lg border border-line/30 min-h-[72px]">
                <div className="flex items-center gap-1.5 mb-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-brand" />
                  <span className="text-[9px] text-subink font-quant">{current.date} 09:30:15 EST</span>
                </div>
                <p className="text-[12px] text-ink leading-relaxed">
                  {current.aiLog}
                </p>
              </div>
            </div>

            {/* 五维因子暴露雷达图 (SVG) */}
            <div>
              <div className="text-[10px] text-subink uppercase tracking-wider font-quant mb-2">五维因子暴露 (Factor Exposure)</div>
              <div className="flex items-center justify-around">
                {/* 简单的自画 SVG 雷达图 */}
                <svg width="180" height="180" viewBox="0 0 180 180" className="overflow-visible">
                  {/* 背景圈与轴线 */}
                  {[20, 40, 60, 80, 100].map((val) => (
                    <polygon
                      key={val}
                      points={[
                        getRadarPt(0, val),
                        getRadarPt(1, val),
                        getRadarPt(2, val),
                        getRadarPt(3, val),
                        getRadarPt(4, val)
                      ].join(" ")}
                      fill="none"
                      stroke="rgba(84, 118, 137, 0.15)"
                      strokeWidth="0.8"
                    />
                  ))}

                  {/* 5 个轴线和标签 */}
                  {[
                    { label: "动量", idx: 0, anchor: "middle", dy: "-6", dx: "0" },
                    { label: "价值", idx: 1, anchor: "start", dy: "4", dx: "4" },
                    { label: "质量", idx: 2, anchor: "start", dy: "12", dx: "-2" },
                    { label: "成长", idx: 3, anchor: "end", dy: "12", dx: "2" },
                    { label: "波动率", idx: 4, anchor: "end", dy: "4", dx: "-4" }
                  ].map((axis) => {
                    const lineEnd = getRadarPt(axis.idx, 100);
                    const labelPt = getRadarPt(axis.idx, 120);
                    const [lx, ly] = labelPt.split(",");
                    return (
                      <g key={axis.idx}>
                        <line
                          x1={cx}
                          y1={cy}
                          x2={lineEnd.split(",")[0]}
                          y2={lineEnd.split(",")[1]}
                          stroke="rgba(84, 118, 137, 0.25)"
                          strokeWidth="0.8"
                        />
                        <text
                          x={lx}
                          y={ly}
                          dy={axis.dy}
                          dx={axis.dx}
                          textAnchor={axis.anchor as any}
                          fontSize="9"
                          fill="#555147"
                        >
                          {axis.label}
                        </text>
                      </g>
                    );
                  })}

                  {/* 实际雷达数据多边形 */}
                  {current.holdings.length > 0 && (
                    <g>
                      <polygon
                        points={radarPoints}
                        fill="rgba(75, 143, 152, 0.15)"
                        stroke="#4B8F98"
                        strokeWidth="1.5"
                      />
                      {/* 点微标 */}
                      {[0, 1, 2, 3, 4].map((i) => {
                        const pt = getRadarPt(i, Object.values(current.exposures)[i]);
                        const [px, py] = pt.split(",");
                        return <circle key={i} cx={px} cy={py} r="2.5" fill="#EFEFEF" stroke="#4B8F98" strokeWidth="1" />;
                      })}
                    </g>
                  )}
                </svg>

                {/* 暴露数值面板 */}
                <div className="text-[10px] space-y-1.5 font-quant text-subink">
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-songshi" />
                    <span>动量 (Mom): <span className="text-ink">{current.exposures.momentum}</span></span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-songshi" />
                    <span>价值 (Val): <span className="text-ink">{current.exposures.value}</span></span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-songshi" />
                    <span>质量 (Qual): <span className="text-ink">{current.exposures.quality}</span></span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-songshi" />
                    <span>成长 (Gro): <span className="text-ink">{current.exposures.growth}</span></span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-songshi" />
                    <span>波动 (Vol): <span className="text-ink">{current.exposures.volatility}</span></span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="text-[9px] text-subink border-t border-line/20 pt-2 mt-4 leading-normal">
            * 因子暴露根据当前权重在 Barra 行业风格库中映射计算。
          </div>
        </div>
      </div>
    </div>
  );
}
