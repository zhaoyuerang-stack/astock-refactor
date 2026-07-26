"""测试发现完整性守卫(Task 16)。

历史:test_all.sh 手工逐文件列调用,漏掉 15 个 test_*.py 从不运行。本守卫扫描
factor_research/test_*.py 与 factor_research/tests/test_*.py,确认每个都能被 pytest 收集
(import 无误)。不再维护第二份手工清单 —— 新测试默认进套件。

违规(有文件无法收集 / 被排除)则 exit 1。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def discover_test_files() -> set[str]:
    """含 ≥1 个 `def test_` 函数的 test_*.py 才算 pytest 测试,必须被收集。
    0 测试函数的脚本式 test_*.py(__main__ 直跑)由 test_all.sh 单独 python3 调用,豁免。"""
    files = set()
    for pat in ("test_*.py", "tests/test_*.py"):
        for p in ROOT.glob(pat):
            src = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*def test_\w+", src, re.MULTILINE):
                files.add(str(p.relative_to(ROOT)))
    return files


def collected_test_files() -> tuple[set[str], str]:
    """跑 pytest --collect-only,返回(被收集的文件相对路径集合, 原始输出)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    out = proc.stdout + "\n" + proc.stderr
    collected = set()
    for line in out.splitlines():
        m = re.match(r"^((?:tests/)?test_[\w/]+\.py)(::|: )", line.strip())
        if m:
            collected.add(m.group(1))
    return collected, out


def main() -> int:
    discovered = discover_test_files()
    collected, raw = collected_test_files()

    if "error" in raw.lower() and "errors" in raw.lower():
        # 收集期报错(import 失败)→ 直接失败,打印线索
        print("❌ pytest 收集期报错:")
        for line in raw.splitlines():
            if "error" in line.lower():
                print("  " + line)
        return 1

    missing = sorted(discovered - collected)
    if missing:
        print(f"❌ 以下 {len(missing)} 个 test_*.py 未被 pytest 收集(静默排除):")
        for m in missing:
            print("  " + m)
        return 1

    print(f"✅ 测试发现完整:{len(discovered)} 个 test_*.py 全部被 pytest 收集。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
