"""R-COST-001 成本钉死执法:CostModel 三费率 hash-pin 比对。

背景:holdout.start 有 EXPECTED_BOUNDARY_HASH 机械锁(改动即 exit 1,强制走 ADR);
但 core.engine.CostModel 的 buy_cost/sell_cost/financing_rate 此前只有
test_cost_single_source 的"单一来源"比对——全部对着 CostModel() 自适应,
下调费率仍全绿。这是「禁止为达标临时下调滑点/佣金」唯一没有机械强制的部分。

范式照抄 check_holdout_compliance.py 的 boundary pin:
  sha256(canonical JSON, sort_keys) vs 钉死的 EXPECTED_COST_HASH。
改费率必须同时更新 EXPECTED_COST 与 EXPECTED_COST_HASH,不能为达标临时下调成本。

口径分层(2026-07-21 架构 P1-1③):cost_snapshot / canonical_cost_json /
cost_hash 三个纯函数的定义层权威在 lake.cost(费率定义归属 app_config 冻结
dataclass),本模块自 lake.cost 引入并 re-export 以兼容既有消费方;本模块只
保留执法——EXPECTED_COST 钉死值与 check_cost_pin 比对(无参时钉执行侧
core.engine.CostModel,语义不变)。

CI 守卫 CLI 入口见 scripts/ci/check_cost_model_pin.py(薄壳,复用本模块符号)。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from lake.cost import canonical_cost_json, cost_hash, cost_snapshot

__all__ = [
    "EXPECTED_COST",
    "EXPECTED_COST_HASH",
    "canonical_cost_json",
    "check_explicit_zero_cost_calls",
    "check_cost_pin",
    "cost_hash",
    "cost_snapshot",
]

# ── 钉死值:与 core.engine.CostModel 默认字段一一对应 ──
# 变更路径:改 CostModel 默认值 -> 更新本 pin -> 重跑成本与回测守卫。
EXPECTED_COST = {
    "buy_cost": 0.00225,
    "sell_cost": 0.00275,
    "financing_rate": 0.065,
}
# sha256(json.dumps(EXPECTED_COST, sort_keys=True, separators=(",", ":")))
EXPECTED_COST_HASH = "40f40fbaefebb10e38b6ccc37e39c77573ebbf0bb941458d745c1645fdbaf43b"

FORMAL_RUNTIME_ROOTS = (
    "strategies",
    "workflow",
    "services/actions",
    "scripts/ops",
)


def check_cost_pin(cost: Any | None = None) -> list[str]:
    """比对三费率 hash 与钉死值。返回违规消息列表(空 = 通过)。

    可注入 ``cost``(dict/dataclass/CostModel)便于 fixture;无参时钉执行侧
    core.engine.CostModel 默认值(R-COST-001 的守护对象)。core.engine 处于
    governance 之下的合法依赖方向,函数内引入仅为缩小守卫 import 面。
    """
    if cost is None:
        from core.engine import CostModel

        cost = CostModel()
    snap = cost_snapshot(cost)
    h = cost_hash(snap)
    if h == EXPECTED_COST_HASH:
        return []
    return [
        "CostModel 默认费率被改动(R-COST-001 hash-pin 失败):\n"
        f"  当前: {snap} (hash {h[:12]}…)\n"
        f"  钉死: {EXPECTED_COST} (hash {EXPECTED_COST_HASH[:12]}…)\n"
        "改费率必须同时更新 EXPECTED_COST 与 EXPECTED_COST_HASH,并重跑成本与回测守卫"
        f"(EXPECTED_COST / EXPECTED_COST_HASH in {Path(__file__).name})。"
    ]


def check_explicit_zero_cost_calls(root: Path | None = None) -> list[str]:
    """Reject explicit zero buy/sell costs in formal execution paths."""
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    errors: list[str] = []
    for relative_root in FORMAL_RUNTIME_ROOTS:
        scan_root = root / relative_root
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                errors.append(f"{path.relative_to(root)} 无法做成本旁路检查: {exc}")
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (
                    node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if name != "CostModel":
                    continue
                zero_fields = [
                    keyword.arg
                    for keyword in node.keywords
                    if keyword.arg in {"buy_cost", "sell_cost"}
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, (int, float))
                    and float(keyword.value.value) == 0.0
                ]
                if zero_fields:
                    errors.append(
                        f"{path.relative_to(root)}:{node.lineno} "
                        f"正式执行路径显式关闭交易成本: {', '.join(zero_fields)}"
                    )
    return errors
