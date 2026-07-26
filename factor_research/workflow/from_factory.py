"""Bridge: factory Hypothesis → workflow 验证管线的 builder。

factory/lines 负责"广度":变异生成 + L0/L1/L2/L3 廉价筛选,候选以
``factory.ontology.Hypothesis`` 形式存活在 ``factory.pool``。
workflow/phase1~4 负责"深度":合成防未来审计 + 多段回测 + WF + 唯一登记。

两端原本不连。本模块把一个 Hypothesis 翻译成 workflow 期望的
``factor_builder(close, volume, amount, dates)`` / ``timing_builder(close, amount)``
可调用对象,使 factory 发现的候选能走同一条 phase1~4 验证+登记闸门。

设计与 ``factory.lines.line2_validation.l1_quick_bt`` / ``line3_marginal`` 中的
``_resolve_factor_fn`` + ``_dispatch_args`` 保持一致(同一套因子解析约定),
避免两套不一致的解析逻辑。
"""
from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from factory.ontology import Hypothesis
    from workflow.explore import FactorSpec


def _resolve_fn(fn_name: str) -> Callable:
    """dotted path -> callable,如 'factors.small_cap.small_cap_factor'."""
    module_path, fn = fn_name.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), fn)


def _dispatch_args(deps: Iterable[str], close: pd.DataFrame, volume: pd.DataFrame,
                   amount: pd.DataFrame) -> list[pd.DataFrame]:
    """按 data_dependencies 选 positional 价量面板(镜像 factory 的同名逻辑)。

    工厂因子函数签名各异(如 small_cap_factor(amount) 只吃 amount),
    由 data_dependencies 决定喂哪几个面板。fundamental/* 依赖此处不处理
    (纯价量桥接);若候选依赖基本面,需扩展此函数。
    """
    s = {d for d in deps if not d.startswith("fundamental/")}
    if "price/close" in s and "price/volume" in s:
        return [close, volume]
    if "price/close" in s:
        return [close]
    if "price/amount" in s:
        return [amount]
    if "price/volume" in s:
        return [volume]
    # 缺省:多数工厂因子吃 amount(成交额)
    return [amount]


def _default_pt_timing(close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
    """缺省择时 = PureTrend MA16(生产标准)。Hypothesis 无 timing 时用此。"""
    from factors.small_cap import small_cap_timing
    t, _, _ = small_cap_timing(close, amount, ma_window=16)
    return t.astype(float)


def factor_builder_from_hypothesis(hyp: Hypothesis) -> Callable[..., pd.DataFrame]:
    """Hypothesis -> factor_builder(close, volume, amount, dates) -> DataFrame。"""
    fn = _resolve_fn(hyp.factor_fn_name)
    deps = tuple(hyp.data_dependencies)
    params = dict(hyp.factor_params)

    def builder(close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame,
                dates: pd.DatetimeIndex) -> pd.DataFrame:  # dates 兼容 phase1 签名,纯价量因子忽略
        args = _dispatch_args(deps, close, volume, amount)
        return fn(*args, **params)

    return builder


def timing_builder_from_hypothesis(hyp: Hypothesis) -> Callable[..., pd.Series]:
    """Hypothesis -> timing_builder(close, amount) -> Series。无 timing 时用 PT-MA16。"""
    if not getattr(hyp, "timing_fn_name", None):
        return _default_pt_timing
    fn = _resolve_fn(hyp.timing_fn_name)
    tparams = dict(getattr(hyp, "timing_params", {}) or {})

    def builder(close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
        out = fn(close, amount, **tparams)
        if isinstance(out, tuple):      # small_cap_timing 返回 (t, nav, dist)
            out = out[0]
        return out.astype(float)

    return builder


def hypothesis_to_spec(hyp: Hypothesis) -> FactorSpec:
    """Hypothesis -> workflow.explore.FactorSpec(可直接喂 phase1~4)。"""
    from workflow.explore import FactorSpec
    base = {"top_n": 25, "rebalance_days": 20, "leverage": 1.25,
            "buy_cost": 0.00225, "sell_cost": 0.00275, "financing_rate": 0.065}
    config = {**base, **dict(hyp.factor_params)}
    thesis = ""
    if getattr(hyp, "thesis", None) is not None:
        thesis = hyp.thesis.mechanism
    elif getattr(hyp, "description", ""):
        thesis = hyp.description
    return FactorSpec(
        name=hyp.name,
        factor_builder=factor_builder_from_hypothesis(hyp),
        timing_builder=timing_builder_from_hypothesis(hyp),
        config=config,
        niche=getattr(hyp, "source", "factory"),
        hypothesis=thesis,
    )
