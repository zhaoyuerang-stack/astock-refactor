"""check_factor_registry 守卫 + factors.registry 注册门 对抗回归测试。

对抗性验收(护栏 C):每条断言先证明"坏东西真的被拒",再证明真实仓库现状通过。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factors.registry import FACTOR_REGISTRY, register_factor
from scripts.ci import check_factor_registry as guard

# ── 注册门:import 期 fail-closed ─────────────────────────────────────────

def test_register_rejects_empty_definition():
    with pytest.raises(ValueError, match="definition 必填"):
        register_factor("_t_no_def", definition="  ")


def test_register_rejects_searchable_without_evidence():
    with pytest.raises(ValueError, match="evidence"):
        register_factor("_t_no_evidence", definition="x", searchable=True)


def test_register_rejects_same_name_different_source():
    @register_factor("_t_collision", definition="第一版")
    def fa(close):
        return close * 1.0

    try:
        with pytest.raises(ValueError, match="同名因子已注册且源码不同"):
            @register_factor("_t_collision", definition="第二版")
            def fb(close):
                return close * 2.0
    finally:
        FACTOR_REGISTRY.pop("_t_collision", None)


def test_register_records_definition_and_source_hash():
    @register_factor("_t_meta", definition="口径说明", evidence="probe:x")
    def fc(close):
        return close

    try:
        rec = FACTOR_REGISTRY["_t_meta"]
        assert rec.definition == "口径说明"
        assert len(rec.source_hash) == 12
    finally:
        FACTOR_REGISTRY.pop("_t_meta", None)


# ── C1:手工接线冻结(双向) ──────────────────────────────────────────────

def _surfaces_with(surface: str, keys: set):
    base = {k: set(v) for k, v in guard.LEGACY_HANDWIRED.items()}
    base[surface] = keys
    return base


def test_c1_new_handwired_entry_fails():
    keys = set(guard.LEGACY_HANDWIRED["dsl"]) | {"rogue_new_factor"}
    errors = guard.check_handwired_frozen(_surfaces_with("dsl", keys))
    assert any("rogue_new_factor" in e and "@register_factor" in e for e in errors)


def test_c1_stale_legacy_entry_fails():
    # 从仍在冻结清单的名字里挑一个,模拟「清单有但字面量已删」
    sample = next(iter(guard.LEGACY_HANDWIRED["whitelist"]), None)
    if sample is None:
        pytest.skip("whitelist LEGACY 已清空")
    keys = set(guard.LEGACY_HANDWIRED["whitelist"]) - {sample}
    errors = guard.check_handwired_frozen(_surfaces_with("whitelist", keys))
    assert any(sample in e and "LEGACY" in e for e in errors)


# ── C2:注册表完整性 ─────────────────────────────────────────────────────

def _rec(name, definition="ok", searchable=False, evidence="", source_hash=None):
    if source_hash is None:
        source_hash = ("h_" + name)[:12].ljust(12, "0")
    return SimpleNamespace(name=name, definition=definition, searchable=searchable,
                           evidence=evidence, source_hash=source_hash)


def test_c2_missing_definition_fails():
    errors = guard.check_registry_integrity({"f1": _rec("f1", definition="")})
    assert any("definition 为空" in e for e in errors)


def test_c2_searchable_without_evidence_fails():
    errors = guard.check_registry_integrity({"f1": _rec("f1", searchable=True)})
    assert any("evidence" in e for e in errors)


def test_c2_duplicate_source_hash_fails():
    reg = {"fa": _rec("fa", source_hash="deadbeef0001"),
           "fb": _rec("fb", source_hash="deadbeef0001")}
    errors = guard.check_registry_integrity(reg)
    assert any("source_hash" in e and "n_trials" in e for e in errors)


# ── C3:注册名与手工条目撞名 ─────────────────────────────────────────────

def test_c3_collision_fails():
    # 用仍在 LEGACY 冻结清单中的名字(迁移后 holdertrade_net 已不在清单)
    sample = next(iter(guard.LEGACY_HANDWIRED["dsl"] | guard.LEGACY_HANDWIRED["whitelist"]
                       | guard.LEGACY_HANDWIRED["catalog"]), None)
    if sample is None:
        pytest.skip("LEGACY 已清空,C3 撞名场景无存量手工名可测")
    reg = {sample: _rec(sample)}
    errors = guard.check_name_collision(reg, {k: set(v) for k, v in guard.LEGACY_HANDWIRED.items()})
    assert any(sample in e and "撞名" in e for e in errors)


def test_c3_no_collision_passes():
    reg = {"fresh_name": _rec("fresh_name")}
    assert guard.check_name_collision(
        reg, {k: set(v) for k, v in guard.LEGACY_HANDWIRED.items()}) == []


# ── C4:死模块处置标记(双向) ────────────────────────────────────────────

def _fake_repo(tmp_path: Path, dead_text: str, consumer_text: str | None = None) -> Path:
    (tmp_path / "factors").mkdir()
    (tmp_path / "factors" / "dead.py").write_text(dead_text, encoding="utf-8")
    if consumer_text is not None:
        (tmp_path / "strategies").mkdir()
        (tmp_path / "strategies" / "user.py").write_text(consumer_text, encoding="utf-8")
    return tmp_path


def test_c4_zero_consumer_without_tag_fails(tmp_path):
    root = _fake_repo(tmp_path, '"""死模块,无标记"""\n')
    errors = guard.check_dispositions(root)
    assert any("dead.py" in e and "Disposition" in e for e in errors)


def test_c4_tag_with_consumer_fails(tmp_path):
    root = _fake_repo(tmp_path,
                      '"""x\n\nDisposition: dormant — 假死\n"""\n',
                      "from factors.dead import something\n")
    errors = guard.check_dispositions(root)
    assert any("dead.py" in e and "标记说谎" in e for e in errors)


def test_c4_honest_states_pass(tmp_path):
    root = _fake_repo(tmp_path,
                      '"""x\n\nDisposition: dormant — 真死,零消费者\n"""\n')
    (root / "factors" / "alive.py").write_text('"""活模块"""\n', encoding="utf-8")
    (root / "strategies").mkdir()
    (root / "strategies" / "user.py").write_text("from factors import alive\n", encoding="utf-8")
    assert guard.check_dispositions(root) == []


# ── 行为等价钉死:收编手工接线后 spec 逐位不变 ────────────────────────────

def test_holder_count_chg_spec_unchanged_after_migration():
    from factory.autoresearch.registry import ALLOWED_FACTORS
    spec = ALLOWED_FACTORS["holder_count_chg"]
    assert spec.params == {"window": (40, 240)}
    assert tuple(spec.data_dependencies) == ("holder/holdernumber",)

    from factors.autoresearch_dsl import _FACTOR_CALLS
    assert _FACTOR_CALLS["holder_count_chg"] == (
        "factors.shareholder", "holder_count_chg", {"window": "window"})

    from strategies.catalog import resolve_factor_builder
    assert resolve_factor_builder("holder_count_chg") is not None


def _assert_migrated_specs(expected: dict) -> None:
    """三面(whitelist params/data、DSL call、catalog builder)逐位钉死。"""
    from factors.autoresearch_dsl import _FACTOR_CALLS
    from factors.registry import discover
    from factory.autoresearch.registry import ALLOWED_FACTORS
    from strategies.catalog import resolve_factor_builder

    reg = discover()
    for name, (params, data, call) in expected.items():
        spec = ALLOWED_FACTORS[name]
        assert dict(spec.params) == params, name
        assert tuple(spec.data_dependencies) == data, name
        assert _FACTOR_CALLS[name] == call, name
        assert resolve_factor_builder(name) is not None
        rec = reg[name]
        assert rec.searchable is True
        assert rec.evidence.strip()


def test_isolated_island_specs_unchanged_after_migration():
    """holdertrade/large_order/northbound 迁 @register_factor 后三面 spec 逐位不变。"""
    _assert_migrated_specs({
        "holdertrade_net": (
            {"window": (40, 250)},
            ("holder/holdertrade",),
            ("factors.shareholder", "holdertrade_net", {"window": "window"}),
        ),
        "large_order_net_ratio": (
            {"window": (3, 60)},
            ("moneyflow",),
            ("factors.capital_flow", "large_order_net_ratio", {"window": "window"}),
        ),
        "northbound_accumulation": (
            {"window": (5, 120)},
            ("capital/northbound",),
            ("factors.northbound", "northbound_accumulation", {"window": "window"}),
        ),
        "northbound_hold_level": (
            {},
            ("capital/northbound",),
            ("factors.northbound", "northbound_hold_level", {}),
        ),
        "northbound_flow_strength": (
            {"window": (3, 20)},
            ("capital/northbound",),
            ("factors.northbound", "northbound_flow_strength", {"window": "window"}),
        ),
    })


def test_fundamental_specs_unchanged_after_migration():
    """基本面 5 因子迁 @register_factor 后三面 spec 逐位不变。"""
    _assert_migrated_specs({
        "roe": ({}, ("fundamental/roe",), ("factors.fundamental", "roe", {})),
        "net_profit_yoy": (
            {},
            ("fundamental/net_profit_yoy",),
            ("factors.fundamental", "net_profit_yoy", {}),
        ),
        "revenue_yoy": (
            {},
            ("fundamental/revenue_yoy",),
            ("factors.fundamental", "revenue_yoy", {}),
        ),
        "bp_proxy": (
            {},
            ("price/close", "fundamental/bps"),
            ("factors.fundamental", "bp_proxy", {}),
        ),
        "ep_proxy": (
            {},
            ("price/close", "fundamental/eps_ttm"),
            ("factors.fundamental", "ep_proxy", {}),
        ),
    })


def test_price_specs_unchanged_after_migration():
    """momentum/illiquidity 迁 @register_factor 后三面 spec 逐位不变。"""
    _assert_migrated_specs({
        "momentum": (
            {"window": (3, 252)},
            ("price/close",),
            ("factors.momentum", "mom_n", {"window": "n"}),
        ),
        "illiquidity": (
            {"window": (5, 120)},
            ("price/close", "price/volume", "price/amount"),
            ("factors.momentum", "illiquidity", {"window": "n"}),
        ),
    })


def test_catalog_specials_unchanged_after_migration():
    """catalog 两特殊 builder 迁注册后仍用手写实现,且 searchable=False。"""
    from factors.registry import discover
    from strategies.catalog import (
        FACTOR_BUILDERS,
        build_amihud_illiquidity,
        build_small_cap_amount,
        resolve_factor_builder,
    )

    reg = discover()
    for name in ("amihud_illiquidity", "small_cap_amount"):
        assert name in reg
        assert reg[name].searchable is False
        assert reg[name].definition.strip()
        assert resolve_factor_builder(name) is not None

    # 行为钉死:仍是迁移前的手写 builder 身份(非 auto 单 input 桩)
    assert FACTOR_BUILDERS["amihud_illiquidity"] is build_amihud_illiquidity
    assert FACTOR_BUILDERS["small_cap_amount"] is build_small_cap_amount
    # 不得进搜索白名单(catalog 纯登记,无 evidence 扩宇宙)
    from factory.autoresearch.registry import ALLOWED_FACTORS
    assert "amihud_illiquidity" not in ALLOWED_FACTORS
    assert "small_cap_amount" not in ALLOWED_FACTORS


# ── 真实仓库集成:守卫全绿 ───────────────────────────────────────────────

def test_live_repo_factor_registry_guard_passes():
    assert guard.check() == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
