"""系统真相层:declared ≠ verified ≠ production_allowed,且声明态在 fail-closed 下仍可见。

核心被测性质:即便部署 fail-closed,``read_declared_manifest`` 仍能读出「清单声称在跑什么」,
``diagnose_leg`` 给出逐项证据与阻断根因——这正是「manifest 看着像 live、实则被拦」误读的解药。
"""
import json

import pytest

from contracts.views import SystemTruthView
from runtime.deployment import (
    DeploymentNotReady,
    diagnose_leg,
    load_active_deployment,
    read_declared_manifest,
)
from services.read.system_truth import get_system_truth

GOOD_HASH = "a" * 64
OTHER_HASH = "b" * 64


def _manifest(tmp_path, status="active"):
    p = tmp_path / "deploy.json"
    p.write_text(json.dumps({
        "deployment_id": "test-prod", "environment": "production", "status": status,
        "portfolio_policy": {"type": "regime_rotation"},
        "legs": [{"family": "illiquidity", "version": "v3.1",
                  "spec_hash": GOOD_HASH, "role": "equity_alpha"}],
    }), encoding="utf-8")
    return p


def _registry(status="在册", spec_hash=GOOD_HASH):
    def lookup(family, version):
        if (family, version) != ("illiquidity", "v3.1"):
            return None
        rec = {"version": version, "status": status}
        if spec_hash is not None:
            rec["executable_spec"] = {"spec_hash": spec_hash}
        return rec
    return lookup


# ---- read_declared_manifest:声明态不做 fail-closed ----

def test_declared_manifest_visible_even_when_demoted(tmp_path):
    """注册表把 v3.1 降为参考(部署会 fail-closed),但声明态仍能读出清单内容。"""
    path = _manifest(tmp_path)
    # 部署校验失败
    with pytest.raises(DeploymentNotReady):
        load_active_deployment(path, registry_lookup=_registry(status="参考"))
    # 声明态照样可见 —— 这是「declared ≠ verified」的关键
    declared = read_declared_manifest(path)
    assert declared is not None
    assert declared["status"] == "active"
    assert declared["legs"][0]["family"] == "illiquidity"
    assert declared["legs"][0]["version"] == "v3.1"


def test_declared_manifest_missing_returns_none(tmp_path):
    assert read_declared_manifest(tmp_path / "nope.json") is None


# ---- diagnose_leg:非抛出式证据,与 _validate_leg 同源判定 ----

def test_diagnose_good_leg_has_no_block(tmp_path):
    diag = diagnose_leg(
        {"family": "illiquidity", "version": "v3.1", "spec_hash": GOOD_HASH, "role": "equity_alpha"},
        registry_lookup=_registry(),
    )
    assert diag["blocking_reason"] == ""
    assert diag["status_deployable"] is True
    assert diag["spec_hash_match"] is True


def test_diagnose_demoted_status_blocks_without_raising(tmp_path):
    diag = diagnose_leg(
        {"family": "illiquidity", "version": "v3.1", "spec_hash": GOOD_HASH, "role": "equity_alpha"},
        registry_lookup=_registry(status="参考"),
    )
    assert diag["registry_found"] is True
    assert diag["registry_status"] == "参考"
    assert diag["status_deployable"] is False
    assert diag["blocking_reason"]  # 非空


def test_diagnose_spec_hash_mismatch(tmp_path):
    diag = diagnose_leg(
        {"family": "illiquidity", "version": "v3.1", "spec_hash": GOOD_HASH, "role": "equity_alpha"},
        registry_lookup=_registry(spec_hash=OTHER_HASH),
    )
    assert diag["spec_hash_match"] is False
    assert diag["declared_spec_hash"] == GOOD_HASH
    assert diag["registry_spec_hash"] == OTHER_HASH
    assert "spec_hash 不匹配" in diag["blocking_reason"]


def test_diagnose_unregistered_leg(tmp_path):
    diag = diagnose_leg(
        {"family": "ghost", "version": "v9", "spec_hash": GOOD_HASH, "role": "equity_alpha"},
        registry_lookup=_registry(),
    )
    assert diag["registry_found"] is False
    assert diag["blocking_reason"]


# ---- get_system_truth:整合视图结构不变式(对真实默认清单/注册表) ----

def test_get_system_truth_structure_and_invariants():
    st = get_system_truth()
    assert isinstance(st, SystemTruthView)
    assert isinstance(st.production_allowed, bool)
    # 证据链与声明腿一一对应
    assert len(st.evidence_chain) == len(st.declared_legs)
    # declared ≠ verified 的核心不变式:有声明但未验证 ⇒ 必有 fail-closed 根因
    if st.declared_present and not st.verified:
        assert st.verify_error, "未验证却没有给出 fail-closed 根因"
    # 已验证 ⇒ 一定允许有 verified 身份;真相源齐全
    assert "deployment" in st.truth_sources and "registry" in st.truth_sources
    # readiness 内嵌既有闸门(同源,不另算)
    assert "allowed" in st.readiness


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
