"""成本参数单一来源回归测试(R-COST-001)。

历史:nine_gates/phase2/phase3/phase4/strategy_runners 把 0.00225/0.00275/0.065
写成字面量——费率一旦在 core.engine.CostModel 调整,这些位置会静默漂移。
本测试机械强制:① phase4 _build_config 的 cost 块与 CostModel() 逐位一致;
② 上述文件源码中不再出现买入费率字面量(0.00225)。旧代码下 ② 必失败。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import CostModel  # noqa: E402

# 禁止出现成本字面量的文件(唯一合法定义点 = core/engine.py::CostModel)
_NO_LITERAL_FILES = [
    "core/analysis/nine_gates.py",
    "workflow/phase2_backtest.py",
    "workflow/phase3_wf.py",
    "workflow/phase4_register.py",
    "portfolio/strategy_runners.py",
]
# 三个默认费率的字面量形态(含尾随零变体)
_LITERAL_PAT = re.compile(r"0\.00225\b|0\.00275\b|0\.0045\b|0\.0055\b|0\.00675\b|0\.00825\b")


def test_phase4_build_config_cost_matches_costmodel():
    from workflow.phase4_register import Phase4Register

    cfg = Phase4Register("f", "v")._build_config({"config": {}})
    base = CostModel()
    assert cfg["cost"]["buy"] == base.buy_cost
    assert cfg["cost"]["sell"] == base.sell_cost
    assert cfg["cost"]["financing_rate"] == base.financing_rate


def test_no_cost_literals_outside_costmodel():
    offenders = []
    for rel in _NO_LITERAL_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            code = line.split("#", 1)[0]  # 注释里允许提及数值(如文档说明)
            if _LITERAL_PAT.search(code):
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "成本字面量必须收敛到 core.engine.CostModel(R-COST-001):\n" + "\n".join(offenders)
    )


def test_phase2_default_cost_matches_costmodel():
    from workflow.phase2_backtest import Phase2Runner

    runner = Phase2Runner(factor_builder=None, timing_builder=None, config={})
    base = CostModel()
    assert runner.base_cost == base


if __name__ == "__main__":
    test_phase4_build_config_cost_matches_costmodel()
    test_no_cost_literals_outside_costmodel()
    test_phase2_default_cost_matches_costmodel()
    print("✅ test_cost_single_source: all passed")
