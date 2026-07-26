# 自动化任务规划

> 基于当前代码现状整理。目标是补齐「数据 -> 研究 -> 审计 -> 入册 -> 生产 -> 监控」闭环里的人工断点，同时保留真实交易前的人工确认边界。
>
> 架构图参考: `factor_research/docs/system_architecture_automation.png`

## 现状摘要

当前已经自动化的部分:

- 数据日更与新鲜度校验: `scripts/ops/scheduled_daily_update.py`
- 每日信号与自动模拟盘: `run_daily.py` -> `signals/` -> `scripts/ops/paper_trade.py`
- AutoResearch 搜索与验证: `services/actions/autoresearch_search.py`, `factory/autoresearch/*`
- 候选验证线: `factory/autoresearch/pipeline.py` -> L0/L1/L2/L3
- workflow 深度验证与登记: `workflow/promote.py` -> `phase1_synthetic.py` -> `phase2_backtest.py` -> `phase3_wf.py` -> `phase4_register.py`
- 9-Gate 审计入口: `scripts/research/run_nine_gates_all.py`
- Web/API 受控动作与状态读取: `api/routers/*`, `services/read/*`, `services/actions/*`
- 治理与交易准备度读取: `services/read/governance.py`, `services/read/trade_readiness.py`

当前主要人工断点:

- 9-Gate 完整审计与 `strategy_registry.py` 回填不是自动串在入册流程后。
- AutoResearch approve 后仍需要人触发 promote，且 ACTIVE/SHADOW 边界需要更硬。
- 数据异常已经能阻断部分流程，但缺少统一分诊、建议、可审计状态。
- `model_risk/` 已有审批对象，但和策略入册/9-Gate/交易准备度还不是闭环。
- `scripts/research/` 大量实验结果没有统一 schema 和自动归档。
- `workflow/pending_lessons/*.json` 尚未稳定回写到 `knowledge/graph.py`。
- `report_nlp_pipeline.py` 还偏 demo/inbox 原型，没有成为真实文件队列处理器。

## 不自动化边界

- 不自动真实下单。
- 不允许 LLM 决定策略去留；LLM 只生成候选、解释材料或提取结构化信息。
- 不允许未通过治理闸门的策略覆盖正式 `signals/state.json`。
- 不允许自动把候选升级为 ACTIVE；自动化最多进入 SHADOW 或待审批状态。
- 不允许在数据质量严重异常时继续生成正式生产信号。

## P0 任务

### P0-1: 9-Gate 审计自动回填台账

**目标:** 策略完成 `workflow/promote.py` 入册后，自动触发 9-Gate 审计，并把 DSR/PSR/PBO 等摘要回写到 `strategy_versions.json`。

**现状依据:**

- `workflow/phase4_register.py` 目前只从 phase2/phase3 抽取可得摘要，完整 DSR/PSR/PBO 依赖独立 `run_nine_gates_all.py --persist`。
- `strategy_registry.py::attach_nine_gate` 已有回填入口。
- `services/read/governance.py` 和 `services/read/trade_readiness.py` 已经会读取 `nine_gate` 状态。

**建议改动文件:**

- `factor_research/workflow/promote.py`
- `factor_research/workflow/phase4_register.py`
- `factor_research/scripts/research/run_nine_gates_all.py`
- `factor_research/strategy_registry.py`
- `factor_research/tests/test_governance_integrity.py`
- `factor_research/tests/test_api_contracts.py`

**任务拆分:**

- [x] 给 `promote_spec()` 增加可选参数 `run_nine_gate: bool = False`。
- [x] 在 `Phase4Register.register()` 返回结果中暴露 `family`, `version`, `registered`, `status`，供后续自动审计使用。
- [x] 抽出 `run_nine_gates_all.py::run_evaluation()` 的纯函数入口，避免只能通过 CLI 使用。
- [x] 在 promote 成功后触发 9-Gate；失败时不回滚登记，但写入 `nine_gate.status = "FAILED_TO_RUN"` 和错误摘要。
- [x] `governance` 页面/接口展示 `待多重检验审计`, `审计失败`, `审计通过`, `审计未通过` 四种状态。
- [x] 加测试覆盖: 入册成功但 9-Gate 未跑时不能被判为自动可交易；9-Gate 回填后状态更新。

**验收标准:**

- `python3 tests/test_governance_integrity.py` 通过。
- `python3 tests/test_api_contracts.py` 通过。
- 对一个测试 family 调用 promote 后，可以在 `strategy_versions.json` 对应版本看到 `nine_gate` 字段。
- 9-Gate 运行失败时，系统显示待处理/失败，而不是误判为 PASS。

### P0-2: Approved 候选自动进入 SHADOW

**目标:** 人工 approve 后，系统自动跑 `workflow.promote.promote_hypothesis()`，但自动结果只能进入 SHADOW/候选，不能直接 ACTIVE。

**现状依据:**

- `services/actions/autoresearch.py::review_autoresearch_candidate()` 已记录 approve/reject。
- `services/actions/autoresearch.py::promote_approved_candidate()` 已能从 approve 候选触发 promote。
- `workflow/phase4_register.py` 已经区分自动入册 standalone 轨与 diversifier 人工判断。

**建议改动文件:**

- `factor_research/services/actions/autoresearch.py`
- `factor_research/services/actions/jobs.py`
- `factor_research/workflow/promote.py`
- `factor_research/workflow/phase4_register.py`
- `factor_research/services/read/autoresearch.py`
- `factor_research/tests/test_action_jobs_phase7.py`
- `factor_research/tests/test_autoresearch_engine.py`

**任务拆分:**

- [x] 新增 `auto_promote_after_approve` 配置项，默认关闭。
- [x] approve 后如配置开启，创建异步 job 调用 `promote_approved_candidate()`。
- [x] 给自动 promote 增加 `target_status="SHADOW"` 或等价 admission 限制。
- [x] 强制校验: 自动 job 不得写 ACTIVE。
- [x] Review queue 展示 `APPROVED`, `PROMOTING`, `PROMOTED_SHADOW`, `PROMOTE_FAILED`。
- [x] Web/API job result 返回 phase1/2/3/4 摘要。

**验收标准:**

- approve 后能自动创建 promote job。
- promote 成功后策略不进入 ACTIVE。
- job 失败时 review queue 保留失败原因和可重试状态。
- 单测断言自动 promote 永不 ACTIVE。

### P0-3: 信号生成前统一 readiness gate

**目标:** `run_daily.py` 正式覆盖 `signals/state.json` 前，统一检查数据、治理、衰减、模拟盘、交易日状态。不通过时只生成草稿报告，不覆盖正式状态。

**现状依据:**

- `scheduled_daily_update.py` 已有新鲜度检查。
- `services/read/trade_readiness.py` 已读取治理闸门。
- `services/read/governance.py` 已识别 DSR 未审计/未通过状态。
- `scripts/research/decay_monitor.py`, `scripts/research/live_readiness.py` 已存在运行态判断脚本。

**建议改动文件:**

- `factor_research/run_daily.py`
- `factor_research/scripts/ops/scheduled_daily_update.py`
- `factor_research/services/read/trade_readiness.py`
- `factor_research/services/read/risk.py`
- `factor_research/contracts/views.py`
- `factor_research/tests/test_api_contracts.py`

**任务拆分:**

- [x] 定义 `ProductionReadiness` 结构: `allowed`, `blocking_reasons`, `warnings`, `data_date`, `expected_trade_date`, `governance_status`, `decay_status`。
- [x] 在 `run_daily.py` 写正式信号前调用 readiness gate。
- [x] 若 `allowed=False`，写 `signals/drafts/YYYY-MM-DD.json`，不改 `signals/state.json`。
- [x] `scheduled_daily_update.py` 把 readiness 结果写入 `reports/ops/daily_update/YYYY-MM-DD.json`。
- [x] Web 总览/风险页展示阻断原因。

**验收标准:**

- 数据 stale 时不会覆盖 `signals/state.json`。
- DSR 未审计或未通过时，生产信号被标记为不可自动放行。
- 模拟盘仍可读取旧正式信号，不被草稿污染。

## P1 任务

### P1-1: 数据异常自动分诊与修复建议

**目标:** 把数据质量问题从“日志 + 人工判断”升级为可追踪队列: 自动分类、建议修复、标记阻断等级。

**建议改动文件:**

- `factor_research/lake/validator.py`
- `factor_research/lake/quarantine.json`
- `factor_research/scripts/repair/*`
- `factor_research/scripts/ops/scheduled_daily_update.py`
- `factor_research/services/read/data.py`
- `factor_research/tests/test_data_layer.py`
- `factor_research/tests/test_lake_invariants.py`

**任务拆分:**

- [x] 定义数据问题分类: `STALE`, `MISSING_BAR`, `OHLC_INVALID`, `NEGATIVE_PRICE`, `AMOUNT_UNIT_SUSPECT`, `FUNDAMENTAL_ALIGNMENT`, `ETF_SOURCE_STALE`。
- [x] 为每类问题定义 severity: `block_production`, `block_backtest`, `warn_only`。
- [x] 生成 `reports/data/data_issue_triage.json`。
- [x] 对低风险问题输出自动修复命令建议。
- [x] 对高风险问题只输出人工复核任务，不自动修。

**验收标准:**

- 日更失败后能在固定 JSON 中看到问题分类和建议。
- 严重数据问题会阻断正式信号。
- 非严重问题只告警，不阻断。

### P1-2: model_risk 自动建卡与审批材料

**目标:** 策略完成入册/审计后自动生成 model card，记录 owner、审批状态、9-Gate、phase 报告、容量和限制。

**建议改动文件:**

- `factor_research/model_risk/model_inventory.py`
- `factor_research/model_risk/approval_workflow.py`
- `factor_research/services/read/governance.py`
- `factor_research/api/routers/governance.py`
- `factor_research/strategy_registry.py`
- `factor_research/tests/test_governance_integrity.py`

**任务拆分:**

- [ ] 定义 model card 字段和 strategy registry 的映射规则。
- [ ] 入册成功后自动创建或更新 model card。
- [ ] 9-Gate 回填后同步更新 model card metadata。
- [ ] Web governance 页面展示审批状态和审计摘要。
- [ ] 审批状态不通过时，`trade_readiness` 强制要求人工审批。

**验收标准:**

- 每个 registered family/version 都有对应 model card。
- 审批状态变化能影响 trade readiness。
- model card 可追溯到 phase1-4 和 9-Gate 证据。

### P1-3: 研究脚本结果统一归档

**目标:** 将 `scripts/research/` 的实验输出统一写入 `research_ledger`，避免研究结论散落在脚本输出和 reports 中。

**建议改动文件:**

- `factor_research/research_ledger/ledger.py`
- `factor_research/research_toolkit/artifacts.py`
- `factor_research/scripts/research/incubation_policy.py`
- `factor_research/scripts/research/run_nine_gates_all.py`
- `factor_research/scripts/research/report_nlp_pipeline.py`

**任务拆分:**

- [x] 定义 `ResearchRunRecord` schema: `script`, `hypothesis`, `data_vintage`, `metrics`, `verdict`, `artifact_paths`, `next_action`。
- [x] 提供 `record_research_run()` helper。
- [x] 先接入 3 个高价值入口: `run_nine_gates_all.py`, `report_nlp_pipeline.py`, `incubation_policy.py`。
- [x] 输出统一索引 `reports/research_ledger/index.json`。
- [x] Web/API 可读取最近研究结论(`/experiments/research-runs`)。

**验收标准:**

- 三个入口运行后都会写 ledger。
- ledger 中能判断: 证伪、待复核、进入 SHADOW、可 promote。
- 不要求一次性改造所有研究脚本。

### P1-4: pending_lessons 自动进入知识图谱

**目标:** 把 `workflow/pending_lessons/*.json` 自动去重、归类，并回写到 `knowledge/graph.py` 可消费的 skip/deprioritize 规则。

**建议改动文件:**

- `factor_research/workflow/phase1_synthetic.py`
- `factor_research/workflow/phase4_register.py`
- `factor_research/knowledge/graph.py`
- `factor_research/factory/autoresearch/reflection.py`
- `factor_research/tests/test_knowledge.py`

**任务拆分:**

- [x] 定义 lesson 分类: `TIMING_PEEK`, `FUND_ALIGNMENT`, `AMOUNT_FORMULA`, `WARMUP`, `DELISTED`, `WF_NEGATIVE_WINDOW`, `CORRELATION`。
- [x] 读取 pending lessons，按 fingerprint 和 pattern 合并。
- [x] 生成 `knowledge/findings.json` 更新建议。
- [x] AutoResearch 搜索前读取 findings，用于 skip/deprioritize。
- [x] 保留保质期/精确策略名 gate，避免错误规则永久污染搜索空间。

**验收标准:**

- 重复 lesson 不再无限堆积。
- AutoResearch 的失败台账提示包含最新归类规则。
- 错误 lesson 可以人工禁用。

## P2 任务

### P2-1: 研报 NLP 从 demo 变成真实 inbox

**目标:** `report_nlp_pipeline.py` 扫描 `data_lake/research_pdf/` 新文件，解析、提取、校验、去重、写入 `research_signals/`，失败进入队列。

**建议改动文件:**

- `factor_research/scripts/research/report_nlp_pipeline.py`
- `factor_research/scripts/research/industry_logical_chain.py`
- `factor_research/factory/ontology/report_logic.py`
- `factor_research/data_lake/research_signals/`
- `factor_research/services/read/fundamentals.py`

**任务拆分:**

- [ ] 新增 PDF inbox 状态文件: `data_lake/research_pdf/_inbox_state.json`。
- [ ] 按文件 hash 去重。
- [ ] 解析失败写 `reports/research/report_nlp_failures.jsonl`。
- [ ] DeepSeek 不可用时不使用 mock 写正式信号，只写失败原因。
- [ ] 输出 `LogicalChain` 和普通 sentiment 两类信号。
- [ ] 接入 9-Gate 或 SHADOW incubation 入口。

**验收标准:**

- 新 PDF 只处理一次。
- 没有 LLM key 时不会生成伪信号。
- 每个信号带 `report_date`, `available_date`, `source_pdf_hash`。

### P2-2: 定时监控告警闭环

**目标:** 日更、周更、API/Web 常驻、健康检查失败时，不只写日志，还生成统一告警事件。

**建议改动文件:**

- `factor_research/scripts/ops/scheduled_daily_update.py`
- `factor_research/scripts/ops/scheduled_weekly_maintenance.py`
- `factor_research/scripts/ops/health_check.py`
- `factor_research/scripts/ops/prod_health_check.py`
- `factor_research/services/read/state.py`
- `factor_research/reports/ops/`

**任务拆分:**

- [ ] 定义 `ops_alerts.jsonl` 格式: `time`, `source`, `severity`, `message`, `artifact`, `action_required`。
- [ ] 日更失败、数据 stale、API down、Web down、模拟盘异常均写 alert。
- [ ] Web 总览读取最近 alert。
- [ ] 告警确认动作写审计日志。

**验收标准:**

- 不看 launchd 日志也能知道最近失败原因。
- 同一失败不会刷屏，按 source + date 去重。
- 告警能被人工确认关闭。

### P2-3: SHADOW 策略影子 NAV 自动跟踪

**目标:** 所有 SHADOW/候选策略自动生成影子净值、衰减和相关性报告，供升降级决策。

**建议改动文件:**

- `factor_research/portfolio/strategy_runners.py`
- `factor_research/services/read/portfolio.py`
- `factor_research/scripts/ops/scheduled_weekly_maintenance.py`
- `factor_research/reports/incubation/`
- `factor_research/strategy_registry.py`

**任务拆分:**

- [ ] 每周读取 registry 中 SHADOW 策略。
- [ ] 运行统一回测/影子 NAV 更新。
- [ ] 计算与 ACTIVE 组合相关性、边际贡献、衰减状态。
- [ ] 输出 `reports/incubation/shadow_nav.json`。
- [ ] 触发建议: `PROMOTE_REVIEW`, `KEEP_SHADOW`, `RETIRE_REVIEW`。

**验收标准:**

- SHADOW 策略每周都有最新状态。
- 升级建议必须带证据，不自动升 ACTIVE。
- 退役建议不删除策略，只打标。

## 推荐实施顺序

1. P0-1 9-Gate 自动回填台账
2. P0-3 信号生成前 readiness gate
3. P0-2 Approved 候选自动进入 SHADOW
4. P1-1 数据异常自动分诊
5. P1-2 model_risk 自动建卡
6. P1-3 研究脚本结果统一归档
7. P1-4 pending_lessons 自动进入知识图谱
8. P2-1 研报 NLP inbox
9. P2-2 定时监控告警闭环
10. P2-3 SHADOW 影子 NAV

## 成功标准

- 研究候选从生成、验证、复核、入册、审计到 SHADOW 观察都有机器可读状态。
- 每个策略版本都能回答: 数据版本是什么、经过哪些门、为什么可用/不可用、是否可交易。
- 生产信号不会在数据或治理状态不合格时覆盖正式状态。
- 人工工作从“找日志和拼材料”转为“审批、解释和最终交易确认”。
- 系统仍保持真实账户执行前的人工边界。
