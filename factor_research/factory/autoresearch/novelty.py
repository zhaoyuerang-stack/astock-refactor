"""行为新颖性:候选因子面板 vs 参考池的行为距离,复用 redundancy 复合分。

只奖励"跑得快"(|ICIR|)的搜索必然同质化坍缩;新颖性项直接奖励
"与众不同",逼搜索去填未被占领的行为生态位。

L0 阶段可廉价获得的行为成分(全部逐日截面操作,天然因果):
- spearman_corr:截面 rank 相关取绝对值(反向克隆同样冗余);
- holding_overlap:top 分位选股集合的 Jaccard(top-N 策略的持仓重叠代理)。
return_corr / normalized_mi / exposure_similarity 需要回测产物,L0 阶段
不可得,按 0 计入(factor_redundancy_score 本就支持部分成分);
新颖性按可得成分的权重质量归一,使其跨成分配置可比地落在 [0, 1]。

冗余取参考池的**最近邻**(max)而非平均:克隆某一个在册策略但远离
其余策略的候选,平均会稀释信号,最近邻不会。

防未来:本模块只在调用方传入的面板上计算;walk-forward 框架下面板已
物理截断 <= cutoff,行为距离自动只用历史(原则:新颖性的定义是
"在当时的历史环境下与已有策略不同")。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .redundancy import WEIGHTS, factor_redundancy_score

# L0 阶段可计算的成分;新颖性归一分母 = 这些成分的权重和
_AVAILABLE_COMPONENTS = ("spearman_corr", "holding_overlap")
_WEIGHT_MASS = sum(WEIGHTS[c] for c in _AVAILABLE_COMPONENTS)

_TOP_QUANTILE = 0.1


def sample_behavior_dates(index: pd.Index, n: int = 60) -> pd.Index:
    """等距采样 n 个行为观测日(确定性,含末日)。"""
    if len(index) <= n:
        return index
    pos = np.linspace(0, len(index) - 1, n).round().astype(int)
    return index[np.unique(pos)]


def candidate_factor_panel(
    ast: dict,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    dates: pd.Index,
    cache_mode: str = "disk",
) -> pd.DataFrame:
    """在完整传入面板上算因子(rolling 需要历史),再切行为观测日。

    compute_dsl_factor 已应用 direction,面板即"实际持仓视角"。
    """
    from factors.autoresearch_dsl import compute_dsl_factor

    panel = compute_dsl_factor(close, volume, ast=ast, cache_mode=cache_mode)
    return panel.loc[panel.index.intersection(dates)]


def _rowwise_abs_spearman(a: pd.DataFrame, b: pd.DataFrame) -> float:
    ra = a.rank(axis=1)
    rb = b.rank(axis=1)
    ra = ra.sub(ra.mean(axis=1), axis=0)
    rb = rb.sub(rb.mean(axis=1), axis=0)
    num = (ra * rb).sum(axis=1)
    den = np.sqrt((ra * ra).sum(axis=1) * (rb * rb).sum(axis=1))
    # 暖机期行(配对不足或零方差,如 DSL fill_value 产生的全 0 行)相关无定义,
    # 不参与均值——否则被记作 0 相关,稀释克隆信号
    valid = ((ra.notna() & rb.notna()).sum(axis=1) >= 3) & (den > 1e-9)
    corr = (num / (den + 1e-12)).where(valid).dropna().abs()
    return float(corr.mean()) if len(corr) else 0.0


def _topq_jaccard(a: pd.DataFrame, b: pd.DataFrame, q: float = _TOP_QUANTILE) -> float:
    top_a = a.rank(axis=1, pct=True).ge(1 - q).fillna(False)
    top_b = b.rank(axis=1, pct=True).ge(1 - q).fillna(False)
    inter = (top_a & top_b).sum(axis=1).astype(float)
    union = (top_a | top_b).sum(axis=1).astype(float)
    ratio = (inter / union.replace(0.0, np.nan)).dropna()
    return float(ratio.mean()) if len(ratio) else 0.0


def behavior_redundancy(a: pd.DataFrame, b: pd.DataFrame) -> float:
    """两个因子面板的复合冗余分(只含 L0 可得成分)。"""
    a, b = a.align(b, join="inner")
    if a.empty:
        return 0.0
    report = factor_redundancy_score(
        spearman_corr=_rowwise_abs_spearman(a, b),
        holding_overlap=_topq_jaccard(a, b),
    )
    return report.score


def novelty_score(panel: pd.DataFrame, references: list[pd.DataFrame]) -> float:
    """新颖性 = 1 - 最近邻冗余(按可得成分权重质量归一到 [0, 1])。

    参考池为空(搜索首个候选)→ 1.0:未知即新颖。
    """
    if not references or panel.empty:
        return 1.0
    nearest = max(behavior_redundancy(panel, ref) for ref in references)
    return 1.0 - min(1.0, nearest / _WEIGHT_MASS)


# ── 边际贡献:对在册组合的收益相关(独立 edge × 低相关)──────────────────
# 新颖性度量"因子长什么样"的行为距离;边际贡献度量"收益怎么动"的相关——
# 二者正交:一个候选可以因子形态新颖却收益与在册同涨同跌(伪多样性,见
# registry_correlation_audit:5 股票腿 0.76 相关)。后者才是组合层真分散。


def topn_long_return(panel: pd.DataFrame, forward_ret: pd.DataFrame, top_n: int = 25) -> pd.Series:
    """因子面板 → top-N 等权多头的前向收益代理序列(无择时,仅供相关性排序)。

    panel 已应用 direction(高=该做多);每个观测日取 top-N 取前向收益均值。
    廉价:只在 panel 的(采样)行为日上算,O(days × N)。
    """
    fr = forward_ret.reindex(index=panel.index, columns=panel.columns)
    out: dict = {}
    for dt in panel.index:
        row = panel.loc[dt].dropna()
        if len(row) < top_n:
            continue
        r = fr.loc[dt, row.nlargest(top_n).index].dropna()
        if len(r):
            out[dt] = float(r.mean())
    return pd.Series(out, dtype="float64")


def topn_turnover(panel: pd.DataFrame, top_n: int = 25) -> float:
    """top-N 成员相邻期 Jaccard 流失率均值 ∈ [0,1],换手代理(高=churn 快=成本高)。

    复用与 topn_long_return 相同的 top-N 选股;采样行为日间隔≈月频,与 20D 调仓
    可比。换手在搜索目标里缺位 → 高 IC 高换手候选(尤其反转型)在 L0 看着好、
    L1 成本后被杀。把它前置进适应度,既省评估又抵消去相关项对反转的偏好。
    """
    dates = list(panel.index)
    if len(dates) < 2:
        return 0.0
    prev: set | None = None
    churns: list[float] = []
    for dt in dates:
        row = panel.loc[dt].dropna()
        if len(row) < top_n:
            prev = None
            continue
        cur = set(row.nlargest(top_n).index)
        if prev:
            union = len(cur | prev)
            if union:
                churns.append(1.0 - len(cur & prev) / union)
        prev = cur
    return float(np.mean(churns)) if churns else 0.0


def max_return_correlation(cand_ret: pd.Series, ref_returns: list[pd.Series]) -> float:
    """候选收益代理与各在册腿收益的**有符号**最大相关。

    取 max:与任一在册腿雷同即算冗余(对应 novelty 的最近邻逻辑)。
    返回有符号值——全负(对所有腿反相关=防御腿)给出负数,调用方据此奖励;
    无可比参考/方差退化 → 0.0(不罚不奖)。
    """
    best = None
    for rr in ref_returns:
        a, b = cand_ret.align(rr, join="inner")
        mask = a.notna() & b.notna()
        a, b = a[mask], b[mask]
        if len(a) < 5 or a.std() == 0 or b.std() == 0:
            continue
        c = float(a.corr(b))
        if c == c:  # not NaN
            best = c if best is None else max(best, c)
    return best if best is not None else 0.0


def _partial_corr(cxy: float, cxm: float, cym: float) -> float | None:
    """偏相关 corr(X,Y|M) = (cxy − cxm·cym) / sqrt((1−cxm²)(1−cym²))。退化(分母≤0/NaN)→ None。"""
    denom = (1.0 - cxm * cxm) * (1.0 - cym * cym)
    if not (denom == denom) or denom <= 1e-12:
        return None
    val = (cxy - cxm * cym) / (denom ** 0.5)
    if val != val:
        return None
    return max(-1.0, min(1.0, val))


def partial_correlation_to_book(
    cand_ret: pd.Series, ref_returns: list[pd.Series], market_ret: pd.Series,
) -> float:
    """根因#2:扣市场偏相关 —— 候选与在册腿"控制市场共同暴露后"的有符号最大相关。

    raw 相关把"两腿都只是在跟大盘"误判成冗余,也会让"靠抵消市场暴露藏共同赌注"的一对
    漏判(各自对市场 beta 相反,raw corr 被漂白成低值,但策略层赌的是同一个东西)。
    先算 corr(候选,市场)、corr(在册腿,市场),再用偏相关公式扣掉市场共同分量。

    market_ret 方差退化(如未传市场代理)→ 退回 raw 相关(向后兼容,不返回 None)。
    取 max(同 max_return_correlation,对应 novelty 最近邻语义);无可比参考 → 0.0。
    """
    best = None
    for rr in ref_returns:
        df = pd.concat(
            {"x": cand_ret, "y": rr, "m": market_ret}, axis=1, join="inner",
        ).dropna()
        if len(df) < 5:
            continue
        x, y, m = df["x"], df["y"], df["m"]
        if x.std() == 0 or y.std() == 0:
            continue
        cxy = float(x.corr(y))
        if cxy != cxy:
            continue
        c = cxy
        if m.std() > 0:
            cxm, cym = float(x.corr(m)), float(y.corr(m))
            if cxm == cxm and cym == cym:
                partial = _partial_corr(cxy, cxm, cym)
                if partial is not None:
                    c = partial
        best = c if best is None else max(best, c)
    return best if best is not None else 0.0
