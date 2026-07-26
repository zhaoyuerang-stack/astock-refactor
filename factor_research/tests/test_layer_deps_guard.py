"""check_layer_deps 守卫回归测试。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci import check_layer_deps as guard


def test_scripts_ops_research_layer_imports_are_guarded(monkeypatch, tmp_path):
    """scripts/ops 默认按生产运维处理,新增研究层 import 必须被守卫拦住。"""
    rogue = tmp_path / "scripts" / "ops" / "rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("from factory.autoresearch import CandidateRepository\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert guard.check() == 1


def test_services_read_cannot_import_action_layer(monkeypatch, tmp_path):
    """services/read 是只读入口,不得反向调用 services/actions。"""
    rogue = tmp_path / "services" / "read" / "rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("from services.actions.autoresearch import run_autoresearch_seeds\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert guard.check() == 1


def test_policy_layer_cannot_import_higher_layers(monkeypatch, tmp_path):
    """policy 是底层硬约束叶子,被 factors.veto 反向 import。若 policy 倒灌
    strategies(或 factory/workflow/factors),就是 R-ARCH-001 分层倒置,守卫必须拦住。"""
    rogue = tmp_path / "policy" / "rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("from strategies.small_cap import build_small_cap\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert guard.check() == 1


def test_policy_layer_cannot_import_factors_no_cycle(monkeypatch, tmp_path):
    """factors→policy 已存在(veto wrapper),policy 反过来 import factors 会成环,必须拦住。"""
    rogue = tmp_path / "policy" / "rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("from factors.veto import loser_veto_reversal\n", encoding="utf-8")

    monkeypatch.setattr(guard, "ROOT", tmp_path)

    assert guard.check() == 1


def test_live_repo_layer_deps_guard_passes():
    assert guard.check() == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
