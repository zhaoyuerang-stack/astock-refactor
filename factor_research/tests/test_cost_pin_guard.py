"""check_cost_model_pin 守卫对抗回归(R-COST-001)。

对抗性验收:每条断言先证明"坏费率真的被拒",再证明真实 CostModel() 通过。
检测函数可注入 dict/dataclass,不依赖改磁盘源码(另有一次性 live 突变验证见任务报告)。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import CostModel  # noqa: E402
from scripts.ci import check_cost_model_pin as guard  # noqa: E402

# ── 注入检测:可吃 dict / dataclass / namespace ───────────────────────────

def test_real_defaults_pass():
    """真实 CostModel() 默认三费率必须绿。"""
    assert guard.check_cost_pin() == []
    assert guard.check_cost_pin(CostModel()) == []
    assert guard.main() == 0


def test_injected_dict_matches_pin_passes():
    assert guard.check_cost_pin(dict(guard.EXPECTED_COST)) == []


def test_injected_buy_cost_lowered_fails():
    """任一费率被改(典型:为达标下调 buy_cost)必须红。"""
    bad = dict(guard.EXPECTED_COST)
    bad["buy_cost"] = 0.001
    errors = guard.check_cost_pin(bad)
    assert errors, "下调 buy_cost 必须被 hash-pin 拦住"
    assert "R-COST-001" in errors[0]
    assert "0.001" in errors[0] or "buy_cost" in errors[0]
    assert "cost_model.md" in errors[0]
    assert "DECISIONS" in errors[0]


def test_injected_sell_cost_lowered_fails():
    bad = dict(guard.EXPECTED_COST)
    bad["sell_cost"] = 0.001
    assert guard.check_cost_pin(bad)


def test_injected_financing_rate_lowered_fails():
    bad = dict(guard.EXPECTED_COST)
    bad["financing_rate"] = 0.01
    assert guard.check_cost_pin(bad)


def test_injected_dataclass_lowered_fails():
    @dataclass(frozen=True)
    class FakeCost:
        buy_cost: float
        sell_cost: float
        financing_rate: float

    bad = FakeCost(buy_cost=0.001, sell_cost=0.00275, financing_rate=0.065)
    errors = guard.check_cost_pin(bad)
    assert errors and "hash-pin" in errors[0]


def test_injected_namespace_passes_when_pinned():
    ok = SimpleNamespace(**guard.EXPECTED_COST)
    assert guard.check_cost_pin(ok) == []


def test_hash_is_canonical_sort_keys():
    """hash 必须对 key 顺序不敏感(canonical sort_keys)。"""
    a = {"buy_cost": 0.00225, "sell_cost": 0.00275, "financing_rate": 0.065}
    b = {"financing_rate": 0.065, "buy_cost": 0.00225, "sell_cost": 0.00275}
    assert guard.cost_hash(a) == guard.cost_hash(b) == guard.EXPECTED_COST_HASH


def test_live_repo_guard_script_exits_0():
    """真实仓库脚本入口 exit 0(与 main() 一致,覆盖 CLI 路径)。"""
    script = ROOT / "scripts" / "ci" / "check_cost_model_pin.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "hash-pin 通过" in proc.stdout


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
