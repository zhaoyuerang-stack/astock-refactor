"""单点因子注册 —— 消除 catalog / ALLOWED_FACTORS / _FACTOR_CALLS 三处手工接线。

`@register_factor` 在 **factors 层**记录因子元数据;上层(strategies.catalog /
factory.autoresearch)从这里 **PULL**(单向依赖合规 R-ARCH-001,绝不反向 import)。
仿 `factors.alpha.base.register_transform` 的同层注册模式。

**只做机械接线(A 类),不碰任何有效性裁决(C 类:probe/hit/DSR/9-Gate 仍是确定性规则)。**

元数据 → 三面映射:
  catalog FACTOR_BUILDERS  ← fn + input(PricePanel 属性) + arg_map
  ALLOWED_FACTORS          ← params(autoresearch 范围) + data(依赖)
  _FACTOR_CALLS(DSL)      ← fn.__module__ + fn.__name__ + arg_map

新增一个因子 = 在函数上 `@register_factor(...)`;三面自动接(seeds 属判断,不自动)。
"""
from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FactorRecord:
    name: str
    fn: Callable                       # 原始因子 fn(<input>, **kwargs) -> DataFrame
    definition: str                    # 口径一句话:数学定义 + 方向含义(必填,防同名不同义)
    params: dict = field(default_factory=dict)   # autoresearch 范围 {"window": (3, 120)}
    data: tuple = ("price/close",)               # 数据依赖
    input: str = "close"                         # PricePanel 属性: close/amount/volume
    arg_map: dict = field(default_factory=dict)  # spec 参数名 → fn 参数名 {"window": "n"}
    searchable: bool = False                      # 是否进 autoresearch 搜索白名单(见下)
    evidence: str = ""                 # probe/研究证据指针(searchable=True 必填)
    source_hash: str = ""              # fn 源码 sha256[:12],注册时自动算;实现改动即变


FACTOR_REGISTRY: dict[str, FactorRecord] = {}


def _fn_source_hash(fn: Callable) -> str:
    """因子函数源码摘要。语义指纹:实现一改 hash 即变,缓存/守卫据此判漂移。"""
    return hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()[:12]


def register_factor(name: str, *, definition: str, params: dict | None = None,
                    data: tuple = ("price/close",), input: str = "close",
                    arg_map: dict | None = None, searchable: bool = False,
                    evidence: str = ""):
    """装饰器:把因子函数 + 元数据登记进 FACTOR_REGISTRY(供上层 PULL)。

    catalog FACTOR_BUILDERS / DSL _FACTOR_CALLS **无条件**自动接(惰性解析/调用表,
    加条目零行为后果,仅在被显式引用时生效)。

    `definition`(必填):口径一句话——数学定义 + 正方向含义。同名不同义(如
    |ret|/volume vs |ret|/amount 两种 "illiquidity")只能在语义层被人看出来,
    所以口径必须写在注册点,而不是散在 docstring 里。

    `searchable`:是否把因子放进 autoresearch **搜索白名单** ALLOWED_FACTORS。默认 **False**
    —— 因为"工厂要不要搜这个因子"是**研究判断**(扩大搜索宇宙会改变确定性搜索行为),
    不是纯机械接线,须**显式 opt-in**。设 True 才让工厂自动探索它,且必须带
    `evidence`(probe 报告 / research_ledger run / 方向登记簿条目的指针)——
    词表入口的证据门,防"整批倒进白名单靠下游门禁烧算力兜底"。

    注册即校验,失败抛 ValueError(fail-closed,import 时就响):
      · definition 为空;
      · searchable=True 但 evidence 为空;
      · 同名重复注册且源码不同(撞名/静默覆盖;同源码幂等放行,容忍模块 reload)。
    """
    if not (definition or "").strip():
        raise ValueError(f"register_factor({name!r}): definition 必填(口径一句话,防同名不同义)")
    if searchable and not (evidence or "").strip():
        raise ValueError(
            f"register_factor({name!r}): searchable=True 必须带 evidence"
            "(probe/research_ledger/方向登记簿指针)——词表入口证据门")

    def _deco(fn):
        src_hash = _fn_source_hash(fn)
        existing = FACTOR_REGISTRY.get(name)
        if existing is not None and existing.source_hash != src_hash:
            raise ValueError(
                f"register_factor({name!r}): 同名因子已注册且源码不同"
                f"(既有 {existing.fn.__module__}.{existing.fn.__name__},"
                f"新 {fn.__module__}.{fn.__name__})——禁止静默覆盖,改名或收敛口径")
        FACTOR_REGISTRY[name] = FactorRecord(
            name=name, fn=fn, definition=definition.strip(), params=dict(params or {}),
            data=tuple(data), input=input, arg_map=dict(arg_map or {}),
            searchable=searchable, evidence=(evidence or "").strip(),
            source_hash=src_hash)
        return fn
    return _deco


# 含 @register_factor 的因子模块(eager import 触发注册)。
# 不吞异常:模块坏了要响,不静默漂移(与 catalog 不静默 fallback 同精神)。
_MODULES = (
    "factors.microstructure",
    "factors.shareholder",
    "factors.capital_flow",
    "factors.northbound",
    "factors.fundamental",
    "factors.momentum",
    "factors.small_cap",
    "factors.amihud_catalog",
)
_discovered = False


def discover() -> dict[str, FactorRecord]:
    """eager import _MODULES 触发装饰器注册,幂等。返回 FACTOR_REGISTRY。"""
    global _discovered
    if not _discovered:
        import importlib
        for m in _MODULES:
            importlib.import_module(m)
        _discovered = True
    return FACTOR_REGISTRY
