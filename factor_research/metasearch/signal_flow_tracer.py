"""Signal Flow Tracer — 找"返回了但没人用"的函数输出。

设计灵感: Band 发现的根因。small_cap_timing 6 个月一直返回
  return timing, small_nav, dist
但所有调用方都 `timing, _, _ = small_cap_timing(...)` 丢弃 dist。
直到用户人工质疑才发现 dist 是金矿。

本工具自动 AST 扫描所有 .py 找这种 "_ 丢弃" 模式,提示:
  · 哪些 return 值被丢弃了
  · 多少处调用都丢
  · 是否一致丢同一个索引 (强提示该输出被默认忽略)

用法:
  python3 -m metasearch.signal_flow_tracer

输出:
  metasearch/unused_signals.json — 完整报告
  stdout — 高优先级候选 (丢弃率 > 50%)
"""
import ast
import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["factor_research", "factor_research/strategies", "factor_research/portfolio"]
SKIP_DIRS = {"__pycache__", "data_lake", "data_full", "reports", "logs"}


@dataclass(frozen=True)
class UnusedSignal:
    """一次 tuple unpack 丢弃事件."""
    caller_file: str            # 调用方文件
    caller_line: int
    callee_name: str            # 被调用的函数名
    discarded_indices: tuple    # 被 _ 丢弃的索引位置
    total_outputs: int          # 总返回数
    full_pattern: str           # "(timing, _, _) = small_cap_timing(...)"


def _is_underscore(node) -> bool:
    return isinstance(node, ast.Name) and node.id == "_"


def _get_callee_name(call: ast.Call) -> str | None:
    """提取被调用的函数名,如 'small_cap_timing' 或 'foo.bar.baz'."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def scan_module(file_path: Path) -> Iterator[UnusedSignal]:
    """扫描单个 .py 文件,yield 所有 _ 丢弃事件."""
    try:
        source = file_path.read_text()
        tree = ast.parse(source)
    except (UnicodeDecodeError, SyntaxError):
        return

    for node in ast.walk(tree):
        # Pattern: a, _, b = some_call(...)
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Tuple):
            continue

        underscores = [i for i, e in enumerate(target.elts) if _is_underscore(e)]
        if not underscores:
            continue

        # RHS must be a Call
        if not isinstance(node.value, ast.Call):
            continue

        callee = _get_callee_name(node.value)
        if not callee:
            continue

        # Build human-readable pattern
        elt_strs = ["_" if _is_underscore(e) else
                    (e.id if isinstance(e, ast.Name) else "?")
                    for e in target.elts]
        pattern = f"{', '.join(elt_strs)} = {callee}(...)"

        yield UnusedSignal(
            caller_file=str(file_path.relative_to(ROOT.parent)),
            caller_line=node.lineno,
            callee_name=callee,
            discarded_indices=tuple(underscores),
            total_outputs=len(target.elts),
            full_pattern=pattern,
        )


def audit_unused_signals(scan_dirs=None) -> list[UnusedSignal]:
    """扫描所有目录,返回扁平的 UnusedSignal 列表."""
    out: list[UnusedSignal] = []
    base = ROOT.parent
    scan_dirs = scan_dirs or SCAN_DIRS
    for d in scan_dirs:
        root_d = base / d
        if not root_d.exists():
            continue
        for fp in root_d.rglob("*.py"):
            if any(part in SKIP_DIRS for part in fp.parts):
                continue
            out.extend(scan_module(fp))
    return out


def summarize(events: list[UnusedSignal]) -> dict:
    """汇总: 每个 callee 的丢弃模式分布."""
    by_callee = defaultdict(list)
    for e in events:
        by_callee[e.callee_name].append(e)

    summary = {}
    for callee, evs in by_callee.items():
        total_outputs = evs[0].total_outputs
        # 每个 output 索引被丢弃的次数
        discard_counts = [0] * total_outputs
        for e in evs:
            for i in e.discarded_indices:
                if i < total_outputs:
                    discard_counts[i] += 1
        n_calls = len(evs)
        # 丢弃率
        discard_rates = [c / n_calls for c in discard_counts]
        summary[callee] = {
            "n_calls": n_calls,
            "total_outputs": total_outputs,
            "discard_counts": discard_counts,
            "discard_rates": discard_rates,
            "patterns": [e.full_pattern for e in evs[:3]],   # 样例
        }
    return summary


def main():
    print("Scanning for unused tuple unpack signals...")
    events = audit_unused_signals()
    summary = summarize(events)
    print(f"  Total _ events: {len(events)}")
    print(f"  Unique callees: {len(summary)}")

    # Write full report
    out_path = ROOT / "metasearch" / "unused_signals.json"
    out_path.write_text(json.dumps({
        "summary": summary,
        "events": [asdict(e) for e in events],
    }, ensure_ascii=False, indent=2, default=str))
    print(f"  Full report: {out_path}")

    # High-priority candidates: ≥3 calls AND ≥1 output discarded ≥50% time
    print(f"\n{'='*70}")
    print("  HIGH PRIORITY — 默认被忽略的输出")
    print(f"{'='*70}")
    print(f"  {'callee':<35} {'#calls':>7} {'output[i]':>10} {'discard%':>10}")
    print(f"  {'-'*68}")

    rows = []
    for callee, s in summary.items():
        if s["n_calls"] < 2:
            continue
        for i, rate in enumerate(s["discard_rates"]):
            if rate >= 0.5:
                rows.append((rate, callee, s["n_calls"], i, s["total_outputs"]))

    rows.sort(reverse=True)
    if not rows:
        print("  (无高优先级候选)")
    for rate, callee, n, idx, total in rows[:20]:
        print(f"  {callee[:35]:<35} {n:>7d} {idx}/{total-1:<6d} {rate:>9.0%}")

    print("\n💡 这些'默认被忽略'的输出是下一个 Band 候选。")
    print("  每个都问: 这个被丢的信号能不能做出新策略? 像 dist 之于 Band。")


if __name__ == "__main__":
    main()
