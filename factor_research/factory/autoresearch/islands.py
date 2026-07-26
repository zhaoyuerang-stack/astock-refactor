"""多岛屿进化搜索:在受控 DSL 空间内变异/交叉/迁移。

每个岛屿独立进化(各自 rng),周期性把最优个体迁移到邻岛——岛屿模型
保多样性、防全局早熟。适应度四项(同一套 canonical 验证线,绝无第二套口径):
  |ICIR|                          真实 run_l0 的独立 edge
  + novelty_weight × 行为新颖性    vs 已评估候选+参考池的最近邻行为距离(因子形态)
  − corr_weight × 对在册组合相关    候选 top-N 收益与在册腿同涨同跌→罚,反相关(防御腿)→奖
  − turnover_weight × 换手代理      top-N 成员相邻期流失率→罚,把 L1 的成本压力前置
  [硬闸] 对在册相关 ≥ rediscovery_corr → |ICIR| 归零(边际为零,不让高 IC 重发现霸榜)
只奖绩效必然同质坍缩;新颖性填未占领的行为生态位;边际项填组合相关空洞
(伪多样性审计:在册 5 股票腿 0.76 相关);换手项防"高 IC 高换手在 L1 被成本杀"
(成本约 12pp/年),并抵消去去相关项对反转(高换手)的偏好。冠军可再走更深的 L1~L3。

全程确定性:同 rng_seed + 同数据 → 同搜索轨迹(实验可复现)。
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field, replace

from app_config.log import get_logger

from .generator import generate_seed_candidates
from .models import Candidate
from .novelty import (
    candidate_factor_panel,
    novelty_score,
    partial_correlation_to_book,
    sample_behavior_dates,
    topn_long_return,
    topn_turnover,
)
from .pipeline import run_validation_pipeline
from .registry import ALLOWED_FACTORS, ALLOWED_TRANSFORMS
from .validator import DSLValidationError, validate_candidate_ast

logger = get_logger(__name__)


class _MockRepo:
    def add(self, *args, **kwargs) -> bool: return True
    def record(self, *args, **kwargs) -> None: pass
    def append(self, *args, **kwargs) -> None: pass


# 三个极性历史 regime 段(ADR-026:min-ICIR 生存适应度;全部 < holdout boundary 2025-01-01)
REGIME_SEGMENTS = (
    ("regime_1", "2024-01-02", "2024-02-08"),  # 小盘流动性踩踏
    ("regime_2", "2024-04-01", "2024-09-30"),  # 蓝筹/价值轮动
    ("regime_3", "2023-01-02", "2023-12-31"),  # 常态牛市
)


def _regime_survival_edge(segment_icirs, fallback_edge: float) -> float:
    """跨 regime 生存 edge = min(|ICIR|),只聚合面板内有数据的段(None = 段不在面板)。

    walk-forward 截断下(如调度 cutoff≈2023 年末)2024 两段不可用;若把"无数据"
    混同 ICIR=0 参与 min,所有候选 edge 恒为 0,fitness 对 ICIR 失明。全段不可用
    时退回全样本 edge(regime_aware 不会让搜索瞎掉)。
    """
    avail = [abs(float(v)) for v in segment_icirs if v is not None]
    return min(avail) if avail else float(fallback_edge)


def ast_expr(ast: dict) -> str:
    expr = " + ".join(
        f"{t.get('factor')}({t.get('params', {}).get('window', '')})×{t.get('weight', 1)}"
        for t in ast.get("terms", [])
    )
    return f"-({expr})" if ast.get("direction") == "negative" else expr


def _random_params(spec, rng: random.Random) -> dict:
    return {name: rng.randint(int(lo), int(hi)) for name, (lo, hi) in spec.params.items()}


def _random_term(rng: random.Random) -> dict:
    name = rng.choice(sorted(ALLOWED_FACTORS))
    return {
        "factor": name,
        "params": _random_params(ALLOWED_FACTORS[name], rng),
        "transforms": ["mad_clip", "zscore", "rank"],
        "weight": round(rng.uniform(0.2, 1.0) * rng.choice([1, -1]), 2),
    }


def mutate_ast(ast: dict, rng: random.Random) -> dict:
    """对 AST 施加 1~2 个白名单内的随机变异,返回新 dict(不改原对象)。"""
    out = copy.deepcopy(ast)
    terms = out.get("terms", [])
    ops = ["window", "weight", "swap_factor", "direction", "transform", "execution"]
    if len(terms) < 3:
        ops.append("add_term")
    if len(terms) > 1:
        ops.append("drop_term")

    for op in rng.sample(ops, k=rng.choice([1, 2])):
        # Avoid picking a term if the mutation only modifies global execution parameters
        t = rng.choice(terms) if terms else None
        if op == "window" and t is not None:
            spec = ALLOWED_FACTORS.get(t["factor"])
            if spec and "window" in spec.params:
                lo, hi = spec.params["window"]
                cur = int(t.get("params", {}).get("window", lo))
                t.setdefault("params", {})["window"] = max(int(lo), min(int(hi), int(cur * rng.uniform(0.5, 2.0)) or int(lo)))
        elif op == "weight" and t is not None:
            t["weight"] = round(max(-2.0, min(2.0, float(t.get("weight", 1.0)) * rng.uniform(0.5, 1.5) * rng.choice([1, 1, -1]))), 2) or 0.3
        elif op == "swap_factor" and t is not None:
            name = rng.choice(sorted(ALLOWED_FACTORS))
            t["factor"] = name
            t["params"] = _random_params(ALLOWED_FACTORS[name], rng)
        elif op == "direction":
            out["direction"] = "negative" if out.get("direction", "positive") == "positive" else "positive"
        elif op == "transform" and t is not None:
            pool = sorted(ALLOWED_TRANSFORMS)
            tr = list(t.get("transforms", []))
            if tr and rng.random() < 0.5:
                tr.remove(rng.choice(tr))
            else:
                cand = rng.choice(pool)
                if cand not in tr:
                    tr.append(cand)
            t["transforms"] = tr
        elif op == "add_term":
            terms.append(_random_term(rng))
        elif op == "drop_term" and t is not None:
            terms.remove(rng.choice(terms))
        elif op == "execution":
            exec_params = out.setdefault("execution", {})
            exec_params["portfolio_size"] = rng.choice([15, 25, 35, 50])
            exec_params["rebalance_freq"] = rng.choice(["5D", "10D", "20D", "40D"])
            if rng.random() < 0.5:
                exec_params["smoothing_window"] = rng.choice([5, 10, 20])
            else:
                exec_params.pop("smoothing_window", None)

    mech = str(out.get("thesis", {}).get("mechanism", "")) or "岛屿搜索变异候选"
    out["thesis"] = {"mechanism": mech, "citation": "autoresearch island search"}
    return out


def crossover_ast(a: dict, b: dict, rng: random.Random) -> dict:
    """各取部分 terms 杂交;thesis 标注双亲机制并融合执行参数。"""
    pool = copy.deepcopy(a.get("terms", [])) + copy.deepcopy(b.get("terms", []))
    k = min(len(pool), rng.choice([1, 2, 2, 3]))
    terms = rng.sample(pool, k=k)
    mech_a = str(a.get("thesis", {}).get("mechanism", ""))[:40]
    mech_b = str(b.get("thesis", {}).get("mechanism", ""))[:40]

    # ── [Crossover Execution Settings] ──
    exec_a = a.get("execution", {})
    exec_b = b.get("execution", {})
    execution = {}
    if exec_a or exec_b:
        execution["portfolio_size"] = rng.choice([exec_a.get("portfolio_size"), exec_b.get("portfolio_size")])
        execution["rebalance_freq"] = rng.choice([exec_a.get("rebalance_freq"), exec_b.get("rebalance_freq")])
        smooth = rng.choice([exec_a.get("smoothing_window"), exec_b.get("smoothing_window")])
        if smooth is not None:
            execution["smoothing_window"] = smooth
        execution = {k: v for k, v in execution.items() if v is not None}

    out = {
        "type": "linear_combo",
        "terms": terms,
        "direction": rng.choice([a.get("direction", "positive"), b.get("direction", "positive")]),
        "thesis": {"mechanism": f"杂交: {mech_a} × {mech_b}" or "岛屿搜索杂交候选",
                   "citation": "autoresearch island search"},
    }
    if execution:
        out["execution"] = execution
    return out


def _merge_provenance(parents: list[Candidate]) -> dict:
    """子代(变异/交叉)继承父代种子来源(ADR-022):汇总祖先 origin + 保留 LLM 种子细节。

    任一祖先是 llm_seed → ancestor_origins 含 'llm_seed' → 晋级时触发 semantic_seed_review。
    """
    origins: set[str] = set()
    llm_ancestors: list[dict] = []
    for p in parents:
        prov = p.provenance or {}
        o = prov.get("origin")
        if o == "derived":
            origins.update(prov.get("ancestor_origins", []))
            llm_ancestors.extend(prov.get("llm_ancestors", []))
        elif o == "llm_seed":
            origins.add(o)
            llm_ancestors.append({k: prov.get(k) for k in ("theme", "model") if prov.get(k)})
        elif o:
            origins.add(o)
    out: dict = {"origin": "derived", "ancestor_origins": sorted(origins)}
    dedup = [m for i, m in enumerate(llm_ancestors) if m and m not in llm_ancestors[:i]]
    if dedup:
        out["llm_ancestors"] = dedup
    return out


def ast_to_features(ast: dict) -> list[float]:
    """将 Candidate AST 转换为固定大小的数值特征向量。"""
    sorted_factors = sorted(ALLOWED_FACTORS.keys())

    factor_presence = {f: 0.0 for f in sorted_factors}
    factor_weight = {f: 0.0 for f in sorted_factors}
    factor_window = {f: 0.0 for f in sorted_factors}

    total_transforms = 0.0
    for term in ast.get("terms", []):
        factor = term.get("factor")
        if factor in factor_presence:
            factor_presence[factor] = 1.0
            factor_weight[factor] += float(term.get("weight", 1.0))
            window = term.get("params", {}).get("window")
            if window is not None:
                factor_window[factor] = float(window)
            total_transforms += len(term.get("transforms", []))

    features = []
    for f in sorted_factors:
        features.append(factor_presence[f])
        features.append(factor_weight[f])
        features.append(factor_window[f])

    features.append(total_transforms)
    features.append(float(len(ast.get("terms", []))))
    direction = 1.0 if ast.get("direction") == "positive" else -1.0
    features.append(direction)

    # ── [Evolved Execution Features] ──
    exec_params = ast.get("execution", {})
    port_size = float(exec_params.get("portfolio_size", 25)) / 100.0
    features.append(port_size)

    freq_str = str(exec_params.get("rebalance_freq", "20D"))
    try:
        freq_days = float(freq_str.replace("D", "").replace("W", ""))
    except ValueError:
        freq_days = 20.0
    features.append(freq_days / 100.0)

    smooth_w = float(exec_params.get("smoothing_window", 0)) / 100.0
    features.append(smooth_w)

    return features


def _spawn_valid(
    parents: list[Candidate],
    rng: random.Random,
    *,
    crossover: bool,
    retries: int = 8,
    surrogate_model=None,
    threshold=None,
) -> Candidate | None:
    """变异/杂交直到产出一个能过白名单校验的候选;超过重试次数放弃。
    如果提供了 surrogate_model，则使用它对候选进行筛选，最多重试 5 次筛选。
    """
    import numpy as np

    last_valid_candidate = None
    max_surrogate_retries = 5

    for surr_attempt in range(max_surrogate_retries + 1):
        cand = None
        base_asts = [p.ast for p in parents]
        prov = _merge_provenance(parents)
        for _ in range(retries):
            try:
                if crossover and len(base_asts) >= 2:
                    pa, pb = rng.sample(base_asts, k=2)
                    ast = crossover_ast(pa, pb, rng)
                else:
                    ast = mutate_ast(rng.choice(base_asts), rng)
                cand = replace(validate_candidate_ast(ast), provenance=prov)
                break
            except DSLValidationError:
                continue

        if cand is None:
            return last_valid_candidate

        last_valid_candidate = cand

        if surrogate_model is None or threshold is None or surr_attempt == max_surrogate_retries:
            return cand

        # Epsilon-Greedy Random Exploration (15% probability to bypass surrogate model screening)
        if rng.random() < 0.15:
            return cand

        # Predict fitness
        features = np.array([ast_to_features(cand.ast)])
        pred_fit = float(surrogate_model.predict(features)[0])

        if pred_fit >= threshold:
            return cand

    return last_valid_candidate


@dataclass
class ChampionRecord:
    fingerprint: str
    island: int
    generation: int
    icir: float
    expr: str
    status: str = ""
    decision: str = ""
    reason: str = ""
    novelty: float = 0.0
    fitness: float = 0.0
    corr_to_book: float = 0.0
    turnover: float = 0.0
    provenance: dict = field(default_factory=dict)  # ADR-022 种子溯源(随冠军透出供审视/晋级)
    complexity: float = 0.0
    regime_icirs: dict[str, float] = field(default_factory=dict)
    ast: dict = field(default_factory=dict)
    # knowledge gate 的 fitness 乘子(1.0 正常 / 0.3 方向降权;R-EVIDENCE 精神:
    # fitness 必须能从记录字段机械复原,pa≠1 时缺它冠军 fitness 无法自证)
    priority_adjustment: float = 1.0


@dataclass
class IslandSearchResult:
    evaluated: int = 0
    champions: list[ChampionRecord] = field(default_factory=list)


def _style_exposure(panel, style_panels) -> float:
    """候选因子面板对 size/流动性等风格的暴露 = 平均 |截面 spearman 相关| ∈ [0,1]。

    高值 = 该候选是风格(尤其 size/流动性)的伪装 / blending 把信号拉回小盘流动性簇 →
    fitness 据此压分,使搜索保持正交。panel 与 style 面板按交集对齐(style 自动截到 panel 日期,
    walk-forward 下即 ≤cutoff 训练期,size/流动性为当期 PIT 特真实特征、无前瞻泄露)。
    """
    import pandas as pd

    vals = []
    for s in style_panels:
        sp = s.reindex(index=panel.index, columns=panel.columns)
        cs = []
        for t in panel.index:
            a, b = panel.loc[t], sp.loc[t]
            if a.notna().sum() > 30 and b.notna().sum() > 30:
                c = a.corr(b, method="spearman")
                if pd.notna(c):
                    cs.append(abs(float(c)))
        if cs:
            vals.append(sum(cs) / len(cs))
    return sum(vals) / len(vals) if vals else 0.0


def _split_stability(panel, forward_ret) -> float:
    """in-sample 内部二分稳定性 ∈ [0,1]:edge 在训练窗 early/late 两半都成立的程度。

    把 panel 与 forward_ret 的共享日期按**时序**二分,各算截面 rank-IC 均值 ic1/ic2;
    把两半投影到全样本方向(d=sign(ic1+ic2)),取较弱半 / |全样本| → ∈[0,1]:
      1 = 两半同样强(稳),0 = 只靠一半或另一半翻号(过拟合签名)。
    **严格防泄露**:只用传入的(walk-forward 下 ≤cutoff)训练面板,不碰 held-out OOS。
    日期不足以二分 → 返回 1.0(不罚,证据不足不误伤)。
    """
    import pandas as pd

    dates = [t for t in panel.index if t in forward_ret.index]
    if len(dates) < 8:
        return 1.0
    mid = len(dates) // 2

    def _ic_mean(idx):
        cs = []
        for t in idx:
            a, b = panel.loc[t], forward_ret.loc[t]
            if a.notna().sum() > 20 and b.notna().sum() > 20:
                c = a.corr(b, method="spearman")
                if pd.notna(c):
                    cs.append(float(c))
        return sum(cs) / len(cs) if cs else 0.0

    ic1, ic2 = _ic_mean(dates[:mid]), _ic_mean(dates[mid:])
    full = (ic1 + ic2) / 2.0
    if abs(full) < 1e-9:
        return 0.0
    d = 1.0 if full > 0 else -1.0
    weaker = min(d * ic1, d * ic2)  # 两半投影到全样本方向后的较弱半
    return max(0.0, min(1.0, weaker / abs(full)))


def _ast_factor_weights(ast: dict) -> dict[str, float]:
    """把线性 AST 压成带方向的基础因子权重向量。

    这是 Phase 1 代数代理的核心输入。rank/zscore/mad_clip 不改变符号;neg 与
    direction 会翻转符号。代理只用于搜索排序加速,不替代 L0-L3 真实验证。
    """
    direction = -1.0 if ast.get("direction") == "negative" else 1.0
    weights: dict[str, float] = {}
    for term in ast.get("terms", []):
        factor = term.get("factor")
        if not factor:
            continue
        sign = direction
        if "neg" in term.get("transforms", []):
            sign *= -1.0
        weights[factor] = weights.get(factor, 0.0) + sign * float(term.get("weight", 1.0))
    return {k: v for k, v in weights.items() if abs(v) > 1e-12}


def _algebraic_metric_proxy(
    ast: dict,
    *,
    corr_by_factor: dict[str, float],
    turnover_by_factor: dict[str, float],
) -> tuple[float | None, float | None]:
    """用基础因子一次性估计表近似候选 corr/turnover。

    corr 使用带符号权重均值,使整体反向候选变成负相关代理;turnover 使用绝对权重
    均值,因为换手不随多空方向变号。缺少基础因子估计时返回 None,调用方回退到
    精确 top-N 代理。
    """
    weights = _ast_factor_weights(ast)

    corr_num = corr_den = 0.0
    for factor, weight in weights.items():
        if factor not in corr_by_factor:
            continue
        corr_num += weight * float(corr_by_factor[factor])
        corr_den += abs(weight)
    corr = None if corr_den <= 1e-12 else max(-1.0, min(1.0, corr_num / corr_den))

    turn_num = turn_den = 0.0
    for factor, weight in weights.items():
        if factor not in turnover_by_factor:
            continue
        turn_num += abs(weight) * float(turnover_by_factor[factor])
        turn_den += abs(weight)
    turnover = None if turn_den <= 1e-12 else max(0.0, min(1.0, turn_num / turn_den))
    return corr, turnover


def _default_params_for_factor(factor: str) -> dict:
    spec = ALLOWED_FACTORS[factor]
    return {
        name: int((int(lo) + int(hi)) // 2)
        for name, (lo, hi) in spec.params.items()
    }


def _single_factor_ast(factor: str) -> dict:
    return {
        "type": "linear_combo",
        "terms": [{
            "factor": factor,
            "params": _default_params_for_factor(factor),
            "transforms": ["mad_clip", "zscore", "rank"],
            "weight": 1.0,
        }],
        "direction": "positive",
        "thesis": {"mechanism": f"{factor} basis proxy", "citation": "algebraic metric proxy"},
    }


def _candidate_factors(candidates: list[Candidate]) -> set[str]:
    out: set[str] = set()
    for c in candidates:
        for term in c.ast.get("terms", []):
            factor = term.get("factor")
            if factor in ALLOWED_FACTORS:
                out.add(factor)
    return out


def _build_algebraic_metric_maps(
    *,
    close,
    volume,
    forward_ret,
    behavior_idx,
    factors: set[str],
    ref_returns: list,
    market_ret,
    top_n: int,
    need_corr: bool,
    need_turnover: bool,
) -> tuple[dict[str, float], dict[str, float]]:
    """一次性为基础因子建立 corr/turnover 估计表。

    失败的基础因子跳过;候选若引用缺失因子,fitness 会回退到精确 top-N 代理。
    """
    corr_by_factor: dict[str, float] = {}
    turnover_by_factor: dict[str, float] = {}
    for factor in sorted(factors):
        try:
            panel = candidate_factor_panel(_single_factor_ast(factor), close, volume, behavior_idx)
        except Exception:
            continue
        if need_corr and ref_returns:
            corr_by_factor[factor] = partial_correlation_to_book(
                topn_long_return(panel, forward_ret, top_n),
                ref_returns,
                market_ret,
            )
        if need_turnover:
            turnover_by_factor[factor] = topn_turnover(panel, top_n)
    return corr_by_factor, turnover_by_factor


def _first_l0_metrics(result) -> dict:
    exps = (result.metrics or {}).get("experiments", [])
    return (exps[0].get("metrics", {}) or {}) if exps else {}


def _multi_fidelity_prefilter(
    candidates: list[Candidate],
    *,
    pipe_kw: dict,
    level1_dates: int = 20,
    level1_ic_min: float = 0.02,
    level2_dates: int = 60,
    level2_keep_ratio: float = 0.5,
    trial_counter: list[int] | None = None,
) -> list[Candidate]:
    """Phase 2 多保真预筛:20日 IC 挡垃圾,60日 ICIR 保留前部候选。

    预筛结果只决定是否进入完整 L0,不写真实 repository/experiment_log/review_queue,
    也不替代后续 L0-L3 验证。
    """
    if not candidates:
        return []

    screen_kw = dict(pipe_kw)
    screen_kw.pop("sample_dates", None)
    screen_kw["repository"] = _MockRepo()
    screen_kw["experiment_log"] = _MockRepo()
    screen_kw["review_queue"] = _MockRepo()

    level1: list[Candidate] = []
    fallback: list[tuple[float, Candidate]] = []
    for c in candidates:
        result = run_validation_pipeline(c, max_stage="l0", sample_dates=level1_dates, **screen_kw)
        if trial_counter is not None:
            trial_counter[0] += 1
        metrics = _first_l0_metrics(result)
        ic = abs(float(metrics.get("IC_mean", metrics.get("rank_ic_mean", 0.0)) or 0.0))
        fallback.append((ic, c))
        if ic >= level1_ic_min:
            level1.append(c)
    if not level1:
        return [max(fallback, key=lambda x: x[0])[1]]

    scored: list[tuple[float, Candidate]] = []
    for c in level1:
        result = run_validation_pipeline(c, max_stage="l0", sample_dates=level2_dates, **screen_kw)
        if trial_counter is not None:
            trial_counter[0] += 1
        metrics = _first_l0_metrics(result)
        score = abs(float(metrics.get("ICIR_nw", metrics.get("ICIR", 0.0)) or 0.0))
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)

    import math

    keep = max(1, int(math.ceil(len(scored) * max(0.0, min(1.0, level2_keep_ratio)))))
    return [c for _, c in scored[:keep]]


def _candidates_pending_evaluation(
    islands: list[list[Candidate]],
    *,
    memo: dict,
    pre_evaluated_results: dict,
    multi_fidelity: bool,
    pipe_kw: dict,
    level1_dates: int,
    level1_ic_min: float,
    level2_dates: int,
    level2_keep_ratio: float,
    trial_counter: list[int] | None = None,
) -> list[Candidate]:
    pending: list[Candidate] = []
    for i, pop in enumerate(islands):
        old = [
            c for c in pop
            if c.fingerprint in memo or c.fingerprint in pre_evaluated_results
        ]
        new = [
            c for c in pop
            if c.fingerprint not in memo and c.fingerprint not in pre_evaluated_results
        ]
        if multi_fidelity:
            kept = _multi_fidelity_prefilter(
                new,
                pipe_kw=pipe_kw,
                level1_dates=level1_dates,
                level1_ic_min=level1_ic_min,
                level2_dates=level2_dates,
                level2_keep_ratio=level2_keep_ratio,
                trial_counter=trial_counter,
            )
            if not old and not kept and new:
                kept = [new[0]]
            islands[i] = old + kept
            pending.extend(kept)
        else:
            pending.extend(new)
    seen: set[str] = set()
    unique: list[Candidate] = []
    for c in pending:
        if c.fingerprint in seen:
            continue
        seen.add(c.fingerprint)
        unique.append(c)
    return unique


def run_island_search(
    close,
    volume,
    amount,
    forward_ret,
    *,
    vintage_id: str,
    n_islands: int = 4,
    generations: int = 3,
    population: int = 8,
    elite: int = 2,
    top_k: int = 5,
    final_stage: str = "l0",
    seeds: list[Candidate] | None = None,
    rng_seed: int = 7,
    sample_dates: int | None = 120,
    novelty_weight: float = 0.25,
    corr_weight: float = 0.0,
    turnover_weight: float = 0.0,
    complexity_weight: float = 0.0,
    orth_weight: float = 0.0,
    stability_weight: float = 0.0,
    use_algebraic_proxies: bool = False,
    multi_fidelity: bool = False,
    mf_level1_dates: int = 20,
    mf_level1_ic_min: float = 0.02,
    mf_level2_dates: int = 60,
    mf_level2_keep_ratio: float = 0.5,
    computation_time_budget: float = 10.0,
    rediscovery_corr: float = 0.5,
    reference_panels: list | None = None,
    style_panels: list | None = None,
    behavior_dates: int = 60,
    top_n: int = 25,
    repository=None,
    experiment_log=None,
    review_queue=None,
    runners: dict | None = None,
    evaluation_backend: str = "thread",
    regime_aware: bool = False,
) -> IslandSearchResult:
    """N 岛屿 × G 代进化;适应度 = edge + novelty_weight×新颖性 − corr_weight×对在册组合相关
    - orth_weight×对 size/流动性风格暴露(§四修法②:把"正交"设为搜索目标)。
    edge = |ICIR_nw|(NW 重叠校正的诚实绝对量级)。
    **stability_weight>0(§四②真版,根治 blending 过拟合)**:edge 乘以 in-sample 内部二分稳定性
    折扣 ∈[0,1]——把训练窗按时序二分 early/late,edge 在两半都成立才保留,只靠一半(过拟合签名)
    则折扣趋零。**严格防泄露**:只用传入的训练面板(walk-forward 下即 ≤cutoff),绝不碰 held-out OOS。
    orth_weight=0 且 stability_weight=0 完全向后兼容。
    style_panels:[size, 流动性] 风格面板(orth_weight>0 时计;size/流动性为当期 PIT 特征,无前瞻)。

    reference_panels:在册母策略的因子面板。两种用法:
      - 新颖性(行为距离):候选因子形态与之雷同 → 压分;
      - 边际贡献(corr_weight>0):候选 top-N 收益与在册腿同涨同跌 → 罚,反相关(防御腿)→ 奖。
    传入面板必须与 close 同口径(walk-forward 下即已截断的训练面板)。
    novelty_weight=0 退回纯绩效;corr_weight=0 不计边际(向后兼容)。
    """
    # 因子面板 memo 搜索内有效:清空以隔离上一次 run / 不同数据口径(防陈旧命中)
    from factors.autoresearch_dsl import clear_factor_cache
    clear_factor_cache()

    # 0. Load historical lessons (系统级反向传播机制预温)
    historical_lessons = []
    try:
        from factory.autoresearch.repositories import (
            CandidateDecision,
            CandidateRepository,
            ExperimentLog,
        )
        exp_repo = ExperimentLog()
        cand_repo = CandidateRepository()
        ast_map = {c.fingerprint: c.ast for c in cand_repo.all()}
        for eval_res in exp_repo.iter_all():
            ast = ast_map.get(eval_res.fingerprint)
            if ast is not None:
                fit_score = 0.0
                if eval_res.decision == CandidateDecision.PROMOTE:
                    # Look at metrics
                    l0_m = eval_res.metrics.get("experiments", [{}])[0].get("metrics", {})
                    fit_score = float(l0_m.get("ICIR", 0.1))
                elif eval_res.decision == CandidateDecision.DISCARD:
                    fit_score = -0.5
                historical_lessons.append((ast, fit_score))
        logger.info(f"[*] Loaded {len(historical_lessons)} historical lessons for surrogate model pre-warming.")
    except Exception as e:
        logger.warning(f"[!] Failed to load historical lessons: {e}")

    seeds = list(seeds) if seeds else list(generate_seed_candidates(limit=max(n_islands * population, 150)))
    pipe_kw = dict(
        close=close, volume=volume, amount=amount, forward_ret=forward_ret,
        vintage_id=vintage_id, repository=repository, experiment_log=experiment_log,
        review_queue=review_queue, runners=runners, sample_dates=sample_dates,
        computation_time_budget=computation_time_budget,
    )

    memo: dict[str, tuple[float, object]] = {}  # fingerprint -> (fitness, result)
    evaluated = 0
    prefilter_trials = [0]

    # 行为档案:已评估候选的因子面板,新颖性 = 与(档案 + 外部参考池)的最近邻距离。
    # 面板即传入的(walk-forward 下已截断的)训练面板,行为距离只用历史。
    behavior_idx = sample_behavior_dates(close.index, behavior_dates)
    refs: list = [p.loc[p.index.intersection(behavior_idx)] for p in (reference_panels or [])]
    archive: dict[str, object] = {}
    # 在册腿的 top-N 收益代理(一次性预算;corr_weight>0 才需要)
    ref_returns: list = []
    market_ret: object = None
    if corr_weight > 0 and forward_ret is not None:
        ref_returns = [topn_long_return(r, forward_ret, top_n) for r in refs]
        # 根因#2:市场代理 = 全市场等权前向收益,供 partial_correlation_to_book 扣共同暴露
        market_ret = forward_ret.mean(axis=1)
    proxy_corr_by_factor: dict[str, float] = {}
    proxy_turnover_by_factor: dict[str, float] = {}
    if use_algebraic_proxies and ((corr_weight > 0 and ref_returns) or turnover_weight > 0):
        proxy_corr_by_factor, proxy_turnover_by_factor = _build_algebraic_metric_maps(
            close=close,
            volume=volume,
            forward_ret=forward_ret,
            behavior_idx=behavior_idx,
            factors=_candidate_factors(seeds),
            ref_returns=ref_returns,
            market_ret=market_ret,
            top_n=top_n,
            need_corr=corr_weight > 0,
            need_turnover=turnover_weight > 0,
        )

    pre_evaluated_results: dict[str, object] = {}

    def fitness(candidate: Candidate, island: int, generation: int) -> float:
        nonlocal evaluated
        if candidate.fingerprint in memo:
            return memo[candidate.fingerprint][0]
        if candidate.fingerprint in pre_evaluated_results:
            result = pre_evaluated_results[candidate.fingerprint]
        else:
            result = run_validation_pipeline(candidate, max_stage="l0", **pipe_kw)
        evaluated += 1
        exps = result.metrics.get("experiments", [])
        _m0 = (exps[0].get("metrics", {}) or {}) if exps else {}
        icir = _m0.get("ICIR")  # raw,仅供报告(向后兼容,阈值口径按 raw 标定)
        # fitness edge 用 NW 重叠校正的 ICIR_nw(诚实量级),非 raw(20日前瞻重叠虚高~3.5x)。
        # 根因#3:raw ICIR 会淹没 fitness 里的 novelty/turnover 项,让搜索只追 IC 无视新颖性;
        # NW 后三项才平衡。ICIR_nw 缺失时退回 raw(见 l0_ic_scan.py)。
        edge_icir = _m0.get("ICIR_nw", icir)

        proxy_corr = proxy_turn = None
        if use_algebraic_proxies:
            proxy_corr, proxy_turn = _algebraic_metric_proxy(
                candidate.ast,
                corr_by_factor=proxy_corr_by_factor,
                turnover_by_factor=proxy_turnover_by_factor,
            )

        # 因子面板算一次,供新颖性(行为距离)与边际(收益相关)共用。
        # 若代数代理覆盖了 corr/turnover,则不为这两项生成候选级 top-N 面板。
        panel = None
        needs_exact_corr = corr_weight > 0 and ref_returns and proxy_corr is None
        needs_exact_turn = turnover_weight > 0 and proxy_turn is None
        if novelty_weight > 0 or needs_exact_corr or needs_exact_turn or orth_weight > 0 or stability_weight > 0:
            try:
                panel = candidate_factor_panel(candidate.ast, close, volume, behavior_idx)
            except Exception:
                panel = None  # 算不出因子 → 不奖不罚(L0 同样会废掉它)

        # 缓存 candidate 用于特征提取
        meta[candidate.fingerprint] = (island, generation, float(icir) if icir is not None else 0.0, candidate, 0.0, 0.0, 0.0, 1.0)

        nov = 0.0
        if novelty_weight > 0 and panel is not None:
            nov = novelty_score(panel, refs + list(archive.values()))
            archive[candidate.fingerprint] = panel

        corr = float(proxy_corr) if proxy_corr is not None else 0.0
        if needs_exact_corr and panel is not None:
            corr = partial_correlation_to_book(
                topn_long_return(panel, forward_ret, top_n), ref_returns, market_ret,
            )

        turn = float(proxy_turn) if proxy_turn is not None else 0.0
        if needs_exact_turn and panel is not None:
            turn = topn_turnover(panel, top_n)

        # Complexity penalty
        comp_val = 0.0
        if complexity_weight > 0:
            from factory.autoresearch.complexity import compute_complexity
            comp_val = float(compute_complexity(candidate).score)

        # 重发现硬闸:与在册腿相关 ≥ 阈值 = 该 edge 在册已捕获,**边际为零** →
        # 把 |ICIR| 归零(无论毛 IC 多高都不该霸占冠军席)。corr/turnover 罚仍计入,
        # 使重发现稳稳沉在所有真候选之下。
        edge = abs(float(edge_icir)) if edge_icir is not None else 0.0  # NW 诚实绝对量级
        if rediscovery_corr and corr_weight > 0 and ref_returns and corr >= rediscovery_corr:
            edge = 0.0
        # in-sample 内部二分稳定性折扣(§四②真版,根治 blending 过拟合):edge 在训练窗 early/late
        # 两半都成立才保留,只靠一半(过拟合签名)则折扣趋零。严格防泄露:只用训练面板(≤cutoff),
        # 不碰 held-out OOS。stability_weight=0 不计(向后兼容)。
        if stability_weight > 0 and panel is not None and forward_ret is not None:
            stab = _split_stability(panel, forward_ret)
            edge = edge * ((1.0 - stability_weight) + stability_weight * stab)
        # 正交增量:罚对 size/流动性风格暴露——blending 若把候选拉回小盘/流动性 → 压分,
        # 逼搜索保持正交(把"正交"从事后否决变成事中搜索目标)。orth_weight=0 不计(向后兼容)。
        style_pen = 0.0
        if orth_weight > 0 and panel is not None and style_panels:
            style_pen = _style_exposure(panel, style_panels)

        # 三段极性 regime ICIR(段定义 = 模块级 REGIME_SEGMENTS,ADR-026)。
        # None = 段不在面板内(walk-forward 截断)——与"真 ICIR≈0"必须区分,
        # 否则 min 聚合把截断误判成零 edge(见 _regime_survival_edge)。
        segment_icirs: dict[str, float | None] = {name: None for name, _, _ in REGIME_SEGMENTS}
        if forward_ret is not None:
            from factors.autoresearch_dsl import compute_dsl_factor
            try:
                factor_panel = compute_dsl_factor(close, volume, ast=candidate.ast, cache_mode="disk")
            except Exception:
                factor_panel = None

            def _get_regime_icir(start_dt: str, end_dt: str) -> float | None:
                if factor_panel is None:
                    return None
                r_idx = forward_ret.index[(forward_ret.index >= start_dt) & (forward_ret.index <= end_dt)]
                common_idx = factor_panel.index.intersection(r_idx)
                if len(common_idx) < 5:
                    return None  # 段不在(截断后)面板内,不是 ICIR=0
                from engine.factor_analysis import calc_ic
                try:
                    ic_slice = calc_ic(factor_panel.loc[common_idx], forward_ret.loc[common_idx], method="rank").dropna()
                    if len(ic_slice) < 5:
                        return None
                    mean_val = ic_slice.mean()
                    std_val = ic_slice.std()
                    if std_val <= 1e-8:
                        return 0.0  # 有数据但因子无区分度 = 真 0,参与 min
                    return float(mean_val / std_val)
                except Exception:
                    return None

            for seg_name, seg_start, seg_end in REGIME_SEGMENTS:
                segment_icirs[seg_name] = _get_regime_icir(seg_start, seg_end)

        # meta 透出保持 dict[str, float] 契约(champion view/JSON):None → 0.0
        regime_meta[candidate.fingerprint] = {
            name: (0.0 if v is None else float(v)) for name, v in segment_icirs.items()
        }

        priority_adjustment = float(
            (result.metrics.get("knowledge_gate", {}) or {}).get("priority_adjustment", 1.0)
        )
        if regime_aware:
            edge = _regime_survival_edge(segment_icirs.values(), edge)
        fit = (edge + novelty_weight * nov - corr_weight * corr - turnover_weight * turn
               - complexity_weight * comp_val - orth_weight * style_pen) * priority_adjustment
        memo[candidate.fingerprint] = (fit, result)
        # 更新完整的 meta(含 priority_adjustment:pa≠1 时冠军 fitness 才能机械复原)
        meta[candidate.fingerprint] = (island, generation, float(icir) if icir is not None else 0.0, candidate, nov, corr, turn, priority_adjustment)
        return fit

    meta: dict[str, tuple[int, int, float, Candidate, float, float, float, float]] = {}
    regime_meta: dict[str, dict[str, float]] = {}

    # 初始化:种子轮转分配 + 变异补满
    islands: list[list[Candidate]] = []
    for i in range(n_islands):
        rng = random.Random(rng_seed + i)
        pop: dict[str, Candidate] = {}
        base_count = max(1, min(len(seeds) // n_islands, population - 1))
        base = [seeds[(i + k * n_islands) % len(seeds)] for k in range(base_count)]
        for c in base:
            pop.setdefault(c.fingerprint, c)
        stall = 0
        while len(pop) < population and stall < population * 4:
            child = _spawn_valid(list(pop.values()), rng, crossover=False)
            if child is None or child.fingerprint in pop:
                stall += 1
                continue
            pop[child.fingerprint] = child
        islands.append(list(pop.values()))

    rngs = [random.Random(rng_seed * 1000 + i) for i in range(n_islands)]
    migrants: list[Candidate | None] = [None] * n_islands

    for gen in range(generations):
        # 1. Apply migrants first
        for i in range(n_islands):
            if migrants[i] is not None:
                islands[i].append(migrants[i])
                migrants[i] = None

        # 2. Batch pre-evaluate all new candidates in parallel
        candidates_to_eval = _candidates_pending_evaluation(
            islands,
            memo=memo,
            pre_evaluated_results=pre_evaluated_results,
            multi_fidelity=multi_fidelity,
            pipe_kw=pipe_kw,
            level1_dates=mf_level1_dates,
            level1_ic_min=mf_level1_ic_min,
            level2_dates=mf_level2_dates,
            level2_keep_ratio=mf_level2_keep_ratio,
            trial_counter=prefilter_trials,
        )
        if candidates_to_eval:
            if evaluation_backend == "thread" or runners is not None:
                from concurrent.futures import ThreadPoolExecutor
                num_workers = min(len(candidates_to_eval), 8)
                with ThreadPoolExecutor(max_workers=num_workers) as executor:
                    jobs = {executor.submit(run_validation_pipeline, c, max_stage="l0", **pipe_kw): c for c in candidates_to_eval}
                    for fut in jobs:
                        c = jobs[fut]
                        try:
                            pre_evaluated_results[c.fingerprint] = fut.result()
                        except Exception as e:
                            logger.warning(f"[!] Error pre-evaluating candidate {c.fingerprint[:8]}: {e}")
            else:
                from concurrent.futures import ProcessPoolExecutor
                num_workers = min(len(candidates_to_eval), 8)
                proc_pipe_kw = pipe_kw.copy()
                proc_pipe_kw["repository"] = _MockRepo()
                proc_pipe_kw["experiment_log"] = _MockRepo()
                proc_pipe_kw["review_queue"] = _MockRepo()
                with ProcessPoolExecutor(max_workers=num_workers) as executor:
                    jobs = {executor.submit(run_validation_pipeline, c, max_stage="l0", **proc_pipe_kw): c for c in candidates_to_eval}
                    for fut in jobs:
                        c = jobs[fut]
                        try:
                            res = fut.result()
                            pre_evaluated_results[c.fingerprint] = res
                            # Parent writes to real repositories
                            if repository:
                                repository.add(c)
                                from .models import CandidateStatus
                                updated = c.with_status(res.status, res.reason)
                                repository.record(updated)
                                if res.status == CandidateStatus.PROMOTED_TO_REVIEW and review_queue:
                                    review_queue.add(updated, res)
                            if experiment_log:
                                experiment_log.append(res)
                        except Exception as e:
                            logger.warning(f"[!] Error pre-evaluating candidate {c.fingerprint[:8]}: {e}")

        # 3. Evaluate and breed next generation
        for i, pop in enumerate(islands):
            ranked = sorted(pop, key=lambda c: fitness(c, i, gen), reverse=True)

            # Fit/refit surrogate model dynamically using evaluated candidates so far
            surrogate_model = None
            surrogate_threshold = None
            evaluated_pairs = list(historical_lessons)  # Pre-warm with all historical lessons!
            for fp, (fit, _) in memo.items():
                if fp in meta:
                    candidate = meta[fp][3]
                    evaluated_pairs.append((candidate.ast, fit))

            if len(evaluated_pairs) >= 15:
                import numpy as np
                from sklearn.linear_model import Ridge
                X_train = np.array([ast_to_features(ast) for ast, _ in evaluated_pairs])
                y_train = np.array([fit for _, fit in evaluated_pairs])
                reg = Ridge(alpha=10.0)
                reg.fit(X_train, y_train)
                surrogate_model = reg
                surrogate_threshold = float(np.percentile(y_train, 25))

            elites = ranked[: max(1, elite)]
            # 下一代 = 精英 + 精英变异/杂交后代
            nxt: dict[str, Candidate] = {c.fingerprint: c for c in elites}
            stall = 0
            while len(nxt) < population and stall < population * 4:
                child = _spawn_valid(
                    elites,
                    rngs[i],
                    crossover=rngs[i].random() < 0.3,
                    surrogate_model=surrogate_model,
                    threshold=surrogate_threshold,
                )
                if child is None or child.fingerprint in nxt:
                    stall += 1
                    continue
                nxt[child.fingerprint] = child
            islands[i] = list(nxt.values())
        # 环形迁移:本岛最优 → 邻岛下一代
        for i, pop in enumerate(islands):
            best = max(pop, key=lambda c: memo.get(c.fingerprint, (0.0, None))[0])
            migrants[(i + 1) % n_islands] = best

        # Print progress of this generation (持续迭代效果可视化)
        all_evaluated_fits = []
        for pop in islands:
            for c in pop:
                fit_val = memo.get(c.fingerprint, (0.0, None))[0]
                all_evaluated_fits.append(fit_val)
        if all_evaluated_fits:
            max_fit = max(all_evaluated_fits)
            mean_fit = sum(all_evaluated_fits) / len(all_evaluated_fits)
            logger.info(f"[Evolution Progress] Generation {gen:02d} completed. Max Fitness: {max_fit:.4f}, Mean Fitness: {mean_fit:.4f}")

    # Batch pre-evaluate final remaining candidates
    candidates_to_eval = _candidates_pending_evaluation(
        islands,
        memo=memo,
        pre_evaluated_results=pre_evaluated_results,
        multi_fidelity=multi_fidelity,
        pipe_kw=pipe_kw,
        level1_dates=mf_level1_dates,
        level1_ic_min=mf_level1_ic_min,
        level2_dates=mf_level2_dates,
        level2_keep_ratio=mf_level2_keep_ratio,
        trial_counter=prefilter_trials,
    )
    if candidates_to_eval:
        if evaluation_backend == "thread" or runners is not None:
            from concurrent.futures import ThreadPoolExecutor
            num_workers = min(len(candidates_to_eval), 8)
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                jobs = {executor.submit(run_validation_pipeline, c, max_stage="l0", **pipe_kw): c for c in candidates_to_eval}
                for fut in jobs:
                    c = jobs[fut]
                    try:
                        pre_evaluated_results[c.fingerprint] = fut.result()
                    except Exception as e:
                        logger.warning(f"[!] Error pre-evaluating candidate {c.fingerprint[:8]}: {e}")
        else:
            from concurrent.futures import ProcessPoolExecutor
            num_workers = min(len(candidates_to_eval), 8)
            proc_pipe_kw = pipe_kw.copy()
            proc_pipe_kw["repository"] = _MockRepo()
            proc_pipe_kw["experiment_log"] = _MockRepo()
            proc_pipe_kw["review_queue"] = _MockRepo()
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                jobs = {executor.submit(run_validation_pipeline, c, max_stage="l0", **proc_pipe_kw): c for c in candidates_to_eval}
                for fut in jobs:
                    c = jobs[fut]
                    try:
                        res = fut.result()
                        pre_evaluated_results[c.fingerprint] = res
                        # Parent writes to real repositories
                        if repository:
                            repository.add(c)
                            from .models import CandidateStatus
                            updated = c.with_status(res.status, res.reason)
                            repository.record(updated)
                            if res.status == CandidateStatus.PROMOTED_TO_REVIEW and review_queue:
                                review_queue.add(updated, res)
                        if experiment_log:
                            experiment_log.append(res)
                    except Exception as e:
                        logger.warning(f"[!] Error pre-evaluating candidate {c.fingerprint[:8]}: {e}")

    # 收最后一代的遗漏评估
    for i, pop in enumerate(islands):
        for c in pop:
            fitness(c, i, generations - 1)

    ranked_all = sorted(memo.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
    final_results = {}
    if final_stage != "l0" and ranked_all:
        final_candidates = [meta[fp][3] for fp, _ in ranked_all]
        if evaluation_backend == "thread" or runners is not None:
            from concurrent.futures import ThreadPoolExecutor
            num_workers = min(len(final_candidates), 8)
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                jobs = {executor.submit(run_validation_pipeline, c, max_stage=final_stage, **pipe_kw): c for c in final_candidates}
                for fut in jobs:
                    c = jobs[fut]
                    try:
                        res = fut.result()
                        final_results[c.fingerprint] = res
                        if repository:
                            repository.add(c)
                            from .models import CandidateStatus
                            updated = c.with_status(res.status, res.reason)
                            repository.record(updated)
                            if res.status == CandidateStatus.PROMOTED_TO_REVIEW and review_queue:
                                review_queue.add(updated, res)
                        if experiment_log:
                            experiment_log.append(res)
                    except Exception as e:
                        logger.warning(f"[!] Error in final stage evaluation for {c.fingerprint[:8]}: {e}")
        else:
            from concurrent.futures import ProcessPoolExecutor
            num_workers = min(len(final_candidates), 8)
            proc_pipe_kw = pipe_kw.copy()
            proc_pipe_kw["repository"] = _MockRepo()
            proc_pipe_kw["experiment_log"] = _MockRepo()
            proc_pipe_kw["review_queue"] = _MockRepo()
            with ProcessPoolExecutor(max_workers=num_workers) as executor:
                jobs = {executor.submit(run_validation_pipeline, c, max_stage=final_stage, **proc_pipe_kw): c for c in final_candidates}
                for fut in jobs:
                    c = jobs[fut]
                    try:
                        res = fut.result()
                        final_results[c.fingerprint] = res
                        if repository:
                            repository.add(c)
                            from .models import CandidateStatus
                            updated = c.with_status(res.status, res.reason)
                            repository.record(updated)
                            if res.status == CandidateStatus.PROMOTED_TO_REVIEW and review_queue:
                                review_queue.add(updated, res)
                        if experiment_log:
                            experiment_log.append(res)
                    except Exception as e:
                        logger.warning(f"[!] Error in final stage evaluation for {c.fingerprint[:8]}: {e}")

    champions: list[ChampionRecord] = []
    for fp, (fit, l0_result) in ranked_all:
        island, gen, icir, candidate, nov, corr, turn, pa = meta[fp]
        result = final_results.get(fp, l0_result)
        from factory.autoresearch.complexity import compute_complexity
        comp_report = compute_complexity(candidate)
        champions.append(ChampionRecord(
            fingerprint=fp, island=island, generation=gen, icir=icir,
            expr=ast_expr(candidate.ast),
            status=result.status.value, decision=result.decision.value, reason=result.reason,
            novelty=nov, fitness=fit, corr_to_book=corr, turnover=turn,
            provenance=dict(candidate.provenance or {}),
            complexity=float(comp_report.score),
            regime_icirs=regime_meta.get(fp, {}),
            ast=candidate.ast,
            priority_adjustment=pa,
        ))
    return IslandSearchResult(evaluated=evaluated + prefilter_trials[0], champions=champions)


def _snap_window_to_grid(w: int | float, lo: int, hi: int) -> int:
    """Clamps a window parameter w and snaps it to the nearest standard trading grid point."""
    GRID = [5, 10, 20, 40, 60, 120, 240]
    w_clamped = max(lo, min(hi, int(w)))
    nearest = min(GRID, key=lambda x: abs(x - w_clamped))
    return nearest
