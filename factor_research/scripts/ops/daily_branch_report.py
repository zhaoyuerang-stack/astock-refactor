#!/usr/bin/env python3
"""每日分支体检:只出报告,不合并、不改动除 report/branch-status 外的任何分支。

判定链(便宜信号优先,test_all.sh 最贵放最后只跑候选):
  ahead=0(相对本地 main 无新增 commit) -> NO_NEW_COMMITS
  命中冻结名单                        -> FROZEN
  分支正被其他 worktree 检出(疑似在写)  -> ACTIVE_WORKTREE
  命中已知 stale 并行家族             -> STALE_PARALLEL
  git merge-tree 检测到冲突           -> CONFLICT
  以上都不命中 -> 拉临时 worktree 跑 scripts/test_all.sh -> MERGEABLE / GUARD_FAIL

是否真的合并,永远由人决定 -- 见 CLAUDE.md §11 与仓库既有反馈:不擅自合并共享分支。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
FACTOR_RESEARCH = REPO / "factor_research"
REPORT_PATH = REPO / "reports" / "ops" / "BRANCH_REPORT.md"
REPORT_BRANCH = "report/branch-status"
TRUNK = "main"
TMP_ROOT = Path("/tmp/branch-report-work")
MAX_DEEP_CHECKS = 5
GUARD_TIMEOUT_SEC = 1200
GC_SCRIPT = FACTOR_RESEARCH / "scripts" / "ops" / "gc_factor_panel_cache.py"
GC_TIMEOUT_SEC = 120

# 2026-07-14/07-19 分支清理中已定性、owner 明确要求继续冻结/别硬合的名单(见 memory
# feedback_meta_system_freeze / project_branch_consolidation_pr1),机械匹配、不做语义猜测。
FROZEN_SUBSTRINGS = ["naughty-raman", "thirsty-rhodes", "brave-golick"]
KNOWN_STALE_PARALLEL = {
    "ws0-ws4-topn",
    "ws2-composite",
    "epic-dubinsky-3a3f52",
    "claude/epic-dubinsky-3a3f52",
}


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )


@dataclass
class BranchInfo:
    name: str
    last_commit: str = ""
    behind: int = 0
    ahead: int = 0
    active_worktree: str | None = None
    status: str = ""
    detail: str = ""


def get_worktree_map() -> dict[str, str]:
    """branch -> worktree path,不含本仓主工作树自身。"""
    out = run(["git", "worktree", "list", "--porcelain"], cwd=REPO).stdout
    mapping: dict[str, str] = {}
    cur_path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_path = line.split(" ", 1)[1]
        elif line.startswith("branch ") and cur_path:
            branch = line.split(" ", 1)[1].removeprefix("refs/heads/")
            if cur_path != str(REPO):
                mapping[branch] = cur_path
    return mapping


def list_branches() -> list[str]:
    out = run(
        ["git", "for-each-ref", "refs/heads", "--format=%(refname:short)"], cwd=REPO
    ).stdout
    names = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return [n for n in names if n not in (TRUNK, REPORT_BRANCH)]


def is_frozen(name: str) -> bool:
    return any(sub in name for sub in FROZEN_SUBSTRINGS)


def is_known_stale_parallel(name: str) -> bool:
    return name in KNOWN_STALE_PARALLEL


def merge_is_clean(name: str) -> bool:
    r = run(["git", "merge-tree", "--write-tree", TRUNK, name], cwd=REPO)
    return r.returncode == 0


def deep_guard_check(name: str) -> tuple[bool, str]:
    """在一次性 worktree 里跑 scripts/test_all.sh,返回 (pass, detail)。"""
    safe_name = name.replace("/", "_")
    wt_dir = TMP_ROOT / safe_name
    if wt_dir.exists():
        shutil.rmtree(wt_dir, ignore_errors=True)
    add = run(["git", "worktree", "add", "--detach", str(wt_dir), name], cwd=REPO)
    if add.returncode != 0:
        return False, f"worktree add 失败(可能已被其他地方检出): {add.stderr.strip()[:300]}"
    try:
        lake_link = wt_dir / "factor_research" / "data_lake"
        real_lake = FACTOR_RESEARCH / "data_lake"
        if lake_link.exists() or lake_link.is_symlink():
            if lake_link.is_symlink() or lake_link.is_dir():
                pass
        else:
            lake_link.symlink_to(real_lake, target_is_directory=True)
        try:
            guard = run(
                ["bash", "scripts/test_all.sh"],
                cwd=wt_dir / "factor_research",
                timeout=GUARD_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired:
            return False, f"test_all.sh 超时(>{GUARD_TIMEOUT_SEC}s)"
        if guard.returncode == 0:
            return True, "test_all.sh 全绿"
        tail = (guard.stdout + guard.stderr).strip().splitlines()[-15:]
        return False, "test_all.sh 失败,末尾日志:\n```\n" + "\n".join(tail) + "\n```"
    finally:
        run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=REPO)


def classify(info: BranchInfo, worktree_map: dict[str, str]) -> None:
    if info.ahead == 0:
        info.status = "NO_NEW_COMMITS"
        info.detail = f"相对 {TRUNK} 无新增 commit(落后 {info.behind}),无内容可合,建议核实后归档/删除"
        return
    if is_frozen(info.name):
        info.status = "FROZEN"
        info.detail = "命中冻结名单(owner 已明确暂缓),不评估"
        return
    if is_known_stale_parallel(info.name):
        info.status = "STALE_PARALLEL"
        if info.name in worktree_map:
            info.active_worktree = worktree_map[info.name]
        info.detail = "已知活跃分支的旧并行影子版本,历史结论=别硬合,等主线收工"
        return
    if info.name in worktree_map:
        info.active_worktree = worktree_map[info.name]
        info.status = "ACTIVE_WORKTREE"
        info.detail = f"当前被其他 worktree 检出({info.active_worktree}),疑似进行中,跳过深检以免打扰"
        return
    if not merge_is_clean(info.name):
        info.status = "CONFLICT"
        info.detail = f"git merge-tree 对 {TRUNK} 检测到冲突,需要人工 rebase/解冲突"
        return
    info.status = "CANDIDATE"
    info.detail = "机械信号初筛通过,待 test_all.sh 深检"


def gc_panel_cache_dry_run() -> str:
    """跑一次 factor_store/panels/ 缓存 GC 的 dry-run,只读、不删。

    仅供日报里带一份磁盘占用趋势参考;真正 --apply 清理永远由人决定。
    """
    try:
        r = run(
            [sys.executable, str(GC_SCRIPT)],
            cwd=FACTOR_RESEARCH,
            timeout=GC_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return f"gc_factor_panel_cache.py dry-run 超时(>{GC_TIMEOUT_SEC}s),跳过。"
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()[-10:]
        return "gc_factor_panel_cache.py dry-run 执行失败,末尾日志:\n```\n" + "\n".join(tail) + "\n```"
    return "```\n" + r.stdout.strip() + "\n```"


def render_report(infos: list[BranchInfo], deep_checked: int, gc_dry_run: str) -> str:
    now = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# 分支状态日报",
        "",
        f"生成时间: {now}",
        "",
        "> 本报告由每日定时任务机械生成,只做只读分析(rev-list / merge-tree / 隔离 worktree 跑 test_all.sh)。",
        "> **不执行任何分支合并、不修改除本文件外的任何内容。是否合并、何时合并由人工决定。**",
        f"> 深检(test_all.sh)每次最多跑 {MAX_DEEP_CHECKS} 个候选分支,本次实际跑了 {deep_checked} 个,以控制机器负载。",
        "",
        "| 分支 | 最后提交 | 落后/领先 main | 状态 | 说明 |",
        "|---|---|---|---|---|",
    ]
    order = {
        "MERGEABLE": 0,
        "GUARD_FAIL": 1,
        "CANDIDATE": 2,
        "CONFLICT": 3,
        "ACTIVE_WORKTREE": 4,
        "STALE_PARALLEL": 5,
        "FROZEN": 6,
        "NO_NEW_COMMITS": 7,
    }
    for info in sorted(infos, key=lambda i: order.get(i.status, 99)):
        detail = info.detail.replace("\n", "<br>")
        lines.append(
            f"| `{info.name}` | {info.last_commit} | -{info.behind}/+{info.ahead} | "
            f"**{info.status}** | {detail} |"
        )
    lines += [
        "",
        "## 状态说明",
        "- `MERGEABLE`: 机械信号+test_all.sh 全绿,可交给人工评审后合并。",
        "- `GUARD_FAIL`: 无冲突但守卫/测试未过,需要人工看失败原因。",
        "- `CANDIDATE`: 未被深检(通常因为已达单次深检上限),下次运行会继续排队。",
        "- `CONFLICT`: 与 main 有冲突,需先 rebase/手动解决。",
        "- `ACTIVE_WORKTREE`: 分支当前被某个 worktree 检出,可能有人/某 agent 正在写,跳过。",
        "- `STALE_PARALLEL`: 已知活跃分支的旧并行版本,历史结论是别硬合,等主线收工。",
        "- `FROZEN`: 命中 owner 冻结名单,不评估。",
        "- `NO_NEW_COMMITS`: 相对 main 没有新提交,无可合并内容。",
        "",
        "## factor_store/panels 缓存 GC(dry-run,只读)",
        "> panels/ 是缓存区,不是资产区(资产 = manifests/ + scores/),详见"
        " `data_lake/factor_store/README.md`。本节只跑 dry-run 报告体积,"
        " **不执行 `--apply`,真正清理由人工确认后手动跑**。",
        "",
        gc_dry_run,
    ]
    return "\n".join(lines) + "\n"


def publish_report(content: str) -> None:
    """report/branch-status 分支从 origin/main 重建,只提交这一个文件,只推这一个分支。"""
    run(["git", "fetch", "origin", "main", "--quiet"], cwd=REPO)
    tmp = TMP_ROOT / "_report_publish"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    add = run(
        ["git", "worktree", "add", "-B", REPORT_BRANCH, str(tmp), "origin/main"],
        cwd=REPO,
    )
    if add.returncode != 0:
        print(f"[publish] worktree add 失败: {add.stderr}", file=sys.stderr)
        return
    try:
        out = tmp / "reports" / "ops" / "BRANCH_REPORT.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        run(["git", "add", "reports/ops/BRANCH_REPORT.md"], cwd=tmp)
        status = run(["git", "status", "--porcelain"], cwd=tmp).stdout
        if not status.strip():
            print("[publish] 报告内容与上次一致,跳过 commit/push")
            return
        today = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
        run(
            ["git", "commit", "-m", f"chore(report): 分支状态日报 {today}"],
            cwd=tmp,
        )
        push = run(
            ["git", "push", "origin", f"HEAD:{REPORT_BRANCH}", "--force-with-lease"],
            cwd=tmp,
        )
        if push.returncode != 0:
            print(f"[publish] push 失败: {push.stderr}", file=sys.stderr)
        else:
            print(f"[publish] 已推送 origin/{REPORT_BRANCH}")
    finally:
        run(["git", "worktree", "remove", "--force", str(tmp)], cwd=REPO)
        run(["git", "branch", "-D", REPORT_BRANCH], cwd=REPO)


def main() -> None:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    worktree_map = get_worktree_map()
    infos: list[BranchInfo] = []
    for name in list_branches():
        info = BranchInfo(name=name)
        info.last_commit = run(
            ["git", "log", "-1", "--format=%ci", name], cwd=REPO
        ).stdout.strip()[:16]
        ab = run(["git", "rev-list", "--left-right", "--count", f"{TRUNK}...{name}"], cwd=REPO).stdout.split()
        if len(ab) == 2:
            info.behind, info.ahead = int(ab[0]), int(ab[1])
        classify(info, worktree_map)
        infos.append(info)

    deep_checked = 0
    for info in infos:
        if info.status != "CANDIDATE":
            continue
        if deep_checked >= MAX_DEEP_CHECKS:
            continue
        ok, detail = deep_guard_check(info.name)
        info.status = "MERGEABLE" if ok else "GUARD_FAIL"
        info.detail = detail
        deep_checked += 1

    gc_dry_run = gc_panel_cache_dry_run()
    content = render_report(infos, deep_checked, gc_dry_run)
    REPORT_PATH.write_text(content, encoding="utf-8")
    publish_report(content)
    print(content)


if __name__ == "__main__":
    main()
