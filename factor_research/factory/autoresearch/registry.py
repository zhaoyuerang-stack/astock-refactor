"""Whitelists for the Auto Factor Research DSL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FactorSpec:
    name: str
    params: dict[str, tuple[int | float, int | float]] = field(default_factory=dict)
    data_dependencies: tuple[str, ...] = ()


ALLOWED_FACTORS: dict[str, FactorSpec] = {
    # momentum/illiquidity 已迁 @register_factor;volume_ratio/volatility 仍手工
    # (搜索白名单无 probe 证据指针,fail-closed 不迁)。
    "volume_ratio": FactorSpec("volume_ratio", {"window": (3, 120)}, ("price/volume",)),
    "volatility": FactorSpec("volatility", {"window": (5, 252)}, ("price/close",)),
    # roe/net_profit_yoy/revenue_yoy/bp_proxy/ep_proxy 已迁 @register_factor
    # searchable=True,经下方自动接线进入,不再手工列。
    # alpha101 白名单已剔除机械退化/近重复项(见 tests/test_alpha101_degeneracy.py):
    # · alpha_005: close-close 常数子项,退化为 price_to_ma 同信息
    # · alpha_020/022/024/033/049: 与 alpha_009 短收益簇 |秩相关|≥0.98,虚增 n_trials
    "alpha_001": FactorSpec("alpha_001", {}, ("price/close", "price/volume")),
    "alpha_002": FactorSpec("alpha_002", {}, ("price/close", "price/volume")),
    "alpha_003": FactorSpec("alpha_003", {}, ("price/close", "price/volume")),
    "alpha_006": FactorSpec("alpha_006", {}, ("price/close", "price/volume")),
    "alpha_008": FactorSpec("alpha_008", {}, ("price/close",)),
    "alpha_009": FactorSpec("alpha_009", {}, ("price/close",)),
    "alpha_012": FactorSpec("alpha_012", {}, ("price/close", "price/volume")),
    "alpha_013": FactorSpec("alpha_013", {}, ("price/close", "price/volume")),
    "alpha_014": FactorSpec("alpha_014", {}, ("price/close", "price/volume")),
    "alpha_015": FactorSpec("alpha_015", {}, ("price/close", "price/volume")),
    "alpha_017": FactorSpec("alpha_017", {}, ("price/close",)),
    "alpha_018": FactorSpec("alpha_018", {}, ("price/close",)),
    "alpha_019": FactorSpec("alpha_019", {}, ("price/close",)),
    "alpha_021": FactorSpec("alpha_021", {}, ("price/close",)),
    "alpha_023": FactorSpec("alpha_023", {}, ("price/close",)),
    "alpha_025": FactorSpec("alpha_025", {}, ("price/close",)),
    "alpha_028": FactorSpec("alpha_028", {}, ("price/close", "price/volume")),
    "alpha_030": FactorSpec("alpha_030", {}, ("price/close", "price/volume")),
    "alpha_032": FactorSpec("alpha_032", {}, ("price/close",)),
    "alpha_034": FactorSpec("alpha_034", {}, ("price/close",)),
    "alpha_037": FactorSpec("alpha_037", {}, ("price/close",)),
    "alpha_038": FactorSpec("alpha_038", {}, ("price/close",)),
    "alpha_040": FactorSpec("alpha_040", {}, ("price/close", "price/volume")),
    "alpha_044": FactorSpec("alpha_044", {}, ("price/close", "price/volume")),
    "alpha_050": FactorSpec("alpha_050", {}, ("price/close", "price/volume")),
    "alpha_055": FactorSpec("alpha_055", {}, ("price/close", "price/volume")),
    # 隔离岛/北向(holdertrade_net/large_order_net_ratio/northbound_*)已迁
    # @register_factor searchable=True,经下方自动接线进入,不再手工列。
}

# ── @register_factor 自动接线: 只有显式 searchable=True 的因子才进搜索白名单 ──
# "工厂搜不搜某因子"是研究判断(扩搜索宇宙改变确定性搜索行为),故 opt-in 不自动。
# 手工条目优先(setdefault);单向依赖合规 factory→factors。
from factors.registry import discover as _discover_factors  # noqa: E402

for _name, _rec in _discover_factors().items():
    if _rec.searchable:
        ALLOWED_FACTORS.setdefault(_name, FactorSpec(_name, dict(_rec.params), _rec.data))

ALLOWED_TRANSFORMS = {
    "mad_clip",
    "zscore",
    "rank",
    "neg",
    "log1p",
    "rolling_mean",
    "rolling_std",
    "regime_gate",
    "fundamental_veto",
    "salience_veto",
    "error_feedback_correction",
}

ALLOWED_NEUTRALIZE = {"industry", "size"}
ALLOWED_DIRECTIONS = {"positive", "negative"}
ALLOWED_TYPES = {"linear_combo"}

FORBIDDEN_TOKENS = (
    "future",
    "forward_return",
    "label",
    "target",
    "next_",
    "tomorrow",
)


def looks_forbidden(value: Any) -> bool:
    text = str(value).lower()
    return any(token in text for token in FORBIDDEN_TOKENS)
