"""周度组合再构成:多策略组合定期重排的确定性内核(WS-D,ADR-034 后续)。

定位(承 R-PROD-001 / R-OBJECTIVE-001):
  - 排名由**后端确定性代码**产出并持久化(编排脚本落 reports/research/
    portfolio_recompose.json),不得只在前端瞬时算;
  - 排名口径是**多目标**:夏普 / Calmar / 边际正交(残差夏普,§5.3)三项 rank 均值,
    衰减腿强制垫底——不是单一收益最大化;口径由 RANKING_VERSION 锚定,
    **改口径必须 bump 版本号 + 记 DECISIONS**,不得为让某策略上榜而调;
  - 本模块纯函数(收益 dict 进 → 排名/提案 dict 出),不读盘不写盘不触台账;
    编排(读 version_returns / 写 artifact)归 scripts/ops/scheduled_portfolio_recompose.py;
  - 提案是 **advisory**:权重生效 / 开 paper 账户 / 退役,全部归人经 canonical 入口
    (LOOP §6);组合(composite)本身按 §5.4 进 decay_check(组合也是策略,默认会失效)。

执法边界(守卫审计 #5 / version_returns provenance):
  · **只管选择路径**(recompose 排名 → paper provisioning 名单)。
    编排层必须经 lake.version_returns.load_verified_version_returns 装腿;
    校验失败的腿排除出排名,并在输出 JSON 显式记录 excluded_no_provenance
    (沿用 missing 如实列出先例,绝不静默消失)。
  · **不管监控路径**:decay_monitor / 看板 / services.read 可读全量 version_returns
    且不 gate——同 holdout 守卫「监控合法用全样本」哲学。不要去改 decay_monitor。
  · **存量硬切**(owner 拍板):存量 CSV 无 sidecar → load_verified 失败 → 被选择
    路径排除。不做 grandfather 标签、不做批量补戳、不动存量文件。
    在册池当前为 0,硬切实际代价为零。
  · 排名口径本身(RANKING_VERSION)不变——本改动只绑输入身份,不改评分构成。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from governance.decay import decay_check
from governance.marginal import REDUNDANT_CORR, marginal_alpha
from portfolio.portfolio_composer import portfolio_metrics

# 排名口径版本:改评分构成/权重/门槛 = 改口径,必须 bump + 记 DECISIONS(R-OBJECTIVE-001)
RANKING_VERSION = "v2"   # v2(ADR-036):inverse-vol + 防守帽(跨资产波动悬殊修复)

# 防守资产判据与权重帽(ADR-036,一次性口径决策非搜索参数——不扫网格):
# v0.2 探针实证 inverse-vol 对跨资产失效(2.5% 波动债腿被灌 87%,组合稀释成债基)。
# 训练窗年化波动 < DEFENSIVE_VOL_ANNUAL 判防守资产(债 ~2.5% vs 股票腿 15-25%,分界不敏感);
# 防守组合计权重帽 DEFENSIVE_CAP(60/40~70/30 资产配置惯例区间取中)。
DEFENSIVE_VOL_ANNUAL = 0.08
DEFENSIVE_CAP = 0.35
RANKING_CRITERION = (
    "score = mean(rank(sharpe), rank(calmar), rank(residual_sharpe 对其余腿 inv-vol book));"
    "decay_check 触发的腿强制垫底不入提案;样本<min_obs 标 insufficient 垫底;"
    f"提案贪心选非冗余腿(两两|corr|≥{REDUNDANT_CORR} 视为同质变体跳过,§5.3),静态 inverse-vol 权重"
)

_MIN_OBS = 252


def _leg_stats(name: str, ret: pd.Series, others: dict[str, pd.Series]) -> dict:
    """单腿确定性体检:绩效 + 对其余腿组合的边际正交 + 衰减。零新判定口径,全部复用 canonical。"""
    m = portfolio_metrics(ret)
    mg = marginal_alpha(ret, others) if others else marginal_alpha(ret, {})
    dc = decay_check(ret)
    return {
        "name": name,
        "n_days": int(m.get("n_days", len(ret.dropna()))),
        "sharpe": round(float(m.get("sharpe", 0.0)), 3),
        "annual": round(float(m.get("annual", 0.0)), 4),
        "maxdd": round(float(m.get("maxdd", 0.0)), 4),
        "calmar": round(float(m.get("calmar", 0.0)), 3),
        "residual_sharpe": mg.get("residual_sharpe"),
        "corr_to_book": mg.get("corr_to_book"),
        "marginal_verdict": mg.get("marginal_verdict"),
        "decayed": bool(dc.get("decayed")),
        "decay_reasons": list(dc.get("reasons", [])),
    }


def rank_strategies(
    returns: dict[str, pd.Series],
    *,
    min_obs: int = _MIN_OBS,
) -> list[dict]:
    """逐腿确定性排名(rank 越靠前越该获得组合权重与 paper 名额)。

    三层(tier):0 = 可入提案(按 score 升序,score 越小越好);
    1 = decayed(强制垫底,保留供退役复核审视,提案不入选);
    2 = insufficient(样本<min_obs,不做任何绩效结论)。
    同分平手按 name 字典序打破(确定性:同输入恒同输出)。
    """
    eligible: list[dict] = []
    decayed: list[dict] = []
    insufficient: list[dict] = []
    for name in sorted(returns):
        ret = pd.Series(returns[name]).dropna()
        if len(ret) < min_obs:
            insufficient.append({
                "name": name, "tier": 2, "n_days": len(ret),
                "reason": f"样本 {len(ret)} < {min_obs},不做绩效结论(诚实拒判,非降级)",
            })
            continue
        others = {k: v for k, v in returns.items() if k != name}
        stats = _leg_stats(name, ret, others)
        if stats["decayed"]:
            decayed.append({**stats, "tier": 1,
                            "reason": f"decay_check 触发:{'; '.join(stats['decay_reasons'])}"})
        else:
            eligible.append({**stats, "tier": 0})

    # 三项 rank 均值(rank 1 = 最好);residual_sharpe 缺算(样本不足等)按最差处理
    if eligible:
        def _ranks(key, default_worst):
            vals = [(e[key] if isinstance(e.get(key), (int, float)) else default_worst)
                    for e in eligible]
            order = pd.Series(vals).rank(ascending=False, method="min")
            return list(order)

        r_sharpe = _ranks("sharpe", -np.inf)
        r_calmar = _ranks("calmar", -np.inf)
        r_resid = _ranks("residual_sharpe", -np.inf)
        for e, a, b, c in zip(eligible, r_sharpe, r_calmar, r_resid, strict=True):
            e["score"] = round(float((a + b + c) / 3.0), 3)
        eligible.sort(key=lambda e: (e["score"], e["name"]))

    ranked = eligible + sorted(decayed, key=lambda e: e["name"]) \
                      + sorted(insufficient, key=lambda e: e["name"])
    for i, e in enumerate(ranked, start=1):
        e["rank"] = i
    return ranked


def propose_weights(
    ranked: list[dict],
    returns: dict[str, pd.Series],
    *,
    top_n: int = 3,
) -> dict:
    """从排名头部贪心选 top_n 条**非冗余**腿,给静态 inverse-vol 权重提案。

    冗余过滤(§5.3):候选与任一已选腿两两 |corr| ≥ REDUNDANT_CORR → 同质变体跳过
    (跳过原因留痕,提案透明可审)。组合收益按静态权重合成,附 portfolio_metrics +
    decay_check(组合也是策略,§5.4)。0 条可选腿 → 诚实空提案,不硬凑。
    """
    selected: list[str] = []
    skipped: list[dict] = []
    for e in ranked:
        if e.get("tier") != 0:
            continue
        if len(selected) >= top_n:
            break
        name = e["name"]
        ret = pd.Series(returns[name]).dropna()
        redundant_with = None
        for s in selected:
            common = ret.index.intersection(pd.Series(returns[s]).dropna().index)
            if len(common) < 100:
                continue
            corr = float(ret.reindex(common).corr(pd.Series(returns[s]).reindex(common)))
            if abs(corr) >= REDUNDANT_CORR:
                redundant_with = (s, round(corr, 3))
                break
        if redundant_with:
            skipped.append({"name": name, "reason": "同质变体(冗余)",
                            "redundant_with": redundant_with[0], "corr": redundant_with[1]})
            continue
        selected.append(name)

    if not selected:
        return {"status": "no_eligible_legs", "weights": {}, "skipped": skipped,
                "note": "无可入提案的腿(全部 decayed/样本不足/冗余)——诚实空提案,不硬凑组合"}

    df = pd.DataFrame({n: pd.Series(returns[n]) for n in selected}).dropna()
    vol = df.std()
    inv = 1.0 / vol.replace(0, np.nan)
    w = (inv / inv.sum()).fillna(1.0 / len(selected))
    # v2 防守帽(ADR-036):inverse-vol 对波动悬殊跨资产池会把权重全灌给低波防守腿
    # (v0.2 探针实证:87% 债 → 组合稀释成债基)。防守组(年化 vol<DEFENSIVE_VOL_ANNUAL)
    # 合计权重超帽时组内等比压缩到 DEFENSIVE_CAP,释放权重按 inverse-vol 比例分给
    # 非防守组。全股票/全防守池不触发(行为同 v1)。
    ann_vol = vol * np.sqrt(252)
    defensive = [n for n in selected if float(ann_vol.get(n, np.inf)) < DEFENSIVE_VOL_ANNUAL]
    offensive = [n for n in selected if n not in defensive]
    d_sum = float(w[defensive].sum()) if defensive else 0.0
    if defensive and offensive and d_sum > DEFENSIVE_CAP:
        w[defensive] = w[defensive] * (DEFENSIVE_CAP / d_sum)
        w[offensive] = w[offensive] * ((1.0 - DEFENSIVE_CAP) / float(w[offensive].sum()))
    composite = (df * w).sum(axis=1)
    comp_metrics = portfolio_metrics(composite)
    comp_decay = decay_check(composite)
    return {
        "status": "ok",
        "weights": {n: round(float(w[n]), 4) for n in selected},
        "weighting": "static inverse-vol + 防守帽 35%(v2/ADR-036;全共同样本窗;advisory 提案口径,非执行系统)",
        "skipped": skipped,
        "composite_metrics": {k: (round(float(v), 4) if isinstance(v, (int, float)) else v)
                              for k, v in comp_metrics.items()},
        "composite_decay": {"decayed": bool(comp_decay.get("decayed")),
                            "reasons": list(comp_decay.get("reasons", []))},
        "common_days": int(len(df)),
    }


def recompose(
    returns: dict[str, pd.Series],
    *,
    top_n: int = 3,
    min_obs: int = _MIN_OBS,
    identity_tiers: dict[str, str] | None = None,
    excluded_no_provenance: list[dict] | None = None,
) -> dict:
    """一步产出周度再构成结果(排名 + 提案 + paper 名单),供编排脚本持久化。

    identity_tiers: 腿名 → "spec"|"config-only"(由编排层 load_verified 透传)。
    excluded_no_provenance: [{leg, reason}, ...] 无/坏 provenance 的腿,如实列出
    不入 returns——不在此函数内二次过滤(调用方已排除);本字段只负责落盘透明。
    """
    tiers = identity_tiers or {}
    ranked = rank_strategies(returns, min_obs=min_obs)
    for leg in ranked:
        tier = tiers.get(leg["name"])
        if tier is not None:
            leg["identity_tier"] = tier
    proposal = propose_weights(ranked, returns, top_n=top_n)
    # paper_candidates 条目透传 identity_tier,供展示层后续消费(web 不在本单)
    paper_candidates = [
        {"name": n, "identity_tier": tiers.get(n, "unknown")}
        for n in proposal.get("weights", {}).keys()
    ]
    return {
        "ranking_version": RANKING_VERSION,
        "criterion": RANKING_CRITERION,
        "top_n": top_n,
        "legs": ranked,
        "proposal": proposal,
        # R-PROD-001:top-N paper 实测名单 = 提案入选腿(后端确定性产出,持久化由编排层落盘)
        "paper_candidates": paper_candidates,
        # 守卫审计 #5:无 provenance 腿排除出排名,但必须如实列出(同 missing 先例)
        "excluded_no_provenance": list(excluded_no_provenance or []),
        "honesty": "advisory 提案:权重生效/开 paper 账户/退役全部归人经 canonical 入口(LOOP §6);"
                   "排名口径由 RANKING_VERSION 锚定,改口径须 bump + 记 DECISIONS(R-OBJECTIVE-001)。",
    }
