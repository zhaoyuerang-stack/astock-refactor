"""分层依赖检查 — 防止跨层倒灌import。

用法:
    python3 scripts/ci/check_layer_deps.py

纯静态AST分析,不执行代码。规则定义见 FORBIDDEN_EDGES:
(source前缀, [禁止依赖的target前缀, ...])

例如 strategies.* 不允许 import factory.* / scripts.research.* / workflow.*,
因为生产组合构建层不应依赖探索层的实现细节。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 不参与分层检查的目录(测试/脚本工具自身)
EXCLUDE_DIRS = {"__pycache__", ".git", "data_lake", "signals", "reports", "logs", "scratch"}

# 架构评审(scripts/ 去库化)发现 scripts/ 目录事实上藏了两条 canonical→scripts
# 反向依赖边:lake.version_returns 反向 import scripts/ci/check_cost_model_pin
# 取 cost_hash;run_daily.py(生产层)反向 import scripts.data.update_lake。两条
# 已分别迁至 governance/cost_pin.py 与 lake/update.py。为防止同类边复发,对
# run_daily / lake. / core.engine / core.analysis / engine. 这五个 src 前缀把
# 原本只挡 scripts.research. 的黑名单扩为挡整个 scripts.(含 scripts.data /
# scripts.ops / scripts.ci 等) —— 这五者是生产/回测/湖三层最底层,不该依赖
# scripts/ 下任何子目录。其余 src 前缀(strategies./factors./policy./
# scripts.data./scripts.ops. 等)未改动。
#
# 第三条边(2026-07-19 补齐):workflow/promote.py 的 R-WF-001 唯一 9-Gate
# 执行点原反向 import scripts.research.run_nine_gates_all——run_evaluation
# 及其依赖已迁至 workflow/nine_gate_runner.py,scripts/research/run_nine_gates_all.py
# 现在只是薄 CLI 壳。同五前缀范式,workflow. 直接挡整个 scripts.(而非只挡
# scripts.research.),防止同类倒灌边复发。
FORBIDDEN_EDGES = [
    ("run_daily", ["factory.", "scripts.", "workflow.", "knowledge.", "api.", "services."]),
    ("workflow.", ["scripts."]),
    # factory 候选工厂(lines/autoresearch/ontology/fundamental/pool):与 workflow 同层并列,
    # 居 services/api/registry/ledger 之下。2026-07-21 P1-1② 起入表:agent_loop.py 模块级
    # import services.agent.llm_adapter 的倒灌边已切(llm_adapter 下沉 providers/);
    # 入表前 AST 全量扫描存量边:core×8/factors×6/knowledge×2/engine×2/providers×1/
    # research_toolkit×1/portfolio×1 均向下或同层机制调用,无其他倒灌。
    # factory→portfolio.marginal / research_toolkit 两条边已取证留待专项评审,不在禁列。
    ("factory.", ["services.", "api.", "workflow.", "scripts.", "strategy_registry.",
                  "research_ledger.", "metasearch.", "run_daily", "apps."]),
    ("strategies.", ["factory.", "scripts.research.", "workflow.", "knowledge.", "api.", "services."]),
    # factor_store. 于 2026-07-21 P1-1① 入禁:factors↔factor_store 环的恶化通道焊死;
    # 存量唯一边 autoresearch_dsl→factor_store.store 是 ADR-038 决策三 scoped 写区的
    # 署名代笔(制度性依赖,非架构倒灌),经 ALLOWED_IMPORT_EXCEPTIONS 显式留痕,
    # scoped 区撤销(署名机制改归 lake/)时一并下线。
    ("factors.", ["factory.", "strategies.", "scripts.research.", "workflow.", "core.", "knowledge.", "api.", "services.", "governance.", "factor_store."]),
    # policy 是候选/持仓硬约束的底层叶子(candidate_filters/constraints),被 factors.veto
    # 等兼容 wrapper 反向 import(factors→policy),因此 policy 必须停在 factors 之下:
    # 不得 import factors(否则 factors↔policy 成环)、engine/core,也不得倒灌
    # strategies/factory/workflow/scripts.research 等上层。只可依赖 stdlib/pandas/lake/contracts。
    ("policy.", ["factors.", "engine.", "core.", "factory.", "strategies.",
                 "scripts.research.", "workflow.", "knowledge.", "api.", "services.", "governance."]),
    ("lake.", ["factors.", "strategies.", "core.", "factory.", "scripts.", "knowledge.", "api.", "services.", "governance."]),
    ("core.engine", ["factory.", "strategies.", "scripts.", "workflow.", "knowledge.", "api.", "services.", "governance."]),
    ("core.analysis", ["factory.", "strategies.", "scripts.", "workflow.", "knowledge.", "api.", "services.", "governance."]),
    # engine/ 是 core.engine 的底层引擎叶子(metrics/composer/portfolio/factor_analysis),
    # 必须停在最底层:不得反向依赖 factors(状态/因子层)、组合构建层(strategies)或探索层
    # (factory/scripts.research/workflow)。黑名单原先漏了这个与 core/ 平级的顶层目录,
    # 导致 regime.py/strategy_composer.py 倒灌未被发现;两者已迁出至 factory/。
    ("engine.", ["factors.", "factory.", "strategies.", "scripts.", "workflow.", "governance."]),
    ("scripts.data.", ["factory.", "strategies.", "scripts.research.", "workflow.", "knowledge.", "api.", "services."]),
    # ops 默认按生产运维入口处理:不得随手倒灌研究/服务/台账层。
    # 少数已审定的研究编排脚本在 ALLOWED_IMPORT_EXCEPTIONS 中放行;新增例外必须显式留痕。
    ("scripts.ops.", ["factory.", "workflow.", "scripts.research.", "services.", "research_ledger.",
                      "strategy_registry.", "knowledge.", "api.", "metasearch."]),
    # knowledge 是纯机制:只依赖 stdlib(+ duck-typed Hypothesis),不得依赖任何业务层
    ("knowledge.", ["core.", "lake.", "factors.", "strategies.", "factory.", "workflow.", "scripts.", "api.", "services.", "governance."]),
    # 产品接缝(Phase 0):api 是薄 HTTP 层,只能走 services/contracts,不得直碰引擎
    ("api.", ["core.", "lake.", "factors.", "strategies.", "factory.", "workflow.",
              "engine.", "metasearch.", "knowledge.", "scripts.", "governance."]),
    # services 是受控接缝(有意允许 import 引擎),但不得反向依赖 api。
    # 子域向序(2026-07-22 P1-4 实测零违规后入表执法):read 是只读查询面(34 文件,
    # 最大子域),不得调用 actions 写入/执行面,也不得依赖 agent 编排环;actions 可按需
    # 读 read 视图(can_agent_do 等 3 边),但不得依赖 agent;agent 是编排器,可消费两侧。
    ("services.read.", ["services.actions.", "services.agent."]),
    ("services.actions.", ["services.agent."]),
    ("services.", ["api."]),
    # contracts 是纯 DTO 叶子:只依赖 pydantic + stdlib,不得依赖任何业务层
    ("contracts.", ["core.", "lake.", "factors.", "strategies.", "factory.", "workflow.",
                    "engine.", "metasearch.", "knowledge.", "scripts.", "services.", "api.", "governance."]),
    # providers 外部服务客户端层(LLM 适配器等):2026-07-21 P1-1② 新建,与 contracts 同范式
    # 焊死在叶子层——只许 stdlib+yaml,配置经文件路径/环境变量注入(不 import 业务配置模块);
    # 各上层(factory/services/api/scripts)向下消费。
    ("providers.", ["core.", "lake.", "factors.", "strategies.", "factory.", "workflow.",
                    "engine.", "metasearch.", "knowledge.", "scripts.", "services.", "api.",
                    "governance.", "contracts.", "research_ledger.", "strategy_registry.",
                    "portfolio.", "policy.", "research_toolkit.", "run_daily", "apps.",
                    "app_config.", "capacity.", "model_risk.", "factor_store."]),
    # governance 治理层(holdout 金库/成本钉/trial 账本/状态机):位于 core 之上、
    # factory/workflow/portfolio 之下。2026-07-21 P1-1③ 起入表:成本/指纹纯口径已
    # 下沉 lake(cost.py/fingerprint.py),本层只留执法;可向下依赖 lake/app_config/core,
    # 不得触碰上层接缝与研究编排层。底层各层反向禁 import governance(见上各条),
    # 杜绝"下层向执法者要定义"的倒灌边复发。
    ("governance.", ["factors.", "strategies.", "factory.", "workflow.", "services.", "api.",
                     "apps.", "portfolio.", "scripts.", "research_ledger.", "strategy_registry.",
                     "metasearch.", "capacity.", "model_risk.", "factor_store.", "knowledge."]),
    # portfolio 组合/边际评估库:位于 strategies/factors/core/governance 之上、
    # factory/workflow 研究编排与产品接缝之下。2026-07-22 专项评审:factory→
    # portfolio.marginal 为合法复用(canonical 边际评估器单一实现);实测出站全部
    # 向下(core/strategies/factors/governance/lake/runtime/strategy_registry),
    # 零违规入表——只禁上层接缝/编排/横向台账面,保向下依赖合法。
    ("portfolio.", ["factory.", "workflow.", "services.", "api.", "apps.", "scripts.",
                    "run_daily", "knowledge.", "metasearch.", "research_ledger.", "policy."]),
    # research_toolkit 研究工具箱(triage/alpha_audit 等):纯函数叶子,实测零业务层
    # 依赖(仅 numpy/pandas/scipy/sklearn)。factory→research_toolkit(veto_triage
    # 路由)为合法向下消费;按 contracts/providers 范式焊死叶子层,防工具箱
    # 向上长业务依赖。2026-07-22 专项评审入表。
    ("research_toolkit.", ["core.", "lake.", "factors.", "strategies.", "factory.", "workflow.",
                           "engine.", "metasearch.", "knowledge.", "scripts.", "services.", "api.",
                           "governance.", "contracts.", "research_ledger.", "strategy_registry.",
                           "portfolio.", "policy.", "providers.", "run_daily", "apps.",
                           "app_config.", "capacity.", "model_risk.", "factor_store.", "runtime."]),
]

# 全局禁止import的模块(无论从哪一层):已退场的兼容层 / 死接口。
# core.backtest 已于解耦收尾阶段退场(重命名为 _deprecated_backtest.py.bak),
# 唯一回测路径是 core.engine.BacktestEngine;新代码绝不能再 import core.backtest。
GLOBAL_FORBIDDEN_IMPORTS = ["core.backtest"]

# 行号级例外集:已于 2026-07-18 清零并冻结——最后一条(search.py walk_forward)
# 因上方插入 4 行代码就失配,实证行号钉脆弱;已迁至下方目标级白名单。
# 历史教训:原 5 条豁免中 4 条指向随 Phase1 框架搬入的死桥接(to_signal()/
# default_*_builder(),其 import 目标 core.signals/EngineConfig/core.interfaces
# 在本仓从未存在),零调用零测试,借行号白名单隐身;已在 P0-1 清理中随死代码
# 一并删除。**新增例外一律用 ALLOWED_IMPORT_EXCEPTIONS,不得再加行号钉。**
ALLOWED_EXCEPTIONS: set[tuple[str, int]] = set()

# 文件 + import 目标级例外:比行号稳定,但仍要求新增桥接显式登记。
ALLOWED_IMPORT_EXCEPTIONS = {
    # ADR-038 决策三 scoped 署名:data_lake/factor_store/ 写区只许 factor_store/ 前缀模块
    # 写,factors 的 DSL 面板缓存只能借 store 署名代笔——制度性依赖,非架构倒灌;
    # scoped 区若撤销(署名机制改归 lake/),此边随机制一并下线。2026-07-21 P1-1① 登记。
    ("factors/autoresearch_dsl.py", "factor_store.store"),
    # 研究编排脚本:批量晋级/周度搜索需要桥接 factory → workflow。
    ("scripts/ops/bulk_promote.py", "factory.autoresearch"),
    ("scripts/ops/bulk_promote.py", "services.actions.autoresearch"),
    ("scripts/ops/bulk_promote.py", "workflow.promote"),
    ("scripts/ops/scheduled_factor_search.py", "services.actions.autoresearch_search"),
    ("scripts/ops/scheduled_factor_search.py", "factory.autoresearch.repositories"),
    ("scripts/ops/scheduled_factor_search.py", "research_ledger.ledger"),
    ("scripts/ops/scheduled_factor_search.py", "factory.autoresearch.models"),
    ("scripts/ops/scheduled_factor_search.py", "factory.autoresearch.pipeline"),
    ("scripts/ops/scheduled_factor_search.py", "workflow.from_factory"),
    # 生产衰减监控:读在册版本并经 attach_decay_check 写回衰减审计字段。
    ("scripts/ops/decay_monitor.py", "strategy_registry"),
    # 周度组合再构成(WS-D):只读在册版本清单定位 version_returns 序列,零台账写入。
    ("scripts/ops/scheduled_portfolio_recompose.py", "strategy_registry"),
}


def module_path(py_file: Path) -> str:
    rel = py_file.relative_to(ROOT).with_suffix("")
    parts = rel.parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_modules(py_file: Path):
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return []
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import, not cross-layer by definition
            if node.module:
                mods.append((node.module, node.lineno))
    return mods


# 台账唯一写入口:strategy_versions.json 只能由 strategy_registry.py 写
# (register_family/register → _save)。其余代码须经 register(),不得直写,
# 否则台账历史/可复现性元数据会被绕过。workflow.phase4_register 也是调
# register(),不直写,因此不在此白名单内也不会被误报。
REGISTRY_FILE = "strategy_versions.json"
REGISTRY_WRITER_ALLOWLIST = {"strategy_registry.py"}


def _mentions_registry(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and REGISTRY_FILE in n.value:
            return True
    return False


def registry_write_violations(py_file: Path):
    """检测对 strategy_versions.json 的直接写操作(write_text/write_bytes/open(...,'w'))。"""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return []
    # 绑定到注册表路径字面量的变量名
    reg_vars = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _mentions_registry(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    reg_vars.add(tgt.id)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("write_text", "write_bytes"):
                obj = node.func.value
                if (isinstance(obj, ast.Name) and obj.id in reg_vars) or _mentions_registry(obj):
                    hits.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            if node.args:
                first = node.args[0]
                is_reg = (isinstance(first, ast.Name) and first.id in reg_vars) or _mentions_registry(first)
                mode_w = False
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode_w = any(c in str(node.args[1].value) for c in ("w", "a"))
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode_w = mode_w or any(c in str(kw.value.value) for c in ("w", "a"))
                if is_reg and mode_w:
                    hits.append(node.lineno)
    return hits


def check() -> int:
    violations = []
    for py_file in sorted(ROOT.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in py_file.relative_to(ROOT).parts):
            continue
        mod = module_path(py_file)
        rel = py_file.relative_to(ROOT)
        # 台账唯一写入口检查
        is_test = "tests" in py_file.relative_to(ROOT).parts
        if py_file.name not in REGISTRY_WRITER_ALLOWLIST and not is_test:
            for lineno in registry_write_violations(py_file):
                violations.append((rel, lineno,
                    f"直写 {REGISTRY_FILE} — 台账只能经 strategy_registry.register()"))
        # 全局禁止import检查(任何文件都不得import这些已退场模块)
        for target, lineno in imported_modules(py_file):
            for forbidden in GLOBAL_FORBIDDEN_IMPORTS:
                if target == forbidden or target.startswith(forbidden + "."):
                    violations.append((rel, lineno, f"import {target} — 已退场模块,禁止导入"))
        for source_prefix, forbidden_targets in FORBIDDEN_EDGES:
            if mod == source_prefix or mod.startswith(source_prefix):
                for target, lineno in imported_modules(py_file):
                    for forbidden in forbidden_targets:
                        if target == forbidden.rstrip(".") or target.startswith(forbidden):
                            if (str(rel), lineno) in ALLOWED_EXCEPTIONS:
                                continue
                            if (str(rel), target) in ALLOWED_IMPORT_EXCEPTIONS:
                                continue
                            violations.append((rel, lineno,
                                f"import {target} — 违反分层:{source_prefix} 不得依赖该前缀"))

    if violations:
        print("发现依赖/写入违规:")
        for f, lineno, msg in violations:
            print(f"  {f}:{lineno}  {msg}")
        return 1

    print("分层依赖检查通过,无违规。")
    return 0


if __name__ == "__main__":
    sys.exit(check())
