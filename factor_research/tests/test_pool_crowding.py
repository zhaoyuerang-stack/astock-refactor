"""对抗性测试:策略池拥挤度(capacity.strategy_pool_crowding)接进衰减归因。

Run:  cd factor_research && python3 tests/test_pool_crowding.py

护栏 C:同质双胞胎必须被标 crowded(想瞒瞒不住);正交腿不得误伤;
<2 腿 / 样本不足必须诚实拒判(不给 0 分假绿);阈值必须与边际冗余判据同源
(governance.marginal.REDUNDANT_CORR,两处口径分叉 = 改一处忘一处)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from capacity.crowding_score import strategy_pool_crowding
from governance.marginal import REDUNDANT_CORR

_IDX = pd.bdate_range("2023-01-02", periods=400)


def _leg(mean, std, seed, idx=_IDX):
    rng = np.random.RandomState(seed)
    return pd.Series(mean + std * rng.randn(len(idx)), index=idx)


def test_twins_flagged_orthogonal_spared():
    base = _leg(0.001, 0.01, 1)
    legs = {
        "twin_a.v1": base,
        "twin_b.v1": base + _leg(0.0, 0.0005, 2),  # 同质变体 corr≈0.99
        "ortho.v1": _leg(0.0008, 0.012, 3),        # 独立信息源
    }
    out = strategy_pool_crowding(legs)
    assert out["computable"]
    assert out["per_leg"]["twin_a.v1"]["crowded"] and out["per_leg"]["twin_b.v1"]["crowded"], \
        "同质双胞胎必须被标 crowded(对池均值口径会被正交腿稀释漏检——两两口径防此坑)"
    assert out["per_leg"]["twin_a.v1"]["max_pair_with"] == "twin_b.v1", "必须点名和谁拥挤(归因可执行)"
    assert not out["per_leg"]["ortho.v1"]["crowded"], "正交腿不得误伤"
    assert out["pool_crowding_latest"] > 0.15, "池里有双胞胎,池级拥挤不应接近 0"


def test_orthogonal_pool_low_crowding():
    legs = {f"leg{i}.v1": _leg(0.001, 0.01, 10 + i) for i in range(3)}
    out = strategy_pool_crowding(legs)
    assert out["computable"]
    assert all(not v["crowded"] for v in out["per_leg"].values())
    assert abs(out["pool_crowding_latest"]) < 0.15, "独立腿池的拥挤度应接近 0"


def test_honest_refusal_not_fake_green():
    # <2 条腿:拥挤是策略间现象,单腿必须拒判而不是给 0 分
    one = strategy_pool_crowding({"only.v1": _leg(0.001, 0.01, 20)})
    assert not one["computable"] and "无拥挤可言" in one["reason"]
    assert "pool_crowding_latest" not in one, "拒判时不得输出任何分数(0 分假绿更危险)"
    # 共同样本不足
    short = strategy_pool_crowding({
        "a.v1": _leg(0.001, 0.01, 21, idx=pd.bdate_range("2026-01-01", periods=30)),
        "b.v1": _leg(0.001, 0.01, 22, idx=pd.bdate_range("2026-01-01", periods=30)),
    })
    assert not short["computable"] and "共同样本" in short["reason"]


def test_threshold_single_source_with_marginal():
    legs = {"a.v1": _leg(0.001, 0.01, 30), "b.v1": _leg(0.001, 0.01, 31)}
    out = strategy_pool_crowding(legs)
    assert out["threshold"] == REDUNDANT_CORR, \
        "拥挤阈值必须与 governance.marginal.REDUNDANT_CORR 同源(禁止第二套口径)"


def test_deterministic():
    legs = {"a.v1": _leg(0.001, 0.01, 40), "b.v1": _leg(0.0008, 0.012, 41)}
    assert strategy_pool_crowding(legs) == strategy_pool_crowding(legs)


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
