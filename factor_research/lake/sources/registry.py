"""数据源注册表：把"数据类别 → 唯一权威源"的口径知识收敛成一处可审计配置。

与 Vibe-Trading 的 `FALLBACK_CHAINS` 关键区别：本表是 **单源权威**（CANONICAL_SOURCE）
而非多源降级链。原因见铁律——跨源静默 fallback 会混口径（后复权 vs 不复权），
正是 2026-06-10 全市场假崩盘事故的根因。每类数据只有一个权威源，挂了宁可缺当日数据，
绝不静默换源。

用法：
    源类用 @register("tencent_daily") 自注册；
    调用方用 resolve_source("price_hfq", **kw) 拿到实例（不再硬 import 具体类）。

注：本表只收敛"装配层"，不改任何 fetch_one 逻辑、不改超时机制、不引入跨源 fallback。
"""
from __future__ import annotations

import importlib

# 源名 → 源类（由 @register 装饰器在源模块被导入时填充）
SOURCE_REGISTRY: dict[str, type] = {}

# 数据类别 → 唯一权威源名（编码铁律：每类一个源，绝不降级混口径）
CANONICAL_SOURCE: dict[str, str] = {
    "price_hfq":         "tencent_daily",   # 后复权日线（铁律：hfq 缺失绝不回退不复权）
    "price_raw":         "tdx_raw",          # 不复权 OHLC（估值 PE/PB 专用，铁律3）
    "margin":            "margin",           # 两融（交易所明细）
    "northbound":        "northbound",       # 北向每日个股统计
    "northbound_stock":  "northbound_stock", # 北向单股完整历史
    # 注：基本面走 build_fundamental_batch.py 的函数式批量（ak.stock_yjbb_em →
    # fundamental_batch.parquet），非 Fetcher 子类，不纳入本表。
}

# 有 import 副作用（如 chdir）或在 lake.sources 之外的源 → 按需懒导入，不放进 eager 列表
_EXTRA_MODULES: dict[str, str] = {
    "tdx_raw": "scripts.data.fetch_raw_close",
}

_registered = False


def register(name: str):
    """源类自注册装饰器。name 应与源类 __init__ 里的 name= 一致，便于追溯。"""
    def deco(cls):
        SOURCE_REGISTRY[name] = cls
        return cls
    return deco


def _ensure_registered() -> None:
    """惰性导入无副作用的源模块，触发其 @register 自注册（不含 chdir 等副作用模块）。"""
    global _registered
    if _registered:
        return
    for mod in ("lake.sources.tencent", "lake.sources.exchange"):
        importlib.import_module(mod)
    _registered = True


def resolve_source(category: str, **kw):
    """按数据类别返回权威源实例。kw 透传给源类构造器（out_dir / max_workers 等）。"""
    _ensure_registered()
    if category not in CANONICAL_SOURCE:
        raise KeyError(
            f"未知数据类别 '{category}'。已登记类别: {sorted(CANONICAL_SOURCE)}"
        )
    name = CANONICAL_SOURCE[category]
    if name not in SOURCE_REGISTRY and name in _EXTRA_MODULES:
        # 副作用模块按需导入（触发其装饰器自注册）
        importlib.import_module(_EXTRA_MODULES[name])
    if name not in SOURCE_REGISTRY:
        raise KeyError(
            f"源 '{name}'（类别 {category}）未注册。"
            f"已注册: {sorted(SOURCE_REGISTRY)}"
        )
    return SOURCE_REGISTRY[name](**kw)
