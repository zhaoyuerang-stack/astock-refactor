"""元级 walk-forward:演化在物理截断的数据上跑,冠军在 cutoff 之后一次性 OOS 评分。

单候选层面 L0~L3 各自防未来;本模块堵的是更隐蔽的元级泄露——岛屿进化的
选择回路若用全样本适应度挑策略,演化引擎本身就偷看了未来(data snooping
的演化版:每个个体都 shift(1),但"谁活下来"由未来绩效决定)。

隔离方式 = 物理截断,而非守卫扫描:
- 训练:close/volume/amount 全部切到 <= cutoff;forward_ret 用截断后的
  close 重算,末尾 horizon 天自然为 NaN——cutoff 之后的价格在物理上不存在。
- OOS:因子计算可见 cutoff 前历史(合法回看,等价于实盘当日可得),但 IC
  只在 (cutoff, oos_end] 的 forward_ret 上打分;forward_ret 同样只用
  <= oos_end 的 close 计算。
- 打分复用 canonical run_l0(或调用方注入的同签名 runner),绝无第二套口径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .pipeline import ast_to_hypothesis
from .repositories import CandidateRepository


@dataclass(frozen=True)
class WalkForwardChampion:
    fingerprint: str
    expr: str
    train_icir: float
    train_status: str
    train_decision: str
    oos_icir: float | None
    oos_ic_mean: float | None
    oos_decision: str
    oos_reason: str
    train_novelty: float = 0.0
    train_corr_to_book: float = 0.0
    train_turnover: float = 0.0
    train_fitness: float = 0.0


@dataclass
class WalkForwardResult:
    cutoff: str
    oos_start: str
    oos_end: str
    train_vintage_id: str
    oos_vintage_id: str
    evaluated: int = 0
    champions: list[WalkForwardChampion] = field(default_factory=list)


def run_walk_forward_search(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    *,
    cutoff: str,
    oos_end: str | None = None,
    vintage_id: str,
    repository: CandidateRepository | None = None,
    runners: dict | None = None,
    oos_sample_dates: int | None = None,
    reference_builder=None,
    **island_kw: Any,
) -> WalkForwardResult:
    """<=cutoff 截断数据上跑岛屿进化选冠军 → (cutoff, oos_end] 一次性 OOS L0 评分。

    island_kw 原样转发 run_island_search(n_islands/generations/population/
    top_k/final_stage/seeds/rng_seed/sample_dates/novelty_weight/corr_weight/...)。
    reference_builder(close, volume, amount)->[panels]:在册组合参考面板的构造器。
    元级防未来铁律:**只在截断后的 train_close 上调用**,绝不传全样本面板,
    否则 cutoff 后的在册收益会泄露进训练期的选择。
    repository 必须贯穿训练与 OOS(冠军 AST 经它回查),不传则用默认仓储。
    """
    from factory.lines.line2_validation.l0_ic_scan import precompute_forward_returns, run_l0

    from .islands import run_island_search

    cutoff_ts = pd.Timestamp(cutoff)
    end_ts = pd.Timestamp(oos_end) if oos_end else close.index[-1]
    if not (close.index[0] < cutoff_ts < end_ts):
        raise ValueError(f"cutoff must fall inside data range: {close.index[0].date()} < {cutoff} < {end_ts.date()}")
    oos_dates = close.index[(close.index > cutoff_ts) & (close.index <= end_ts)]
    if oos_dates.empty:
        raise ValueError(f"no OOS dates in ({cutoff}, {end_ts.date()}]")

    repository = repository or CandidateRepository()

    # 训练面板物理截断;forward_ret 必须从截断后的 close 重算,
    # 绝不能切全样本 forward_ret(其末端 horizon 天掺有 cutoff 后的价格)。
    train_close = close.loc[:cutoff_ts]
    train_volume = volume.loc[:cutoff_ts]
    train_amount = amount.loc[:cutoff_ts]
    train_forward = precompute_forward_returns(train_close)
    train_vintage = f"{vintage_id}|train<={cutoff_ts.date()}"
    # 在册参考面板只在截断后的训练面板上构造(防未来:不让 cutoff 后收益进选择)
    if reference_builder is not None and island_kw.get("corr_weight", 0) > 0:
        island_kw = {**island_kw,
                     "reference_panels": reference_builder(train_close, train_volume, train_amount)}
    search = run_island_search(
        train_close, train_volume, train_amount, train_forward,
        vintage_id=train_vintage,
        repository=repository,
        runners=runners,
        **island_kw,
    )

    oos_close = close.loc[:end_ts]
    oos_forward = precompute_forward_returns(oos_close).loc[oos_dates]
    oos_vintage = f"{vintage_id}|oos:{oos_dates[0].date()}..{oos_dates[-1].date()}"
    l0 = (runners or {}).get("l0", run_l0)

    champions: list[WalkForwardChampion] = []
    for champ in search.champions:
        candidate = repository.get(champ.fingerprint)
        if candidate is None:
            raise ValueError(f"champion {champ.fingerprint} not found in repository; pass the same repository through")
        exp = l0(
            ast_to_hypothesis(candidate),
            oos_close, volume.loc[:end_ts], amount.loc[:end_ts], oos_forward,
            vintage_id=oos_vintage,
            sample_dates=oos_sample_dates,
        )
        metrics = exp.result.metrics or {}
        icir = metrics.get("ICIR")
        ic_mean = metrics.get("IC_mean")
        champions.append(WalkForwardChampion(
            fingerprint=champ.fingerprint,
            expr=champ.expr,
            train_icir=champ.icir,
            train_status=champ.status,
            train_decision=champ.decision,
            oos_icir=float(icir) if icir is not None else None,
            oos_ic_mean=float(ic_mean) if ic_mean is not None else None,
            oos_decision=exp.decision.value,
            oos_reason=exp.notes,
            train_novelty=champ.novelty,
            train_corr_to_book=champ.corr_to_book,
            train_turnover=champ.turnover,
            train_fitness=champ.fitness,
        ))

    return WalkForwardResult(
        cutoff=str(cutoff_ts.date()),
        oos_start=str(oos_dates[0].date()),
        oos_end=str(oos_dates[-1].date()),
        train_vintage_id=train_vintage,
        oos_vintage_id=oos_vintage,
        evaluated=search.evaluated,
        champions=champions,
    )
