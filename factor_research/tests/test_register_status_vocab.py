"""register() version status 词表(守卫审计 #3)对抗测试。"""
import json

import pytest

import strategy_registry as sr


def _setup(tmp_path, monkeypatch):
    reg = tmp_path / "strategy_versions.json"
    reg.write_text(json.dumps({"families": []}, ensure_ascii=False))
    monkeypatch.setattr(sr, "REGISTRY", reg)
    sr.register_family("fam", "测试家族", status="active")
    return reg


def test_register_status_active_raises_with_zaice(tmp_path, monkeypatch):
    """register(status='active') 必 raise 且报错含「在册」。"""
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="在册") as ei:
        sr.register(
            "fam", "v1.0", "d", {}, "scope",
            {"annual": 0.1, "maxdd": 0.1}, status="active",
        )
    assert "审计 #3" in str(ei.value) or "双轨" in str(ei.value)


@pytest.mark.parametrize("syn", ["ACTIVE", "APPROVED", "registered"])
def test_register_status_english_synonyms_blocked(tmp_path, monkeypatch, syn):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="在册"):
        sr.register(
            "fam", "v1.0", "d", {}, "scope",
            {"annual": 0.1, "maxdd": 0.1}, status=syn,
        )


def test_register_status_garbage_raises(tmp_path, monkeypatch):
    """register(status='随便写的') 必 raise。"""
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="未知 version status"):
        sr.register(
            "fam", "v1.0", "d", {}, "scope",
            {"annual": 0.1, "maxdd": 0.1}, status="随便写的",
        )


def test_register_status_candidate_ok(tmp_path, monkeypatch):
    """存量合法词「候选」仍可通过。"""
    _setup(tmp_path, monkeypatch)
    tag = sr.register(
        "fam", "v1.0", "d", {}, "scope",
        {"annual": 0.1, "maxdd": 0.1}, status="候选",
    )
    assert tag == "fam/v1.0"


def test_register_family_active_untouched(tmp_path, monkeypatch):
    """register_family(status='active') 是另一字段,不碰。"""
    reg = tmp_path / "strategy_versions.json"
    reg.write_text(json.dumps({"families": []}, ensure_ascii=False))
    monkeypatch.setattr(sr, "REGISTRY", reg)
    assert sr.register_family("f2", "名", status="active") == "f2"


def test_allowed_vocab_frozen_from_ledger_plus_zaice():
    """词表 = 枚举日存量 + 在册;不含英文同义词。"""
    assert "在册" in sr.ALLOWED_VERSION_STATUS
    assert "候选" in sr.ALLOWED_VERSION_STATUS
    for syn in ("active", "ACTIVE", "APPROVED", "registered"):
        assert syn not in sr.ALLOWED_VERSION_STATUS
        assert syn in sr.VERSION_STATUS_SYNONYMS_BLOCKED


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
