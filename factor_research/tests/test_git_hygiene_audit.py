from scripts.ops.git_hygiene_audit import (
    _parse_status,
    _parse_worktrees,
    _relation,
    classify_untracked,
)


def test_relation_covers_all_graph_states():
    assert _relation(0, 0) == "aligned"
    assert _relation(0, 3) == "behind"
    assert _relation(2, 0) == "ahead"
    assert _relation(2, 3) == "diverged"


def test_parse_worktrees_keeps_detached_and_branch_records():
    records = _parse_worktrees(
        "worktree /repo\n"
        "HEAD abc\n"
        "branch refs/heads/main\n\n"
        "worktree /tmp/task tree\n"
        "HEAD def\n"
        "detached\n\n"
        "worktree /missing\n"
        "HEAD 123\n"
        "prunable gitdir file points to non-existent location\n\n"
    )

    assert records == [
        {"worktree": "/repo", "HEAD": "abc", "branch": "refs/heads/main"},
        {"worktree": "/tmp/task tree", "HEAD": "def", "detached": True},
        {
            "worktree": "/missing",
            "HEAD": "123",
            "prunable": "gitdir file points to non-existent location",
        },
    ]


def test_parse_status_separates_tracked_and_untracked():
    tracked, untracked = _parse_status(
        " M AGENTS.md\0"
        "A  new.py\0"
        "?? scratch/output with spaces.json\0"
        "R  renamed.py\0old.py\0"
    )

    assert tracked == 3
    assert untracked == ["scratch/output with spaces.json"]


def test_untracked_classification_is_conservative():
    assert classify_untracked("desktop/app/node_modules/react/index.js") == "local_ignore"
    assert classify_untracked("project.private.config.json") == "local_ignore"
    assert classify_untracked("factor_research/scratch/probe.py") == "archive_or_promote"
    assert classify_untracked("factor_research/scripts/research/probe.py") == "review_source"
    assert classify_untracked("reports/research/result.md") == "review_evidence"
    assert classify_untracked(".agents/AGENTS.md") == "local_ignore"
    assert classify_untracked(".agents/skills/shared/SKILL.md") == "review_source"
    assert (
        classify_untracked(".agents/skills/local-link", is_symlink=True) == "local_ignore"
    )
    assert classify_untracked("unknown.txt") == "review"
