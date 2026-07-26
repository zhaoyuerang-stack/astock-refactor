"use client";

// 「研究审计面板」三视图 —— Leaderboard / Family / Gate + Drilldown。
// 设计原则:① 决策信息一律数据化(条形/色阶,可一眼比较)② 去重去噪 ③ 清晰层次。
// 数据全部来自 toInstitutionalRow(StrategyView);算不出的渲染「未计算」,绝不编造。
import { pct, num } from "@/lib/api";
import Link from "next/link";
import { displayRegistryStatus } from "@/lib/researchWorkspace.mjs";
import type { InstitutionalRow, FamilyGroup, GateCell, Verdict } from "@/lib/institutional";
import { GATE_COLUMNS, gateCell } from "@/lib/institutional";
import type { StrategyView } from "@/lib/types";

// ── 数据化原子 ─────────────────────────────────────────────────────────────
const DASH = <span className="text-subink/40">—</span>;
const NV = <span className="text-warn/70 text-[10px]" title="无样本外结果,不可与已验证行直接比较">未验证</span>;

function Sharpe({ v }: { v: number | null }) {
  if (v == null) return DASH;
  const c = v >= 1.5 ? "text-songshi font-semibold" : v >= 0.8 ? "text-ink" : v > 0 ? "text-warn" : "text-danger";
  return <span className={`font-mono ${c}`}>{num(v)}</span>;
}
function Decay({ v }: { v: number | null }) {
  if (v == null) return DASH;
  const c = v >= 0.8 ? "text-songshi" : v >= 0.5 ? "text-warn" : "text-danger";
  return <span className={`font-mono ${c}`} title="OOS夏普 / IS夏普 留存率(<0.5=过拟合嫌疑)">{num(v)}×</span>;
}
// 发散条:负向左红、正向右绿(风格β、regime 用)
function DivergeBar({ v, max, w = 72 }: { v: number; max: number; w?: number }) {
  const half = w / 2;
  const mag = Math.max(0, Math.min(1, Math.abs(v) / max)) * half;
  const pos = v >= 0;
  return (
    <span className="relative inline-block h-2 rounded-full bg-line/30" style={{ width: w }}>
      <span className="absolute top-0 bottom-0 left-1/2 w-px bg-subink/30" />
      <span className={`absolute top-0 bottom-0 rounded-full ${pos ? "bg-songshi" : "bg-danger"}`}
        style={{ width: mag, left: pos ? half : half - mag }} />
    </span>
  );
}

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const color =
    verdict.tone === "success" ? "bg-songshi/10 text-songshi border-songshi/30"
    : verdict.tone === "warn" ? "bg-warn/10 text-warn border-warn/30"
    : verdict.tone === "ok" ? "bg-brand/10 text-brand border-brand/30"
    : verdict.tone === "danger" ? "bg-danger/10 text-danger border-danger/30"
    : "bg-subink/10 text-subink border-line";
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] border ${color}`}>
      <span>{verdict.icon}</span><span>{verdict.short}</span>
    </span>
  );
}

// PBO 徽章(色阶):≥0.3 红 / ≥0.1 黄 / 否则绿
function PboTag({ v, risk }: { v: number | null; risk: string | null }) {
  if (v == null) return DASH;
  const c = v >= 0.3 ? "text-danger" : v >= 0.1 ? "text-warn" : "text-songshi";
  return <span className={`font-mono ${c}`} title={`家族版本池 CSCV 过拟合概率${risk ? ` (${risk})` : ""}`}>{num(v)}</span>;
}
// 与父版本相关:≥0.9 红(换皮)
function CorrTag({ v, parent }: { v: number | null; parent: string | null }) {
  if (v == null) return DASH;
  const dup = v >= 0.9;
  return <span className={`font-mono ${dup ? "text-danger" : "text-ink"}`} title={`与父版本 ${parent ?? ""} 收益相关${dup ? ":疑似换皮" : ""}`}>ρ{num(v)}</span>;
}

// ── 1. Leaderboard View(决策排行)──────────────────────────────────────────
const GH = "py-2 px-2 font-medium";       // 表头
const GL = "border-l border-line/40";      // 列组分隔
export function LeaderboardView({ rows, activeId, onSelect }: {
  rows: InstitutionalRow[]; activeId: string | null; onSelect: (s: StrategyView) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] min-w-[1080px]">
        <thead>
          <tr className="text-subink text-left border-b border-line bg-jilan sticky top-0 z-10">
            <th className={GH}>策略 · 状态</th>
            <th className={`${GH} text-center`}>判定</th>
            <th className={`${GH} ${GL} text-right`} title="样本内夏普">IS</th>
            <th className={`${GH} text-right`} title="样本外夏普(OOS / walk-forward)">OOS</th>
            <th className={`${GH} text-right`} title="压力期夏普(2010-2026)">压力</th>
            <th className={`${GH} text-right`} title="OOS/IS 夏普留存率">衰减</th>
            <th className={`${GH} ${GL} text-right`} title="全样本净年化(已扣成本)">净年化</th>
            <th className={`${GH} text-right`}>回撤</th>
            <th className={`${GH} ${GL} text-right`} title="Deflated Sharpe p值 (试验次数)">DSR p</th>
            <th className={`${GH} text-right`} title="家族版本池 CSCV 过拟合概率">PBO</th>
            <th className={`${GH} text-right`} title="与 lineage 父版本收益相关性(≥0.9 疑似换皮)">ρ→父</th>
            <th className={`${GH} ${GL} text-right`} title="规模因子暴露(风格伪装检测)">Size β</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const sel = r.id === activeId;
            const sc = r.status === "在册" ? "text-brand" : r.status === "退役" || r.status === "已证伪" ? "text-subink" : "text-warn";
            return (
              <tr key={r.id} onClick={() => onSelect(r.s)}
                className={`border-b border-line/45 cursor-pointer transition-colors ${sel ? "bg-brand/10" : "hover:bg-jilan/10"}`}>
                <td className="py-2 px-2">
                  <div className="font-quant text-ink font-semibold whitespace-nowrap">{r.id}</div>
                  <div className="text-[10px] whitespace-nowrap"><span className={sc}>{displayRegistryStatus(r.status)}</span>{r.track && <span className="text-subink"> · {r.track === "diversifier" ? "分流器" : "单体"}</span>}</div>
                </td>
                <td className="py-2 px-2 text-center"><VerdictBadge verdict={r.verdict} /></td>
                <td className={`py-2 px-2 text-right ${GL}`} title={r.is.source}><Sharpe v={r.is.sharpe} /></td>
                <td className="py-2 px-2 text-right" title={r.oos.source}>{r.oos.sharpe == null ? NV : <Sharpe v={r.oos.sharpe} />}</td>
                <td className="py-2 px-2 text-right" title={r.stress.source}><Sharpe v={r.stress.sharpe} /></td>
                <td className="py-2 px-2 text-right"><Decay v={r.isOosDecay} /></td>
                <td className={`py-2 px-2 text-right font-mono text-songshi font-bold ${GL}`}>{r.netAnnual != null ? pct(r.netAnnual) : DASH}</td>
                <td className="py-2 px-2 text-right font-mono text-subink">{r.maxdd != null ? pct(r.maxdd) : DASH}</td>
                <td className={`py-2 px-2 text-right font-mono ${GL}`}>
                  {r.dsrP == null ? <span className="text-subink/60 text-[10px]">未审计</span>
                    : <span className={r.dsrSignificant ? "text-songshi font-semibold" : "text-warn"} title={`N=${r.nTrials ?? "?"}`}>{r.dsrP.toFixed(3)}<span className="text-subink/60">{r.nTrials != null ? ` (${r.nTrials})` : ""}</span></span>}
                </td>
                <td className="py-2 px-2 text-right"><PboTag v={r.pbo} risk={r.pboRisk} /></td>
                <td className="py-2 px-2 text-right"><CorrTag v={r.corrToParent} parent={r.corrParentVersion} /></td>
                <td className={`py-2 px-2 text-right ${GL}`}>
                  {r.sizeBeta == null ? DASH : (
                    <span className="inline-flex items-center gap-1.5 justify-end">
                      <DivergeBar v={r.sizeBeta} max={1} w={40} />
                      <span className={`font-mono ${Math.abs(r.sizeBeta) >= 0.6 ? "text-warn" : "text-ink"}`}>{num(r.sizeBeta)}</span>
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
          {rows.length === 0 && <tr><td colSpan={12} className="py-4 text-center text-subink">暂无策略版本数据</td></tr>}
        </tbody>
      </table>
      <div className="mt-2 text-[10px] text-subink/60">按 production_score(OOS质量 × DSR显著 × 风险 × 容量)排序 · ρ≥0.9 换皮 · PBO≥0.3 过拟合高 · Size β≥0.6 风格伪装嫌疑 · 容量/尾部/成本见详情</div>
    </div>
  );
}

// ── 2. Family View(lineage 折叠,吸收假设 + 噪音池)──────────────────────────
export function FamilyView({ groups, onSelect }: {
  groups: FamilyGroup[]; onSelect: (s: StrategyView) => void;
}) {
  return (
    <div className="space-y-3">
      {groups.map((g) => {
        const selectionBias = g.bestSharpe != null && g.medianSharpe != null && g.medianSharpe > 0 && g.bestSharpe / g.medianSharpe >= 2;
        const pboRow = g.rows.find((r) => r.pbo != null);
        const head = g.rows[0]?.s;
        return (
          <div key={g.family} className="card">
            <div className="flex items-start justify-between gap-3 flex-wrap border-b border-line/30 pb-2 mb-2">
              <div className="min-w-0">
                <div className="font-bold text-ink">{g.familyName} <span className="font-quant text-[11px] text-subink">/ {g.family}</span></div>
                {head?.regime && <div className="text-[11px] text-brand mt-0.5">适用状态:{head.regime}</div>}
              </div>
              <div className="flex items-center gap-2 text-[10px] flex-wrap shrink-0">
                <span className="px-1.5 py-0.5 rounded border border-songshi/30 text-songshi bg-songshi/5">在册 {g.nRegistered}/{g.rows.length}</span>
                <span className="px-1.5 py-0.5 rounded border border-line text-subink" title="家族总试验次数(多重检验合并计数)">试验 {g.totalTrials ?? "—"}</span>
                <span className="px-1.5 py-0.5 rounded border border-line text-subink" title="家族内最优/中位夏普">best {g.bestSharpe != null ? num(g.bestSharpe) : "—"} / med {g.medianSharpe != null ? num(g.medianSharpe) : "—"}</span>
                {pboRow?.pbo != null && (() => { const p = pboRow.pbo as number; const c = p >= 0.3 ? "border-danger/30 text-danger bg-danger/5" : p >= 0.1 ? "border-warn/30 text-warn bg-warn/5" : "border-songshi/30 text-songshi bg-songshi/5"; return <span className={`px-1.5 py-0.5 rounded border ${c}`} title="家族版本池 CSCV 过拟合概率">PBO {num(p)}{pboRow.pboRisk ? ` · ${pboRow.pboRisk}` : ""}</span>; })()}
                {selectionBias && <span className="px-1.5 py-0.5 rounded border border-warn/30 text-warn bg-warn/5" title="best 远高于 median:疑似版本选择偏差">⚠ 选择偏差</span>}
              </div>
            </div>
            {head?.hypothesis && <p className="text-[11px] text-ink/70 leading-relaxed mb-2 line-clamp-2">{head.hypothesis}</p>}
            {/* 版本血缘链:夏普 + 与父版本 ρ(换皮一眼可见)*/}
            <div className="flex flex-wrap gap-1.5">
              {g.rows.map((r) => (
                <button key={r.id} onClick={() => onSelect(r.s)}
                  className="text-[10px] px-2 py-1 rounded border border-line hover:border-brand/40 hover:bg-jilan/20 font-quant flex items-center gap-1.5 transition-colors">
                  <span className="text-ink font-medium">{r.version}</span>
                  <VerdictBadge verdict={r.verdict} />
                  <span className="text-subink">夏普 {r.is.sharpe != null ? num(r.is.sharpe) : "—"}</span>
                  {r.corrToParent != null && <span className={r.corrToParent >= 0.9 ? "text-danger" : "text-subink"} title={`与父版本 ${r.corrParentVersion ?? ""} 收益相关${r.corrToParent >= 0.9 ? ":疑似换皮" : ""}`}>ρ{num(r.corrToParent)}</span>}
                  {r.incrementalAlpha != null && <span className={r.incrementalAlpha >= 0.05 ? "text-songshi" : "text-subink"} title="对父版本正交增量 alpha(年化)">+{pct(r.incrementalAlpha)}</span>}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── 3. Gate View(9-Gate 热力图)──────────────────────────────────────────
const gateColor: Record<GateCell, string> = {
  PASS: "bg-songshi/20 text-songshi", WARN: "bg-warn/20 text-warn", FAIL: "bg-danger/20 text-danger", NA: "bg-subink/5 text-subink/40",
};
const gateGlyph: Record<GateCell, string> = { PASS: "✓", WARN: "!", FAIL: "✕", NA: "·" };

export function GateView({ rows, onSelect }: { rows: InstitutionalRow[]; onSelect: (s: StrategyView) => void; }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] min-w-[760px]">
        <thead>
          <tr className="text-subink text-left border-b border-line bg-jilan sticky top-0">
            <th className="py-2 px-2 font-medium">策略ID</th>
            {GATE_COLUMNS.map((c) => <th key={c.key} className="py-2 px-1 font-medium text-center" title={c.label}>{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onSelect(r.s)} className="border-b border-line/45 cursor-pointer hover:bg-jilan/10">
              <td className="py-2 px-2 font-quant text-ink font-semibold whitespace-nowrap">{r.id}</td>
              {GATE_COLUMNS.map((c) => {
                const cell = gateCell(r, c.key);
                return (
                  <td key={c.key} className="py-1.5 px-1 text-center">
                    <span className={`inline-flex items-center justify-center w-6 h-6 rounded text-[11px] font-bold ${gateColor[cell]}`} title={`${c.label}: ${cell}`}>{gateGlyph[cell]}</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 text-[10px] text-subink/60">✓ 通过 · ! 警告 · ✕ 失败 · · 未计算/不适用。成本(P2)/PBO(P2) 列为后端补算占位,绝不假绿。</div>
    </div>
  );
}

// ── 4. Drilldown Panel(数据化:三段样本 + regime 发散条 + 风格条 + 血缘)──────
function StatBlock({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "danger" }) {
  return (
    <div className="text-left">
      <div className={`text-[22px] font-bold font-quant leading-none tracking-tight ${tone === "danger" ? "text-[#E54D42]" : "text-[#111827]"}`}>{value}</div>
      <div className="text-[11px] text-[#888888] mt-1.5">{label}</div>
    </div>
  );
}
function Kv({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex justify-between gap-2 border-b border-[#EFECE3]/40 pb-1"><span className="text-[#888888]">{k}</span><span className="text-[#333333] font-medium text-right">{v}</span></div>;
}

export function DrilldownPanel({ row, onClose }: { row: InstitutionalRow; onClose: () => void; }) {
  const r = row;
  const fb = r.s.failure_boundaries || {};
  const betas = Object.entries(r.s.style_betas || {});
  const sample = [{ tag: "IS 样本内", c: r.is }, { tag: "OOS 样本外", c: r.oos }, { tag: "Stress 压力", c: r.stress }];
  const todo: string[] = [];
  if (r.paramStability == null) todo.push("参数邻域稳定性(需 param grid)");
  return (
    <div className="lg:col-span-2 bg-white border border-[#EFECE3] rounded-2xl p-6 h-[calc(100vh-160px)] sticky top-5 overflow-y-auto shadow-md flex flex-col scrollbar-thin">
      <div className="space-y-5 flex-1">
        {/* 头栏 */}
        <div className="flex items-start justify-between border-b border-[#EFECE3] pb-4">
          <div className="min-w-0">
            <h3 className="text-md font-bold text-[#111827] truncate leading-tight">{r.id}</h3>
            <div className="text-[11px] text-[#888888] mt-0.5">{r.s.family_name || r.family} · 审计 {r.s.nine_gate?.run_date || "未审计"}</div>
            <div className="mt-2"><VerdictBadge verdict={r.verdict} /></div>
          </div>
          <button onClick={onClose} className="text-[#888888] hover:text-[#111827] p-1 rounded-md hover:bg-jilan/45 shrink-0" title="关闭详情" aria-label="关闭详情">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>

        {/* 核心 4 指标(真实台账值)*/}
        <div className="grid grid-cols-4 gap-2 border-b border-[#EFECE3] pb-5">
          <StatBlock label="净年化" tone="danger" value={r.netAnnual != null ? pct(r.netAnnual) : "—"} />
          <StatBlock label="最大回撤" value={r.maxdd != null ? pct(Math.abs(r.maxdd)) : "—"} />
          <StatBlock label="夏普" value={r.is.sharpe != null ? num(r.is.sharpe) : "—"} />
          <StatBlock label="卡玛" value={r.calmar != null ? num(r.calmar) : "—"} />
        </div>

        {/* 三段样本(IS/OOS/Stress)+ 衰减 */}
        <div>
          <div className="text-[10px] uppercase tracking-wider text-[#888888] font-quant font-medium mb-2">样本分离 (IS / OOS / Stress)</div>
          <table className="w-full text-[11px]">
            <thead><tr className="text-subink border-b border-[#EFECE3]"><th className="text-left py-1 font-medium">区段</th><th className="text-right py-1 font-medium">夏普</th><th className="text-right py-1 font-medium">年化</th><th className="text-right py-1 font-medium">回撤</th><th className="text-right py-1 font-medium">口径</th></tr></thead>
            <tbody>
              {sample.map((s) => (
                <tr key={s.tag} className="border-b border-[#EFECE3]/50">
                  <td className="py-1.5 text-ink">{s.tag}</td>
                  <td className="py-1.5 text-right"><Sharpe v={s.c.sharpe} /></td>
                  <td className="py-1.5 text-right">{s.c.annual != null ? <span className="font-mono">{pct(s.c.annual)}</span> : DASH}</td>
                  <td className="py-1.5 text-right">{s.c.maxdd != null ? <span className="font-mono text-subink">{pct(s.c.maxdd)}</span> : DASH}</td>
                  <td className="py-1.5 text-right text-[10px] text-subink/70">{s.c.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-[10px] text-subink/70 mt-1">IS→OOS 留存率 <Decay v={r.isOosDecay} /> · &lt;0.5 过拟合嫌疑</div>
        </div>

        {/* Regime 发散条(决策关键:牛市强/熊市塌)*/}
        {(r.bullSharpe != null || r.bearSharpe != null) && (
          <div className="border-t border-[#EFECE3] pt-3">
            <div className="text-[10px] uppercase tracking-wider text-[#888888] font-quant font-medium mb-2">Regime 依赖(牛 / 熊 夏普)</div>
            {[{ k: "牛市", v: r.bullSharpe }, { k: "熊市", v: r.bearSharpe }].map((x) => (
              <div key={x.k} className="flex items-center gap-2 text-[11px] mb-1">
                <span className="w-8 text-[#888888]">{x.k}</span>
                {x.v != null ? <><DivergeBar v={x.v} max={6} w={120} /><span className={`font-mono ${x.v >= 0 ? "text-songshi" : "text-danger"}`}>{num(x.v)}</span></> : DASH}
              </div>
            ))}
            {r.bullSharpe != null && r.bearSharpe != null && r.bullSharpe - r.bearSharpe > 6 && <div className="text-[10px] text-warn mt-1">⚠ 强 regime 依赖:熊市/流动性挤压时系统性失效风险</div>}
          </div>
        )}

        {/* 风格暴露条(是否风格伪装)*/}
        {betas.length > 0 && (
          <div className="border-t border-[#EFECE3] pt-3">
            <div className="text-[10px] uppercase tracking-wider text-[#888888] font-quant font-medium mb-2">风格暴露 β(是否风格伪装)</div>
            {betas.sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2 text-[11px] mb-1">
                <span className="w-16 text-[#888888] truncate" title={k}>{k}</span>
                <DivergeBar v={v} max={1} w={120} />
                <span className={`font-mono ${Math.abs(v) >= 0.6 ? "text-warn" : "text-[#333333]"}`}>{num(v)}</span>
              </div>
            ))}
          </div>
        )}

        {/* 显著性 / 因子有效性 / 成本 / 容量(数字网格)*/}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] border-t border-[#EFECE3] pt-3">
          <Kv k="DSR p值" v={r.dsrP != null ? `${r.dsrP.toFixed(3)} ${r.dsrSignificant ? "✓" : "✕"}` : "未审计"} />
          <Kv k="试验次数 N" v={r.nTrials != null ? String(r.nTrials) : "—"} />
          <Kv k="PSR" v={r.psr != null ? r.psr.toFixed(3) : "—"} />
          <Kv k="Rank ICIR(NW)" v={r.rankIcIr != null ? num(r.rankIcIr) : "—"} />
          <Kv k="MonoCorr 单调性" v={r.monoCorr != null ? num(r.monoCorr) : "—"} />
          <Kv k="中性化后 ICIR" v={r.neutNwIcir != null ? num(r.neutNwIcir) : "—"} />
          <Kv k="中性化 alpha 留存" v={r.icirRetention != null ? pct(r.icirRetention) : "—"} />
          <Kv k="成本侵蚀 gross→net" v={r.costDecay != null ? pct(r.costDecay) : "—"} />
          <Kv k="CVaR 95%" v={r.cvar95 != null ? pct(r.cvar95) : "—"} />
          <Kv k="容量上限" v={r.capacityM != null ? `${num(r.capacityM, 0)} 百万` : "—"} />
        </div>

        {/* 血缘 / 过拟合 */}
        <div className="border-t border-[#EFECE3] pt-3">
          <div className="text-[10px] uppercase tracking-wider text-[#888888] font-quant font-medium mb-2">血缘 / 过拟合(PBO · lineage)</div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
            <Kv k="PBO 过拟合概率" v={r.pbo != null ? <span className={r.pbo >= 0.3 ? "text-danger" : r.pbo >= 0.1 ? "text-warn" : "text-songshi"}>{num(r.pbo)}{r.pboRisk ? ` (${r.pboRisk})` : ""}</span> : "—"} />
            <Kv k={`与父版本相关${r.corrParentVersion ? ` (vs ${r.corrParentVersion})` : ""}`} v={r.corrToParent != null ? <span className={r.corrToParent >= 0.9 ? "text-danger" : "text-ink"}>{num(r.corrToParent)}{r.corrToParent >= 0.9 ? " 换皮" : ""}</span> : "—"} />
            <Kv k="正交增量 alpha(年化)" v={r.incrementalAlpha != null ? pct(r.incrementalAlpha) : "—"} />
            <Kv k="Size β" v={r.sizeBeta != null ? num(r.sizeBeta) : "—"} />
          </div>
        </div>

        {/* 失效边界 + 死因/下一步 */}
        <div className="border-t border-[#EFECE3] pt-3 space-y-1.5 text-[11px]">
          <Kv k="失效边界" v={Object.keys(fb).length ? Object.entries(fb).map(([k, v]) => `${k}=${v}`).join(" · ") : "—"} />
          <Kv k="实测衰减(滚动3年夏普)" v={r.decayed != null ? (
            <span className={r.decayed ? "text-danger" : "text-songshi"}>
              {r.decayed ? "是" : "否"}{r.rolling3ySharpeLatest != null ? ` (${num(r.rolling3ySharpeLatest)})` : ""}
            </span>
          ) : "未计算"} />
          {r.deathCause ? <div className="text-danger">死因:{r.deathCause}</div> : <Kv k="失效信号 / 下一步(静态)" v={r.nextAction || "—"} />}
        </div>

        {/* config + 研究记录(折叠)*/}
        <details className="border-t border-[#EFECE3] pt-3">
          <summary className="text-xs text-[#888888] cursor-pointer hover:text-[#333333] select-none">假设 / 研究记录 / Config</summary>
          <div className="mt-2 space-y-2">
            {r.s.hypothesis && <div className="text-[11px] text-[#333333] bg-[#FAF8F5]/50 p-2 rounded border border-[#EFECE3]">{r.s.hypothesis}</div>}
            {r.s.notes && <div className="text-[11px] text-[#333333] bg-[#FAF8F5]/50 p-2 rounded border border-[#EFECE3] whitespace-pre-wrap">{r.s.notes}</div>}
            <pre className="text-[10px] font-mono text-brand bg-[#FAF8F5] border border-[#EFECE3] rounded-lg p-3 overflow-x-auto max-h-40 scrollbar-thin">{JSON.stringify(r.s.config, null, 2)}</pre>
          </div>
        </details>
        <Link
          href={`/factors/${encodeURIComponent(r.family)}/${encodeURIComponent(r.version)}`}
          className="block w-full text-center rounded-lg border border-brand/30 text-brand py-2 text-[11px] font-semibold hover:bg-brand/5"
        >
          查看完整研究档案
        </Link>
      </div>
      <div className="border-t border-[#EFECE3] pt-3 mt-4 text-[9.5px] text-[#888888] leading-relaxed">
        {todo.length > 0 && <span>未计算:{todo.join("、")}。</span>} 数值直读自台账(strategy_registry),口径真实不漂移。仅供研究参考,不构成投资建议。
      </div>
    </div>
  );
}
