"""strategy_registry.attach_path_metrics 的对抗性测试(R-REG-001 唯一写入口)。

来源:移植自协作 grok 分支的 strategy-path-analysis skill 机制层(见
docs/agent_skills 与 .claude/skills/strategy-path-analysis)——该函数是
"live re-run 结果诚实写回台账"的唯一入口,本测试独立验证其铁律,不依赖来源分支。

全部测试用 tmp_path 假台账,绝不触碰真实 strategy_versions.json。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy_registry as sr  # noqa: E402


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    fp = tmp_path / "strategy_versions.json"
    monkeypatch.setattr(sr, "REGISTRY", fp)
    return fp


def _seed(tmp_registry, *, metrics=None, nine_gate=None, status="在册"):
    doc = {
        "families": [{
            "id": "fam-x",
            "versions": [{
                "version": "v1.0",
                "status": status,
                "metrics": metrics or {},
                "nine_gate": nine_gate or {},
                "evidence": {},
            }],
        }],
    }
    tmp_registry.write_text(json.dumps(doc, ensure_ascii=False))


# ── 探针:非 live_rerun provenance 一律拒绝(禁止陈旧 CSV 冒充事实)──
def test_rejects_non_live_rerun_provenance(tmp_registry):
    _seed(tmp_registry)
    with pytest.raises(ValueError, match="live_rerun"):
        sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.1, "maxdd": -0.1},
                               provenance="stale_csv")


# ── 探针:缺 annual/maxdd 拒绝写入 ──
def test_requires_annual_and_maxdd(tmp_registry):
    _seed(tmp_registry)
    with pytest.raises(ValueError, match="annual"):
        sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.1},
                               provenance="live_rerun")


# ── 探针:未登记 family/version 拒绝(不能悄悄新建)──
def test_unknown_family_raises(tmp_registry):
    _seed(tmp_registry)
    with pytest.raises(ValueError, match="未登记"):
        sr.attach_path_metrics("no-such-fam", "v1.0", {"annual": 0.1, "maxdd": -0.1},
                               provenance="live_rerun")


def test_unknown_version_raises(tmp_registry):
    _seed(tmp_registry)
    with pytest.raises(ValueError, match="不存在"):
        sr.attach_path_metrics("fam-x", "v9.9", {"annual": 0.1, "maxdd": -0.1},
                               provenance="live_rerun")


# ── hit 必须由 compute_hit 重算,禁止调用方手填被采信 ──
def test_hit_is_recomputed_not_trusted_from_caller(tmp_registry):
    _seed(tmp_registry)
    result = sr.attach_path_metrics(
        "fam-x", "v1.0",
        {"annual": 0.30, "maxdd": -0.10, "hit": False},  # 手填 hit=False,应被覆盖
        provenance="live_rerun",
    )
    expected = sr.compute_hit(0.30, -0.10)
    assert result["hit"] == expected
    saved = json.loads(tmp_registry.read_text())
    assert saved["families"][0]["versions"][0]["metrics"]["hit"] == expected


# ── 旧 metrics 归档到 evidence.metrics_history,不静默抹掉 ──
def test_old_metrics_archived_to_history(tmp_registry):
    _seed(tmp_registry, metrics={"annual": 0.05, "maxdd": -0.30, "hit": False})
    sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.20, "maxdd": -0.15},
                           provenance="live_rerun")
    saved = json.loads(tmp_registry.read_text())
    v = saved["families"][0]["versions"][0]
    hist = v["evidence"]["metrics_history"]
    assert len(hist) == 1
    assert hist[0]["metrics"] == {"annual": 0.05, "maxdd": -0.30, "hit": False}
    assert v["metrics"]["annual"] == 0.20  # 新数字已生效


# ── 不改 status/admission(生命周期另走 register/workflow)──
def test_does_not_touch_status_or_admission(tmp_registry):
    _seed(tmp_registry, status="在册")
    sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.20, "maxdd": -0.15},
                           provenance="live_rerun")
    saved = json.loads(tmp_registry.read_text())
    v = saved["families"][0]["versions"][0]
    assert v["status"] == "在册"
    assert "admission" not in v or v.get("admission") is None or v.get("admission") == {}


# ── 实质偏差 + 已有 nine_gate → 标记 stale_vs_path_metrics,不伪造新门禁结果 ──
def test_material_delta_marks_nine_gate_stale_without_faking_it(tmp_registry):
    _seed(tmp_registry, metrics={"annual": 0.05, "maxdd": -0.10},
          nine_gate={"dsr_p": 0.03, "passed_all": True})
    result = sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.25, "maxdd": -0.10},
                                    provenance="live_rerun")
    assert "annual" in result["material_delta_keys"]
    saved = json.loads(tmp_registry.read_text())
    ng = saved["families"][0]["versions"][0]["nine_gate"]
    assert ng["stale_vs_path_metrics"] is True
    assert ng["dsr_p"] == 0.03          # 旧门禁数字原样保留,未被伪造覆盖
    assert ng["passed_all"] is True


# ── 无实质偏差(数字几乎一致)→ 不误标 stale ──
def test_no_material_delta_does_not_mark_stale(tmp_registry):
    _seed(tmp_registry, metrics={"annual": 0.200, "maxdd": -0.100},
          nine_gate={"dsr_p": 0.03})
    sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.201, "maxdd": -0.1005},
                           provenance="live_rerun")
    saved = json.loads(tmp_registry.read_text())
    ng = saved["families"][0]["versions"][0]["nine_gate"]
    assert "stale_vs_path_metrics" not in ng


# ── 首次写入(old metrics 为空)不产生虚假历史条目 ──
def test_first_write_no_empty_history_entry(tmp_registry):
    _seed(tmp_registry, metrics={})
    sr.attach_path_metrics("fam-x", "v1.0", {"annual": 0.10, "maxdd": -0.10},
                           provenance="live_rerun")
    saved = json.loads(tmp_registry.read_text())
    assert saved["families"][0]["versions"][0]["evidence"]["metrics_history"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
