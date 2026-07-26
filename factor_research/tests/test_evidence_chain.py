"""证据链闭合测试 — registry 版本须锚定 hypothesis_id + L0-L3 实验 ID。

Run:  cd /Users/kiki/astcok/factor_research && python3 tests/test_evidence_chain.py

覆盖:
1. register() 把 evidence 写进版本记录(向后兼容:省略 → 空 dict)。
2. Phase4Register.register() 把 hypothesis_id / evidence_experiment_ids 透传进台账。

全程用临时台账文件,绝不写脏真实 strategy_versions.json。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import strategy_registry as reg
from workflow.phase4_register import Phase4Register


def _with_tmp_registry(fn):
    """把 reg.REGISTRY 临时重定向到空文件,跑完恢复。"""
    orig = reg.REGISTRY
    reg.REGISTRY = Path(tempfile.mktemp(suffix=".json"))
    try:
        fn()
    finally:
        reg.REGISTRY = orig


def _versions(family_id):
    fam = next(f for f in reg._load()["families"] if f["id"] == family_id)
    return fam["versions"]


def test_register_persists_evidence():
    def body():
        reg.register_family("fam-a", "测试家族")
        reg.register(
            "fam-a", "v1.0", "desc", config={}, data_scope={}, metrics={},
            evidence={"hypothesis_id": "abc12345def", "experiment_ids": ["e1", "e2"]},
        )
        v = _versions("fam-a")[0]
        assert v["evidence"]["hypothesis_id"] == "abc12345def"
        assert v["evidence"]["experiment_ids"] == ["e1", "e2"]
    _with_tmp_registry(body)
    print("✅ test_register_persists_evidence passed")


def test_register_evidence_defaults_empty():
    def body():
        reg.register_family("fam-b", "测试家族")
        reg.register("fam-b", "v1.0", "desc", config={}, data_scope={}, metrics={})
        assert _versions("fam-b")[0]["evidence"] == {}
    _with_tmp_registry(body)
    print("✅ test_register_evidence_defaults_empty passed")


def test_phase4_threads_evidence_into_registry():
    """Phase4Register.register() 把 hypothesis_id + 实验 ID 透传进台账。"""
    def body():
        p1 = []  # 无 phase1 check → 不 blocked
        p2 = {
            "config": {"top_n": 25},
            "segments": {
                "IS  2018-2022": {"annual": 0.20, "maxdd": -0.10, "sharpe": 1.5, "calmar": 2.0},
                "OOS 2023-2026": {"annual": 0.18, "maxdd": -0.12, "sharpe": 1.3},
                "压力 2010-2017": {"annual": 0.15, "maxdd": -0.15, "sharpe": 1.0},
            },
        }
        p3 = {"aggregate": {"verdict": "PASS", "annual": 0.19, "maxdd": -0.11, "sharpe": 1.4}}

        report = Phase4Register("fam-c", "v1.0").register(
            p1, p2, p3,
            hypothesis="测试假设",
            hypothesis_id="hyp9999aaaa",
            evidence_experiment_ids=["exp_l0", "exp_l1", "exp_l3"],
        )
        assert report.registered, f"应登记成功: {report.detail}"
        v = _versions("fam-c")[0]
        assert v["evidence"]["hypothesis_id"] == "hyp9999aaaa"
        assert v["evidence"]["experiment_ids"] == ["exp_l0", "exp_l1", "exp_l3"]
    _with_tmp_registry(body)
    print("✅ test_phase4_threads_evidence_into_registry passed")


def _passing_phase2(config=None):
    return {
        "config": dict(config or {"top_n": 25}),
        "segments": {
            "IS  2018-2022": {"annual": 0.20, "maxdd": -0.10, "sharpe": 1.5, "calmar": 2.0},
            "OOS 2023-2026": {"annual": 0.18, "maxdd": -0.12, "sharpe": 1.3},
            "压力 2010-2017": {"annual": 0.15, "maxdd": -0.15, "sharpe": 1.0},
        },
    }


def _passing_phase3():
    return {"aggregate": {"verdict": "PASS", "annual": 0.19, "maxdd": -0.11, "sharpe": 1.4}}


def test_phase4_blocks_offset_sensitivity_fail():
    """调仓偏移扰动失败时,Phase4 必须拒绝登记。"""
    def body():
        p2 = _passing_phase2()
        p2["offset_sensitivity"] = {"verdict": "FAIL"}
        report = Phase4Register("fam-offset", "v1.0").register([], p2, _passing_phase3(), hypothesis="test")
        assert report.registered is False
        assert "offset sensitivity FAIL" in report.detail
    _with_tmp_registry(body)
    print("✅ test_phase4_blocks_offset_sensitivity_fail passed")


def test_phase4_blocks_rebalance_slower_than_factor_half_life():
    """短窗口因子不能靠过慢调仓规避滑点/噪声。"""
    def body():
        p2 = _passing_phase2({
            "top_n": 25,
            "ast": {
                "type": "linear_combo",
                "terms": [{"factor": "momentum", "params": {"window": 5}, "weight": 1.0}],
                "execution": {"portfolio_size": 25, "rebalance_freq": "15D"},
            },
        })
        report = Phase4Register("fam-half-life", "v1.0").register([], p2, _passing_phase3(), hypothesis="test")
        assert report.registered is False
        assert "rebalance_days=15" in report.detail
        assert "max window=5" in report.detail
    _with_tmp_registry(body)
    print("✅ test_phase4_blocks_rebalance_slower_than_factor_half_life passed")


def test_phase4_persists_executable_spec_from_ast_execution():
    """Phase4 应把 AST execution 编成 ExecutableStrategySpec 并写入台账。"""
    def body():
        p2 = _passing_phase2({
            "top_n": 99,
            "data_dependencies": ["holder/holdernumber", "price/close"],
            "factor_fn_name": "factors.autoresearch_dsl.compute_dsl_factor",
            "factor_params": {"ast": {"type": "linear_combo", "terms": []}},
            "ast": {
                "type": "linear_combo",
                "terms": [{"factor": "holder_count_chg", "params": {"window": 20}, "weight": 1.0}],
                "execution": {"portfolio_size": 35, "rebalance_freq": "30D", "smoothing_window": 10},
            },
        })
        report = Phase4Register("fam-spec", "v1.0").register([], p2, _passing_phase3(), hypothesis="test")
        assert report.registered, f"应登记成功: {report.detail}"
        v = _versions("fam-spec")[0]
        executable = v["executable_spec"]
        assert len(executable["spec_hash"]) == 64
        spec = executable["spec"]
        assert spec["selection"]["top_n"] == 35
        assert spec["selection"]["rebalance_days"] == 30
        assert spec["execution"]["smoothing_window"] == 10
        assert spec["data"]["dependencies"] == ["holder/holdernumber", "price/close"]
    _with_tmp_registry(body)
    print("✅ test_phase4_persists_executable_spec_from_ast_execution passed")


if __name__ == "__main__":
    print("Running evidence chain tests...\n")
    test_register_persists_evidence()
    test_register_evidence_defaults_empty()
    test_phase4_threads_evidence_into_registry()
    test_phase4_blocks_offset_sensitivity_fail()
    test_phase4_blocks_rebalance_slower_than_factor_half_life()
    test_phase4_persists_executable_spec_from_ast_execution()
    print("\n🎉 All evidence chain tests passed!")
