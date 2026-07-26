"""Adversarial tests for the version-controlled pre-push enforcement point."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


FACTOR_ROOT = Path(__file__).resolve().parents[1]
HOOK = FACTOR_ROOT / "scripts" / "hooks" / "pre-push"


def _run(*args: str, cwd: Path, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    env = dict(kwargs.pop("env", os.environ))
    # Nested repositories must not inherit an outer validation harness's Git
    # plumbing.  Besides producing false failures, GIT_DIR could make a test
    # mutate the real repository's config or index.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
        env=env,
        **kwargs,
    )


def _init_repo(tmp_path: Path, *, with_runner: bool = True) -> tuple[Path, Path]:
    repo = tmp_path / "repo with spaces"
    _run("git", "init", "-q", str(repo), cwd=tmp_path)
    runner = repo / "factor_research" / "scripts" / "test_all.sh"
    if with_runner:
        runner.parent.mkdir(parents=True)
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "printf 'cwd=%s\\nargs=%s\\n' \"$PWD\" \"$#\" > \"$HOOK_MARKER\"\n"
            "exit \"${FAKE_TEST_EXIT:-0}\"\n",
            encoding="utf-8",
        )
        runner.chmod(0o755)
    return repo, runner


def _invoke_hook(
    repo: Path,
    marker: Path,
    *,
    exit_code: int = 0,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOOK_MARKER": str(marker),
        "FAKE_TEST_EXIT": str(exit_code),
        **(extra_env or {}),
    }
    return _run(
        "bash",
        str(HOOK),
        "origin",
        "unused-remote-url",
        cwd=cwd or repo,
        check=False,
        env=env,
        input="refs/heads/topic a refs/heads/topic b\n",
    )


def test_hook_is_executable_and_shell_syntax_is_valid():
    assert HOOK.stat().st_mode & stat.S_IXUSR
    result = _run("bash", "-n", str(HOOK), cwd=FACTOR_ROOT, check=False)
    assert result.returncode == 0, result.stderr


def test_resolves_worktree_root_and_runs_canonical_verifier(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    nested = repo / "nested" / "directory"
    nested.mkdir(parents=True)
    marker = tmp_path / "marker"

    result = _invoke_hook(repo, marker, cwd=nested)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        f"cwd={repo}",
        "args=0",
    ]
    assert "factor_research/scripts/test_all.sh" in result.stderr


@pytest.mark.parametrize("failure_code", [1, 23, 127])
def test_verifier_failure_code_is_propagated(tmp_path: Path, failure_code: int):
    repo, _ = _init_repo(tmp_path)
    marker = tmp_path / "marker"

    result = _invoke_hook(repo, marker, exit_code=failure_code)

    assert marker.exists(), "the canonical verifier was not executed"
    assert result.returncode == failure_code


def test_missing_canonical_verifier_fails_closed(tmp_path: Path):
    repo, runner = _init_repo(tmp_path, with_runner=False)
    marker = tmp_path / "marker"

    result = _invoke_hook(repo, marker)

    assert result.returncode != 0
    assert str(runner) in result.stderr
    assert "missing or unreadable" in result.stderr
    assert not marker.exists()


def test_non_git_directory_fails_closed(tmp_path: Path):
    marker = tmp_path / "marker"
    result = _run(
        "bash",
        str(HOOK),
        cwd=tmp_path,
        check=False,
        env={**os.environ, "HOOK_MARKER": str(marker)},
    )

    assert result.returncode != 0
    assert "cannot resolve the Git worktree root" in result.stderr
    assert not marker.exists()


def test_legacy_skip_environment_does_not_bypass_verification(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    marker = tmp_path / "marker"

    result = _invoke_hook(
        repo,
        marker,
        exit_code=19,
        extra_env={"SKIP_GUARDS": "1", "SKIP_TESTS": "1"},
    )

    assert marker.exists(), "legacy skip variables must not bypass the canonical verifier"
    assert result.returncode == 19


def test_real_git_push_dispatches_hook_and_blocks_on_failure(tmp_path: Path):
    repo, _ = _init_repo(tmp_path)
    remote = tmp_path / "remote.git"
    marker = tmp_path / "marker"
    _run("git", "init", "-q", "--bare", str(remote), cwd=tmp_path)
    _run(
        "git",
        "-c",
        "user.name=Hook Test",
        "-c",
        "user.email=hook@example.invalid",
        "commit",
        "--allow-empty",
        "-qm",
        "initial",
        cwd=repo,
    )

    installed_hook = repo / ".git" / "hooks" / "pre-push"
    shutil.copy2(HOOK, installed_hook)
    installed_hook.chmod(0o755)
    result = _run(
        "git",
        "push",
        str(remote),
        "HEAD:refs/heads/topic",
        cwd=repo,
        check=False,
        env={
            **os.environ,
            "HOOK_MARKER": str(marker),
            "FAKE_TEST_EXIT": "29",
        },
    )

    assert result.returncode != 0
    assert marker.exists(), "Git did not dispatch the pre-push hook"
    assert "Running canonical verification" in result.stderr
    remote_refs = _run(
        "git",
        "for-each-ref",
        "--format=%(refname)",
        cwd=remote,
    ).stdout
    assert "refs/heads/topic" not in remote_refs
