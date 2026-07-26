#!/usr/bin/env python3
"""check_print_budget.py — print 预算 ratchet 守卫(P1-3,2026-07-22)。

背景:库层曾 156 处裸 print 散喷 stdout,无时间戳/级别/模块名,观测不可审计。
P1-3 已把 workflow/factory/services 库路径 print 全部归并 get_logger(commit
5c560873);app_config/log.py 是唯一 logger 工厂。本守卫防回退。

口径(AST 计数,只数裸 ``print(...)`` 调用,docstring/注释/``x.print`` 不计):

- **默认拒止**:factor_research/ 下所有 .py 裸 print=0,除 EXEMPT_DIRS 与
  WHITELIST——新目录/新文件自动被覆盖,无需维护零违规目录清单;
- **EXEMPT_DIRS**:scripts/(CLI 正当)、tests/(测试正当)、scratch/(探索沙盒,
  AGENTS.md 定位)、apps/(CLI 应用表示层)、data_lake/(数据 payload);
- **WHITELIST {文件: 冻结计数}**:存量正当 CLI 表示层(台账表格/操作台进度/
  main() 终态)与研究报告打印层,**只降不升**——超出报错;低于冻结值或清零时
  提示收紧(不阻断)。确需新增保留 print 的正当 CLI 界面,须在本表冻结计数并
  在 commit message 说明理由;
- 库层输出唯一入口:``from app_config.log import get_logger``。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # factor_research/

EXEMPT_DIRS = {"scripts", "tests", "scratch", "apps", "data_lake"}

WHITELIST: dict[str, int] = {
    # ── CLI 表示层(表格/进度/终态),正当 print ──
    "strategy_registry.py": 18,  # cmd_list/cmd_migrate 台账表格
    "strategy_lake.py": 6,  # run_backtest 结果打印
    "run_daily.py": 42,  # 日更管线操作台进度
    "test_engine.py": 16,  # 根目录脚本式测试(test_all.sh 直跑)
    "validate_final.py": 7,  # 验证脚本
    "workflow/promote.py": 1,  # __main__ 用法提示
    "factory/autoresearch/agent_loop.py": 3,  # main() ❌/✨ 终态
    # ── 研究报告打印层(信息距离矩阵/分布表)──
    "metasearch/factor_mi_audit.py": 23,
    "metasearch/information_map.py": 28,
    "metasearch/signal_flow_tracer.py": 13,
}


def count_prints(path: Path) -> int:
    """AST 数裸 print(...) 调用;解析失败的文件交给 ruff F 门禁,本守卫不计。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "print"
    )


def main() -> int:
    errors: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.split("/", 1)[0] in EXEMPT_DIRS or "__pycache__" in path.parts:
            continue
        n = count_prints(path)
        if n == 0:
            continue
        seen.add(rel)
        budget = WHITELIST.get(rel)
        if budget is None:
            errors.append(
                f"{rel}: {n} 处裸 print(非白名单文件;库层/新增文件一律走 get_logger)"
            )
        elif n > budget:
            errors.append(
                f"{rel}: {n} 处裸 print,超白名单冻结值 {budget}"
                "(只降不升;新增请走 get_logger)"
            )
        elif n < budget:
            notes.append(f"{rel}: {n} < 冻结值 {budget},可收紧白名单")
    for rel, _budget in sorted(WHITELIST.items()):
        if rel in seen:
            continue
        if (ROOT / rel).exists():
            notes.append(f"{rel}: print 已清零,可从白名单删除")
        else:
            notes.append(f"{rel}: 文件不存在,可从白名单删除")
    for msg in notes:
        print(f"NOTE: {msg}")
    if errors:
        for msg in errors:
            print(f"❌ {msg}")
        print("\n库层输出唯一入口:from app_config.log import get_logger(__name__)")
        print("正当 CLI 界面(main()/__main__/表格展示)确需保留 print 的,")
        print("在本守卫 WHITELIST 冻结计数并在 commit message 说明理由。")
        return 1
    print(f"print 预算检查通过(默认拒止 + 白名单 {len(WHITELIST)} 项冻结,只降不升)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
