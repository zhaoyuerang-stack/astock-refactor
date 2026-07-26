"""workflow/promote 的 Nine-Gate 自动触发测试。

Run:
  cd /Users/kiki/astcok/factor_research && python3 tests/test_promote_nine_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def test_promote_spec_runs_nine_gate_after_registered():
    import workflow.phase1_synthetic as phase1
    import workflow.phase2_backtest as phase2
    import workflow.phase3_wf as phase3
    import workflow.phase4_register as phase4
    import workflow.promote as promote
    from workflow.phase4_register import RegistrationReport

    old_phase1 = phase1.Phase1Checker
    old_phase2 = phase2.Phase2Runner
    old_phase3 = phase3.WF3Runner
    old_phase4 = phase4.Phase4Register
    calls: list[dict] = []

    class FakePhase1Checker:
        def __init__(self, *args, **kwargs):
            pass

        def run_all(self, use_clean=True, save_lessons=False):
            return []

    class FakePhase2Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, warmup_start="2010-01-01"):
            return {"segments": {}, "config": {}}

    class FakeWF3Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, warmup_start="2010-01-01"):
            return {"aggregate": {"verdict": "PASS"}}

    class FakePhase4Register:
        def __init__(self, family, version="v1.0"):
            self.family = family
            self.version = version

        def register(self, *args, **kwargs):
            return RegistrationReport(
                family=self.family,
                version=self.version,
                registered=True,
                repro_meta={},
                lessons_saved=0,
                detail="registered",
            )

    def fake_nine_gate_runner(strategy_name, n_trials=15, persist=False, version=None, start=None):
        calls.append({
            "strategy_name": strategy_name,
            "n_trials": n_trials,
            "persist": persist,
            "version": version,
            "start": start,
        })

    try:
        phase1.Phase1Checker = FakePhase1Checker
        phase2.Phase2Runner = FakePhase2Runner
        phase3.WF3Runner = FakeWF3Runner
        phase4.Phase4Register = FakePhase4Register

        spec = SimpleNamespace(
            name="small-cap-size",
            factor_builder=lambda *args, **kwargs: None,
            timing_builder=lambda *args, **kwargs: None,
            config={},
            hypothesis="test",
        )
        report = promote.promote_spec(
            spec,
            version="v-test",
            run_nine_gate=True,
            nine_gate_runner=fake_nine_gate_runner,
        )

        assert report.registered is True
        assert calls == [{
            "strategy_name": "small_cap",
            "n_trials": 15,
            "persist": True,
            "version": "v-test",
            "start": None,
        }]
    finally:
        phase1.Phase1Checker = old_phase1
        phase2.Phase2Runner = old_phase2
        phase3.WF3Runner = old_phase3
        phase4.Phase4Register = old_phase4


def test_nine_gate_failure_is_attached_to_registry():
    import strategy_registry as registry
    import workflow.promote as promote
    from workflow.phase4_register import RegistrationReport

    old_registry = registry.REGISTRY
    registry.REGISTRY = Path(tempfile.mkdtemp()) / "strategy_versions.json"

    def failing_runner(*args, **kwargs):
        raise RuntimeError("nine-gate boom")

    try:
        registry.register_family("small-cap-size", "小盘成交额因子")
        # 本测试验 run_nine_gate_after_registration 把失败态写回台账,与 status 无关;
        # 登记为「候选」即可(ADR-020:在册 standalone 须 DSR,此处不涉准入轨)。
        registry.register(
            "small-cap-size",
            "v-fail",
            "test",
            config={},
            data_scope={},
            metrics={"annual": 0.30, "maxdd": -0.10},
            status="候选",
        )
        report = RegistrationReport(
            family="small-cap-size",
            version="v-fail",
            registered=True,
            repro_meta={},
            lessons_saved=0,
            detail="registered",
        )

        result = promote.run_nine_gate_after_registration(
            report,
            strategy_name="small_cap",
            runner=failing_runner,
        )

        version = registry._load()["families"][0]["versions"][0]
        assert result["status"] == "FAILED_TO_RUN"
        assert version["nine_gate"]["status"] == "FAILED_TO_RUN"
        assert version["nine_gate"]["strategy"] == "small_cap"
        assert "nine-gate boom" in version["nine_gate"]["error"]
    finally:
        registry.REGISTRY = old_registry


def test_promote_spec_skips_marginal_for_candidate_registration():
    import workflow.phase1_synthetic as phase1
    import workflow.phase2_backtest as phase2
    import workflow.phase3_wf as phase3
    import workflow.phase4_register as phase4
    import workflow.promote as promote
    from workflow.phase4_register import RegistrationReport

    old_phase1 = phase1.Phase1Checker
    old_phase2 = phase2.Phase2Runner
    old_phase3 = phase3.WF3Runner
    old_phase4 = phase4.Phase4Register
    old_run_marginal = promote._run_marginal
    calls: list[str] = []

    class FakePhase1Checker:
        def __init__(self, *args, **kwargs):
            pass

        def run_all(self, use_clean=True, save_lessons=False):
            return []

    class FakePhase2Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, warmup_start="2010-01-01"):
            return {"segments": {}, "config": {}}

    class FakeWF3Runner:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, warmup_start="2010-01-01"):
            return {"aggregate": {"verdict": "FAIL"}}

    class FakePhase4Register:
        def __init__(self, family, version="v1.0"):
            self.family = family
            self.version = version

        def register(self, *args, **kwargs):
            return RegistrationReport(
                family=self.family,
                version=self.version,
                registered=True,
                repro_meta={},
                lessons_saved=0,
                detail="registered as candidate",
                status="候选",
            )

    def fake_run_marginal(*args, **kwargs):
        calls.append("called")

    try:
        phase1.Phase1Checker = FakePhase1Checker
        phase2.Phase2Runner = FakePhase2Runner
        phase3.WF3Runner = FakeWF3Runner
        phase4.Phase4Register = FakePhase4Register
        promote._run_marginal = fake_run_marginal

        spec = SimpleNamespace(
            name="small-cap-size",
            factor_builder=lambda *args, **kwargs: None,
            timing_builder=lambda *args, **kwargs: None,
            config={},
            hypothesis="test",
        )
        report = promote.promote_spec(spec, version="v-candidate", run_marginal=True)

        assert report.registered is True
        assert report.status == "候选"
        assert calls == []
    finally:
        phase1.Phase1Checker = old_phase1
        phase2.Phase2Runner = old_phase2
        phase3.WF3Runner = old_phase3
        phase4.Phase4Register = old_phase4
        promote._run_marginal = old_run_marginal


if __name__ == "__main__":
    test_promote_spec_runs_nine_gate_after_registered()
    print("✅ test_promote_spec_runs_nine_gate_after_registered")
    test_nine_gate_failure_is_attached_to_registry()
    print("✅ test_nine_gate_failure_is_attached_to_registry")
    test_promote_spec_skips_marginal_for_candidate_registration()
    print("✅ test_promote_spec_skips_marginal_for_candidate_registration")
