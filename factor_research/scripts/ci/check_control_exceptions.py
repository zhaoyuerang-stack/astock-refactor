"""控制路径静默异常守卫(Task 17 / 守卫审计 #7)。

控制路径(准入/裁决/信号/执行/可用性/Agent 控制面)里的静默 handler 会把异常
悄悄吞成「通过」——这是防自欺体系最危险的漏洞。本守卫 AST 扫描下列路径,
禁止 handler 体仅为:
  - pass / ...(Ellipsis)
  - continue
  - 裸 return(无值)

**不扩** `return None` / `return {}` / `return 'BLOCKED'` 等带值返回——后者
可能是合法 fail-closed,误报风险大(审计 #7 边界)。允许的处理必须有动作
(log / raise / 返回失败态字符串)。

只读 AST,违规 exit 1。检测函数吃源码字符串,便于 fixture 测试。
存量命中进 PENDING_REMEDIATION(响而不阻),新增即红。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 控制路径清单:文件显式 + 目录 glob(ADR-037 services/agent/ 今后新增自动纳入)
CONTROL_PATHS_FILES = [
    "core/analysis/nine_gates.py",
    "core/analysis/nine_gate_policy.py",
    "services/read/trade_readiness.py",
    "governance/holdout.py",
    "runtime/production_readiness.py",
    "runtime/deployment.py",
    "workflow/admission.py",
    "workflow/promote.py",
    "workflow/phase4_register.py",
    "governance/state_machine.py",
    "governance/control_events.py",
    "portfolio/paper_engine.py",
    "portfolio/paper_accounts.py",
    "apps/agent_cli.py",  # ADR-037 控制面 CLI
]
CONTROL_PATH_GLOBS = [
    "services/agent/*.py",  # ADR-037 Agent 控制面(目录级,新增自动纳入)
]

# 存量欠债。响而不阻;修复后须从此处移除。
# ADR-038:agent 面四处已改 log.warning 留痕 → control PENDING 清零。
PENDING_REMEDIATION: dict[str, str] = {}


def resolve_control_paths(root: Path | None = None) -> list[str]:
    """解析控制路径清单(文件 + glob),返回相对 root 的路径列表。"""
    base = root or ROOT
    out: list[str] = []
    seen: set[str] = set()
    for rel in CONTROL_PATHS_FILES:
        if rel not in seen:
            out.append(rel)
            seen.add(rel)
    for pattern in CONTROL_PATH_GLOBS:
        for p in sorted(base.glob(pattern)):
            if not p.is_file() or p.suffix != ".py":
                continue
            rel = str(p.relative_to(base))
            if rel not in seen:
                out.append(rel)
                seen.add(rel)
    return out


# 向后兼容:测试/外部可能仍读 CONTROL_PATHS 名字
CONTROL_PATHS = resolve_control_paths()


def _body_is_silent(handler: ast.ExceptHandler) -> bool:
    """handler 体是否纯吞。

    命中:仅 pass / ... / continue / 裸 return(无值)。
    不命中:return None / return {} / return 'X' / log / raise —— 带值返回
    可能是合法 fail-closed,不扩(审计 #7 边界)。
    """
    body = [n for n in handler.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                            and isinstance(n.value.value, str))]  # 去掉 docstring
    if len(body) != 1:
        return False
    only = body[0]
    if isinstance(only, ast.Pass):
        return True
    if isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) and only.value.value is Ellipsis:
        return True
    if isinstance(only, ast.Continue):
        return True
    # 裸 return(无值);return None 是 Return(value=Constant(None)),不在此列
    if isinstance(only, ast.Return) and only.value is None:
        return True
    return False


def scan_source(src: str, label: str = "") -> list[str]:
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"[{label}] 语法错误,无法扫描: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and _body_is_silent(node):
            out.append(f"[{label}:L{node.lineno}] 控制路径静默吞异常"
                       f"—— 必须 log / raise / 返回 UNKNOWN|FAILED_TO_RUN|BLOCKED,不得转成通过")
    return out


def _violation_key(msg: str) -> str:
    """从 `[rel:L99] ...` 提取 `rel:L99`。"""
    if not msg.startswith("["):
        return msg
    return msg.split("]", 1)[0][1:]


def main(root: Path | None = None) -> int:
    base = root or ROOT
    paths = resolve_control_paths(base)
    raw: list[str] = []
    for rel in paths:
        p = base / rel
        if not p.exists():
            continue
        raw += scan_source(p.read_text(encoding="utf-8"), label=rel)

    new_v = []
    pending = []
    for msg in raw:
        key = _violation_key(msg)
        if key in PENDING_REMEDIATION:
            pending.append((key, msg))
        else:
            new_v.append(msg)

    for key, msg in pending:
        print(f"  ⚠️ 待处置(基线): {msg} — {PENDING_REMEDIATION[key]}")

    if new_v:
        print("❌ 控制路径静默异常守卫失败:")
        for v in new_v:
            print("  " + v)
        return 1
    print(
        f"✅ 控制路径静默异常守卫通过(扫描 {len(paths)} 个控制模块,"
        f"无新增静默吞;{len(pending)} 项待处置已基线)。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
