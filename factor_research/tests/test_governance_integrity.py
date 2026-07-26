"""治理完整性单测：hit 唯一权威 / 双轨准入 / 审批映射 / ledger hash-chain / Nine-Gate 摘要 / 模型卡持久化。

覆盖本轮 P0+P1 整改的全部新机制；全部用临时路径，绝不触碰 live 台账/账本/清单。
运行：python3 tests/test_governance_integrity.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _restore_registry_path():
        """hermetic:每个测试结束后恢复 R.REGISTRY,避免临时台账污染后续测试文件
        (全量 pytest 发现下,本文件多处把 R.REGISTRY 指向 tmp;不恢复会污染 risk_phase3 等)。"""
        import strategy_registry as R
        saved = R.REGISTRY
        try:
            yield
        finally:
            R.REGISTRY = saved
except ImportError:
    pass  # 作为 __main__ 脚本直跑时无 pytest;独立进程不会污染他人


# ── A1: compute_hit 唯一权威（严格不等号）+ 机构级指标 ──────────────────────────
def test_compute_hit_strict_boundaries():
    from engine.metrics import compute_hit
    assert compute_hit(0.20, -0.10) is True            # 双双达标
    assert compute_hit(0.15, -0.10) is False           # 年化恰好 15% → 严格不达标
    assert compute_hit(0.16, -0.20) is False           # 回撤恰好 20% → 严格不达标
    assert compute_hit(0.16, -0.199) is True
    assert compute_hit(None, -0.10) is False           # 缺失安全
    # 阈值可被 config 覆盖，但语义恒严格
    assert compute_hit(0.30, -0.25, target_annual=0.28, target_maxdd=0.30) is True
    print("✅ test_compute_hit_strict_boundaries")


def test_institutional_metrics_keys():
    from engine.metrics import institutional_metrics, metrics
    rng = np.random.default_rng(7)
    ret = pd.Series(rng.normal(0.0008, 0.012, 300))
    im = institutional_metrics(ret)
    for k in ("sortino", "var_95", "cvar_95", "skew", "kurtosis_excess", "tail_ratio"):
        assert k in im, f"缺指标 {k}"
    # 带基准 → 相对指标出现
    bench = pd.Series(rng.normal(0.0004, 0.010, 300))
    imb = institutional_metrics(ret, bench=bench)
    for k in ("info_ratio", "tracking_error", "beta", "alpha_annual", "excess_annual"):
        assert k in imb, f"缺基准相对指标 {k}"
    # metrics() 合并后仍含核心字段且 hit 为 compute_hit 结果
    full = metrics(ret)
    assert {"annual", "vol", "sharpe", "maxdd", "calmar", "hit", "n"} <= set(full)
    assert "sortino" in full
    print("✅ test_institutional_metrics_keys")


# ── A2: register 自动重算 hit + 双轨准入闸门 ─────────────────────────────────────
def _fresh_registry():
    import strategy_registry as R
    tmp = Path(tempfile.mkdtemp()) / "tv.json"
    R.REGISTRY = tmp
    return R


def test_register_recomputes_hit_blocks_handfill():
    R = _fresh_registry()
    R.register_family("famx", "测试族")
    # 手填 hit=True 但单体不达标 → 必须被公式覆盖为 False（候选态不触发闸门）
    R.register("famx", "v1", "d", {}, {"source": "data_lake"},
               {"annual": 0.05, "maxdd": -0.10, "hit": True}, status="候选")
    data = R._load()
    v = data["families"][0]["versions"][0]
    assert v["metrics"]["hit"] is False, "hit 未被公式覆盖（记分牌可被手填）"
    print("✅ test_register_recomputes_hit_blocks_handfill")


def test_register_admission_gate():
    R = _fresh_registry()
    R.register_family("famy", "测试族")
    # 1) 单体达标 + DSR 显著(dsr_p<0.05) → 自动 standalone 在册（ADR-020:hit 已不够）
    R.register("famy", "v-pass", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册",
               nine_gate={"dsr_p": 0.01})
    v = next(x for x in R._load()["families"][0]["versions"] if x["version"] == "v-pass")
    assert v["admission"]["track"] == "standalone" and v["metrics"]["hit"] is True

    # 1b) 单体达标但无 dsr_p → standalone 自动补轨仍被 DSR 门拦（堵自动补轨后门）
    raised = False
    try:
        R.register("famy", "v-nodsr", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册")
    except ValueError:
        raised = True
    assert raised, "hit 达标但无 DSR 仍裸入 standalone（DSR 门失效）"

    # 1c) 单体达标但 DSR 不显著(>=0.05) → 抛错
    raised = False
    try:
        R.register("famy", "v-dsrfail", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册",
                   admission={"track": "standalone"}, nine_gate={"dsr_p": 0.34})
    except ValueError:
        raised = True
    assert raised, "DSR>=0.05 仍能入 standalone（多重测试惩罚门失效）"

    # 2) 单体不达标 + 无 admission + 在册 → 抛错
    raised = False
    try:
        R.register("famy", "v-bad", "d", {}, {}, {"annual": 0.05, "maxdd": -0.10}, status="在册")
    except ValueError:
        raised = True
    assert raised, "不达标却能裸入在册（闸门失效）"

    # 3) 不达标 + diversifier(有 rationale) + 在册 → 通过
    R.register("famy", "v-div", "d", {}, {}, {"annual": 0.05, "maxdd": -0.10}, status="在册",
               admission={"track": "diversifier", "rationale": "与主力负相关，组合层增量"})
    v = next(x for x in R._load()["families"][0]["versions"] if x["version"] == "v-div")
    assert v["admission"]["track"] == "diversifier"

    # 4) diversifier 但 rationale 空 → 抛错
    raised = False
    try:
        R.register("famy", "v-div2", "d", {}, {}, {"annual": 0.05, "maxdd": -0.10}, status="在册",
                   admission={"track": "diversifier", "rationale": "  "})
    except ValueError:
        raised = True
    assert raised, "空 rationale 的 diversifier 不应通过"
    print("✅ test_register_admission_gate")


def test_migrate_two_track_idempotent_and_invariant():
    R = _fresh_registry()
    R.DIVERSIFIER_FAMILIES = {"divfam"}
    # 直接构造一个含违规态的台账（绕过 register 闸门，模拟历史脏数据），经 _save 落临时盘
    data = {"families": [
        {"id": "stdfam", "versions": [
            {"version": "v1", "status": "在册", "metrics": {"annual": 0.30, "maxdd": -0.10, "hit": False}},
            {"version": "v2", "status": "在册", "metrics": {"annual": 0.05, "maxdd": -0.40, "hit": True}},  # 假 hit
        ]},
        {"id": "divfam", "versions": [
            {"version": "v1", "status": "在册", "metrics": {"annual": 0.03, "maxdd": -0.11, "hit": True}},  # 假 hit，对冲族
        ]},
    ]}
    R.REGISTRY.write_text(json.dumps(data, ensure_ascii=False))

    R.migrate_two_track_admission(apply=True)
    d1 = R._load()

    def reg_versions(d):
        return [(f["id"], v["version"], (v.get("admission") or {}).get("track"),
                 (v.get("metrics") or {}).get("hit"))
                for f in d["families"] for v in f["versions"] if v["status"] == "在册"]

    regs = reg_versions(d1)
    # 不变量：每个在册要么 standalone+hit，要么 diversifier+rationale
    for fid, ver, track, hit in regs:
        if track == "standalone":
            assert hit is True, f"{fid}/{ver} standalone 却 hit!=True"
        elif track == "diversifier":
            v = next(x for f in d1["families"] if f["id"] == fid
                     for x in f["versions"] if x["version"] == ver)
            assert (v["admission"].get("rationale") or "").strip(), "diversifier 缺 rationale"
        else:
            raise AssertionError(f"{fid}/{ver} 在册却无合法 admission 轨")
    # stdfam/v1 应保留 standalone（真达标），stdfam/v2 应被降级（假 hit、非对冲），divfam/v1 → diversifier
    assert ("stdfam", "v1", "standalone", True) in regs
    assert ("divfam", "v1", "diversifier", False) in regs
    assert not any(fid == "stdfam" and ver == "v2" for fid, ver, _, _ in regs), "假 hit 非对冲版本未被降级"

    # 幂等：再跑一次无状态变更
    t2 = R.migrate_two_track_admission(apply=True)
    changed = [t for t in t2 if t["new_status"] != t["old_status"]]
    assert not changed, f"迁移非幂等，二次仍有变更: {changed}"
    print("✅ test_migrate_two_track_idempotent_and_invariant")


# ── A4: 审批映射 ────────────────────────────────────────────────────────────────
def test_approval_mapping():
    from services.read.governance import _approval_from_status
    assert _approval_from_status("在册") == "APPROVED"
    assert _approval_from_status("候选") == "PENDING"
    for s in ("参考", "退役", "已证伪", None, "active", "LIVE"):
        assert _approval_from_status(s) == "REJECTED", f"{s} 误判"
    print("✅ test_approval_mapping")


# ── B3: ledger hash-chain ───────────────────────────────────────────────────────
def _mk_entry(eid, sharpe):
    from research_ledger.ledger import LedgerEntry
    return LedgerEntry(
        experiment_id=eid, parent_experiment_id=None, hypothesis_text="h",
        llm_prompt_hash=None, factor_ast_hash="ast", code_commit_hash="c",
        data_snapshot_hash="d", universe_version="u", cost_model_version="v",
        random_seed=1, tried_parameters={}, result_metrics={"sharpe": sharpe},
        rejection_reason=None, reviewer="t", run_at="2026-06-18",
    )


def test_ledger_hash_chain_detects_tamper():
    from research_ledger.ledger import ResearchLedger
    tmp = Path(tempfile.mkdtemp()) / "led.jsonl"
    L = ResearchLedger(path=tmp)
    for i in range(3):
        L.log_experiment(_mk_entry(f"E{i}", 1.0 + i))
    ok, probs = L.verify_chain()
    assert ok, f"干净链却报错: {probs}"
    # 链接性：第二条 prev_hash == 第一条 entry_hash
    lines = [json.loads(x) for x in tmp.read_text().splitlines()]
    assert lines[1]["prev_hash"] == lines[0]["entry_hash"]
    # 篡改中间内容 → 检出
    lines[1]["result_metrics"] = {"sharpe": 99.9}
    tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
    ok2, probs2 = L.verify_chain()
    assert ok2 is False and any("篡改" in p for p in probs2), f"未检出篡改: {probs2}"
    print("✅ test_ledger_hash_chain_detects_tamper")


def test_ledger_migrate_legacy():
    from research_ledger.ledger import ResearchLedger
    tmp = Path(tempfile.mkdtemp()) / "led.jsonl"
    # 写两条无 hash 的遗留行
    legacy = [{"experiment_id": "L0", "parent_experiment_id": None, "hypothesis_text": "x",
               "llm_prompt_hash": None, "factor_ast_hash": "a", "code_commit_hash": "c",
               "data_snapshot_hash": "d", "universe_version": "u", "cost_model_version": "v",
               "random_seed": 1, "tried_parameters": {}, "result_metrics": {}, "rejection_reason": None,
               "reviewer": "t", "run_at": "2026-01-01", "notes": ""}]
    legacy.append({**legacy[0], "experiment_id": "L1"})
    tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in legacy) + "\n")
    L = ResearchLedger(path=tmp)
    n = L.migrate_chain()
    assert n == 2
    ok, probs = L.verify_chain()
    assert ok, f"回填后仍报错: {probs}"
    print("✅ test_ledger_migrate_legacy")


# ── B1: Nine-Gate 摘要抽取 + attach 到台账 ───────────────────────────────────────
def test_nine_gate_summarize_and_attach():
    from core.analysis.nine_gates import GateReport, NineGatesReport
    reports = [
        GateReport(4, "Multiple Testing", True, "PASS",
                   {"dsr": 1.2, "dsr_p_value": 0.01, "dsr_significant": True, "psr": 0.97,
                    "n_trials": 15, "skew": -0.1, "kurt": 4.0}, "d"),
        GateReport(7, "Stress", True, "PASS", {"wf_sharpe": 0.8, "wf_positive_ratio": 0.7}, "d"),
    ]
    rep = NineGatesReport("famz_v1", "2026-06-18", True, reports)
    s = rep.summarize()
    assert s["dsr_p"] == 0.01 and s["psr"] == 0.97 and s["n_trials"] == 15 and s["wf_sharpe"] == 0.8

    R = _fresh_registry()
    R.register_family("famz", "测试族")
    R.register("famz", "v1", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册",
               nine_gate={"dsr_p": 0.01})  # ADR-020:standalone 在册须 DSR 显著
    R.attach_nine_gate("famz", "v1", s, evidence={"hypothesis_id": "H1", "experiment_ids": ["E1", "E2"]})
    v = R._load()["families"][0]["versions"][0]
    assert v["nine_gate"]["dsr_p"] == 0.01
    assert v["evidence"]["experiment_ids"] == ["E1", "E2"]
    print("✅ test_nine_gate_summarize_and_attach")


# ── B2: 模型卡持久化 ────────────────────────────────────────────────────────────
def test_model_card_sync_persists_real_cards():
    import services.read.governance as G
    import strategy_registry as R
    from model_risk.model_inventory import ModelInventory
    # 临时台账：一个在册（应 APPROVED）+ 一个候选（PENDING）
    tmp_reg = Path(tempfile.mkdtemp()) / "tv.json"
    R.REGISTRY = tmp_reg
    R.register_family("fcard", "卡测试族", hypothesis="假设H", regime="R", decay_signal="D")
    R.register("fcard", "v1", "d", {}, {"source": "data_lake", "period": "2023-2026"},
               {"annual": 0.30, "maxdd": -0.10}, status="在册", nine_gate={"dsr_p": 0.01})
    R.register("fcard", "v2", "d", {}, {}, {"annual": 0.05, "maxdd": -0.10}, status="候选")
    # 临时模型清单（依赖注入，避免污染真实清单）
    tmp_inv = Path(tempfile.mkdtemp()) / "inv.json"
    inv = ModelInventory(path=tmp_inv)
    n = G.sync_model_cards_from_registry(inventory=inv)
    assert n == 2
    inv2 = ModelInventory(path=tmp_inv)   # 重新加载，确认已落盘
    c1 = inv2.get_card("fcard/v1")
    c2 = inv2.get_card("fcard/v2")
    assert c1.approval_status == "APPROVED" and c2.approval_status == "PENDING"
    assert c1.metadata.get("admission_track") == "standalone"
    print("✅ test_model_card_sync_persists_real_cards")


# ── W2: 决策闸门 get_strategy_gate_status(供 trade-readiness 消费) ──────────────
def test_strategy_gate_status_consumes_dsr():
    import strategy_registry as R
    from services.read.governance import get_strategy_gate_status
    old_registry = R.REGISTRY  # hermetic:跑完恢复,避免污染后续测试(全量 pytest 发现下必要)
    try:
        tmp = Path(tempfile.mkdtemp()) / "tv.json"
        R.REGISTRY = tmp
        R.register_family("gfam", "闸门族")
        # 本测试验的是 get_strategy_gate_status 读取器(track 无关:registered 只看 status=在册,
        # dsr 来自 nine_gate)。用 diversifier 轨入册以绕开 standalone DSR 门(ADR-020),
        # 不影响读取器三态(DSR失败/未审计/DSR通过)的验证。
        DIV = {"track": "diversifier", "rationale": "组合层增量"}
        # 在册,带 DSR 失败的 nine_gate
        R.register("gfam", "v1", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册", admission=DIV)
        # Task 9: 审批只认 passed_all。完整门未过 → 闸门识别为未通过(不再靠 DSR-only/gate4 推断)
        R.attach_nine_gate("gfam", "v1", {"passed_all": False, "dsr_p": 0.40, "dsr_significant": False})
        g = get_strategy_gate_status("gfam", "v1")
        assert g["registered"] and g["dsr_audited"] and g["dsr_passed"] is False, "完整门失败未被闸门识别"
        # 未审计版本:dsr_audited=False、dsr_passed=None(不应误判失败)
        R.register("gfam", "v2", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册", admission=DIV)
        g2 = get_strategy_gate_status("gfam", "v2")
        assert g2["registered"] and g2["dsr_audited"] is False and g2["dsr_passed"] is None
        # DSR 通过
        R.register("gfam", "v3", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册", admission=DIV)
        R.attach_nine_gate("gfam", "v3", {"passed_all": True, "dsr_p": 0.01, "dsr_significant": True})
        assert get_strategy_gate_status("gfam", "v3")["dsr_passed"] is True
    finally:
        R.REGISTRY = old_registry
    print("✅ test_strategy_gate_status_consumes_dsr")


def test_trade_readiness_requires_nine_gate_pass():
    import app_config.settings as C
    import strategy_registry as R
    from app_config.settings import Settings, StrategyConfig
    from services.read import trade_readiness as TR

    old_registry = R.REGISTRY
    old_settings = C._SETTINGS
    old_data_quality = TR.data_quality
    old_risk_report = TR.risk_report
    old_production_readiness = TR.get_production_readiness
    old_factor_health = TR._factor_health_from_decay
    try:
        # factor_health 现读真实 decay 报告;本测试只验 model_version 闸,故固定为 normal 隔离。
        TR._factor_health_from_decay = lambda: ("normal", {})
        R.REGISTRY = Path(tempfile.mkdtemp()) / "tv.json"
        R.register_family("readyfam", "准备度测试族")
        # diversifier 轨入册以绕开 standalone DSR 门(ADR-020);本测试验 trade-readiness 对
        # DSR 待审/失败/通过三态的处置,与准入轨无关(读取器只看 nine_gate 状态)。
        R.register("readyfam", "v1", "d", {}, {}, {"annual": 0.30, "maxdd": -0.10}, status="在册",
                   admission={"track": "diversifier", "rationale": "组合层增量"})

        C._SETTINGS = Settings(strategy=StrategyConfig(family="readyfam", version="v1"))
        TR.data_quality = lambda with_duckdb=False: SimpleNamespace(verdict="可用")
        TR.risk_report = lambda: SimpleNamespace(verdict="正常")
        TR.get_production_readiness = lambda governance_status=None: SimpleNamespace(
            allowed=True,
            model_dump=lambda: {"allowed": True},
        )

        pending = TR.get_trade_readiness()
        assert pending.model_version == "dsr_pending"
        assert pending.allowed_to_trade is False
        assert pending.human_approval_required is True

        R.attach_nine_gate("readyfam", "v1", {"status": "FAILED_TO_RUN", "error": "boom"})
        failed = TR.get_trade_readiness()
        assert failed.model_version == "nine_gate_failed"
        assert failed.allowed_to_trade is False

        R.attach_nine_gate("readyfam", "v1", {"passed_all": True, "dsr_p": 0.01, "dsr_significant": True})
        passed = TR.get_trade_readiness()
        assert passed.model_version == "approved"
        assert passed.allowed_to_trade is True
    finally:
        R.REGISTRY = old_registry
        C._SETTINGS = old_settings
        TR.data_quality = old_data_quality
        TR.risk_report = old_risk_report
        TR.get_production_readiness = old_production_readiness
        TR._factor_health_from_decay = old_factor_health
    print("✅ test_trade_readiness_requires_nine_gate_pass")


if __name__ == "__main__":
    test_compute_hit_strict_boundaries()
    test_institutional_metrics_keys()
    test_register_recomputes_hit_blocks_handfill()
    test_register_admission_gate()
    test_migrate_two_track_idempotent_and_invariant()
    test_approval_mapping()
    test_ledger_hash_chain_detects_tamper()
    test_ledger_migrate_legacy()
    test_nine_gate_summarize_and_attach()
    test_model_card_sync_persists_real_cards()
    test_strategy_gate_status_consumes_dsr()
    test_trade_readiness_requires_nine_gate_pass()
    print("\n🎉 治理完整性测试全部通过！")
