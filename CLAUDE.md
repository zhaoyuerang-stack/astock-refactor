# CLAUDE.md — A股全市场因子量化研究操作宪法

> 本文件是本仓库的**单一入口**。
> 每次 AI / Agent 接手任务，必须先读本文件，再读 `STATUS.md`。
> 本文件只定义**不可违反的规则、接手流程、文档路由、架构边界与执行纪律**。
> 具体命令、运行细节、接口状态、Web 开发规范、数据湖细节、成本数值，不在本文展开，按文档地图(§2)跳转。

---

## 0. 接手 90 秒协议

每次开始任务，必须按顺序执行：

1. 阅读本文件的 `P0 / P1` 规则。
2. 阅读 `STATUS.md`，确认当前进度、最近失败项、未完成任务。
3. 运行或要求查看 `git status --short`，确认工作树是否已有他人改动（多 agent 共享工作树）。
4. 判断任务类型：`data / factor / engine / strategy / workflow / registry / production / web / docs`；命中 `docs/agent_skills/` 固定剧本的任务**先读剧本再动手**（尤其：接入/回填新数据源 → 必读 [`data_source_onboarding.md`](factor_research/docs/agent_skills/data_source_onboarding.md) 按 S0-S7 执行，临场自创接入流程 = 工程事故源）。
5. 只修改本任务相关文件；不得顺手重构无关模块。
6. 修改前说明计划；修改后运行对应检查(§13)。
7. 提交前只显式 stage 本次文件，必须检查 `git diff --cached --stat` 和 `git diff --cached`。
8. 没有检查输出，不得报告“完成”。

---

## 1. 项目定位

本项目是一个**A股全市场、日频、因子量化研究系统**。

真正资产不是某一个策略，而是：

1. 数据基础设施；
2. 因子与策略工厂；
3. 防自欺研究流程；
4. 母策略生命周期管理；
5. 可复测、可审计、可替换的有效策略池。

核心假设：

* 策略是会有生命周期的，任何单一策略默认会失效。
* 漂亮回测默认先假设为过拟合、幸存者偏差、未来函数、成本低估或特定行情依赖。
* 研究目标不是找到一个“神策略”，而是持续完成：发现 → 证伪 → 入册 → 监控 → 退役 → 替换。
* 策略按**母策略 / alpha 家族**组织，而不是按孤立版本组织。

---

## 2. 文档地图

| 层级      | 文档                                            | 作用                                            |
| ------- | --------------------------------------------- | --------------------------------------------- |
| 入口      | `CLAUDE.md`                                   | 本文件。项目操作宪法、P0/P1规则、接手协议、架构边界                  |
| 状态      | `STATUS.md`                                   | 当前进度、最近变更、当前失败项、高频更新                          |
| 任务      | `TASKS.md`                                    | 开放任务 backlog，动态维护                             |
| 架构      | `SPEC.md`                                     | 引擎架构、七层结构、单向依赖链、回测权威                          |
| 演化      | `LOOP_ENGINEERING.md`                         | 自进化研究机制、9-Gate、如何持续发现真 alpha                  |
| 流程      | `WORKFLOW.md`                                 | 端到端流程、每一步谁负责、闸门与交接                            |
| 多 Agent | `MULTI_AGENT.md`                              | DeepSeek / Codex / Claude / Antigravity 平台分负载 |
| 协作底线    | `AGENTS.md`                                   | 跨工具(Codex/Cursor/Antigravity)共读的提交/协作底线       |
| 执行      | `RUNBOOK.md`                                  | 日常命令、数据更新、信号生成、复测、监控                          |
| 数据      | `factor_research/docs/data_infrastructure.md` | 数据源、数据湖、接口、超时、代理、质量判定                         |
| 数据湖     | `factor_research/data_lake/README.md`         | 数据湖结构、字段、落盘规范                                 |
| 成本      | `factor_research/docs/cost_model.md`          | 交易成本模型、费率、滑点、融资、变更记录                          |
| 产品      | `WEB_DESIGN.md`                               | Web 端 UI/UX canonical 规格(研究分析平台·九页三栏)         |
| 决策层     | `DECISION_COCKPITS.md`                        | 前端决策驾驶舱架构:每页服务哪个决策、沿 alpha 生命周期重排(约束 WEB_DESIGN 的页面职责) |
| 落地      | `Implement.md`                                | 把研究引擎接成 Web 产品的执行计划                           |
| Web     | `web/CLAUDE.md`                               | Next.js 前端开发纪律、命令、缓存故障处理                      |
| 决策      | `DECISIONS.md`                                | ADR 决策记录，append-only                          |
| 经验      | `LESSONS.md`                                  | 踩坑、故障、修复经验、接口血泪铁律细节                           |
| Agent 操作层 | `factor_research/docs/agent_operating_model.md` | Agent 如何用 skill/工具:分层操作模型(读事实/选动作/禁越权)、控制平面入口 |
| Agent 技能   | `factor_research/docs/agent_skills/`            | 技能剧本(data-health / data-source-onboarding / factor-audit / candidate-promote / production-readiness / module-cleanup / literature-scan / quant-strategy-scan / daily-research-round) |
| 归档      | `docs/archive/`                               | 已完成或废弃方案，只作历史参考，不作实现依据                        |

**本表是根目录活文档的唯一白名单(机械强制)**：`scripts/ci/check_doc_map.py` 断言 ① 根目录 `*.md` 集合 == 本表登记集合(禁孤儿文档、禁地图列不存在的文件)；② 全仓文档引用零悬空；③ `STATUS.md` ≤ 200 行(强制历史外溢到 `docs/archive/`)。
新增根目录文档必须同时登记本表，否则 CI 红。缘由见 ADR-041(ADR-019 的一次性清理一个月后即反弹)。

消歧规则：

* 本仓 `CLAUDE.md` 是**研究引擎宪法**；home 级 `~/CLAUDE.md` 是 Web 产品宪法，作用域不同、并存。
* `web/CLAUDE.md` 是**Web 前端宪法**；根 `SPEC.md` 是**研究引擎架构规格**。
* 产品侧 PRD / Web 侧 SPEC 不得覆盖本仓引擎规格。
* 冲突时优先级：`CLAUDE.md` > `SPEC.md` > `STATUS.md` > 其他文档 > archive。

**source-of-truth 约定**(防止重复规则改一处忘多处)：本文 §4/§8/§10 等"摘要"以专章/子文档为唯一真相 —— 成本数值唯一权威 = `core/engine.py::CostModel`(文档面 `cost_model.md`)；9-Gate 细节唯一真相 = `LOOP_ENGINEERING.md`；守卫现状唯一真相 = `scripts/ci/` 实际脚本(§16 表是其索引)。

**Agent 操作层(控制平面)入口**：任何 AI/Agent 操作 `factor_research` 应经受控**读事实 / 选动作**入口，而非自由乱翻仓库（详见 [`agent_operating_model.md`](factor_research/docs/agent_operating_model.md)）：

* **读事实**：`services.read.module_inventory`（模块状态/角色/边界，源 各模块 `MODULE_STATUS.md`）、`services.read.artifact_inventory`（各产物区读/写/是否可作正式证据）、`services.read.strategy_lifecycle`（family/version 生命周期与允许/禁止动作）、及既有 `services.read.*`。
* **选动作**：写台账 / 写数据湖 / 晋级 / 部署 / 用某路径作正式证据**前，先问** `services.read.action_policy.can_agent_do(action, target)` —— 它判允/拒并指向 canonical 入口（`strategy_registry.register`、`workflow.promote`、`run_daily.py`、受控 lake writer）。任务剧本见 [`docs/agent_skills/`](factor_research/docs/agent_skills/)（data-health / data-source-onboarding / factor-audit / candidate-promote / production-readiness / module-cleanup / literature-scan / quant-strategy-scan / daily-research-round）。
* **边界(重要)**：该层是**建议(advisory)不是强制** —— `can_agent_do` 只答"该不该"，**挡不住**绕过它直接 `open()` 写文件。真正的强制仍是 §16 的确定性 CI 守卫 + `strategy_registry.register` 唯一写入口。故：走入口是纪律，不是护栏；守卫全绿前不得报"完成"。参见 `DECISIONS.md` ADR-030。
* **产品 Agent 身份(ADR-037)**：自然语言**验证编排器**(Workflow-as-Protocol)，不是改核心代码的 Codex。验证只能走既有协议;正式证据只认 Strict tool/CLI;Lab sandbox 默认非证据;上屏须 Evidence Envelope(`evidence_tier` / `can_claim_valid` 默认 false)。永不由 LLM 宣布 alpha 有效。详见 `DECISIONS.md` ADR-037。

---

## 3. 规则等级

本仓规则分四级：

| 等级 | 含义         | 处理方式            |
| -- | ---------- | --------------- |
| P0 | 违反则研究结果作废  | 必须停止，修正后重跑      |
| P1 | 违反则架构或流程失效 | CI / 守卫应失败，必须修复 |
| P2 | 违反则需要人工复核  | 不一定作废，但必须记录     |
| P3 | 背景信息       | 仅供参考，可迁移到子文档    |

---

## 4. P0 作废级规则

### R-DATA-001：禁止使用旧口径数据

正式研究、入册、汇报、策略对比，必须使用 `data_lake` + `core/` 统一口径。
禁止使用 `data_full` 旧缓存作为达标依据。历史脚本仍引用 `data_full` 的，只能用于迁移/对照/废弃验证，不得作为有效性证据。

### R-DATA-002：必须全市场口径

正式研究必须覆盖 A 股全市场：沪深主板、创业板 `300*`、科创板 `688*`、中小市值、退市/停牌/上市时间/涨跌停状态。不得用只含沪市主板、只含存活股、只含高流动性股的样本伪造全市场结论。

### R-DATA-003：禁止未来函数

财务/公告/业绩/估值/事件数据必须按**公告日 / 披露日**对齐到交易日。T 日信号只能用 T 日之前已公开的信息。不得用报告期结束日直接 ffill 财务，不得用未来价格/成分/可交易状态生成当期信号。

### R-DATA-004：估值必须使用正确价格口径

PE/PB/PS/PCF 等估值指标必须用不复权价格或与定义一致的口径。禁止用后复权价直接算估值。复权价只能用于收益率/动量/技术指标等价格序列。

### R-COST-001：正式回测必须扣真实成本

正式回测、进化筛选、入册评估、横向比较，必须扣真实交易成本（佣金/印花税/过户费/冲击滑点/融资/买卖双边差异/小盘容量约束）。
canonical 成本数值 = `core/engine.py::CostModel`；口径文档 = [`cost_model.md`](factor_research/docs/cost_model.md)。
**禁止为达标临时下调滑点/佣金/冲击。** 改费率必须四处同步（见 `cost_model.md` §4）。

### R-BT-001：回测唯一权威

正式回测唯一权威 = `core.engine.BacktestEngine`。禁止重启/import 已退役回测模块(`core.backtest` 已退场，见 `core/_deprecated_backtest.py.bak`)；禁止每策略各写一套回测；禁止研究脚本绕开统一内核生成正式绩效。
canonical 路径：`strategies.small_cap` / `factors.small_cap` / `engine.metrics` / `factors.utils`。所有正式绩效必须可由统一引擎复现。

### R-LLM-001：LLM 禁止判断 Alpha 是否有效

LLM 可做：候选因子生成、研报/公告/新闻 NLP 抽取、假设草拟、代码草案、报告解释、错误定位辅助、研究编排建议。
LLM 禁止做：判断因子是否有效、判断是否入册、替代 Alpha Audit / 回测 / 样本外检验 / DSR 多重测试惩罚 / 成本敏感性 / 风险闸门。
所有“是否有效”的判断必须由确定性代码、统计检验、统一回测和固定门禁完成。

### R-REG-001：策略入册只能走唯一入口

新母策略先 `register_family(...)`，新版本经 `register(family, version, ...)`。唯一写入口 = `strategy_registry.register_family / strategy_registry.register`。任何代码不得直写 `strategy_versions.json` / `strategy_families.json` / `registry/*.json`。改台账 schema 必须先更 `SPEC.md` 和对应测试。

### R-WF-001：候选到入册必须走 workflow

canonical 通道：`factory/candidates → L0-L3 cheap-first 筛选 → workflow/promote.py 或 apps/factory_cli.py promote → phase1_synthetic 防未来审计 → phase2/phase3 复测压力 → phase4_register 入册`。`phase1_synthetic` 是防未来函数机械审计的唯一执行点。绕过该流程的结果不得进正式台账。

### R-EVIDENCE-001：9-Gate 证据自证铁律

门禁证据必须由**本策略、本宇宙的一次可复现运行机械产出**（源 ADR-017，三策略造假证否）：
① **禁跨家族照抄**——不同 family 共享逐位相同的 IC/9-Gate 证据块 = 判失败（同 family 多版本共享合法）；
② **config 须能机械复现**台账绩效；
③ 任何 `gate=None` / `nine_gate={}`（门未实算）**禁 standalone 准入**；
④ `n_trials ≥` 含宇宙/veto/择时/网格的**全部搜索自由度**（不得低报以骗过 DSR 惩罚）；
⑤ 候选**生成码须在 canonical 层**（可追溯、可复现）。
守卫：`scripts/ci/check_registry_evidence.py`（机械强制 ①）。违反即该 standalone 准入作废。

### R-OBJECTIVE-001：收益门槛不是优化目标

收益门槛是入册观察条件，不是候选搜索的唯一目标函数。禁止单纯最大化年化/Sharpe/Calmar。候选排序必须同时考虑：样本外稳定性、压力期表现、成本敏感性、换手与容量、与现有母策略相关性、多重测试惩罚、经济学假设、失效信号清晰度、可执行性。

### R-PROD-001：不自动下单；排名靠前策略自动模拟盘

系统**永不**对真实账户自动下单。真实交易 / 资金划转永远需人工执行（承 `SPEC.md` 执行边界、`LOOP_ENGINEERING.md` §6）。
**可自动**：模拟盘（paper trade）全自动、T+1 口径、零真金，可对**系统评估排名靠前的 top-N 策略并行运行**，作为可展示的实测证据。
「不下单 ≠ 不实测」是产品定位一等原则：排名须由**后端确定性代码产出并持久化**（不得只在前端瞬时算），paper engine 支持多账户并行、与正式部署单策略生产信号解耦，展示层并排对比实测净值 / 回撤 / 回测偏差。
违反（任何自动真实下单路径）= 越界与资本风险，必须立即停止。排名口径**不得**为「让某策略上榜」而更改（同 R-OBJECTIVE-001 精神）。

---

## 5. P1 架构守卫规则

### R-ARCH-001：单向依赖链

```text
data_lake → factors → core.engine → strategies / factory / workflow → registry → production
```

下层不得 import 上层；生产层不得 import 研究层；回测核心不得依赖具体策略；registry 不得依赖 production；data 层不得依赖 factors/strategies/workflow。守卫：`scripts/ci/check_layer_deps.py`。

### R-ARCH-002：生产层禁止依赖研究层

`run_daily.py` / production 目录 / 实盘信号模块，不得 import `factory.* / workflow.* / scripts.research.* / notebooks.* / experimental.*`。生产层只用：已入册策略、canonical 数据/因子/回测/信号接口、registry 正式读取接口。

### R-ARCH-003：配置必须走统一入口

配置走 `app_config/settings.yaml` + `get_settings()`。禁止把模型名/路径/费率/数据源/API 开关/代理端口硬编码在业务逻辑里。例外：测试 fixture 可用局部临时配置，但不得污染正式路径。

### R-ARCH-004：数据湖写入必须可审计

落盘必须 schema 明确、字段含义明确、交易日对齐明确、增量可重复、异常有质量报告、重跑不产生静默口径变化。写 `data_lake` 核心区必须走 canonical writer 并更新 manifest。守卫：`scripts/ci/check_lake_writers.py`；质量校验：`validate_final.py` → `data_lake/quality_report.json`。

### R-ARCH-005：废弃模块不得复活

废弃模块必须路径标记 deprecated/archive/bak、不被正式代码 import、不作新功能参考。恢复需新增 ADR 到 `DECISIONS.md` 说明原因/风险/迁移计划。

---

## 6. 9-Gate R2P 门禁摘要

所有候选策略入册前必须通过 9-Gate R2P 流水线。**唯一真相 = [`LOOP_ENGINEERING.md`](LOOP_ENGINEERING.md)**，本表只作速查：

| Gate | 名称           | 核心问题                     |
| ---- | ------------ | ------------------------ |
| G1   | 数据可用性        | 数据是否覆盖完整、字段可信、无口径缺陷      |
| G2   | 防未来函数        | 信号是否只用当时可知信息             |
| G3   | 成本扣除         | 是否扣真实交易与冲击成本             |
| G4   | 样本外检验        | 样本外是否仍稳定                 |
| G5   | 压力期检验        | 极端/熊市/风格逆风期是否可承受         |
| G6   | 换手与容量        | 换手、冲击、成交容量是否现实           |
| G7   | 中性化与相关性      | 是否只是暴露于已知风格或现有策略         |
| G8   | DSR / 多重测试惩罚 | 是否经得起大规模搜索后的统计惩罚         |
| G9   | 入册材料完整性      | thesis/配置/绩效/风险/失效信号是否齐全 |

代码入口：`workflow/` 与 `scripts/research/run_nine_gates_all.py`。

---

## 7. 策略生命周期

### 7.1 母策略 family 必填字段

family id、策略名称、核心经济学假设、alpha 来源、适用市场状态、不适用市场状态、预期失效信号、主要风险、与现有母策略关系、研究负责人/生成来源、创建时间。

### 7.2 策略 version 必填字段

version id、所属 family、因子定义、参数配置、股票池、调仓频率、持仓数量、成本模型、样本内绩效、样本外绩效、压力测试绩效、换手、容量评估、相关性评估、9-Gate 结果、入册结论、退役条件。

### 7.3 入册门槛

单母策略最低观察条件：年化 > 15%、最大回撤 < 20%、样本外不塌陷、压力期可解释、成本敏感性不过度脆弱、失效信号明确、与现有策略不高度重复。
项目级组合目标：满意线 年化 ≥ 20% & Sharpe ≥ 1.0；卓越线 年化 ≥ 28% 或 Calmar ≥ 1.6。
注意：上述目标不是搜索程序的唯一目标函数(违反 `R-OBJECTIVE-001` 作废)。

### 7.4 退役纪律

策略失效不得删历史。必须：registry 标记 retired/deprecated、记录失效时间、记录触发的失效信号、记录失效归因（市场状态变化/拥挤/数据问题/成本上升/容量约束/过拟合暴露）、保留历史绩效与配置、禁止用新参数覆盖旧版本历史。

---

## 8. LLM 分工铁律

```text
强模型：研究编排 / 推理 / 复杂代码修改 / 风险识别
便宜模型：批量候选生成 / NLP 抽取 / 标签生成 / 初筛辅助
确定性代码：所有有效性判断 / 回测 / 审计 / 入册 / 风控
```

* **强模型**可分析架构/设计实验/写代码/修 bug/解释测试失败/审查流程/生成报告/提风险假设；不得直接宣布策略有效、绕过回测门禁、用语言代替统计检验、为达标改成本/样本/shift/T+1/退市处理/股票池。
* **便宜模型**默认干批量候选/NLP/标签/初步 thesis；统一入口 `services/agent/llm_adapter.py::get_adapter()` + `app_config/settings.yaml::ai_model`(现 `deepseek-v4-flash`)，无 key 退规则式；输出必须进确定性筛选，不得直接入册。
* **确定性代码**负责：数据质量校验、防未来审计、因子计算、中性化、回测、成本扣除、Alpha Audit、RidgeCV/Newey-West/置换、DSR/多重测试惩罚、L0-L3 筛选、9-Gate、入册判断、生产信号。把这些判断交给 LLM = 研究流程失效。

---

## 9. 数据纪律

细节见 [`data_infrastructure.md`](factor_research/docs/data_infrastructure.md) + [`data_lake/README.md`](factor_research/data_lake/README.md)；**接口反封禁/超时血泪铁律细节见** [`LESSONS.md`](LESSONS.md)。**接入任何新数据源必须走固定剧本** [`data_source_onboarding.md`](factor_research/docs/agent_skills/data_source_onboarding.md)（S0 立项五判 → 探针 → 契约声明含时间轴口径三选一 → 回填 → 质量门 → 加载层 → 登记，逐步 fail-closed；不得临场自创接入流程）。本文保留不可违反原则 + 最关键操作铁律：

原则：① 全市场优先，不用幸存者样本；② point-in-time 优先，不未来泄露；③ 统一数据湖优先，不临时拼私有口径；④ 质量报告优先，不忽略 OHLC 错误/负价/异常跳变；⑤ A股正常现象（停牌/新股首日/一字板/涨跌停）不得误判为脏数据；⑥ 接口异常不得通过改变研究口径绕过；⑦ 数据更新失败必须显式记录，不得静默使用半截数据。

操作铁律（违反极易触发封禁/挂死）：
* **东财逐只下 40-50 只就封** → 换批量/聚合接口（如按报告期 `yjbb_em`），**绝不加多线程**（更快触发封禁）。
* **akshare 唯一可靠超时 = daemon 线程 + join(timeout)**；`ThreadPoolExecutor` / socket timeout 都无效。
* **联网需 `dangerouslyDisableSandbox`**；clash 代理(7897)下新浪源可用、东财 push2 被拦。

---

## 10. 交易成本纪律

数值与变更记录见 [`cost_model.md`](factor_research/docs/cost_model.md) + `core/engine.py::CostModel`(唯一权威)。本文只保留原则：① 正式回测用审慎成本；② 小盘必显式计冲击/滑点；③ 高换手必做成本敏感性；④ 杠杆必计融资；⑤ 成本参数变化必进 `DECISIONS.md`；⑥ 不得为入册临时降成本；⑦ 所有报告说明成本口径。

---

## 11. Git 与多 Agent 提交纪律

多 agent 共享工作树，提交纪律是 P1。跨工具协作底线另见 [`AGENTS.md`](AGENTS.md)。

* **11.1 禁止一锅端**：禁 `git add -A` / `git add .`；必须显式 `git add <file>...`，只 stage trace 得清、属于本次意图的文件。
* **11.2 一个 commit 一个意图**：一个完整、可独立 revert 的意图；禁止把数据修复/策略变更/Web UI/文档/测试/重构/格式化混在一个 commit。宁可多个小 commit。
* **11.3 提交前必核对 diff**：`git diff --cached --stat` + `git diff --cached`；每个文件属本任务、每行可解释、没卷入他人半成品、没误删测试、没弱化断言、没改无关配置、没把大数据产物入库。
* **11.4 禁止擅自改 Git 历史**：共享分支上禁 `reset --hard` / `rebase` / `push --force` / `clean -fd`，除非任务明确要求且确认无他人工作树风险。
* **11.5 Commit message**：`type(scope): 标题` + 正文写"为什么改"（diff 看不出的根因）+ "验证"（命令与结果）；尾注固定 `Co-Authored-By:` 与 `Claude-Session:`。

---

## 12. 任务执行循环协议

每个任务按循环执行，不按直线。

**12.1 标准循环**：理解任务 → 定位文件 → 写变更 → 运行检查 → 失败则读错误 → 找根因 → 修复 → 回到运行检查。最多 5 次。
**12.2 停止条件**（任一即停并报告）：所有检查通过 / 5 次用完 / 同一错误连续两次 / 发现规则冲突 / 发现疑似数据口径污染 / 发现可能卷入他人改动 / 发现需改 P0/P1 规则。
**12.3 禁止行为**：没检查输出就报完成；删测试/弱化断言让结果变绿；改样本/成本/shift/T+1/公告日对齐让策略达标；把失败甩锅"可能环境问题"却无证据；测试失败后盲目反复改。

---

## 13. 常用检查入口

具体命令见 [`RUNBOOK.md`](RUNBOOK.md)，本文只保留检查类别。每次任务至少选相关检查：

| 任务类型       | 必查                               |
| ---------- | -------------------------------- |
| 数据         | 数据质量校验、schema 校验、样本覆盖、异常报告       |
| 因子         | 单元测试、防未来检查、截面 sanity check       |
| 回测引擎       | engine tests、成本测试、边界条件测试         |
| 策略         | 样本内、样本外、压力测试、成本敏感性               |
| workflow   | phase1-4 流程测试、入册测试、失败路径测试        |
| registry   | schema 测试、唯一写入口测试、历史兼容测试         |
| production | 生产信号 smoke test、禁止研究层 import     |
| web        | 类型检查、lint、组件测试；开发期不得用 build 代替检查 |
| docs       | 链接、规则编号、状态同步                     |

一键入口：`bash scripts/test_all.sh`（含分层守卫 + 数据湖写入守卫 + 全量测试发现），具体以 `RUNBOOK.md` 为准。

---

## 14. Web 作用域规则

Web 不是本文件主要作用域。涉及 Web 必须先读 [`web/CLAUDE.md`](web/CLAUDE.md) + [`WEB_DESIGN.md`](WEB_DESIGN.md) + [`Implement.md`](Implement.md)。

根规则：① 非 Web 任务不得顺手改 `web/`；② Web 开发期优先类型检查/lint/组件测试；③ 开发服务运行时不得随意跑生产 build；④ Web 缓存损坏/端口占用/`.next` 问题按 `web/CLAUDE.md` 处理；⑤ Web 展示层不得改变研究/回测/成本口径或入册规则。

---

## 15. 并行与机器纪律

机器：本机 = **Apple M5(10 核:4 性能 + 6 能效)/ 24GB**。运行环境细节见 [`RUNBOOK.md`](RUNBOOK.md) + [`data_infrastructure.md`](factor_research/docs/data_infrastructure.md)。原则：

① 可并行的独立计算应并行（多因子回测/多策略复测/跨接口抓取/多 agent 审计），用 `&`+`wait` 或后台任务；② API 限速/封禁风险优先于并行速度；③ 东财等易封接口不得加多线程硬冲（见 §9）；④ akshare 等易 hang 必须用 daemon+join 超时模式；⑤ 内存是高并发硬约束（并发前看 `memory_pressure`，吃紧降并发）；⑥ 并行只能加速计算，不能改样本/公式/成本/shift/T+1/真实口径；⑦ 后台任务必须可追踪、可停止、可报告，不得变孤儿进程。

---

## 16. 规则守卫表

下表是 `scripts/ci/` 实际守卫的索引（**唯一真相 = 脚本本身**，均由 `scripts/test_all.sh` 调用）：

| 规则 / 关注点              | 等级 | 守卫脚本(`scripts/ci/`)        | 说明                                   |
| --------------------- | -- | ------------------------- | ------------------------------------ |
| R-ARCH-001 单向依赖       | P1 | `check_layer_deps.py`     | AST 静态分析 FORBIDDEN_EDGES，禁下层/生产层反向 import |
| R-ARCH-004 数据湖写入可审计  | P1 | `check_lake_writers.py`   | 写 data_lake 核心区必须走 canonical writer + 更新 manifest |
| R-WF-001 候选入册通道       | P0 | `check_no_force_promote.py`| 禁自动晋级脚本 `force=True` 跳过 phase1/2 防未来门 |
| R-REG-001 / R-EVIDENCE-001 证据自证 | P0 | `check_registry_evidence.py`| 禁跨家族 IC 证据照抄；standalone 在册 DSR 缺算/不显著(≥0.05)亦判失败(G3) |
| R-OBJECTIVE-001 DSR 强制门 | P0 | `strategy_registry.register()` + `check_registry_evidence.py`(G3) | standalone 准入须 dsr_p<0.05(多重测试惩罚显著)，hit 达标≠通过 |
| G8 防自欺 / holdout      | P0 | `check_holdout_compliance.py`| 自动环+promote 验证栈(phase2/3)load 全样本必须截到 <boundary；锁 holdout.start hash(ADR-021)；强制 boundary 只进不退+账本一致(ADR-023)，禁偷看金库 |
| R-COST-001 成本费率钉死   | P0 | `check_cost_model_pin.py`  | 钉死 CostModel 三费率(buy/sell/financing)默认值 hash；改费率须先 ADR+同步四处(cost_model.md §4)再更新 pin，禁为达标临时下调 |
| 防自欺 / 控制路径可观测         | P0 | `check_control_exceptions.py`| 准入/裁决/信号/执行路径禁 `except: pass` 静默吞异常 |
| 测试发现完整                | P1 | `check_test_discovery.py` | 全量收集 `test_*.py`，杜绝漏跑的手工清单           |
| R-DATA-001 禁用旧口径      | P0 | `check_no_legacy_data.py`  | AST 禁代码 import data_full / 从 data_full 目录读盘(放过注释/口径标签/迁移目录) |
| R-ARCH-002 生产层隔离      | P1 | `check_layer_deps.py`(覆盖) | production 禁 import research 在依赖图内强制 |
| 因子词表完整性(词表端防熵)   | P1 | `check_factor_registry.py` + `factors.registry.register_factor`(注册期校验) | 手工三面接线冻结(legacy 只减不增,新因子必走 @register_factor);definition 口径必填;searchable 须带 probe 证据;禁同名双 spec/同源码重复注册;factors/ 零消费者模块必带 Disposition 标记 |
| 文档熵增(ADR-041)        | P1 | `check_doc_map.py`         | 根目录 `*.md` == §2 地图白名单(禁孤儿/禁悬空指针);已跟踪文档链接零悬空;`STATUS.md` ≤200 行(强制历史外溢 `docs/archive/`,防旧绩效冒充当前状态) |
| Git 禁止一锅端             | P1 | 人工 diff                   | 多 agent 共享工作树必守，无脚本可代替               |

凡“缺/待建”的守卫，应在 `TASKS.md` 立项。

---

## 17. 修改文档的规则

修改本文件属架构级变更。允许情形：发现 P0/P1 规则缺失、文档冲突、新增已验证架构边界、成本/数据/入册/回测权威正式决策变化、多 agent 协作协议变化。
修改时必须：① 同步 `STATUS.md`；② 必要时同步 `SPEC.md`；③ 涉及决策追加 `DECISIONS.md`；④ 涉及踩坑追加 `LESSONS.md`；⑤ 不得把临时命令/临时环境/一次性排障细节塞回本文。

---

## 18. 最终原则

```text
宁可少发现策略，也不要相信假 alpha。
宁可慢一点，也不要污染数据口径。
宁可不入册，也不要绕过防未来函数。
宁可让 LLM 干苦力，也不要让 LLM 做判断。
宁可多写守卫，也不要靠人或 AI 自觉。
```

如果任务目标与上述原则冲突，必须停止并报告冲突，不得继续执行。
