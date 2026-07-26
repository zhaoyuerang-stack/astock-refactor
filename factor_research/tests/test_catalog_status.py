"""attach_catalog_status() —— 边际贡献定级(ACTIVE/SHADOW)的台账写入口。

workflow/promote.py::_run_marginal 算完 governance.marginal_alpha 的残差判决后调用本函数,
取代过去"算完只打印,人工改 portfolio/strategy_runners.py 里的字符串"的流程。
"""
import json

import pytest

import strategy_registry as sr


def _seed(tmp_path, monkeypatch, status="在册"):
    reg = tmp_path / "strategy_versions.json"
    monkeypatch.setattr(sr, "REGISTRY", reg)
    sr.register_family("toyfam", "玩具策略", hypothesis="h", regime="r", decay_signal="d")
    sr.register(
        "toyfam", "v1.0", "desc", {}, {}, {"annual": 0.3, "maxdd": -0.1},
        status=status, admission={"track": "standalone"},
        nine_gate={"dsr_p": 0.01},  # ADR-020:在册 standalone 须 DSR 显著;本测试验 catalog_status 与之正交
    )
    return reg


def test_attach_catalog_status_writes_status_and_marginal(tmp_path, monkeypatch):
    reg = _seed(tmp_path, monkeypatch)
    sr.attach_catalog_status(
        "toyfam", "v1.0", "SHADOW",
        marginal={"corr_to_book": 0.82, "residual_sharpe": 0.12, "marginal_verdict": "冗余"},
    )
    data = json.loads(reg.read_text())
    v = next(x for x in data["families"][0]["versions"] if x["version"] == "v1.0")
    assert v["catalog_status"]["status"] == "SHADOW"
    assert v["catalog_status"]["marginal"]["residual_sharpe"] == 0.12
    assert "changed_at" in v["catalog_status"]
    # 不碰准入闸
    assert v["status"] == "在册"


def test_attach_catalog_status_rejects_invalid_status(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        sr.attach_catalog_status("toyfam", "v1.0", "MAYBE")


def test_attach_catalog_status_unknown_identity_raises(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        sr.attach_catalog_status("nope", "v1.0", "ACTIVE")
    with pytest.raises(ValueError):
        sr.attach_catalog_status("toyfam", "v9.9", "ACTIVE")


def test_research_strategy_catalog_picks_up_registry_override(tmp_path, monkeypatch):
    """portfolio/strategy_runners.py::_apply_registry_catalog_status 真的会覆盖写死的 status。"""
    _seed(tmp_path, monkeypatch)
    sr.attach_catalog_status("toyfam", "v1.0", "SHADOW")

    from portfolio.strategy_runners import _apply_registry_catalog_status

    catalog = {"toyfam.v1.0": {"status": "ACTIVE", "fn": None}}
    _apply_registry_catalog_status(catalog)
    assert catalog["toyfam.v1.0"]["status"] == "SHADOW"

    # 台账里查不到的(如 ETF 跨资产腿)保留写死默认值,不被误清空
    catalog2 = {"gov_bond_etf_511010.MA60": {"status": "ACTIVE", "fn": None}}
    _apply_registry_catalog_status(catalog2)
    assert catalog2["gov_bond_etf_511010.MA60"]["status"] == "ACTIVE"
