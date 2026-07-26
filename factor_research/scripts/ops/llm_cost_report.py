#!/usr/bin/env python3
"""LLM 成本月度汇总(P2 ②,2026-07-22):读 providers/llm_ledger 落账 JSONL。

读 ``reports/llm_cost/llm_cost_YYYYMM.jsonl``(运维观测账,非数据湖;``--dir`` 或
``ASTOCK_LLM_COST_DIR`` 覆盖),按月透视 capability × model:调用数/token(api 与
estimated 分列)/成本/失败率/平均延迟 + caller 分布。

成本口径(与 llm_ledger 同款诚实):cost_usd 仅费率表命中的调用有值;汇总给
"成本合计 + 覆盖调用数",未配费率的调用单独计数,不混入合计、不编造。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = Path(os.environ.get("ASTOCK_LLM_COST_DIR", str(ROOT / "reports" / "llm_cost")))


def load_events(dir_path: Path, month: str | None = None) -> dict[str, list[dict]]:
    """账文件 → {month: [event, ...]};month 指定时只读该月,坏行跳过不阻断。"""
    if month is None:
        files = sorted(dir_path.glob("llm_cost_*.jsonl"))
    else:
        files = [dir_path / f"llm_cost_{month}.jsonl"]
    out: dict[str, list[dict]] = {}
    for f in files:
        if not f.exists():
            continue
        events = []
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue  # 半行/损坏行:跳过,不阻断汇总
        out[f.stem.removeprefix("llm_cost_")] = events
    return out


def _blank_row() -> dict:
    return {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "cost_known_calls": 0, "estimated_calls": 0, "latency_ms": 0}


def _acc(row: dict, ev: dict) -> None:
    row["calls"] += 1
    row["errors"] += 1 if ev.get("outcome") == "error" else 0
    pt, ct = ev.get("prompt_tokens"), ev.get("completion_tokens")
    row["prompt_tokens"] += pt if isinstance(pt, int) else 0
    row["completion_tokens"] += ct if isinstance(ct, int) else 0
    cost = ev.get("cost_usd")
    if isinstance(cost, (int, float)):
        row["cost_usd"] += cost
        row["cost_known_calls"] += 1
    row["estimated_calls"] += 1 if ev.get("token_source") == "estimated" else 0
    lat = ev.get("latency_ms")
    row["latency_ms"] += lat if isinstance(lat, (int, float)) else 0


def summarize(events: list[dict]) -> dict:
    """纯函数:账行 → {rows: {(cap, model): row}, callers: {caller: n}, total: row}。"""
    rows: dict[tuple[str, str], dict] = {}
    callers: dict[str, int] = {}
    total = _blank_row()
    for ev in events:
        key = (str(ev.get("capability") or "?"), str(ev.get("model") or "?"))
        row = rows.setdefault(key, _blank_row())
        _acc(row, ev)
        _acc(total, ev)
        caller = ev.get("caller")
        if caller:
            callers[str(caller)] = callers.get(str(caller), 0) + 1
    return {"rows": rows, "callers": callers, "total": total}


def _fmt_cost(row: dict) -> str:
    if row["cost_known_calls"] == 0:
        return f"—(0/{row['calls']} 配费率)"
    s = f"${row['cost_usd']:.4f}"
    if row["cost_known_calls"] < row["calls"]:
        s += f"({row['cost_known_calls']}/{row['calls']} 配费率)"
    return s


def render(month: str, summary: dict) -> str:
    """汇总结构 → 文本报表(纯函数,便于单测)。"""
    t = summary["total"]
    lines = [f"== {month} ==  无账" if t["calls"] == 0 else f"== {month} =="]
    if t["calls"] == 0:
        return "\n".join(lines)
    err_rate = t["errors"] / t["calls"]
    avg_lat_s = t["latency_ms"] / t["calls"] / 1000
    lines.append(
        f"调用 {t['calls']}(失败 {t['errors']}, {err_rate:.1%}) | "
        f"prompt {t['prompt_tokens']:,} tok | completion {t['completion_tokens']:,} tok | "
        f"成本 {_fmt_cost(t)} | 平均延迟 {avg_lat_s:.1f}s | estimated {t['estimated_calls']} 行"
    )
    lines.append(f"  {'capability':<11}{'model':<24}{'calls':>6}{'err':>5}"
                 f"{'prompt_tok':>12}{'compl_tok':>11}  cost")
    for (cap, model), r in sorted(summary["rows"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        lines.append(f"  {cap:<11}{model:<24}{r['calls']:>6}{r['errors']:>5}"
                     f"{r['prompt_tokens']:>12,}{r['completion_tokens']:>11,}  {_fmt_cost(r)}")
    if summary["callers"]:
        dist = "  ".join(f"{k}={v}" for k, v in sorted(summary["callers"].items(), key=lambda kv: -kv[1]))
        lines.append(f"caller 分布: {dist}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM 成本月度汇总(providers/llm_ledger 账)")
    parser.add_argument("--month", help="只汇总指定月,格式 YYYYMM;默认全部月份")
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="账目录(默认 reports/llm_cost)")
    args = parser.parse_args(argv)
    months = load_events(args.dir, args.month)
    if not months:
        print(f"无账({args.dir} 下无 llm_cost_*.jsonl)" + (f",月份 {args.month}" if args.month else ""))
        return 0
    for month in sorted(months):
        print(render(month, summarize(months[month])))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
