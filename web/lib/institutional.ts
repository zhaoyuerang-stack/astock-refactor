// 机构级候选表派生逻辑 —— 把台账 StrategyView 折算成「研究审计面板」所需字段。
// 铁律:只用台账真实字段;算不出的(PBO/lineage相关性/成本分解/IC)一律返回 null,
//       前端渲染「未计算」,绝不编造。Phase 2 后端 9-Gate 补算后这些字段自然填上。

import type { StrategyView } from "./types";

export type SampleCell = { sharpe: number | null; annual: number | null; maxdd: number | null; source: string };

export interface InstitutionalRow {
  s: StrategyView;
  id: string;
  family: string;
  version: string;
  status: string;
  track: string | null;            // standalone / diversifier
  // 三段样本:IS(样本内) / OOS(样本外) / Stress(压力)
  is: SampleCell;
  oos: SampleCell;
  stress: SampleCell;
  isOosDecay: number | null;       // OOS夏普 / IS夏普(留存率,<1 = 衰减)
  netAnnual: number | null;        // 全样本净年化(已扣成本,CostModel)
  maxdd: number | null;
  calmar: number | null;
  // 显著性 / 多重检验
  dsrP: number | null;
  dsrSignificant: boolean | null;
  psr: number | null;
  nTrials: number | null;
  // 尾部风险
  cvar95: number | null;
  tailRatio: number | null;
  sortino: number | null;
  // 成本 / 容量
  capacityM: number | null;        // 容量上限(百万)
  // 风险归因
  sizeBeta: number | null;
  topStyle: { name: string; beta: number } | null;  // 最大风格暴露
  // 判定 / 操作
  verdict: Verdict;
  deathCause: string | null;
  nextAction: string | null;
  productionScore: number | null;  // 综合排序分(只用已有真实成分;无审计=null)
  // 因子有效性(Gate2)+ 中性化残差(Gate3)+ 成本(Gate6)+ regime(Gate7)—— Phase 2A 已落台账
  rankIcIr: number | null;          // Rank ICIR(Newey-West)
  icMean: number | null;
  monoCorr: number | null;          // 分组单调性
  icDecay: number | null;
  neutNwIcir: number | null;        // 中性化后 ICIR(残差 alpha 代理)
  icirRetention: number | null;     // 中性化后 alpha 留存率(=增量 alpha 代理)
  costDecay: number | null;         // gross→net 成本侵蚀比例
  capacityLimitAum: number | null;  // 容量上限 AUM
  bullSharpe: number | null;
  bearSharpe: number | null;
  // Phase 2B/2C(lineage_pbo.py 落台账)
  pbo: number | null;               // 2B: 家族版本 CSCV 过拟合概率
  pboRisk: string | null;           // low / moderate / high
  corrToParent: number | null;      // 2C: 与 lineage 父版本收益相关性
  corrParentVersion: string | null; // 相关性对照的父版本号
  incrementalAlpha: number | null;  // 2C: 对父版本正交后的增量 alpha(年化)
  paramStability: number | null;    // 仍待:参数邻域通过率(需 param grid,未计算)
  // 实测衰减(governance/decay.py::decay_check,版本级,与上面家族级 deathCause/nextAction 是两件事)
  decayed: boolean | null;
  rolling3ySharpeLatest: number | null;
  decayCheckedAt: string | null;
}

export type VerdictTone = "success" | "ok" | "warn" | "danger" | "default";
export interface Verdict { label: string; short: string; tone: VerdictTone; icon: string; }

function n(x: unknown): number | null {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

// 评级判定:多重检验(DSR)优先,不凭原始收益把噪音评成精英。对齐原 getEvaluationVerdict。
export function deriveVerdict(s: StrategyView): Verdict {
  if (s.status === "已证伪") return { label: "已证伪 (Falsified)", short: "已证伪", tone: "danger", icon: "🚫" };
  if (s.status === "退役") return { label: "已退役 (Retired)", short: "已退役", tone: "default", icon: "💤" };

  const track = s.admission?.track;
  const ng = s.nine_gate || {};
  const dsrAudited = ng.dsr_p != null;
  const dsrPass = ng.gate4_verdict === "PASS";

  if (track === "diversifier") return { label: "分流器 (Diversifier)", short: "分流器", tone: "ok", icon: "🔗" };
  if (dsrAudited && !dsrPass) return { label: "未过多重检验 (Noise risk)", short: "噪音风险", tone: "warn", icon: "🌫️" };
  if (dsrAudited && dsrPass) {
    const annual = s.metrics?.annual ?? 0;
    const sharpe = s.metrics?.sharpe ?? 0;
    const calmar = s.metrics?.calmar ?? 0;
    if (annual >= 0.28 || calmar >= 1.6) return { label: "卓越·已过DSR (Elite)", short: "卓越", tone: "success", icon: "👑" };
    if (annual >= 0.2 && sharpe >= 1.0) return { label: "满意·已过DSR (Satisfactory)", short: "满意", tone: "success", icon: "⭐️" };
    return { label: "已验证 (Validated)", short: "已验证", tone: "ok", icon: "🛡️" };
  }
  if (s.status === "在册") return { label: "待多重检验审计", short: "待审计", tone: "warn", icon: "🔍" };
  return { label: "候选·未证实 (Unproven)", short: "未证实", tone: "default", icon: "⚗️" };
}

export function toInstitutionalRow(s: StrategyView): InstitutionalRow {
  const m = s.metrics || {};
  const ng = s.nine_gate || {};
  const sb = s.style_betas || {};

  // 三段样本:有专列优先用专列;IS 缺专列时退回全样本(标注 source 让前端能区分)
  const is: SampleCell = n(m.sharpe_2018) != null
    ? { sharpe: n(m.sharpe_2018), annual: n(m.annual_2018), maxdd: n(m.maxdd_2018), source: "2018-2026" }
    : { sharpe: n(m.sharpe), annual: n(m.annual), maxdd: n(m.maxdd), source: "全样本" };
  const oos: SampleCell = n(m.sharpe_2023) != null
    ? { sharpe: n(m.sharpe_2023), annual: n(m.annual_2023), maxdd: n(m.maxdd_2023), source: "2023-2026" }
    : (n(ng.wf_sharpe) != null
        ? { sharpe: n(ng.wf_sharpe), annual: null, maxdd: null, source: "walk-forward" }
        : { sharpe: null, annual: null, maxdd: null, source: "未验证" });
  const stress: SampleCell = n(m.sharpe_2010) != null
    ? { sharpe: n(m.sharpe_2010), annual: n(m.annual_2010), maxdd: n(m.maxdd_2010), source: "2010-2026" }
    : { sharpe: null, annual: null, maxdd: null, source: "未跑" };

  const isOosDecay = is.sharpe != null && is.sharpe !== 0 && oos.sharpe != null ? oos.sharpe / is.sharpe : null;

  const verdict = deriveVerdict(s);
  const isDead = s.status === "已证伪" || s.status === "退役";
  const deathCause = isDead
    ? (s.decay_signal ? s.decay_signal.split("/")[0].trim() : (s.status === "已证伪" ? "回测不可复现" : "alpha 衰减"))
    : null;
  const nextAction = isDead ? null : (s.decay_signal || null);

  const dc = s.decay_check || {};
  const decayed = typeof dc.decayed === "boolean" ? dc.decayed : null;
  const rolling3ySharpeLatest = n(dc.rolling_3y_sharpe_latest);
  const decayCheckedAt = typeof dc.checked_at === "string" ? dc.checked_at : null;

  const dsrP = n(ng.dsr_p);
  const dsrSig = typeof ng.dsr_significant === "boolean" ? ng.dsr_significant : null;
  const psr = n(ng.psr);
  const calmar = n(m.calmar);
  const netAnnual = n(m.annual);

  // 综合排序分(production_score):只用真实成分,无审计 → null(排末位 + 标未审计)。
  // = OOS质量 × 显著性 × 风险可承受 × 容量。各成分都映射到 [0,1] 再相乘,缺则中性=0.5。
  let productionScore: number | null = null;
  if (dsrP != null) {
    const oosQ = clamp01((oos.sharpe ?? is.sharpe ?? 0) / 2.0);           // 夏普2.0→满分
    const sig = dsrSig ? clamp01(psr ?? 0.5) : 0.2;                       // DSR 不显著重罚
    const riskQ = clamp01((calmar ?? 0) / 2.0);                          // 卡玛2.0→满分
    const capQ = s.capacity_m ? clamp01(s.capacity_m / 200) : 0.5;        // 容量2亿→满分,无=中性
    productionScore = oosQ * sig * riskQ * capQ;
  }

  // 最大风格暴露(判断是否风格伪装)
  let topStyle: { name: string; beta: number } | null = null;
  for (const [k, v] of Object.entries(sb)) {
    const b = n(v);
    if (b != null && (topStyle == null || Math.abs(b) > Math.abs(topStyle.beta))) topStyle = { name: k, beta: b };
  }

  return {
    s, id: s.strategy_id, family: s.family, version: s.version, status: s.status,
    track: s.admission?.track ?? null,
    is, oos, stress, isOosDecay,
    netAnnual, maxdd: n(m.maxdd), calmar,
    dsrP, dsrSignificant: dsrSig, psr, nTrials: n(ng.n_trials),
    cvar95: n(ng.cvar_95), tailRatio: n(ng.tail_ratio), sortino: n(ng.sortino),
    capacityM: n(s.capacity_m) || null,
    sizeBeta: n(sb.size), topStyle,
    verdict, deathCause, nextAction, productionScore,
    // Phase 2A 已落台账(来自 9-Gate gate2/3/6/7)
    rankIcIr: n(ng.nw_icir), icMean: n(ng.ic_mean), monoCorr: n(ng.monotonicity_corr),
    icDecay: n(ng.ic_decay), neutNwIcir: n(ng.neut_nw_icir), icirRetention: n(ng.icir_retention),
    costDecay: n(ng.cost_decay_rate), capacityLimitAum: n(ng.capacity_limit_aum),
    bullSharpe: n(ng.bull_sharpe), bearSharpe: n(ng.bear_sharpe),
    // Phase 2B/2C(lineage_pbo.py)
    pbo: n(ng.pbo), pboRisk: typeof ng.pbo_risk === "string" ? ng.pbo_risk : null,
    corrToParent: n(ng.corr_to_parent),
    corrParentVersion: typeof ng.corr_parent_version === "string" ? ng.corr_parent_version : null,
    incrementalAlpha: n(ng.incremental_alpha),
    paramStability: null,   // 参数邻域通过率仍需 param grid,未计算
    decayed, rolling3ySharpeLatest, decayCheckedAt,
  };
}

function clamp01(x: number): number { return Math.max(0, Math.min(1, x)); }

// 按 production_score 降序;无分(未审计)排末位,组内按净年化降序。
export function sortLeaderboard(rows: InstitutionalRow[]): InstitutionalRow[] {
  return [...rows].sort((a, b) => {
    if (a.productionScore == null && b.productionScore == null) return (b.netAnnual ?? -1) - (a.netAnnual ?? -1);
    if (a.productionScore == null) return 1;
    if (b.productionScore == null) return -1;
    return b.productionScore - a.productionScore;
  });
}

// ── Family view 聚合(按 lineage 折叠,避免参数换皮版平铺重复计数)─────────────
export interface FamilyGroup {
  family: string;
  familyName: string;
  rows: InstitutionalRow[];
  nRegistered: number;
  bestSharpe: number | null;     // 家族内最优(全样本)夏普
  medianSharpe: number | null;   // 家族内中位夏普 —— best 远高于 median = 选择偏差信号
  totalTrials: number | null;    // 家族总试验次数(各版本 n_trials 求和)
  anyDsrPass: boolean;
}

export function groupFamilies(rows: InstitutionalRow[]): FamilyGroup[] {
  const map = new Map<string, InstitutionalRow[]>();
  for (const r of rows) {
    if (!map.has(r.family)) map.set(r.family, []);
    map.get(r.family)!.push(r);
  }
  const groups: FamilyGroup[] = [];
  for (const [family, rs] of Array.from(map.entries())) {
    const sharpes = rs.map((r) => r.is.sharpe).filter((x): x is number => x != null).sort((a, b) => a - b);
    const trials = rs.map((r) => r.nTrials).filter((x): x is number => x != null);
    groups.push({
      family,
      familyName: rs[0].s.family_name || family,
      rows: rs,
      nRegistered: rs.filter((r) => r.status === "在册").length,
      bestSharpe: sharpes.length ? sharpes[sharpes.length - 1] : null,
      medianSharpe: sharpes.length ? sharpes[Math.floor((sharpes.length - 1) / 2)] : null,
      totalTrials: trials.length ? trials.reduce((a, b) => a + b, 0) : null,
      anyDsrPass: rs.some((r) => r.s.nine_gate?.gate4_verdict === "PASS"),
    });
  }
  // 有在册版本的家族优先,其次按 best 夏普
  return groups.sort((a, b) => (b.nRegistered - a.nRegistered) || ((b.bestSharpe ?? -1) - (a.bestSharpe ?? -1)));
}

// ── Gate view:9-Gate 通过矩阵(只用台账 nine_gate 已有的 gate 判定)──────────
export type GateCell = "PASS" | "WARN" | "FAIL" | "NA";
export interface GateColumn { key: string; label: string; }

// 台账 nine_gate 当前真实落了:DSR显著(gate4)、尾部/风险(gate7)、walk-forward、CV、PSR。
// 其余 9 道门(成本/容量/中性化/IC/PBO 等)Phase 2 补算前标 NA,绝不假绿。
export const GATE_COLUMNS: GateColumn[] = [
  { key: "g_audited", label: "已审计" },
  { key: "g_dsr", label: "DSR显著" },
  { key: "g_psr", label: "PSR" },
  { key: "g_wf", label: "WalkFwd" },
  { key: "g_cv", label: "CV稳定" },
  { key: "g_tail", label: "尾部风险" },
  { key: "g_oos", label: "OOS可比" },
  { key: "g_cost", label: "成本(P2)" },
  { key: "g_pbo", label: "PBO(P2)" },
];

export function gateCell(r: InstitutionalRow, key: string): GateCell {
  const ng = r.s.nine_gate || {};
  switch (key) {
    case "g_audited": return ng.dsr_p != null ? "PASS" : "NA";
    case "g_dsr": return ng.dsr_p == null ? "NA" : (ng.gate4_verdict === "PASS" ? "PASS" : "FAIL");
    case "g_psr": return r.psr == null ? "NA" : (r.psr >= 0.95 ? "PASS" : r.psr >= 0.9 ? "WARN" : "FAIL");
    case "g_wf": {
      const wf = n(ng.wf_sharpe); const pr = n(ng.wf_positive_ratio);
      if (wf == null) return "NA";
      return (wf >= 0.5 && (pr ?? 0) >= 0.6) ? "PASS" : wf > 0 ? "WARN" : "FAIL";
    }
    case "g_cv": {
      const cv = n(ng.cv_sharpe);
      if (cv == null) return "NA";
      return cv >= 0.8 ? "PASS" : cv > 0 ? "WARN" : "FAIL";
    }
    case "g_tail": return ng.gate7_verdict == null ? "NA" : (ng.gate7_verdict === "PASS" ? "PASS" : ng.gate7_verdict === "WARN" ? "WARN" : "FAIL");
    case "g_oos": return r.oos.sharpe == null ? "NA" : (r.oos.sharpe >= 0.5 ? "PASS" : r.oos.sharpe > 0 ? "WARN" : "FAIL");
    case "g_cost": return "NA";   // Phase 2 后端补算 gross/net 成本分解
    case "g_pbo": return "NA";    // Phase 2 后端补算 PBO
    default: return "NA";
  }
}
