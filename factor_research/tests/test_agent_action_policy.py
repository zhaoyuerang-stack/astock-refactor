"""Agent action policy tests.

Run:
    cd factor_research && python3 tests/test_agent_action_policy.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.agent_control import AgentAction
from services.read.action_policy import can_agent_do


def test_registry_direct_write_is_blocked():
    decision = can_agent_do(AgentAction.WRITE_REGISTRY, "strategy_versions.json")
    assert decision.allowed is False
    assert decision.required_entrypoint == "strategy_registry.register"


def test_workflow_promotion_is_allowed_only_via_workflow():
    decision = can_agent_do(AgentAction.PROMOTE_CANDIDATE, "candidate:abc")
    assert decision.allowed is True
    assert decision.required_entrypoint == "workflow.promote"


def test_scratch_formal_evidence_is_blocked():
    decision = can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, "scratch/foo.json")
    assert decision.allowed is False


def test_run_daily_is_allowed_through_entrypoint():
    decision = can_agent_do(AgentAction.RUN_DAILY, "production_signal")
    assert decision.allowed is True
    assert decision.required_entrypoint == "run_daily.py"


# --- Adversarial: the formal-evidence block must not be bypassable ---
# Prior bug: _target_starts did a case-sensitive prefix check, so any of these
# laundered a scratch/results/logs artifact into "formal evidence".

def test_scratch_evidence_block_is_case_insensitive():
    for target in ["Scratch/foo.json", "SCRATCH/foo.json", "scRAtch/foo"]:
        assert can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, target).allowed is False, target


def test_forbidden_evidence_block_matches_any_path_segment():
    for target in [
        "/abs/path/scratch/x",      # absolute path
        "data/scratch/x.json",      # scratch not at the front
        "out/results/best.json",    # results nested
        "a/b/logs/run.json",        # logs nested
        "work\\scratch\\foo",       # backslash separators
    ]:
        assert can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, target).allowed is False, target


def test_unknown_evidence_path_is_blocked_fail_closed():
    # Positive whitelist: only known evidence areas count as formal evidence.
    for target in ["random/path.json", "tmp/whatever", "scratchpad_but_not_scratch/x"]:
        assert can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, target).allowed is False, target


def test_known_evidence_areas_are_allowed():
    for target in [
        "reports/research/x.json",
        "data_lake/daily_all/2024.parquet",
        "signals/2024-06-30.json",
        "paper/positions.json",
        "workflow/phase4_register.py",
        "research_ledger/ledger.json",
        "strategy_versions.json",
    ]:
        assert can_agent_do(AgentAction.USE_FORMAL_EVIDENCE, target).allowed is True, target


if __name__ == "__main__":
    test_registry_direct_write_is_blocked()
    test_workflow_promotion_is_allowed_only_via_workflow()
    test_scratch_formal_evidence_is_blocked()
    test_run_daily_is_allowed_through_entrypoint()
    test_scratch_evidence_block_is_case_insensitive()
    test_forbidden_evidence_block_matches_any_path_segment()
    test_unknown_evidence_path_is_blocked_fail_closed()
    test_known_evidence_areas_are_allowed()
    print("agent action policy tests passed")
