from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "factor-research-ci.yml"


def test_repository_ci_runs_the_canonical_verifier_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "push:" in text
    assert "bash factor_research/scripts/test_all.sh" in text
    assert "continue-on-error" not in text


def test_client_hook_and_server_ci_share_one_verification_entrypoint():
    hook = REPO_ROOT / "factor_research" / "scripts" / "hooks" / "pre-push"
    hook_text = hook.read_text(encoding="utf-8")
    workflow_text = WORKFLOW.read_text(encoding="utf-8")

    canonical = "factor_research/scripts/test_all.sh"
    assert canonical in hook_text
    assert canonical in workflow_text
