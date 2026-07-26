"""Catalog 专用 Amihud 非流动性因子注册入口。

strategies.catalog.build_amihud_illiquidity 仍是可执行 builder(多字段 PricePanel +
OO 表达式链)。本模块只把名字挂进 @register_factor,供词表/discover 收编;
catalog 在 auto-register 后用手工 builder 覆盖,保证行为逐位不变。

Disposition: 非 dormant —— 生产/台账策略依赖 catalog builder。
"""
from __future__ import annotations

from factors.registry import register_factor


@register_factor(
    "amihud_illiquidity",
    definition=(
        "Amihud 非流动性:|ret|/amount 的 window 均值 → mad_clip → 截面 zscore → "
        "shift(防未来);正=低流动性。catalog 用手写 multi-input builder"
    ),
    params={"window": (5, 252), "shift": (0, 5), "mad_clip": (1.0, 10.0)},
    data=("price/close", "price/volume", "price/amount"),
    input="close",
    arg_map={"window": "window", "shift": "shift", "mad_clip": "mad_clip"},
    searchable=False,
)
def amihud_illiquidity(close, window: int = 20, shift: int = 1, mad_clip: float = 5.0, **_):
    """元数据注册桩:正式计算走 catalog.build_amihud_illiquidity(prices, params)。

    不在此复刻 OO 链(需 volume/amount 完整 PricePanel);被误调用时显式失败,
    避免静默用错单 input auto-builder。
    """
    raise RuntimeError(
        "amihud_illiquidity 须经 strategies.catalog.build_amihud_illiquidity"
        "(PricePanel) 计算,不可仅喂 close"
    )
