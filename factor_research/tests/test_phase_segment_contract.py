"""phase2 ↔ phase4 段间契约回归测试(2026-07-11 review)。

历史缺陷:phase2 动态生成 OOS 显示标签(终点年随 holdout boundary 变,如
"OOS 2023-2024"),phase4 _build_metrics 硬编码精确匹配 "OOS 2023-2026" ——
boundary=2025 时 OOS 段永远查不到,台账 annual_2023/maxdd_2023 静默缺失。
本测试在旧代码上必失败(test_oos_metrics_survive_dynamic_label)。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow.phase4_register import Phase4Register  # noqa: E402


def _fake_p2_dynamic_label():
    """模拟 boundary=2025-01-01 下 phase2 的旧式报告(只有显示标签,无 role 键)。"""
    return {
        "config": {},
        "segments": {
            "IS  2018-2022": {"annual": 0.20, "maxdd": -0.15, "sharpe": 1.2, "calmar": 1.3},
            "OOS 2023-2024": {"annual": 0.11, "maxdd": -0.12, "sharpe": 0.8, "calmar": 0.9},
            "压力 2010-2017": {"annual": 0.05, "maxdd": -0.30, "sharpe": 0.3, "calmar": 0.2},
        },
    }


def test_oos_metrics_survive_dynamic_label():
    m = Phase4Register("f", "v")._build_metrics(_fake_p2_dynamic_label(), {})
    # 旧代码精确匹配 "OOS 2023-2026" → annual_2023 缺失(此断言在旧代码失败)
    assert m.get("annual_2023") == 0.11
    assert m.get("maxdd_2023") == -0.12
    assert m.get("annual_2010") == 0.05


def test_segments_by_role_takes_priority():
    p2 = _fake_p2_dynamic_label()
    p2["segments_by_role"] = {
        "is": {"annual": 0.21, "maxdd": -0.14, "sharpe": 1.3, "calmar": 1.4},
        "oos": {"annual": 0.12, "maxdd": -0.11, "sharpe": 0.9, "calmar": 1.0},
        "stress": {"annual": 0.06, "maxdd": -0.29, "sharpe": 0.4, "calmar": 0.2},
    }
    m = Phase4Register("f", "v")._build_metrics(p2, {})
    assert m["annual_2023"] == 0.12  # role 键优先于标签回退
    assert m["annual"] == 0.21       # 顶层 = IS 段(样本内口径,行为保持)


def test_top_level_metrics_remain_is_segment():
    m = Phase4Register("f", "v")._build_metrics(_fake_p2_dynamic_label(), {})
    assert m["annual"] == 0.20
    assert m["maxdd"] == -0.15


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
