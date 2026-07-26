#!/usr/bin/env python3
"""Audit local branches, worktrees, and untracked files.

The default invocation is read-only. ``--fetch`` updates remote-tracking refs
before the audit. There is intentionally no cleanup flag: this command never
deletes, stages, stashes, switches, or rewrites working-tree content or history.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


def _run(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=check,
    )


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd=repo, check=check)


def _relation(ahead: int, behind: int) -> str:
    if ahead == 0 and behind == 0:
        return "aligned"
    if ahead == 0:
        return "behind"
    if behind == 0:
        return "ahead"
    return "diverged"


def _parse_worktrees(raw: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*raw.splitlines(), ""]:
        if not line:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key in {"detached", "bare", "prunable", "locked"}:
            current[key] = value or True
        else:
            current[key] = value
    return records


def _parse_status(raw: str) -> tuple[int, list[str]]:
    tracked = 0
    untracked: list[str] = []
    entries = raw.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if status == "??":
            untracked.append(path)
        else:
            tracked += 1
        if "R" in status or "C" in status:
            index += 1  # porcelain -z stores the original path as the next entry
    return tracked, untracked


def classify_untracked(path: str, *, is_symlink: bool = False) -> str:
    """Classify without ever deciding that an untracked file is safe to commit."""
    normalized = path.rstrip("/")
    parts = set(Path(normalized).parts)
    basename = Path(normalized).name

    generated_dirs = {
        "node_modules",
        "dist",
        ".vite",
        ".runtime",
        "__pycache__",
        ".thumbnails",
        ".waveform-cache",
        "renders",
    }
    if parts & generated_dirs or basename == ".DS_Store":
        return "local_ignore"
    if (
        normalized.startswith((".superpowers/", ".workbuddy/"))
        or fnmatch.fnmatch(normalized, "*.code-workspace")
        or basename == "project.private.config.json"
        or normalized == "desktop/stock-diagnosis-client/auth.json"
        or normalized == "factor_research/.strategy_versions.json.lock"
    ):
        return "local_ignore"
    if normalized.startswith("factor_research/scratch/"):
        return "archive_or_promote"
    if normalized.startswith("factor_research/scripts/"):
        return "review_source"
    if normalized.startswith("reports/research/"):
        return "review_evidence"
    if normalized == ".agents/AGENTS.md":
        return "local_ignore"
    if normalized.startswith(".agents/"):
        return "local_ignore" if is_symlink else "review_source"
    return "review"


def _branch_refs(repo: Path) -> list[dict[str, str]]:
    fmt = "%(refname:short)%09%(upstream:short)%09%(committerdate:short)%09%(objectname)"
    rows = []
    for line in _git(repo, "for-each-ref", f"--format={fmt}", "refs/heads").stdout.splitlines():
        name, upstream, date, sha = line.split("\t", 3)
        rows.append({"name": name, "upstream": upstream, "date": date, "sha": sha})
    return rows


def _remote_refs(repo: Path) -> list[dict[str, str]]:
    fmt = "%(refname:short)%09%(committerdate:short)%09%(objectname)"
    rows = []
    for line in _git(
        repo, "for-each-ref", f"--format={fmt}", "refs/remotes/origin"
    ).stdout.splitlines():
        name, commit_date, sha = line.split("\t", 2)
        if name in {"origin", "origin/HEAD", "origin/main"}:
            continue
        rows.append({"name": name, "date": commit_date, "sha": sha})
    return rows


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    proc = _git(repo, "merge-base", "--is-ancestor", older, newer, check=False)
    if proc.returncode not in {0, 1}:
        raise RuntimeError(proc.stderr.strip() or f"cannot compare {older} and {newer}")
    return proc.returncode == 0


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def _status_for_worktree(path: Path) -> dict[str, Any]:
    raw = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    tracked, untracked = _parse_status(raw)
    ignored = [
        item
        for item in _git(
            path,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--directory",
            "-z",
        ).stdout.split("\0")
        if item
    ]
    buckets: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    for item in untracked:
        bucket = classify_untracked(item, is_symlink=(path / item).is_symlink())
        buckets[bucket] += 1
        samples.setdefault(bucket, [])
        if len(samples[bucket]) < 5:
            samples[bucket].append(item)
    return {
        "tracked_changes": tracked,
        "untracked_files": len(untracked),
        "ignored_entries": len(ignored),
        "ignored_samples": ignored[:5],
        "untracked_buckets": dict(sorted(buckets.items())),
        "untracked_samples": dict(sorted(samples.items())),
        "clean": tracked == 0 and not untracked and not ignored,
    }


def audit(repo: Path, *, fetch: bool = False) -> dict[str, Any]:
    root = Path(_git(repo, "rev-parse", "--show-toplevel").stdout.strip())
    if fetch:
        _git(root, "fetch", "origin", "--prune")

    local_main = _git(root, "rev-parse", "main").stdout.strip()
    remote_main = _git(root, "rev-parse", "origin/main").stdout.strip()
    ahead_raw = _git(root, "rev-list", "--left-right", "--count", "main...origin/main")
    main_ahead, main_behind = map(int, ahead_raw.stdout.split())

    worktrees = _parse_worktrees(_git(root, "worktree", "list", "--porcelain").stdout)
    branch_to_path: dict[str, str] = {}
    audited_worktrees: list[dict[str, Any]] = []
    for item in worktrees:
        path = Path(item["worktree"])
        branch_ref = item.get("branch", "")
        branch = branch_ref.removeprefix("refs/heads/") or None
        if branch:
            branch_to_path[branch] = str(path)
        missing = bool(item.get("prunable")) or not path.exists()
        status = (
            {
                "tracked_changes": None,
                "untracked_files": None,
                "ignored_entries": None,
                "ignored_samples": [],
                "untracked_buckets": {},
                "untracked_samples": {},
                "clean": False,
            }
            if missing
            else _status_for_worktree(path)
        )
        head = item.get("HEAD", "")
        audited_worktrees.append(
            {
                "path": str(path),
                "branch": branch,
                "head": head,
                "detached": bool(item.get("detached")),
                "locked": bool(item.get("locked")),
                "prunable": bool(item.get("prunable")),
                "missing": missing,
                "head_merged": bool(head and _is_ancestor(root, head, "origin/main")),
                **status,
            }
        )

    branches = _branch_refs(root)
    unmerged: list[dict[str, Any]] = []
    merged_without_worktree: list[dict[str, str]] = []
    for branch in branches:
        name = branch["name"]
        merged = _is_ancestor(root, name, "origin/main")
        if merged:
            if name != "main" and name not in branch_to_path:
                merged_without_worktree.append(branch)
            continue

        counts = _git(root, "rev-list", "--left-right", "--count", f"origin/main...{name}")
        main_only, branch_only = map(int, counts.stdout.split())
        cherry = _git(root, "cherry", "origin/main", name).stdout.splitlines()
        upstream = branch["upstream"]
        upstream_ahead: int | None = None
        upstream_behind: int | None = None
        upstream_relation = "none"
        if upstream:
            if _ref_exists(root, upstream):
                upstream_counts = _git(
                    root, "rev-list", "--left-right", "--count", f"{name}...{upstream}"
                )
                upstream_ahead, upstream_behind = map(int, upstream_counts.stdout.split())
                upstream_relation = _relation(upstream_ahead, upstream_behind)
            else:
                upstream_relation = "gone"
        unmerged.append(
            {
                **branch,
                "stale": (date.today() - date.fromisoformat(branch["date"])).days > 7,
                "main_only_commits": main_only,
                "branch_only_commits": branch_only,
                "patch_unique_commits": sum(line.startswith("+") for line in cherry),
                "patch_equivalent_commits": sum(line.startswith("-") for line in cherry),
                "upstream_ahead": upstream_ahead,
                "upstream_behind": upstream_behind,
                "upstream_relation": upstream_relation,
                "worktree": branch_to_path.get(name),
            }
        )

    unmerged_remote: list[dict[str, Any]] = []
    merged_remote: list[dict[str, str]] = []
    for remote in _remote_refs(root):
        if _is_ancestor(root, remote["name"], "origin/main"):
            merged_remote.append(remote)
            continue
        counts = _git(
            root,
            "rev-list",
            "--left-right",
            "--count",
            f"origin/main...{remote['name']}",
        )
        main_only, branch_only = map(int, counts.stdout.split())
        unmerged_remote.append(
            {
                **remote,
                "stale": (date.today() - date.fromisoformat(remote["date"])).days > 7,
                "main_only_commits": main_only,
                "branch_only_commits": branch_only,
            }
        )

    clean_merged_worktrees = [
        item
        for item in audited_worktrees
        if item["path"] != str(root)
        and item["branch"] != "main"
        and item["clean"]
        and item["head_merged"]
        and not item["locked"]
    ]

    return {
        "repo_root": str(root),
        "remote_refreshed": fetch,
        "main": {
            "local_sha": local_main,
            "remote_sha": remote_main,
            "ahead": main_ahead,
            "behind": main_behind,
            "relation": _relation(main_ahead, main_behind),
        },
        "unmerged_branches": sorted(unmerged, key=lambda row: row["name"]),
        "unmerged_remote_branches": sorted(unmerged_remote, key=lambda row: row["name"]),
        "worktrees": audited_worktrees,
        "cleanup_candidates": {
            "merged_branches_without_worktree": sorted(
                merged_without_worktree, key=lambda row: row["name"]
            ),
            "clean_merged_worktrees": clean_merged_worktrees,
            "merged_remote_branches": sorted(merged_remote, key=lambda row: row["name"]),
        },
    }


def _print_human(report: dict[str, Any]) -> None:
    main = report["main"]
    freshness = "refreshed" if report["remote_refreshed"] else "cached; run with --fetch"
    print("Repository hygiene audit (read-only)")
    print(f"remote: {freshness}")
    print(
        f"main: {main['relation']} "
        f"(ahead={main['ahead']}, behind={main['behind']})"
    )

    branches = report["unmerged_branches"]
    print(f"\nunmerged local branches: {len(branches)}")
    for branch in branches:
        location = branch["worktree"] or "no-worktree"
        upstream = branch["upstream"] or "none"
        upstream_state = branch["upstream_relation"]
        if branch["upstream_ahead"] is not None:
            upstream_state += (
                f"(ahead={branch['upstream_ahead']},behind={branch['upstream_behind']})"
            )
        print(
            f"- {branch['name']}: main-only={branch['main_only_commits']} "
            f"branch-only={branch['branch_only_commits']} "
            f"patch-unique={branch['patch_unique_commits']} "
            f"patch-equivalent={branch['patch_equivalent_commits']} "
            f"stale={branch['stale']} upstream={upstream}:{upstream_state} [{location}]"
        )

    remote_branches = report["unmerged_remote_branches"]
    print(f"\nunmerged remote branches: {len(remote_branches)}")
    for branch in remote_branches:
        print(
            f"- {branch['name']}: main-only={branch['main_only_commits']} "
            f"branch-only={branch['branch_only_commits']} stale={branch['stale']}"
        )

    print("\nworktrees:")
    for item in report["worktrees"]:
        branch = item["branch"] or "detached"
        buckets = ", ".join(
            f"{name}={count}" for name, count in item["untracked_buckets"].items()
        ) or "none"
        state = "missing/prunable" if item["missing"] else "present"
        print(
            f"- {item['path']} [{branch}]: state={state} "
            f"tracked={item['tracked_changes']} untracked={item['untracked_files']} "
            f"ignored={item['ignored_entries']} buckets={buckets}"
        )

    candidates = report["cleanup_candidates"]
    print(
        "\ncleanup candidates (review before deleting): "
        f"branches={len(candidates['merged_branches_without_worktree'])}, "
        f"worktrees={len(candidates['clean_merged_worktrees'])}, "
        f"remote-merged={len(candidates['merged_remote_branches'])}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="update origin remote-tracking refs before auditing",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        report = audit(Path.cwd(), fetch=args.fetch)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        stderr = getattr(exc, "stderr", "")
        print(f"git hygiene audit failed: {stderr or exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
