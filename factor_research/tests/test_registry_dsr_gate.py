"""register() standalone DSR 准入门 + demote_dsr_insignificant_standalone 迁移测试(ADR-020)。

隔离:monkeypatch strategy_registry.REGISTRY → tmp 台账,经真实 _load/_save 走唯一写入口。
"""
import json

import pytest

import strategy_registry as sr


def _fresh(path):
    path.write_text(json.dumps({"families": []}, ensure_ascii=False))


def _setup(tmp_path, monkeypatch):
    reg = tmp_path / "strategy_versions.json"
    _fresh(reg)
    monkeypatch.setattr(sr, "REGISTRY", reg)
    sr.register_family("fam", "测试家族", status="active")
    return reg


HIT = {"annual": 0.25, "maxdd": 0.15}  # compute_hit → True


def test_standalone_without_dsr_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="dsr_p"):
        sr.register("fam", "v1.0", "d", {}, "scope", dict(HIT), status="在册",
                    admission={"track": "standalone"})


def test_standalone_insignificant_dsr_blocked(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="DSR p<0.05"):
        sr.register("fam", "v1.0", "d", {}, "scope", dict(HIT), status="在册",
                    admission={"track": "standalone"}, nine_gate={"dsr_p": 0.34})


def test_standalone_significant_dsr_passes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    tag = sr.register("fam", "v1.0", "d", {}, "scope", dict(HIT), status="在册",
                      admission={"track": "standalone"}, nine_gate={"dsr_p": 0.01})
    assert tag == "fam/v1.0"


def test_auto_standalone_path_also_gated(tmp_path, monkeypatch):
    # hit=True 不显式声明轨道 → 自动补 standalone,仍须过 DSR 门(堵自动补轨后门)
    _setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="dsr_p"):
        sr.register("fam", "v1.0", "d", {}, "scope", dict(HIT), status="在册")


def test_diversifier_not_gated_by_dsr(tmp_path, monkeypatch):
    # diversifier 凭组合边际入册,不受 DSR 约束(dsr_p=0.9 仍可登记)
    _setup(tmp_path, monkeypatch)
    tag = sr.register("fam", "v1.0", "d", {}, "scope", dict(HIT), status="在册",
                      admission={"track": "diversifier", "rationale": "负相关对冲"},
                      nine_gate={"dsr_p": 0.9})
    assert tag == "fam/v1.0"


def test_demote_dsr_insignificant_standalone(tmp_path, monkeypatch):
    reg = tmp_path / "strategy_versions.json"
    # 手工写入三条在册 standalone:一条显著、一条不显著、一条 DSR=None;外加一条 diversifier
    reg.write_text(json.dumps({"families": [{
        "id": "fam", "versions": [
            {"version": "ok", "status": "在册", "admission": {"track": "standalone"},
             "nine_gate": {"dsr_p": 0.01}, "metrics": {"annual": 0.3, "maxdd": 0.1}},
            {"version": "bad", "status": "在册", "admission": {"track": "standalone"},
             "nine_gate": {"dsr_p": 0.34}, "metrics": {"annual": 0.3, "maxdd": 0.1}},
            {"version": "none", "status": "在册", "admission": {"track": "standalone"},
             "nine_gate": {}, "metrics": {"annual": 0.3, "maxdd": 0.1}},
            {"version": "div", "status": "在册", "admission": {"track": "diversifier", "rationale": "x"},
             "nine_gate": {"dsr_p": 0.9}, "metrics": {"annual": 0.05, "maxdd": 0.1}},
        ]}]}, ensure_ascii=False))
    monkeypatch.setattr(sr, "REGISTRY", reg)

    ts = sr.demote_dsr_insignificant_standalone(apply=True)
    assert {t["id"] for t in ts} == {"fam/bad", "fam/none"}

    data = sr._load()
    by_ver = {v["version"]: v for v in data["families"][0]["versions"]}
    assert by_ver["ok"]["status"] == "在册"                      # 显著 standalone 保留
    assert by_ver["div"]["status"] == "在册"                     # diversifier 不动
    for ver in ("bad", "none"):
        assert by_ver[ver]["status"] == "参考"                   # 降级
        assert by_ver[ver]["admission"] == {}                    # 退出准入轨
        assert by_ver[ver]["dsr_demotion"]["from_track"] == "standalone"  # 审计块
    assert by_ver["bad"]["nine_gate"] == {"dsr_p": 0.34}         # 历史 nine_gate 原样保留
    assert by_ver["none"]["nine_gate"] == {}                     # 原本即空,保留不伪造

    # 幂等:再跑无新变更
    assert sr.demote_dsr_insignificant_standalone(apply=True) == []


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
