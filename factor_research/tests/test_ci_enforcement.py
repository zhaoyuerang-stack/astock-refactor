from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factor-research-ci.yml"


def test_repository_ci_runs_source_only_verifier_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "push:" in text
    assert "bash factor_research/scripts/test_source_only.sh" in text
    assert "continue-on-error" not in text


def test_client_hook_keeps_full_data_verifier():
    hook = REPO_ROOT / "factor_research" / "scripts" / "hooks" / "pre-push"
    hook_text = hook.read_text(encoding="utf-8")
    assert "factor_research/scripts/test_all.sh" in hook_text
    assert "test_source_only.sh" not in hook_text


def test_source_only_verifier_does_not_claim_data_readiness():
    verifier = REPO_ROOT / "factor_research" / "scripts" / "test_source_only.sh"
    text = verifier.read_text(encoding="utf-8")
    assert "python3 -m pytest -q" in text
    assert "Production readiness remains unverified" in text
    assert "check_fundamental_batch_pit.py" not in text
