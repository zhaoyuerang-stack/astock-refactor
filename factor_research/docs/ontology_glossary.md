# 本体论术语基线(Ontology Glossary)

> **本文档是"概念/命名 ↔ 本体层 映射"的唯一 home(ADR-010)。**
> 目的：把当前代码里散落的"同名不同义/同义不同名"现象**记录在案**，作为术语基线。
> **范围声明**：本文档只做**只读盘点 + 标注**，不重命名、不修改任何代码。本体驱动重构是未来工作(见文末)。

> **执行规则**：目标命名 taxonomy 见 [`naming_taxonomy.md`](naming_taxonomy.md)。本文保留当前冲突盘点；新代码和分批迁移以 taxonomy 为准。

---

## 1. 本体层定义(精简版，适配本仓库)

本仓库的单向依赖链(`CLAUDE.md`)是 `data(lake) → factors → core.engine → {strategies, factory/workflow} → registry → production`，
但这只是**目录依赖关系**，不是**概念分类**。下表给出本仓库实际承载的语义层(裁剪自 `/Users/kiki/CLAUDE.md` 的本体论，
只取与本仓库相关的子集，不整体搬用 web 平台的 12 层 schema)：

| 本体层 | 含义 | 典型产物 |
|---|---|---|
| **Factor** | 单一截面公式，输入(price/amount/fundamental)→输出(因子值面板) | `factors/*.py` 里的纯函数 |
| **Signal** | Factor 经过方向/选股数量/调仓频率包装后，可喂给回测引擎的对象 | `core.engine.Signal`、各 `to_signal()` |
| **Timing/Regime** | 对市场状态的分类或择时判断，输出仓位暴露或状态标签 | `small_cap_timing`、`RegimeEngine`、`dist_lagged` |
| **Strategy** | 把 Factor + Timing + Policy 组装成可执行的选股/调仓逻辑 | `strategies/*.py::latest_signal/backtest_weights` |
| **Policy** | 对候选池/仓位的硬性约束或过滤规则(不产生 alpha，只做风控/筛选) | `loser_veto_reversal`、`capped_weight` |
| **Portfolio** | 多策略/多腿的组合与权重分配 | `portfolio/composer.py`、`engine/strategy_composer.py` |
| **Engine** | 统一回测/指标计算内核(唯一权威) | `core.engine.BacktestEngine`、`engine/metrics.py` |

---

## 2. 命名冲突清单

> 每条格式：当前名称(file:line) → 含义摘要 → 实际所属本体层 vs 命名暗示的层 → **未来重命名候选标注**。

### 2.1 `veto`

- `factors/veto.py:16 loser_veto_reversal` — "Score stocks for loser-side veto use"。**本体层 = Policy**(候选池排除规则，status=已证伪)。
- `factors/veto.py:35 salience_covariance_veto` — "faded Salience Covariance (-ST_cov) as a veto factor"。**本体层 = Factor**(illiquidity v3.1 LIVE 公式的一部分，ADR-003)。

**冲突**：同一模块、同一 `_veto` 后缀，一个是 Policy 层的候选池过滤器，一个是 Factor 层的公式分量。命名完全无法区分二者所属层。

> 🔖 **未来重命名候选**：拆分为两个模块，如 `policy/candidate_filters.py::loser_reversal_filter` 与 `factors/illiquidity_components.py::salience_covariance`，或至少改函数名去掉共享的 "veto" 前缀。本次不执行。

### 2.2 `composer`

- `engine/composer.py` — Factor 层的多因子合成(equal_weight/ic_weight/pca_composite/`to_signal`)，**本体层 = Factor→Signal 桥接**。
- `portfolio/composer.py` — 多策略/多腿的权重分配(equal_weight/risk_parity/capped_weight/`compose`)，**本体层 = Portfolio**。

**冲突**：两个文件同名 `composer.py`，且 `engine/composer.py` 里也有一个 `equal_weight`，与 `portfolio/composer.py::equal_weight` 同名但输入(因子字典 vs 收益矩阵)、输出(因子面板 vs 权重 Series)完全不同。

> 🔖 **未来重命名候选**：`engine/composer.py` → `engine/factor_composer.py`，`portfolio/composer.py` → `portfolio/portfolio_composer.py`(或反之)，并消除两个 `equal_weight` 的同名碰撞。本次不执行。

### 2.3 `signal` / `to_signal`

三个 `to_signal`，签名互不相同：

- `engine/composer.py:129 to_signal(factor, top_n, direction, rebalance_freq, timing, family, version)` — 把**合成后的因子面板**包装成 `core.engine.Signal`。
- `engine/portfolio.py:56 to_signal(factor, n, direction, ...)` — "Wrap a factor panel into a `core.engine.Signal` (factor mode)"，与上面几乎同名同义但参数命名不同(`top_n` vs `n`)。
- `factors/alpha/base.py:169` — `Factor` 类的 `.to_signal()` 方法，**本体层 = Factor→Signal**(lazy 计算图节点上的方法，与上面两个独立函数语义重叠)。
- 各 `strategies/*.py::latest_signal()` — Strategy 层的"生成今日信号"，与上面三个 `to_signal` 是**不同概念**(一个是"把因子包装成回测对象"，一个是"策略今天该怎么操作")，但名字都含 "signal"。

**冲突**：`engine/composer.py::to_signal` 与 `engine/portfolio.py::to_signal` 几乎是重复实现(同义不同形)；`strategies/*::latest_signal` 与前两者是同词不同义。

> 🔖 **未来重命名候选**：审查 `engine/composer.py::to_signal` 与 `engine/portfolio.py::to_signal` 是否可合并为一个规范实现；`latest_signal` 改名避免与 `to_signal` 共享 "signal" 词根造成混淆(如 `today_action`/`latest_holding_decision`)。本次不执行。

### 2.4 `build_rebalance_weights`

- `strategies/small_cap.py:76` 与 `strategies/size_earnings.py:108` — 签名几乎一致的调仓权重构建函数，各自独立实现。

**冲突**：不是命名歧义，而是**缺少共享抽象**——两个 Strategy 模块各自重写了同一段 Policy/组合构造逻辑。

> 🔖 **未来重命名候选**：提取为共享 helper(如 `strategies/_common.py::build_topn_equal_weight`)，两边调用。本次不执行，仅记录重复点。

### 2.5 `zscore`

- `engine/neutralize.py:24 zscore(s: pd.Series)` — 对单个 Series 做 z-score。
- `factors/alpha/transforms.py:17 zscore(df: pd.DataFrame)` — "Cross-sectional z-score (row-wise)"，对 DataFrame 按行(截面)做 z-score。
- `factors/alpha/base.py` 另有两处 zscore 相关方法(约 line 97/229)，与上面输入输出均不同。

**冲突**：四处 `zscore` 同名，但输入类型(Series vs DataFrame)、归一化方向(整体 vs 截面)均不同——**同名不代表可互换**，混用会产生隐蔽的数值错误。

> 🔖 **未来重命名候选**：按语义区分命名，如 `zscore_series`(时序) vs `zscore_cross_section`(截面)，并审查是否能合并为同一实现的不同入口。本次不执行。

### 2.6 `regime` / `timing` / `filter`

- `engine/regime.py::RegimeEngine` — 多维 regime 分类器(trend/volatility/liquidity/breadth)，**本体层 = Timing/Regime**，输出"环境标签"。
- `engine/strategy_composer.py::add_legs(regime=...)` — 按 regime 标签做"腿(leg)激活条件匹配"，**本体层 = Portfolio**(消费 regime 标签，不产生标签)。
- `portfolio/regime_gate.py` — "Regime-gated LIVE 模式"，**本体层 = Policy**(小盘失宠时切换 large-cap 的硬开关，ADR-011)。
- `run_daily.py` 里的 bull/bear 状态 — Production 层的择时结果展示，源头是 `factors/small_cap.py::small_cap_timing`。
- `factors/small_cap.py::small_cap_timing` — "Small-cap regime timing: long when small-cap nav > MA"，**本体层 = Factor 输出的 Timing 信号**，但函数名里既有 "regime" 又有 "timing"。
- 各策略的 `timing_signal` 参数 — Strategy 层接收上面的 Timing 信号作为输入。

**冲突**：`regime` 在四个不同抽象层级被使用(分类器输出 / leg 激活条件 / 风险偏好开关 / 生产状态展示)；`timing` 与 `regime` 在 `small_cap_timing` 函数名里被合并使用。`filter` 一词同样跨"数据剪枝(统计)"、"Policy 候选池过滤(业务规则)"、"模型内部滤波(算法)"三种含义复用，分布在 `factors/gap_reversal.py`、`factors/veto.py`、`factors/market_stress.py` 等多处。

> 🔖 **未来重命名候选**：明确区分"regime 分类器输出"(标签数据)与"regime 门控"(Policy 开关)与"regime 轮动"(Strategy 内的择时切换)三个不同概念的命名前缀；`small_cap_timing` 改名避免与 `RegimeEngine` 的 "regime" 混用。本次不执行。

---

## 3. 本体驱动重构 — 未来方向(指针)

本文档只完成**术语基线确认**：列出当前命名与本体层的错位/重复，不改代码。
若要推进"本体驱动重构"，需要：① 基于 §1 的本体层定义设计完整的命名 taxonomy(前缀/目录约定)；② 按 §2 的清单逐项评估重命名/拆分/合并的影响面与测试覆盖；③ 分批小步执行，每次只动一个术语并跑 `test_all`。

该工作已记入 `TASKS.md` backlog，作为后续任务，不在本次范围内。

---

## 免责声明

本文档仅为代码命名与概念分类的盘点记录，不涉及策略逻辑或投资建议。
