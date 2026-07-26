"""Holdout 合规守卫——自动选择路径必须把择优数据截到 <boundary(LOOP_ENGINEERING §5.2)。

背景(§5.2 缝③):holdout 边界此前靠各脚本手工加,默认就漏——全仓 80+ 处 load 全样本,
只有少数截断。任何**在自动环里 load 全样本并据此择优/排序/边际定级**的路径,若不截到
<boundary,就是"loop 偷看金库" = 工业化自欺。本守卫锁定已知的自动选择路径:每个必须引用
boundary() / assert_search_clean / validate_on_holdout 之一。

新增自动选择路径(load 全样本 + 排序/择优/边际定级)时:① 把择优数据截到 <boundary;
② 把文件加进 REQUIRED。纯监控/报表/实盘信号(decay_monitor/tradability/dashboard/
paper_trade/live_readiness)合法使用全/近期数据,**不是选择**,不在此列。

**扫描边界(守卫审计 #2,刻意保留)**:
  - `scan_direct_holdout_access` 扫 `scripts/research/` + `factory/` + `workflow/`
    + `services/actions/`
  - **不加** `scripts/ops/` / `portfolio/`——监控/生产路径合法用全样本
  - 金库字面量:任意 ISO 日期常量 `YYYY-MM-DD` 且 `>= EXPECTED_BOUNDARY` 均视为
    金库引用(不只精确 "2025-01-01");仅用于既有三类判定(>= 比较、从边界起切片、
    传入评估调用),不新增判定类别;`< boundary` 截断语义不变
"""
import ast
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 已知的"自动选择路径":load 全样本后据此择优/排序/打分/验证 → 必须 holdout 截断。
REQUIRED = {
    "scripts/ops/scheduled_factor_search.py": "周度因子搜索 + 9-Gate 选择",
    "portfolio/cross_asset.py": "跨资产防御腿 Δsharpe 择优",
    "workflow/promote.py": "_run_marginal 边际 ACTIVE/SHADOW 定级",
    "workflow/phase2_backtest.py": "三段回测/成本/相关性/decay 验证栈(ADR-021)",
    "workflow/phase3_wf.py": "walk-forward 训练/测试窗口(ADR-021)",
}
BOUND = re.compile(r"boundary\(|assert_search_clean|validate_on_holdout")

# 直接金库访问扫描目录(研究/工厂/工作流/动作;不含 ops/portfolio 监控生产路径)
DIRECT_HOLDOUT_SCAN_DIRS = (
    "scripts/research",
    "factory",
    "workflow",
    "services/actions",
)

# 存量欠债。响而不阻;修复后须从此处移除。
# ADR-038 决策二:paper_forward 改走 MONITORED_EXEMPT 显式豁免 → holdout PENDING 清零。
PENDING_REMEDIATION: dict[str, str] = {}

# 显式豁免(ADR-038 决策二):非「待处置」语义,须带 ADR + rationale 留痕。
# 照 check_layer_deps.ALLOWED_IMPORT_EXCEPTIONS 范式;缺键 → 守卫自身 exit 1。
MONITORED_EXEMPT: dict[str, dict] = {
    "scripts/research/paper_forward_smallcap.py": {
        "adr": "ADR-024",
        "rationale": (
            "纸面前向观察旁路:读金库前向段是实验目的,非自动选择/择优路径"
        ),
    },
}

# ── P0-B(ADR-021):锁定 holdout.start 配置值 ──
# boundary 是软配置(settings.yaml::holdout.start),改它 = 改金库范围:原受保护的金库段
# 突然变「可搜索」,所有基于旧 boundary 的 validate_on_holdout 记录失去唯一性意义。
# 此处把当前值的 hash 钉死;任何改动让守卫 exit 1,强制走 DECISIONS(ADR)+ 更新本 pin。
SETTINGS_YAML = ROOT / "app_config" / "settings.yaml"
EXPECTED_BOUNDARY = "2025-01-01"
EXPECTED_BOUNDARY_HASH = "14973c591b26d5116b3ce3508c60adfe345a1201723a45f18dacc0293da2ec7a"
# ISO 日期常量且 >= EXPECTED_BOUNDARY → 金库字面量(审计 #2 泛化)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> set[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {
        child.id
        for target in targets
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    }


def _is_boundary_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id == "boundary"
    return isinstance(node.func, ast.Attribute) and node.func.attr == "boundary"


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _is_vault_date_constant(value: object) -> bool:
    """ISO 日期字符串常量且 >= EXPECTED_BOUNDARY → 视为金库字面量(审计 #2)。

    仅用于既有三类违规判定,不新增类别。起始日如 "2018-01-01"(< boundary)不命中。
    """
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return False
    return value >= EXPECTED_BOUNDARY  # ISO YYYY-MM-DD 字典序 = 时间序


def _contains_boundary_ref(node: ast.AST | None, names: set[str]) -> bool:
    if node is None:
        return False
    return any(
        (isinstance(child, ast.Name) and child.id in names)
        or (isinstance(child, ast.Constant) and _is_vault_date_constant(child.value))
        for child in ast.walk(node)
    )


def _is_boundary_scalar(node: ast.AST | None, names: set[str]) -> bool:
    """仅传播边界/金库标量别名，不把 ``frame[frame.index < b]`` 的结果污染为边界。"""
    if node is None:
        return False
    if _is_boundary_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Constant):
        return _is_vault_date_constant(node.value)
    return (
        isinstance(node, ast.Call)
        and _call_name(node) in {"Timestamp", "to_datetime"}
        and any(_contains_boundary_ref(arg, names) for arg in node.args)
    )


def _slice_starts_at_boundary(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, ast.Slice):
        return _contains_boundary_ref(node.lower, names)
    if isinstance(node, ast.Tuple):
        return any(_slice_starts_at_boundary(elt, names) for elt in node.elts)
    return False


def scan_direct_holdout_access(src: str, label: str = "") -> list[str]:
    """识别直接读取 >= boundary 的研究代码，不依赖 search/rank 等函数命名。

    允许 ``index < boundary`` 的搜索窗截断；拒绝定义 HOLDOUT_START、从 boundary
    开始切片、``index >= boundary``，以及把边界或其别名传给评估函数。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"[{label}] 语法错误，无法证明 holdout 合规: {exc}"]

    boundary_names: set[str] = set()
    direct_start_names: set[str] = set()
    violations: list[str] = []
    assignments: list[ast.Assign | ast.AnnAssign] = [
        node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for node in assignments:
        names = _assigned_names(node)
        for name in names:
            lowered = name.lower()
            if "holdout" in lowered and "start" in lowered:
                direct_start_names.add(name)
                violations.append(
                    f"[{label}:L{node.lineno}] 研究代码不得定义 {name};"
                    "金库只能由 governance.holdout.validate_on_holdout 消费"
                )

    # 固定点传播：START="2025-01-01"、b=boundary()、alias=b 都视为边界引用。
    changed = True
    while changed:
        changed = False
        for node in assignments:
            names = _assigned_names(node)
            if _is_boundary_scalar(node.value, boundary_names):
                new_names = names - boundary_names
                if new_names:
                    boundary_names.update(new_names)
                    changed = True

    boundary_names.update(direct_start_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _slice_starts_at_boundary(node.slice, boundary_names):
            violations.append(
                f"[{label}:L{node.lineno}] 检测到从 holdout boundary 开始的直接切片"
            )
        elif isinstance(node, ast.Compare):
            left = node.left
            for op, right in zip(node.ops, node.comparators, strict=True):
                reads_after_boundary = (
                    isinstance(op, (ast.Gt, ast.GtE))
                    and _contains_boundary_ref(right, boundary_names)
                ) or (
                    isinstance(op, (ast.Lt, ast.LtE))
                    and _contains_boundary_ref(left, boundary_names)
                )
                if reads_after_boundary:
                    violations.append(
                        f"[{label}:L{node.lineno}] 检测到 >= holdout boundary 的直接访问"
                    )
                    break
                left = right
        elif isinstance(node, ast.Call):
            call_name = _call_name(node).lower()
            call_args = [*node.args, *[kw.value for kw in node.keywords]]
            evaluation_call = any(
                token in call_name
                for token in ("evaluate", "backtest", "metric", "screen", "probe", "window")
            )
            if evaluation_call and any(
                _contains_boundary_ref(arg, boundary_names) for arg in call_args
            ):
                violations.append(
                    f"[{label}:L{node.lineno}] holdout boundary 被直接传入评估调用，"
                    "绕过唯一消费入口"
                )
    return violations


def check_boundary_lock() -> list[tuple[str, str]]:
    """holdout.start 必须 == 钉死值,否则违规(改金库须 ADR + 更新本 pin)。"""
    try:
        import yaml
        cfg = yaml.safe_load(SETTINGS_YAML.read_text(encoding="utf-8")) or {}
        start = str((cfg.get("holdout") or {}).get("start", ""))
    except Exception as exc:  # noqa: BLE001 — 读不到配置即视为违规,不静默
        return [("app_config/settings.yaml", f"无法读取 holdout.start: {exc}")]
    h = hashlib.sha256(start.encode()).hexdigest()
    if h != EXPECTED_BOUNDARY_HASH:
        return [(
            "app_config/settings.yaml::holdout.start",
            f"金库边界被改动:当前 {start!r}(hash {h[:12]})≠ 钉死 {EXPECTED_BOUNDARY!r}"
            f"(hash {EXPECTED_BOUNDARY_HASH[:12]})。改金库 = 改唯一性语义,须先记 DECISIONS(ADR)"
            f"并同步更新 check_holdout_compliance.py 的 EXPECTED_BOUNDARY[_HASH]。",
        )]
    return []


def check_boundary_monotonic() -> list[tuple[str, str]]:
    """ADR-023:强制 boundary 只进不退,且 settings.holdout.start == 历史账本最大值。

    边界历史账本(app_config/holdout_boundary_history.jsonl,git 跟踪)是 active 金库的权威:
      ① 账本必须存在且非空(genesis 基线);
      ② 严格递增(任一条 <= 前一条 = 后退/重复 = 复活已偷看金库 → 违规);
      ③ settings.holdout.start 必须 == 账本最大值——手改前进(未经 migrate 记录)或后退都判违规。
    推进金库的唯一合法路径 = governance.holdout.migrate_holdout_boundary()。
    """
    import json
    from datetime import date as _date
    hist_path = ROOT / "app_config" / "holdout_boundary_history.jsonl"
    if not hist_path.exists():
        return [("app_config/holdout_boundary_history.jsonl",
                 "边界历史账本缺失:需 genesis 基线(ADR-023 强制)。")]
    hist = []
    for lineno, line in enumerate(hist_path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                hist.append(json.loads(line))
            except json.JSONDecodeError as exc:
                return [(
                    "app_config/holdout_boundary_history.jsonl",
                    f"boundary history line {lineno} JSON 损坏: {exc}",
                )]
    if not hist:
        return [("app_config/holdout_boundary_history.jsonl",
                 "边界历史账本为空:需 genesis 基线(ADR-023 强制)。")]
    try:
        bs = [_date.fromisoformat(str(h["boundary"])) for h in hist]
    except Exception as exc:  # noqa: BLE001
        return [("app_config/holdout_boundary_history.jsonl", f"账本解析失败: {exc}")]
    out = []
    for prev, cur in zip(bs, bs[1:], strict=False):
        if cur <= prev:
            out.append(("app_config/holdout_boundary_history.jsonl",
                        f"边界非严格递增:{cur} <= {prev} —— 后退/重复 = 复活已偷看金库(只进不退)。"))
    try:
        import yaml
        cfg = yaml.safe_load(SETTINGS_YAML.read_text(encoding="utf-8")) or {}
        settings_b = _date.fromisoformat(str((cfg.get("holdout") or {}).get("start", "")))
    except Exception as exc:  # noqa: BLE001
        return out + [("app_config/settings.yaml", f"无法读取/解析 holdout.start: {exc}")]
    active = max(bs)
    if settings_b != active:
        out.append((
            "app_config/settings.yaml::holdout.start",
            f"settings.holdout.start={settings_b} ≠ 历史账本 active={active}。"
            f"推进金库须经 migrate_holdout_boundary()(记账+作废旧金库)后再同步 settings+pin;"
            f"后退则被只进不退禁止。",
        ))
    return out


def validate_monitored_exempt(exempt: dict[str, dict] | None = None) -> list[str]:
    """MONITORED_EXEMPT 自检:每条必须含非空 adr 与 rationale,否则守卫自身失败。"""
    table = MONITORED_EXEMPT if exempt is None else exempt
    errors: list[str] = []
    for rel, meta in table.items():
        if not isinstance(meta, dict):
            errors.append(f"MONITORED_EXEMPT[{rel!r}] 必须为 dict,含 adr/rationale")
            continue
        if not meta.get("adr"):
            errors.append(f"MONITORED_EXEMPT[{rel!r}] 缺必填键 adr(须引用 ADR)")
        if not meta.get("rationale"):
            errors.append(f"MONITORED_EXEMPT[{rel!r}] 缺必填键 rationale")
    return errors


def scan_direct_holdout_dirs(
    root: Path | None = None,
    *,
    exempt_rels: set[str] | None = None,
    note_exempt: bool = False,
) -> list[tuple[str, str]]:
    """扫 DIRECT_HOLDOUT_SCAN_DIRS 下直接金库访问(可注入 root 供 fixture)。

    不含 scripts/ops、portfolio——监控/生产合法全样本(见模块 docstring)。
    exempt_rels 中的相对路径跳过 scan_direct_holdout_access(ADR-038 显式豁免)。
    """
    base = root or ROOT
    skip = exempt_rels if exempt_rels is not None else set(MONITORED_EXEMPT)
    out: list[tuple[str, str]] = []
    for rel_dir in DIRECT_HOLDOUT_SCAN_DIRS:
        d = base / rel_dir
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.py")):
            if "archive" in path.parts or "__pycache__" in path.parts:
                continue
            rel = str(path.relative_to(base)).replace("\\", "/")
            if rel in skip:
                if note_exempt:
                    meta = MONITORED_EXEMPT.get(rel) or {}
                    adr = meta.get("adr", "?")
                    print(f"  ℹ️ 豁免({adr}): {rel}")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for message in scan_direct_holdout_access(text, rel):
                out.append((rel, message))
    return out


def main(root: Path | None = None) -> int:
    base = root or ROOT
    # ADR-038:豁免表缺 adr/rationale → 守卫自身 exit 1(不依赖业务扫描)
    exempt_errors = validate_monitored_exempt()
    if exempt_errors:
        print("🚨 MONITORED_EXEMPT 配置非法(ADR-038 决策二自检):")
        for msg in exempt_errors:
            print(f"  - {msg}")
        return 1

    violations: list[tuple[str, str]] = []
    # 配置锁/账本只对真实 ROOT 有意义(fixture root 无 settings 时跳过)
    if base == ROOT:
        violations.extend(check_boundary_lock())  # P0-B:金库边界配置锁
        violations.extend(check_boundary_monotonic())  # ADR-023:边界只进不退 + 账本一致
    violations.extend(scan_direct_holdout_dirs(base, note_exempt=(base == ROOT)))
    for rel, why in REQUIRED.items():
        p = base / rel
        if base != ROOT and not p.exists():
            continue  # fixture 不必带齐 REQUIRED
        if not p.exists():
            violations.append((rel, "文件不存在(REQUIRED 名单过期?)"))
            continue
        if not BOUND.search(p.read_text(encoding="utf-8")):
            violations.append((rel, f"自动选择路径({why})未引用 holdout 截断 → §5.2 缝③ 泄露"))
    scheduled_path = base / "scripts/ops/scheduled_factor_search.py"
    if scheduled_path.exists():
        scheduled = scheduled_path.read_text(encoding="utf-8")
        if "review_queue.all()" in scheduled:
            violations.append((
                "scripts/ops/scheduled_factor_search.py",
                "自动审计不得遍历 ReviewQueue.all();只能处理本轮新增 pending",
            ))
    for path in base.rglob("*.py"):
        if any(part in {"data_lake", "scratch", "__pycache__"} for part in path.parts):
            continue
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "validate_on_holdout(" not in text or path.name in {"holdout.py", "check_holdout_compliance.py"}:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name != "validate_on_holdout":
                continue
            keywords = {kw.arg for kw in node.keywords}
            if "spec_hash" not in keywords or "data_fingerprint" not in keywords:
                violations.append((
                    str(path.relative_to(base)).replace("\\", "/"),
                    "validate_on_holdout 调用缺 spec_hash/data_fingerprint 身份",
                ))
                break

    new_v: list[tuple[str, str]] = []
    pending: list[tuple[str, str]] = []
    for rel, msg in violations:
        if rel in PENDING_REMEDIATION:
            pending.append((rel, msg))
        else:
            new_v.append((rel, msg))

    for rel, msg in pending:
        print(f"  ⚠️ 待处置(基线): {rel}: {msg} — {PENDING_REMEDIATION[rel]}")

    if new_v:
        print("🚨 Holdout 合规违规(自动选择路径必须截到 <boundary,见 LOOP_ENGINEERING §5.2):")
        for rel, msg in new_v:
            print(f"  - {rel}: {msg}")
        return 1
    print(
        f"Holdout 合规检查通过({len(REQUIRED)} 个自动选择路径均已 holdout 截断;"
        f"{len(MONITORED_EXEMPT)} 项显式豁免;"
        f"{len(pending)} 项待处置已基线)。"
    )
    return 0



if __name__ == "__main__":
    sys.exit(main())
