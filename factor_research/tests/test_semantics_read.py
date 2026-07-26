"""对抗测试:services.read.semantics 语义卡片只读聚合门面。

覆盖:
1. 未知实体 KeyError(fail-closed)
2. PULL 活引用(临时注入 FactorRecord)
3. 30 个数据集 contract PULL 自声明表(contract_missing 全 False + timeline 合法)
4. 只读(manifest sha256 前后不变)
5. 与 list_strategies() 字段一致
6. 真实数据冒烟(三类计数 > 0)

Run:
    cd factor_research && python3 -m pytest tests/test_semantics_read.py -q
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.registry import FACTOR_REGISTRY, FactorRecord, discover  # noqa: E402
from lake.schema import TIMELINE_MODES_WITH_NA  # noqa: E402
from services.read import semantics  # noqa: E402
from services.read.registry import list_strategies  # noqa: E402


def test_unknown_entities_raise_keyerror():
    with pytest.raises(KeyError):
        semantics.factor_card("不存在的因子")
    with pytest.raises(KeyError):
        semantics.dataset_card("不存在的数据集_xyz_nope")
    with pytest.raises(KeyError):
        semantics.strategy_card("不存在的family_xyz")
    with pytest.raises(KeyError):
        semantics.strategy_card("不存在的family_xyz", version="v9.9")


def test_factor_card_pulls_live_registry_not_snapshot():
    """临时注入假 FactorRecord,factor_card 立刻反映 definition —— 活引用非快照。"""
    discover()
    fake_name = "__test_semantics_fake_factor__"
    assert fake_name not in FACTOR_REGISTRY
    FACTOR_REGISTRY[fake_name] = FactorRecord(
        name=fake_name,
        fn=lambda x: x,
        definition="测试口径XYZ",
        params={},
        data=("price/close",),
        input="close",
        searchable=False,
        evidence="",
        source_hash="deadbeef0000",
    )
    try:
        card = semantics.factor_card(fake_name)
        assert card.definition == "测试口径XYZ"
        assert card.name == fake_name
        assert card.source_hash == "deadbeef0000"
    finally:
        FACTOR_REGISTRY.pop(fake_name, None)
    with pytest.raises(KeyError):
        semantics.factor_card(fake_name)


def test_dataset_card_contract_pulled_from_schema_for_all_30():
    """30 个数据集 contract_missing 全 False,timeline ∈ 合法词表;契约来自声明表非编造。"""
    inv = semantics.list_semantic_entities()
    assert inv.n_datasets == 30, f"expected 30 datasets, got {inv.n_datasets}: {inv.datasets}"
    invented_markers = ("默认口径", "暂无说明", "TODO contract", "placeholder")
    for name in inv.datasets:
        card = semantics.dataset_card(name)
        assert card.contract_missing is False, f"{name} contract_missing"
        assert isinstance(card.contract, dict)
        assert card.contract.get("declared_in") == "lake/schema.py"
        timeline = card.contract.get("timeline")
        assert timeline in TIMELINE_MODES_WITH_NA, f"{name} bad timeline {timeline!r}"
        assert "store" in card.contract
        assert "fields" in card.contract
        assert "kind" in card.contract
        serialized = json.dumps(card.model_dump(), ensure_ascii=False)
        for marker in invented_markers:
            assert marker not in serialized


def test_dataset_card_is_read_only_on_manifest():
    """对 _manifest.json 调用前后 sha256 不变。"""
    manifest_path = ROOT / "data_lake" / "_manifest.json"
    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    # 触发读取
    entries = [
        k for k in json.loads(manifest_path.read_text(encoding="utf-8"))
        if not str(k).startswith("_")
    ]
    assert entries
    semantics.dataset_card(entries[0])
    semantics.list_semantic_entities()
    after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert before == after


def test_strategy_card_matches_list_strategies():
    rows = list_strategies()
    assert rows, "registry must have at least one strategy for consistency check"
    ref = rows[0]
    card = semantics.strategy_card(ref.family, ref.version)
    assert card.family == ref.family
    assert card.version == ref.version
    assert card.hypothesis == ref.hypothesis
    assert card.regime == ref.regime
    assert card.decay_signal == (ref.decay_signal or "")
    assert card.status == ref.status
    expected_present = bool(ref.nine_gate) and len(ref.nine_gate) > 0
    assert card.nine_gate_present is expected_present
    # version=None → 该 family 第一个 version(与 list_strategies 顺序一致)
    first_of_family = next(r for r in rows if r.family == ref.family)
    card_default = semantics.strategy_card(ref.family)
    assert card_default.version == first_of_family.version
    assert card_default.hypothesis == first_of_family.hypothesis
    assert card_default.regime == first_of_family.regime


def test_list_semantic_entities_smoke_real_data():
    inv = semantics.list_semantic_entities()
    assert inv.n_factors > 0
    assert inv.n_datasets > 0
    assert inv.n_strategies > 0
    assert len(inv.factors) == inv.n_factors
    assert len(inv.datasets) == inv.n_datasets
    assert len(inv.strategies) == inv.n_strategies
    # factors 来自 discover
    discovered = set(discover().keys())
    assert set(inv.factors) == discovered
    # strategies 形如 family/version
    assert all("/" in s for s in inv.strategies)
