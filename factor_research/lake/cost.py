"""R-COST-001 成本三费率的快照/哈希口径——纯函数,定义层(数据/配置层)。

费率定义归属:app_config.settings.CostModelConfig(frozen dataclass,买 0.225%/
卖 0.275%/融资 6.5%);执行侧 core.engine.CostModel 默认值由 test_cost_single_source
单源比对保持一致。钉死执法(EXPECTED_COST/EXPECTED_COST_HASH 比对)留在
governance.cost_pin——定义下沉本层,执法留在治理层。

历史(2026-07-21 架构 P1-1③):本口径原存于 governance/cost_pin.py,导致最底层
lake.version_returns 为取 cost_hash 反向 sys.path 补丁 + 延迟 import 治理层,
违反 data(lake)→…→production 单向分层。纯函数迁回本层后,方向恢复为
governance(执法)→ lake(定义)。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app_config.settings import CostModelConfig

_RATE_KEYS = ("buy_cost", "sell_cost", "financing_rate")


def cost_snapshot(cost: Any | None = None) -> dict[str, float]:
    """从 CostModel / Mapping / 可属性访问对象提取三费率。

    ``cost=None`` 时读 app_config 冻结默认(定义层口径);需要钉执行侧
    core.engine.CostModel 的调用方(如 governance.cost_pin)应显式传实例。
    测试可注入 dict 或 SimpleNamespace/dataclass 做对抗。
    """
    if cost is None:
        cost = CostModelConfig()
    if isinstance(cost, Mapping):
        return {k: float(cost[k]) for k in _RATE_KEYS}
    return {k: float(getattr(cost, k)) for k in _RATE_KEYS}


def canonical_cost_json(snapshot: Mapping[str, float]) -> str:
    """稳定序列化:sort_keys + 无空格,保证 hash 可复现。"""
    payload = {k: float(snapshot[k]) for k in sorted(_RATE_KEYS)}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def cost_hash(snapshot: Mapping[str, float]) -> str:
    return hashlib.sha256(canonical_cost_json(snapshot).encode("utf-8")).hexdigest()
