# Implement.md — 产品级 AI-native 量化研究平台实施路线(v2.0)

> **定位**:把研究引擎接成 Web 产品的**执行计划**(怎么建、按什么顺序建)。
> **何时读**:推进 Web 产品落地、排期、决定下一步建什么时。
> **不归这管**:产品**长什么样** → [WEB_DESIGN.md](WEB_DESIGN.md)(canonical,研究分析平台);**阶段目标** → [TASKS.md](TASKS.md)(原 `ROADMAP.md` 已冻结归档为 `docs/archive/ROADMAP_saas_v0.1_FROZEN.md`:SaaS 定位与项目定位冲突,见 ADR-041);引擎架构 → [SPEC.md](SPEC.md)。
>
> 把现有 `factor_research` **研究引擎**(已 ~80%)接成 [SPEC(产品)] + [WEB_DESIGN.md](WEB_DESIGN.md) 所定义的**产品级 Web 平台**的执行计划。
> 三份文档分工:**SPEC = WHAT/WHY**(七层 / 8 数据模型 / 控制回路);**WEB_DESIGN = 长什么样**(三栏九页 / 组件 / UX,§15 即 Next.js 栈);**本文 = 怎么建、按什么顺序建**。
> 进度看 [STATUS.md](STATUS.md);引擎内部架构看 [SPEC.md](SPEC.md)(引擎架构 spec,与产品 SPEC 不是同一份)。
>
> **v2.0 变更**:目标从"个人研究"明确为"**产品**"。主前端由 Streamlit 改为 **Next.js + FastAPI** 产品级栈;Streamlit 降级为内部调试/一次性原型工具。**核心未变**:`services/` 接缝层 + 分阶段路线原样保留——这是"换前端 + 加基础设施层",不是重启。

---

## 0. 技术决策(已定)

```text
Frontend:   Next.js + React + TypeScript + Tailwind + shadcn/ui
            TanStack Table(复杂表格) + ECharts/Recharts(金融图表)
            Zustand(状态) + TanStack Query(服务端数据)
Backend:    FastAPI + Pydantic + SQLAlchemy + Alembic
DB:         PostgreSQL(事务:用户/权限/实验/策略/配置)
Analytical: Parquet + DuckDB(行情/因子/回测结果,只读现有湖)
Worker:     Redis + Celery / RQ / Dramatiq(数据更新/回测/因子/报告,异步)
Agent:      独立 Agent Service(工具调用 + RAG + 结构化输出)
Deploy:     Docker Compose 起步,云端后置
```

| 维度 | 决策 | 关键约束 |
|---|---|---|
| 主前端 | **Next.js / React**(对齐 WEB_DESIGN §15) | Streamlit 仅作内部调试,不进产品 |
| 后端 | **FastAPI** 包住现有引擎 | `services` 从 import 接缝升级为 HTTP 接缝 |
| 引擎 | **原地包住,不物理搬迁** | 保住防未来/成本/口径铁律 + 现有 CI 守卫;Monorepo 重构留作后续独立步 |
| 数据 | 事务走 **Postgres**,分析走 **Parquet/DuckDB** | 不重写 pandas 防未来加载器;DuckDB/Polars 只用于新增分析路径 |
| 基础设施 | **按功能需要才引入**(同步先行,Worker/Auth/Postgres 渐进上) | 闭环跑通前不先搭 Celery/Auth/Monorepo |
| 范围 | 产品级架构 + **收敛版 MVP**(P0 页面优先) | "产品级架构 ≠ 一次做完全部功能" |

---

## 1. 起点基线 —— SPEC 模块 → 现有代码 → 动作

| SPEC §6 模块 | 现有代码(`factor_research/`) | 完成度 | 动作 |
|---|---|---|---|
| Data Hub | `lake/`、`data_lake/`、`validate_final.py`、`_manifest.json` | ~85% | **复用**(FastAPI 包) |
| Factor Research | `factors/`、`factors/alpha/`、`metasearch/`(MI/正交)、因子健康 | ~85% | **复用** |
| Backtest | `core/engine.py`(唯一权威)、`core/analysis/walk_forward`、`cost_sensitivity` | ~90% | **复用** |
| Experiment Lab | `factory/{ontology,pool,lines}`、`workflow/{phase1-4,promote}`、`strategy_registry`、`knowledge/` | ~80% | **复用** |
| Portfolio | `portfolio/{composer,marginal,regime,analysis,strategy_runners}`、`paper/` | ~55% | **扩展** |
| 状态层 State | `regime.py`、`core/overlays/hmm_macro`、因子健康、`quality_report.json` | ~60% | **扩展** |
| Risk Control | `audit_six_risks`、压力测试 | ~30% | **新建为主** |
| 报告 Reports | Obsidian 日卡、`reports/` | ~40% | **扩展** |
| AI Agent(嵌入式) | (外部 Claude Code) | ~15% | **新建(Agent Service)** |
| Web 三栏九页 | `web_prototype/`(静态 HTML mockup) | ~10% | **新建(Next.js)** |

**一句话**:难的引擎已挖到 v0.2 严谨度;剩下是**产品壳(Next.js)+ 后端服务化(FastAPI)+ 基础设施(Postgres/Redis/Worker)+ 组合/风控控制回路 + 嵌入式 Agent Service**。

---

## 2. 目标架构与接缝

### 2.1 系统分层(产品级)

```text
Next.js 前端 (三栏:左导航 | 中研究区 | 右 Agent Copilot)
        ↓ HTTP / WebSocket
FastAPI 后端 (Auth | Experiment | Strategy | Portfolio | Risk | Report | Agent API)
   ↓                         ↓                          ↓
PostgreSQL              Parquet + DuckDB           Worker Queue (Redis + Celery)
(用户/权限/实验/配置)    (行情/因子/回测结果)       (数据更新/回测/因子/报告 异步)
                                                         ↓
                                            Quant Research Engine(现有 factor_research,原地复用)
                                                         ↓
                                            AI Agent Service(工具调用 services 白名单)
```

### 2.2 目标目录(**第一阶段原地包,Monorepo 物理重构后置**)

```text
astcok/
├── factor_research/          ← 现有引擎,原样不动(原地被 api 包住)
│   └── services/             ← 新:读模型 + 动作派发(FastAPI 与 Agent 共用的唯一业务入口)
│       ├── read/  {overview,data_hub,factors,backtest,portfolio,risk,experiments}.py
│       ├── actions/  {run_backtest,run_experiment,rebalance,risk_action}.py  (动作分级+二次确认)
│       └── agent/  {context,tools,output}.py
├── api/                      ← 新:FastAPI(薄,只做 HTTP+鉴权+校验,业务全转 services)
│   ├── routers/  {auth,data,factors,backtest,portfolio,risk,experiments,reports,agent}.py
│   ├── schemas/  (Pydantic = SPEC §7 八模型的 API DTO)
│   ├── db/  (SQLAlchemy models + Alembic;Postgres)
│   ├── workers/  (Celery 任务;回测/因子/报告)
│   └── main.py
├── web/                      ← 新:Next.js(对齐 WEB_DESIGN §15 目录)
│   ├── app/  {overview,data,factors,backtest,portfolio,risk,experiments,agent,settings}/
│   ├── components/  {shell,charts,cards,tables,agent,forms,finance}/
│   ├── lib/  {api.ts,format.ts,permissions.ts}  stores/  types/
├── docker-compose.yml        ← 新:postgres + redis + api + web + worker
└── docs/  (SPEC / WEB_DESIGN / API.md / AGENT_DESIGN.md)
```

> **物理 Monorepo(`apps/` + `packages/quant-engine`)是后续独立迁移**,不在前几个 Phase。先用 `factor_research/services` 原地接,守住铁律与现有 CI。

### 2.3 数据落点(事务 vs 分析,必须分清)

| 数据 | 存储 | 理由 |
|---|---|---|
| 用户 / 权限 / 工作区 / 配置 | **Postgres** | 事务、关系、并发 |
| 实验 / 策略台账 / 控制动作 / Agent 任务 | **Postgres**(v0 可暂留 registry-json,功能需要时迁) | 可查询、可审计 |
| 行情 / 因子值 / 回测结果 / IC 序列 | **Parquet + DuckDB**(读现有 `data_lake`) | 列式大规模,DuckDB 即席/serving 查询 |
| 防未来对齐后的面板 | **pandas(现有 `lake/` 加载器,不改)** | 安全关键,绝不重写成 SQL/Polars |

---

## 3. 数据模型先行(SPEC §7)—— API + DB 双形态契约

落 `api/schemas`(Pydantic,API DTO)+ `api/db`(SQLAlchemy,Postgres 表)。两者一一对应。

| 模型 | 现状 | 动作 |
|---|---|---|
| `Hypothesis` | `factory/ontology`(无 direction) | 复用 + 补 market_assumption/failure_condition |
| `Experiment` / `ExperimentResult` | `factory/pool` + `workflow` + registry | 映射到 Pydantic/SQLAlchemy |
| `Strategy` | `strategy_registry`(family/version) | 映射 |
| `FactorDefinition` | `factors/` 隐式 | **新建**显式(neutralization/winsorize/direction) |
| `PortfolioState` | `portfolio/` + `paper/nav.csv` 部分 | **扩展**(暴露/VaR/risk_status 完整快照) |
| `ControlAction` | 无 | **新建**(action 分级 + requires_confirmation + executed_by) |
| `AgentTask` | 无 | **新建**(page_context / tools_used / 结构化 output / confidence) |

---

## 4. 分阶段路线

> 每阶段:**交付物 → 验收(可验证)**。先纵深打通端到端闭环并证明"FastAPI 包引擎成立",再横向铺九页;**基础设施按需引入**。

### Phase 0 — 引擎包壳 + 接缝 + 守卫(不碰 UI,不上 Worker/Auth)
- 交付:`factor_research/services/`(read + actions + agent 骨架);`api/`(FastAPI,**同步**调 services,先不接 Celery/Postgres,元数据走现有 registry-json);`contracts/Pydantic` 8 模型;扩展 `check_layer_deps.py`:`api.` 只能 import `services.`/`schemas.`,`services.` 受控碰 registry/engine/factory。
- **验收**:`GET /backtest/run?...` 返回的绩效与 `strategy_lake.py` **可复现一致**;CI 0 违规;故意让 `api/` 直接 import `core.engine` → 被守卫标违规。

### Phase 1 — Next.js 三栏壳 + 端到端薄闭环(SPEC §15)
- 交付:Next.js AppShell(左导航九项 + 中央 + 右 Agent 占位,WEB_DESIGN §2/§3 配色组件);**总览(精简)+ 因子研究 + 策略回测**三页接真 API。闭环:`数据 → 因子 → TopN 回测 → 净值 → 风险 → 报告 → Agent 解释`。前端**绝不旁路重算**,全部经 API。
- **验收**:UI 选真因子 → 绩效与引擎一致可复现;报告一键生成;Agent 给结构化解读(解释/风险/下一步)。

### Phase 2 — 状态层 + 数据中心页(SPEC §3.2 / §5.2)
- 交付:`services/read` 暴露三类 state(Regime / Strategy Health / Data Quality);数据中心页接 `validate_final`(覆盖率/质量评分/流水线/数据集);总览补全市场状态+因子看板+预警。**此阶段引入 DuckDB** 做数据 QA 即席查询。
- **验收**:数据中心指标与 `quality_report.json` 一致;数据异常时页面提示"数据质量不足,不建议回测"(WEB_DESIGN §14.5)。

### Phase 3 — 异步化 + 组合/风控页 + 控制回路(SPEC §3.3/3.4 + §5.5/5.6 + §10.3)
- 交付:**首次引入 Redis + Worker**(回测/因子计算转异步,任务状态回传);**声明式 `risk_policy`**(单票/行业/回撤/VaR/杠杆/换手);`ControlAction` 落地;组合管理页(持仓/暴露/调仓/订单篮子)接 `portfolio/`;风控页(VaR/集中度/压力/熔断 + 二次确认)。
- **验收**:超 `max_industry_weight` 组合 → 风控页红预警 + 生成 `ControlAction`(待确认,未执行);长回测走 Worker 不阻塞 UI。

### Phase 4 — 持久化升级 + 研究实验页(SPEC §5.7 + §12.1)
- 交付:**引入 PostgreSQL**(实验/策略/控制动作/Agent 任务从 json 迁入,registry 写入口仍唯一);研究实验页接 `factory/pool` + `workflow`(假设池/pipeline/对比/timeline);记录 §12.1 全套可复现元数据。
- **验收**:UI 发起候选 → 走 `promote`(phase1 防未来审计→phase2/3)→ 结论写回 + knowledge graph 长 finding;同 config_hash 可复现。

### Phase 5 — Agent Service + 嵌入式右栏(SPEC §4.4/§9 + WEB_DESIGN §2.5/§14)
- 交付:独立 **Agent Service**(工具调用 `services/agent/tools` 白名单 + RAG over 研究产出);右栏常驻、页面上下文感知(current_page/selected_object/filters);**结构化 `agent_output`**;`AgentTask` 落地;**不越权分级**(只读/低/中/高,高风险二次确认);@对象引用 / 报告预览。
- **验收**:因子页提问 → Agent 用当前上下文作答并标注"研究辅助";让 Agent 改风险规则 → 被分级拦截要确认;Agent 无法绕 services 直写台账(守卫覆盖)。

### Phase 6 — Auth + AI 主页 + 设置 + 报告中心 + 审计(SPEC §5.8/5.9 + §12.2)
- 交付:**引入 Auth(NextAuth/Auth.js)**(单人起,预留多用户);AI 研究助手主工作台;系统设置页(数据源/回测默认参数**只读展示成本铁律**/risk_policy/AI 模型/权限);报告中心(模板化);审计日志(§12.2 关键动作)。
- **验收**:回测默认参数页**不能**把成本调到铁律值以下(只读+备注);关键动作全部入审计。

### Phase 7 — 打磨 + 部署(WEB_DESIGN §13/§17)
- 交付:页面状态机(Loading/Empty/Error/Partial/Stale/Ready/Running/Completed);卡片统一(名/值/变化/更新时间/解释入口);`docker-compose`(postgres+redis+api+web+worker);视觉统一 + 1280px 适配。
- **验收**:逐条过 WEB_DESIGN §17 验收清单;`docker compose up` 一键起全栈。

### (后续,独立步)Phase 8 — 物理 Monorepo 重构
- 把 `factor_research` 抽成 `packages/quant-engine`,`api`/`web` 进 `apps/`。**单独验收**:重构前后所有回测/测试结果不变,CI 0 违规。**非必须,功能稳定后再做**。

---

## 5. 铁律护栏(贯穿全程,违反 = 结论作废)

1. **防未来**:UI/API 只消费引擎产出;任何回测一律走 `core.engine`;**绝不**在前端/SQL/Polars 旁路重算因子或估值;财务 avail_date ffill、资金 shift(1) 不变。
2. **成本铁律**:展示与回测都用 `CostModel` 固化值(买 0.225%/卖 0.275%/融资 6.5%);设置页只读,**UI/API 不可调低**。
3. **口径**:只用 `data_lake` 全市场口径,绝不用 `data_full`;不得引入幸存者偏差子集凑好看。
4. **Agent 不越权**(SPEC §9.2 / WEB_DESIGN §14.2):只读/低风险可自动;中/高风险(改参数/调仓/改风控/下单)必须人工二次确认;Agent 工具走 services 白名单。
5. **分层**:`web → api → services → {registry/engine/factory}` 单向;`check_layer_deps.py` 扩展覆盖 `api.`/`services.`;台账唯一写入口仍是 `strategy_registry.register()`。
6. **包不搬 / 按需上基础设施**:前 7 个 Phase 原地包引擎,不物理搬迁、不重写成 Polars;Postgres/Redis/Worker/Auth 在对应 Phase 因功能需要才引入。

---

## 6. 里程碑验收汇总

| Phase | 核心交付 | 新增基础设施 | 一句话验收 |
|---|---|---|---|
| 0 | FastAPI 包引擎 + 接缝 + 守卫 | (无,同步) | API 绩效与 `strategy_lake.py` 一致 |
| 1 | Next.js 三栏 + 端到端闭环 | Next.js | UI 闭环可复现 |
| 2 | 状态层 + 数据中心页 | DuckDB(QA) | 指标与 `quality_report.json` 一致 |
| 3 | 异步 + 组合/风控 + 控制回路 | **Redis + Worker** | 超限→ControlAction(待确认),长回测不阻塞 |
| 4 | 研究实验页 + 持久化 | **PostgreSQL** | UI 候选走 promote,可复现 |
| 5 | Agent Service + 右栏 | Agent Service | 高风险动作被分级拦截 |
| 6 | Auth + 设置 + 报告 + 审计 | **Auth** | 成本不可调低;动作入审计 |
| 7 | 状态机 + 部署打磨 | docker-compose | 过 WEB_DESIGN §17;一键起栈 |
| 8(后置) | 物理 Monorepo | — | 重构前后结果不变 |

---

## 7. MVP 收敛(产品级架构,功能收敛)

```text
P0(先做):总览 / 数据中心 / 因子研究 / 策略回测 / 右栏 Agent
P1:        组合管理 / 风险控制 / 研究实验
P2:        系统设置 / 权限 / 报告中心 / 多用户协作
```
对应 Phase 0–5 即覆盖 P0+P1 主体;P2 在 Phase 6 收口。

---

## 8. 不做 / 边界

- **不做实盘下单**(SPEC §11.2):订单篮子止于"调仓候选 + 执行前检查"。
- **不重构引擎口径**:全程不改 `core.engine` 回测、成本模型、漏斗逻辑;不重写成 Polars。
- **不预置结论**:knowledge/findings 零预置,UI 只展示现场长出的 finding。
- **不提前上全套基础设施**:闭环跑通前不搭 Celery/Postgres/Auth/Monorepo。
- **借机制不照搬结论**:SPEC/WEB_DESIGN 示例数值都是占位,一律本地用 registry 重算。

---

## 9. 主要风险

| 风险 | 缓解 |
|---|---|
| 物理搬迁引擎破坏防未来/口径铁律 | 前 7 Phase **原地包壳**;Monorepo 重构作 Phase 8 单独验收(结果不变) |
| 前期把 Postgres/Redis/Celery/Auth 一次铺满 → 全是 yak-shaving | 基础设施按 Phase 因功能需要才引入;Phase 0 同步起步 |
| UI/Agent 绕过 services 直 import 引擎,分层瓦解 | Phase 0 即扩展 CI 守卫锁死 `web.`/`api.` 依赖 |
| 在 UI 上"顺手"放宽成本/口径凑好看 | 成本只读 + 口径锁死 + 护栏 §5 写进 CI |
| Next.js+FastAPI 全栈工期长、失焦 | Phase 1 先交付可演示端到端闭环;其余按 ROI 顺序、独立验收 |
