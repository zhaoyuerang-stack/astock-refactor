"""Task 5: ExecutableStrategySpec —— 不可变、可哈希、身份含执行语义。

策略身份 = sha256(canonical_json)，对 dict key 顺序稳定，且把 execution.fill 纳入身份，
使「执行语义变了 = 策略身份变了」成为机械事实(为 Task 13 T+1 改动自动失效旧 hash 铺路)。
"""
import pytest

from core.strategy_spec import ExecutableStrategySpec


def _spec():
    return ExecutableStrategySpec(
        family="illiquidity",
        version="v3.1",
        universe={"market": "A_SHARE", "exclude_star": False},
        data={"price_units": "shares_yuan", "warmup_start": "2010-01-01"},
        factor={"type": "amihud_illiquidity", "window": 20, "shift": 1},
        selection={"top_n": 25, "rebalance_days": 20},
        timing={"type": "pure_trend_band", "ma": 16, "cap": 1.5},
        policy={"veto": "salience_covariance", "veto_q": 0.30},
        execution={"fill": "T_PLUS_1_CLOSE", "cost_model": "A_SHARE_STANDARD_V1"},
    )


def test_spec_hash_is_stable_under_dict_key_order():
    left = _spec()
    right = ExecutableStrategySpec.from_dict(dict(reversed(list(left.to_dict().items()))))
    assert left.spec_hash == right.spec_hash


def test_identity_includes_execution_semantics():
    left = _spec()
    changed = left.replace(execution={**left.execution, "fill": "T_PLUS_1_OPEN"})
    assert left.spec_hash != changed.spec_hash


def test_roundtrip_from_dict_preserves_identity():
    left = _spec()
    assert ExecutableStrategySpec.from_dict(left.to_dict()).spec_hash == left.spec_hash


def test_validate_rejects_shift_below_one():
    bad = _spec().replace(factor={"type": "amihud_illiquidity", "window": 20, "shift": 0})
    with pytest.raises(ValueError):
        bad.validate()


def test_validate_rejects_unknown_fill():
    bad = _spec().replace(execution={"fill": "INTRADAY", "cost_model": "A_SHARE_STANDARD_V1"})
    with pytest.raises(ValueError):
        bad.validate()


def test_validate_rejects_missing_cost_model():
    bad = _spec().replace(execution={"fill": "T_PLUS_1_CLOSE"})
    with pytest.raises(ValueError):
        bad.validate()


def test_validate_rejects_nonpositive_selection():
    bad = _spec().replace(selection={"top_n": 0, "rebalance_days": 20})
    with pytest.raises(ValueError):
        bad.validate()


def test_validate_rejects_empty_identity():
    with pytest.raises(ValueError):
        _spec().replace(family="").validate()


def test_validate_accepts_canonical_spec():
    _spec().validate()  # should not raise


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
