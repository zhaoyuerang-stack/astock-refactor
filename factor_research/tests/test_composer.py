"""capped 权重组合测试(防御腿封顶,2026-06-14 国债/黄金入 LIVE)。

Run:
    cd factor_research && python3 tests/test_composer.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio.composer import capped_weight, compose  # noqa: E402


def _panel():
    idx = pd.bdate_range("2024-01-01", periods=300)
    rng = np.random.default_rng(7)
    return pd.DataFrame({
        "eq1": rng.normal(0.001, 0.02, 300),
        "eq2": rng.normal(0.001, 0.02, 300),
        "bond": rng.normal(0.0003, 0.003, 300),
        "gold": rng.normal(0.0004, 0.008, 300),
    }, index=idx)


def test_capped_weight_caps_defensive_total():
    df = _panel()
    _, w = capped_weight(df, {"bond", "gold"}, cap=0.30)
    assert abs(w["bond"] + w["gold"] - 0.30) < 1e-9   # 防御合计=cap
    assert abs(w["eq1"] + w["eq2"] - 0.70) < 1e-9     # 进攻合计=1-cap
    assert abs(w["bond"] - 0.15) < 1e-9 and abs(w["eq1"] - 0.35) < 1e-9  # 组内等权
    # 不同 cap → 防御合计随之变
    _, w20 = capped_weight(df, {"bond", "gold"}, cap=0.20)
    assert abs(w20["bond"] + w20["gold"] - 0.20) < 1e-9
    print("✅ capped 防御腿合计封顶,组内等权")


def test_capped_degrades_to_equal_when_one_group_empty():
    df = _panel()
    _, w = capped_weight(df, set(), cap=0.30)  # 无防御腿 → 全等权
    assert all(abs(v - 0.25) < 1e-9 for v in w)
    _, w2 = capped_weight(df, {"eq1", "eq2", "bond", "gold"}, cap=0.30)  # 全防御 → 全等权
    assert all(abs(v - 0.25) < 1e-9 for v in w2)
    print("✅ 单组为空时退回等权")


def test_compose_capped_returns_static_weights():
    df = _panel()
    pr, wdf = compose({c: df[c] for c in df.columns}, method="capped",
                      defensive={"bond", "gold"}, cap=0.30)
    assert len(pr) > 0
    assert abs(float(wdf["bond"].iloc[0]) - 0.15) < 1e-9  # 报告真实静态权重
    print("✅ compose(capped) 输出真实静态权重")


if __name__ == "__main__":
    test_capped_weight_caps_defensive_total()
    test_capped_degrades_to_equal_when_one_group_empty()
    test_compose_capped_returns_static_weights()
    print("\n🎉 Composer capped tests passed!")
