#!/usr/bin/env python3
"""R-COST-001 守卫 CLI:钉死 CostModel 三费率默认值(hash-pin)。

逻辑唯一权威 = governance.cost_pin(EXPECTED_COST / EXPECTED_COST_HASH /
cost_snapshot / canonical_cost_json / cost_hash / check_cost_pin)——原因见
架构评审:下层 lake.version_returns 曾反向 import 本 CI 脚本取 cost_hash 口径,
是一条 canonical→scripts 的反向依赖边,已把逻辑迁到 governance/cost_pin.py。

本文件只保留 CLI 入口(main() / 退出码 / 输出文案),供 scripts/test_all.sh
与直接 `python3 scripts/ci/check_cost_model_pin.py` 调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # factor_research/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance.cost_pin import (  # noqa: E402
    EXPECTED_COST,
    EXPECTED_COST_HASH,
    canonical_cost_json,
    check_cost_pin,
    cost_hash,
    cost_snapshot,
)

__all__ = [
    "EXPECTED_COST",
    "EXPECTED_COST_HASH",
    "canonical_cost_json",
    "check_cost_pin",
    "cost_hash",
    "cost_snapshot",
    "main",
]


def main() -> int:
    errors = check_cost_pin()
    if errors:
        for msg in errors:
            print(f"❌ {msg}", file=sys.stderr)
        return 1
    snap = cost_snapshot()
    print(
        f"✅ CostModel 费率 hash-pin 通过: "
        f"buy={snap['buy_cost']} sell={snap['sell_cost']} "
        f"fin={snap['financing_rate']} "
        f"(hash {EXPECTED_COST_HASH[:12]}…)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
