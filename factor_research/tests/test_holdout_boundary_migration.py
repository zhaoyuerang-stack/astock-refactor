"""boundary 迁移强制机制(ADR-023)测试:只进不退 + 旧金库作废 + 守卫单调。

#6「boundary 迁移无机制」从草案转为机械强制。本测试钉死:
  · migrate_holdout_boundary 是唯一推进入口,后移/相等抛 HoldoutBoundaryRegression;
  · 迁移记账 + 旧金库自动 superseded;
  · 多重检验 n_trials 按 active boundary 计(旧金库 peek 不污染新金库);
  · 同一候选可对新金库重校验(不被旧金库记录误判 IdentityMismatch);
  · 守卫强制 settings.holdout.start == 历史 active,且历史严格递增。
"""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import governance.holdout as H


def _hist(tmp_path, *boundaries):
    p = tmp_path / "hist.jsonl"
    p.write_text("".join(
        json.dumps({"boundary": b, "kind": "genesis" if i == 0 else "migration"}) + "\n"
        for i, b in enumerate(boundaries)))
    return p


def test_migration_forward_only_rejects_backward(tmp_path):
    hist = _hist(tmp_path, "2025-01-01")
    for bad in ("2024-06-01", "2025-01-01"):  # 后移 / 相等
        with pytest.raises(H.HoldoutBoundaryRegression):
            H.migrate_holdout_boundary(bad, reason="x", path=hist)


def test_migration_records_and_supersedes(tmp_path):
    hist = _hist(tmp_path, "2025-01-01")
    r = H.migrate_holdout_boundary("2027-01-01", reason="年度推进", path=hist)
    assert r == {"new": "2027-01-01", "previous": "2025-01-01", "superseded": ["2025-01-01"]}
    assert H.latest_boundary(hist) == pd.Timestamp("2027-01-01")
    assert H.superseded_boundaries(hist) == {"2025-01-01"}
    # append-only:旧 genesis 仍在
    assert len(H.boundary_history(hist)) == 2


def test_holdout_trials_filtered_by_boundary(tmp_path):
    val = tmp_path / "val.jsonl"
    val.write_text(
        json.dumps({"candidate_id": "a", "spec_hash": "s", "data_fingerprint": "d",
                    "holdout_boundary": "2025-01-01"}) + "\n" +
        json.dumps({"candidate_id": "b", "spec_hash": "s", "data_fingerprint": "d",
                    "holdout_boundary": "2027-01-01"}) + "\n")
    assert H.holdout_trials(val) == 2  # 全部(向后兼容)
    assert H.holdout_trials(val, boundary_filter="2027-01-01") == 1  # 只新金库


def _returns():
    idx = pd.bdate_range("2024-06-01", "2027-03-01")
    return pd.Series(0.001, index=idx)


def test_revalidation_against_new_boundary_not_blocked(tmp_path):
    # 同一候选先校验旧金库(2025),迁移后再校验新金库(2027):不应被 IdentityMismatch 拦,
    # 且新金库的 n_trials 不含旧金库 peek。
    val = tmp_path / "val.jsonl"
    rets = _returns()
    H.validate_on_holdout("cand1", rets, spec_hash="s1", data_fingerprint="d1",
                          holdout_boundary="2025-01-01", path=val)
    # 另一个候选也偷看过旧金库(制造旧金库 trial 负担)
    H.validate_on_holdout("cand2", rets, spec_hash="s2", data_fingerprint="d2",
                          holdout_boundary="2025-01-01", path=val)
    # 同一 cand1 对新金库 2027 重校验:合法,不抛
    res = H.validate_on_holdout("cand1", rets, spec_hash="s1", data_fingerprint="d1",
                                holdout_boundary="2027-01-01", path=val)
    # 新金库 n_trials 只含 2027 段的 cand1(=1),不含旧金库的 cand1/cand2
    assert res["holdout_trials"] == 1


def test_guard_passes_on_genesis():
    from scripts.ci.check_holdout_compliance import check_boundary_monotonic
    assert check_boundary_monotonic() == []  # 当前仓库 genesis 2025-01-01 == settings


def test_guard_flags_backward_and_mismatch(tmp_path, monkeypatch):
    import scripts.ci.check_holdout_compliance as G
    # 造一个 tmp app_config:历史非递增 + settings 与 active 不一致
    appcfg = tmp_path / "app_config"
    appcfg.mkdir()
    (appcfg / "holdout_boundary_history.jsonl").write_text(
        json.dumps({"boundary": "2025-01-01"}) + "\n" +
        json.dumps({"boundary": "2024-01-01"}) + "\n")  # 后退!
    (appcfg / "settings.yaml").write_text("holdout:\n  start: \"2025-01-01\"\n")
    monkeypatch.setattr(G, "ROOT", tmp_path)
    monkeypatch.setattr(G, "SETTINGS_YAML", appcfg / "settings.yaml")
    viol = G.check_boundary_monotonic()
    assert any("非严格递增" in m for _, m in viol), viol


def test_boundary_history_rejects_malformed_nonempty_line(tmp_path):
    hist = tmp_path / "hist.jsonl"
    hist.write_text(
        json.dumps({"boundary": "2025-01-01"}) + "\n" +
        "{broken json\n" +
        json.dumps({"boundary": "2026-01-01"}) + "\n"
    )

    with pytest.raises(H.HoldoutHistoryCorrupt, match="line 2"):
        H.boundary_history(hist)


def test_boundary_rejects_unreadable_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("holdout: [broken")
    monkeypatch.setattr(H, "_SETTINGS_YAML", settings)

    with pytest.raises(RuntimeError, match="cannot load holdout boundary"):
        H.boundary()


def test_guard_flags_malformed_boundary_history_line(tmp_path, monkeypatch):
    import scripts.ci.check_holdout_compliance as G

    appcfg = tmp_path / "app_config"
    appcfg.mkdir()
    (appcfg / "holdout_boundary_history.jsonl").write_text(
        json.dumps({"boundary": "2025-01-01"}) + "\n{broken json\n"
    )
    (appcfg / "settings.yaml").write_text("holdout:\n  start: \"2025-01-01\"\n")
    monkeypatch.setattr(G, "ROOT", tmp_path)
    monkeypatch.setattr(G, "SETTINGS_YAML", appcfg / "settings.yaml")

    violations = G.check_boundary_monotonic()

    assert any("line 2" in message for _, message in violations), violations


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
