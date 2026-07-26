"""Unified 9-Gate Evaluation CLI runner for all codebase strategies.

Supports:
- small_cap (small-cap-size)
- size_earnings (size-earnings)
- large_cap (large-cap-growth-hedged)
- hq_momentum (high quality momentum)

Usage:
  python3 scripts/research/run_nine_gates_all.py --strategy size_earnings
  python3 scripts/research/run_nine_gates_all.py --strategy large_cap

实现细节(run_evaluation 及其依赖的策略构建分支)已迁至 workflow/nine_gate_runner.py
(架构评审 2026-07-18:canonical 层的 R-WF-001 唯一执行点不应反向依赖 scripts/ 下的
研究脚本实现)。本文件只是薄 CLI 壳,re-export 该模块,CLI 行为与输出保持不变。
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow.nine_gate_runner import (  # noqa: E402
    audit_stale_registered,
    run_evaluation,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run 9-Gate Strategy Evaluator")
    parser.add_argument("--strategy",
                        help="Strategy name to run evaluation on")
    parser.add_argument("--audit-stale", action="store_true",
                        help="自动补审:扫描所有未审计的在册版本,对配置已知者自动跑并落台账")
    parser.add_argument("--trials", type=int, default=None,
                        help="多重检验试验数 N；缺省自动取该母策略台账迭代数（逐家族搜索广度）")
    parser.add_argument("--persist", action="store_true",
                        help="把 DSR/PSR/多重检验摘要写回台账对应版本的 nine_gate 字段")
    parser.add_argument("--version", default=None,
                        help="审计指定台账版本（如 v1.1 / v1.0-full）；自动套用该版本真实 config")
    parser.add_argument("--start", default=None, help="覆盖回测起始（如全历史变体 2012-01-01）")
    args = parser.parse_args()

    if args.audit_stale:
        audit_stale_registered(persist=args.persist)
    elif args.strategy:
        run_evaluation(args.strategy, args.trials, persist=args.persist,
                       version=args.version, start=args.start)
    else:
        parser.error("需指定 --strategy <name> 或 --audit-stale")
