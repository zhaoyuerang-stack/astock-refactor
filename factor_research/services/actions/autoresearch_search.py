"""多岛屿搜索编排:LLM(可选)按主题给各岛播种 → factory 岛屿进化引擎。

LLM 不可用时退回确定性种子 + 变异(seeded_by 字段如实标注,不伪装)。
适应度与验证全部走 canonical 验证线,本模块不引入任何第二套口径。
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from app_config.log import get_logger
from contracts.views import (
    AutoResearchChampionView,
    AutoResearchIslandSearchResponse,
    AutoResearchOOSChampionView,
    AutoResearchWalkForwardResponse,
)
from factory.autoresearch import CandidateRepository
from factory.autoresearch.islands import run_island_search
from factory.autoresearch.walkforward import run_walk_forward_search
from governance.trial_ledger import record_trials  # §5.1 chokepoint:搜索即记账

from .autoresearch import _load_validation_data
from .autoresearch_llm import generate_llm_candidates

logger = get_logger(__name__)

_ISLAND_THEMES = [
    "动量与趋势延续",
    "价值与基本面质量",
    "流动性与微观结构(Amihud 类)",
    "波动率与风险溢价",
    # LOOP_ENGINEERING.md #5 独立数据族隔离岛:股东行为(holder_count_chg/holdertrade_net)
    # + 资金流(large_order_net_ratio)。i % len(_ISLAND_THEMES) 轮询——islands<5 时不会
    # 轮到此主题(不影响既有 4 个主题的分配),需 islands>=5 才生效。
    "股东行为与资金流(独立数据族)",
]


def _stamped_vintage(start: str, close) -> str:
    """vintage 带数据内容指纹:数据湖日内漂移时,不同面板绝不共享同一凭证。"""
    from lake.fingerprint import stamp_vintage

    return stamp_vintage(f"data_lake:{start}:{date.today().isoformat()}", close)


def active_book_panels(close, volume, amount) -> list:
    """在册 ACTIVE 母策略的因子面板(高=该策略做多),供边际适应度算收益相关。

    ACTIVE 集 = small-cap-size.v2.0 + illiquidity.v1.0(见 registry_correlation_audit:
    其余为 SHADOW)。面板口径必须与 close 一致(walk-forward 下传截断面板)。
    任一面板算不出 → 跳过该腿(best-effort,不拖垮搜索)。
    """
    panels = []
    try:
        from factors.small_cap import small_cap_factor
        panels.append(small_cap_factor(amount, 60).reindex(index=close.index, columns=close.columns))
    except Exception:
        pass
    try:
        from factors.momentum import illiquidity
        panels.append(illiquidity(close, volume, 20).reindex(index=close.index, columns=close.columns))
    except Exception:
        pass
    return panels


def _style_panels(close) -> list:
    """风格面板 [size(log 流通市值), 流动性(换手率)],供正交增量罚(§四修法②)。

    size/流动性是当期 PIT 特征(daily_basic by_date,无前瞻),reindex 到 close;
    walk-forward 下 _style_exposure 会按候选面板日期(≤cutoff)自截断,无泄露。算不出则空。
    """
    import numpy as np

    try:
        from lake.load_lake import load_tushare_panel
        db = load_tushare_panel("daily_basic", close.index, fields=["circ_mv", "turnover_rate"])
        size = np.log(db["circ_mv"]).reindex(index=close.index, columns=close.columns)
        liq = db["turnover_rate"].reindex(index=close.index, columns=close.columns)
        return [size, liq]
    except Exception:
        return []


def _llm_seeds(islands: int, adapter, repository, experiment_log=None) -> tuple[list, str]:
    """按主题给各岛 LLM 播种;LLM 未配置时退回确定性种子(seeded_by 如实标注)。

    experiment_log 提供时注入失败台账反思(P3),播种绕开已被系统性证伪的形态。
    """
    seeds: list = []
    for i in range(islands):
        theme = _ISLAND_THEMES[i % len(_ISLAND_THEMES)]
        # 逐岛容错:单岛解析/生成失败不拖垮其余岛的播种;原因如实打印不静默
        try:
            accepted, _, model = generate_llm_candidates(
                n=2, theme=theme, adapter=adapter, repository=repository,
                experiment_log=experiment_log,
            )
            # ADR-022 种子溯源:LLM 先验可能含 2025+ 行情认知(语义泄露),如实标注 theme/model,
            # 不丢弃(原 `accepted, _, _` 把 model 扔了)。晋级时进 evidence 触发人工额外审视。
            stamped = [
                replace(c, provenance={
                    "origin": "llm_seed", "theme": theme, "model": model or "unknown",
                    "generated_at": date.today().isoformat(),
                })
                for c in accepted
            ]
            seeds.extend(stamped)
        except ValueError as e:
            logger.warning(f"[llm_seeds] 岛{i}({theme})播种失败: {str(e)[:120]}")
    return seeds, ("llm" if seeds else "seeds")


def run_autoresearch_island_search(
    *,
    islands: int = 4,
    generations: int = 3,
    population: int = 8,
    top_k: int = 5,
    final_stage: str = "l0",
    use_llm: bool = True,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    rng_seed: int = 7,
    novelty_weight: float = 0.25,
    corr_weight: float = 0.3,
    turnover_weight: float = 0.15,
    complexity_weight: float = 0.0,
    orth_weight: float = 0.2,
    stability_weight: float = 0.0,  # opt-in:walk-forward 实测未改善 OOS(过拟合多为 regime-break,in-sample 抓不到);代码留作工具
    use_algebraic_proxies: bool = False,
    multi_fidelity: bool = False,
    mf_level1_dates: int = 20,
    mf_level1_ic_min: float = 0.02,
    mf_level2_dates: int = 60,
    mf_level2_keep_ratio: float = 0.5,
    computation_time_budget: float = 10.0,
    rediscovery_corr: float = 0.5,
    repository: CandidateRepository | None = None,
    experiment_log=None,
    review_queue=None,
    adapter=None,
    close=None,
    volume=None,
    amount=None,
    forward_ret=None,
    vintage_id: str | None = None,
    regime_aware: bool = False,
) -> AutoResearchIslandSearchResponse:
    tradable = None
    mask_version = None
    if close is None or volume is None or amount is None or forward_ret is None:
        close, volume, amount, forward_ret, tradable, mask_version = _load_validation_data(start)
    vintage = vintage_id or _stamped_vintage(start, close)
    repository = repository or CandidateRepository()

    # 边际适应度:对在册 ACTIVE 组合去相关(伪多样性审计后默认开启)
    reference_panels = active_book_panels(close, volume, amount) if corr_weight > 0 else None
    # 正交增量罚(§四修法②):对 size/流动性风格暴露压分,逼搜索保持正交、根治 blending 回流
    style_panels = _style_panels(close) if orth_weight > 0 else None

    seeds, seeded_by = ([], "seeds")
    if use_llm:
        seeds, seeded_by = _llm_seeds(islands, adapter, repository, experiment_log)

    def _island():
        return run_island_search(
            close, volume, amount, forward_ret,
            vintage_id=vintage,
            n_islands=islands,
            generations=generations,
            population=population,
            top_k=top_k,
            final_stage=final_stage,
            seeds=seeds or None,
            rng_seed=rng_seed,
            sample_dates=sample_dates,
            novelty_weight=novelty_weight,
            corr_weight=corr_weight,
            turnover_weight=turnover_weight,
            complexity_weight=complexity_weight,
            orth_weight=orth_weight,
            stability_weight=stability_weight,
            use_algebraic_proxies=use_algebraic_proxies,
            multi_fidelity=multi_fidelity,
            mf_level1_dates=mf_level1_dates,
            mf_level1_ic_min=mf_level1_ic_min,
            mf_level2_dates=mf_level2_dates,
            mf_level2_keep_ratio=mf_level2_keep_ratio,
            computation_time_budget=computation_time_budget,
            rediscovery_corr=rediscovery_corr,
            reference_panels=reference_panels,
            style_panels=style_panels,
            repository=repository,
            experiment_log=experiment_log,
            review_queue=review_queue,
            regime_aware=regime_aware,
        )

    if tradable is not None and mask_version is not None:
        from factors.tradable_mask import feature_mask_context

        with feature_mask_context(tradable, version=mask_version):
            result = _island()
    else:
        result = _island()
    # §5.1 chokepoint:每次真实搜索都记账,honest_n_trials 据此惩罚 DSR。所有 orchestrator
    # 调用者(scheduled_factor_search / research 脚本)经此自动累计,不靠各 caller 自觉——
    # 杜绝「搜了不记 = DSR 虚松」(LOOP_ENGINEERING §5.1 半接失守的根治)。
    record_trials("autoresearch", max(1, int(result.evaluated)), context="island search")
    return AutoResearchIslandSearchResponse(
        vintage_id=vintage,
        islands=islands,
        generations=generations,
        evaluated=result.evaluated,
        seeded_by=seeded_by,
        champions=[
            AutoResearchChampionView(
                fingerprint=c.fingerprint, island=c.island, generation=c.generation,
                icir=c.icir, expr=c.expr, status=c.status, decision=c.decision, reason=c.reason,
                novelty=c.novelty, corr_to_book=c.corr_to_book, turnover=c.turnover, fitness=c.fitness,
                provenance=dict(c.provenance or {}),
            )
            for c in result.champions
        ],
    )


def run_autoresearch_walk_forward(
    *,
    cutoff: str,
    oos_end: str | None = None,
    islands: int = 4,
    generations: int = 3,
    population: int = 8,
    top_k: int = 5,
    final_stage: str = "l0",
    use_llm: bool = True,
    start: str = "2018-01-01",
    sample_dates: int | None = 120,
    rng_seed: int = 7,
    novelty_weight: float = 0.25,
    corr_weight: float = 0.3,
    turnover_weight: float = 0.15,
    orth_weight: float = 0.2,
    stability_weight: float = 0.0,  # opt-in:walk-forward 实测未改善 OOS(过拟合多为 regime-break,in-sample 抓不到);代码留作工具
    rediscovery_corr: float = 0.5,
    repository: CandidateRepository | None = None,
    experiment_log=None,
    review_queue=None,
    adapter=None,
    close=None,
    volume=None,
    amount=None,
    vintage_id: str | None = None,
    runners: dict | None = None,
    regime_aware: bool = False,
) -> AutoResearchWalkForwardResponse:
    """元级防未来的岛屿搜索:演化只见 <=cutoff,冠军在 (cutoff, oos_end] 一次性 OOS 评分。

    不接收预计算 forward_ret——训练/OOS 的 forward_ret 必须由 walkforward
    引擎从各自截断后的 close 重算,外部传入的全样本版本本身就是泄露源。
    """
    tradable = None
    mask_version = None
    if close is None or volume is None or amount is None:
        close, volume, amount, _, tradable, mask_version = _load_validation_data(start)
    vintage = vintage_id or _stamped_vintage(start, close)
    repository = repository or CandidateRepository()

    seeds, seeded_by = ([], "seeds")
    if use_llm:
        seeds, seeded_by = _llm_seeds(islands, adapter, repository, experiment_log)

    def _wf():
        return run_walk_forward_search(
            close, volume, amount,
            cutoff=cutoff,
            oos_end=oos_end,
            vintage_id=vintage,
            repository=repository,
            runners=runners,
            reference_builder=active_book_panels,  # walkforward 在截断面板上调用,防未来
            n_islands=islands,
            generations=generations,
            population=population,
            top_k=top_k,
            final_stage=final_stage,
            seeds=seeds or None,
            rng_seed=rng_seed,
            sample_dates=sample_dates,
            novelty_weight=novelty_weight,
            corr_weight=corr_weight,
            turnover_weight=turnover_weight,
            orth_weight=orth_weight,
            stability_weight=stability_weight,
            # 正交增量罚的风格面板(size/流动性当期 PIT;_style_exposure 按候选 ≤cutoff 日期自截断,无泄露)
            style_panels=(_style_panels(close) if orth_weight > 0 else None),
            rediscovery_corr=rediscovery_corr,
            experiment_log=experiment_log,
            review_queue=review_queue,
            # WS6:跨 regime 生存适应度(ADR-026)透传;截断面板下缺段由
            # islands._regime_survival_edge 只聚合可用段,不会 edge 归零。
            regime_aware=regime_aware,
        )

    if tradable is not None and mask_version is not None:
        from factors.tradable_mask import feature_mask_context

        with feature_mask_context(tradable, version=mask_version):
            result = _wf()
    else:
        result = _wf()
    # §5.1 chokepoint:walk-forward 搜索同样记账(此前完全漏计 = DSR 虚松)。
    record_trials("autoresearch", max(1, int(result.evaluated)), context="walk-forward search")
    return AutoResearchWalkForwardResponse(
        vintage_id=vintage,
        cutoff=result.cutoff,
        oos_start=result.oos_start,
        oos_end=result.oos_end,
        islands=islands,
        generations=generations,
        evaluated=result.evaluated,
        seeded_by=seeded_by,
        champions=[
            AutoResearchOOSChampionView(
                fingerprint=c.fingerprint, expr=c.expr,
                train_icir=c.train_icir, train_status=c.train_status, train_decision=c.train_decision,
                train_novelty=c.train_novelty, train_corr_to_book=c.train_corr_to_book,
                train_turnover=c.train_turnover, train_fitness=c.train_fitness,
                oos_icir=c.oos_icir, oos_ic_mean=c.oos_ic_mean,
                oos_decision=c.oos_decision, oos_reason=c.oos_reason,
            )
            for c in result.champions
        ],
    )
