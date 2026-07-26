"""ExecutableStrategySpec —— 不可变、可哈希、可执行的策略领域实体(Task 5)。

把「策略」从字符串 + 重复公式升级为一等实体:身份 = sha256(canonical_json),
对 dict key 顺序稳定,且把数据/因子/择时/政策/成本/成交语义全部纳入哈希。
回测、注册、部署、生产信号共享同一个 spec_hash —— 任何一处漂移都会被哈希暴露。

纯数据模块:不读写数据湖、不持有函数对象/本机路径/动态日期(否则哈希不可复现)。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace as _dc_replace
from typing import Any

# execution.fill 的显式枚举 —— 禁止自由文本,使「成交时点」成为受控身份字段。
# Task 13 会把生产/回测统一到 T_PLUS_1_CLOSE;两者并存于枚举,改 fill 即换 spec_hash。
ALLOWED_FILLS = ("T_PLUS_1_CLOSE", "T_PLUS_1_OPEN")


def _canonical(obj: Any) -> Any:
    """递归归一:dict 按 key 排序,float 用 repr 保稳定量纲,其余原样。"""
    if isinstance(obj, Mapping):
        return {k: _canonical(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    if isinstance(obj, float):
        # repr(float) 在 CPython 下是最短可往返表示,跨进程稳定
        return repr(obj)
    return obj


@dataclass(frozen=True)
class ExecutableStrategySpec:
    family: str
    version: str
    universe: dict
    data: dict
    factor: dict
    selection: dict
    timing: dict
    policy: dict
    execution: dict

    # ---- 序列化 ----
    _FIELDS = ("family", "version", "universe", "data", "factor",
               "selection", "timing", "policy", "execution")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self._FIELDS}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ExecutableStrategySpec:
        missing = [k for k in cls._FIELDS if k not in d]
        if missing:
            raise ValueError(f"spec 缺字段: {missing}")
        return cls(**{k: d[k] for k in cls._FIELDS})

    def canonical_json(self) -> str:
        return json.dumps(_canonical(self.to_dict()), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def replace(self, **changes: Any) -> ExecutableStrategySpec:
        return _dc_replace(self, **changes)

    # ---- 校验 ----
    def validate(self) -> None:
        """结构性校验。不通过即抛 ValueError,杜绝半成品 spec 进入注册/执行。"""
        if not str(self.family).strip():
            raise ValueError("spec.family 不能为空")
        if not str(self.version).strip():
            raise ValueError("spec.version 不能为空")

        ftype = self.factor.get("type")
        if not ftype:
            raise ValueError("spec.factor.type 必填")
        shift = self.factor.get("shift")
        if not isinstance(shift, int) or shift < 1:
            raise ValueError(f"spec.factor.shift 必须为 >=1 的整数(防未来),收到 {shift!r}")

        fill = self.execution.get("fill")
        if fill not in ALLOWED_FILLS:
            raise ValueError(f"spec.execution.fill={fill!r} 不在枚举 {ALLOWED_FILLS}")
        if not str(self.execution.get("cost_model", "")).strip():
            raise ValueError("spec.execution.cost_model 必须命名(成本模型不得隐式)")

        top_n = self.selection.get("top_n")
        rebal = self.selection.get("rebalance_days")
        if not isinstance(top_n, int) or top_n <= 0:
            raise ValueError(f"spec.selection.top_n 必须为正整数,收到 {top_n!r}")
        if not isinstance(rebal, int) or rebal <= 0:
            raise ValueError(f"spec.selection.rebalance_days 必须为正整数,收到 {rebal!r}")
