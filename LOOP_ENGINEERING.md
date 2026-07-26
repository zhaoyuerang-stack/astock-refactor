# LOOP_ENGINEERING.md

> **定位**:自进化机制的**设计宪法**——如何不自欺地持续发现真 alpha。
> **何时读**:设计/修改进化循环、适应度函数、防自欺闸门时。
> **不归这管**:静态架构 → [SPEC.md](SPEC.md);每步流程谁干 → [WORKFLOW.md](WORKFLOW.md);为什么这么决策 → [DECISIONS.md](DECISIONS.md);具体策略/因子实现在 `factor_research/`。
>
> 系统自进化机制的**设计宪法**。回答「如何让系统不断发现真 alpha 而不自欺」。
> 与文档矩阵:架构 → [SPEC.md](SPEC.md);决策记录 → [DECISIONS.md](DECISIONS.md);端到端流程 → [WORKFLOW.md](WORKFLOW.md);操作宪法 → [CLAUDE.md](CLAUDE.md);踩坑 → [LESSONS.md](LESSONS.md) + auto-memory。
> 本文定义**进化机制本身**;具体策略/因子实现在 `factor_research/`。

---

## 0. 为什么需要这份文档

系统的资产 = **持续发现 → 证伪 → 替换** 有效策略(任何策略默认会失效)。把这个循环工程化,就是 Loop Engineering。但**自动化一个有缺陷的循环,只会让系统以工业速度自欺**——2026-06 台账三连暴雷(`illiquidity-large-cap`/`industry-neglect v1.3`/`ai-compute-toc`,详见 [DECISIONS.md](DECISIONS.md) ADR-017)就是证据:旧循环的进化压力指向「钻适应度函数空子」,产出 MA16 择时伪装 / 照抄证据 / 事后叙事,而非真 alpha。本文把「不自欺地进化」固化为机制。

---

## 1. 第一性原理:适应度函数 = 系统

**自进化系统精确优化你度量的东西,包括钻它空子(Goodhart 定律)。**

```
你度量头条夏普  → 进化出 MA16择时+杠杆+照抄证据(最廉价的拉高夏普路径)
你度量真 alpha → 进化出真因子
```

- 系统不会「坏」,它会**太听话**:`illiquidity-large-cap` 夏普 2.2 是进化出的「适应度寄生虫」——裸因子 IC=−0.084 反向,收益 100% 来自择时 overlay。
- 推论:Loop Engineering 的核心**不是「怎么自动跑」,是「怎么设计不可钻空子的适应度函数」**。
- 适应度函数恒为**确定性代码**(承 [CLAUDE.md](CLAUDE.md) LLM 分工铁律:判断归代码)。把适应度判断交给 LLM = 适应度可被说服 = 自欺。

---

## 2. 双层 Loop 架构

可进化 = 两个嵌套循环。**只做 L1 的系统必然退化**(进化压力指向 L1 漏洞);L2 是让适应度函数进化得比寄生虫快的免疫系统。

### L1 — 策略进化环(快 / 便宜 / 高方差 / 可全自动)
```
生成候选 → 适应度筛选(验真) → 登记存活者 → 持续复测 → 衰减则退役
```

### L2 — 验证器进化环(慢 / 关键 / 强模型+人在环)
```
坏策略蒙混过关 → 对抗性复盘「它怎么钻的空子」 → 硬化适应度函数 / 加机械守卫
```
> 本 session 即一次 L2 迭代:发现验真器被「照抄证据 / 择时伪装 / 跳门」钻空 → 落地验真机 + 台账证据守卫硬化它。

**铁律 L-1:L2 不得全自动。** 发现「一类新的自欺方式」是强模型(Claude)+ 人的判断活,代码无法预知。代码只能固化**已知**自欺(守卫);**未知**自欺靠 L2 对抗复盘发现。把 L2 交给被进化的系统 = 让囚徒管监狱。

---

## 3. L1 四阶段 × 现有代码 × LLM 分工

| 阶段 | 职责 | 现有代码 | LLM 分工铁律 |
|------|------|----------|--------------|
| 生成 | 提议候选(DSL 变异/组合/研报假设/参数) | `factory/lines`、报告-NLP | **便宜模型**(候选/抽取) |
| 验真 | 确定性对抗筛选(=适应度函数) | 9-Gate `core/analysis/nine_gates.py` + 验真机(§4) | **确定性代码**(判断) |
| 登记 | 防未来审计 → 入册存活者 | `workflow` phase1_synthetic→phase4_register | 代码 |
| 退役 | 复测 + 衰减检测 → 标退役 | `decay_signal` / 复测 | 代码 |
| 编排 | 串联 / 决定方向 / L2 硬化 | (人 + 强模型) | **强模型 Claude** |

> 生成可廉价高方差;**唯一不可妥协的是验真层的对抗性与确定性**。

---

## 4. 适应度函数规范(验真机 = 新适应度)

候选必须由**本策略本宇宙的一次可复现运行机械产出**全部证据(承 ADR-017「9-Gate 证据自证铁律」)。判据(`scripts/research/strategy_truth_screen.py`):

1. **L0 去 overlay 归因**:剥掉择时/杠杆/veto,看**裸因子**是否自身赚钱。
   - 真 alpha = `L0 夏普 ≥ 0.8` 且独立 IC 方向对、`|IC|≥0.02`。
   - **不看 L0 hit**(回撤过深是 overlay 合法解决的,不否定 alpha)。
   - 择时伪装 = L0 夏普≈0/反向但完整夏普高(钱全来自 overlay)。
2. **本宇宙独立 IC + t-stat**:`IC-IR × √n` 才是因子真显著性(illiq t=3.88)。
   - **铁律 L-2:IC 显著 ≠ 可交易**。vol_neglect/reversal_20 IC t>4.9 却 L0 夏普≈0(成本+换手吃光)。L0 夏普门负责拒掉它们。
3. **诚实 DSR**:`n_trials` = 全局累计搜索规模(见 §5.1),非手填。注意 DSR 对 A 股小盘**肥尾过严**,须与 IC t-stat 对账,别误杀真因子。
4. **容量**:`AUM上界 ≈ n_pos × 10%ADV × median(选中票ADV)`。容量小是个人平台的特性不是缺陷(没被套利抹平的原因)。
5. **PBO**:候选族 CSCV;`None` 不得标 passed。

机械守卫(已接入 `scripts/test_all.sh`):`scripts/ci/check_registry_evidence.py`(G1 跨家族 IC 照抄 / G2 证据全空/跳门)。

---

## 5. 四大防自欺地基(loop 敢放开跑的前提)

**没有这四样,自动化只会更快地自欺。优先级高于「自动编排」。**

### 5.1 持久化诚实 trial 账本 ⟵ 最致命、最常被忽略
永远在搜的 loop,搜得越多多重检验惩罚越重。必须**跨时间持久记录**每个 alpha 是「历史累计搜过的第几个里挑出来的」,DSR 用此累计计数而非手填。否则系统稳定产出幸存者偏差假 alpha。
> ✅ 已实现:`governance/trial_ledger.py`(append-only `data_lake/governance/trial_ledger.jsonl`;`honest_n_trials(scope)` 喂 DSR)。**记账已下沉到搜索 chokepoint** `run_autoresearch_island_search`/`run_autoresearch_walk_forward`——所有搜索路径(含 walk-forward / research 脚本)经此自动记账,不靠各 caller 自觉,堵住「搜了不记 = DSR 虚松」(缝①);+ `apps/factory_cli.py`(mutate 新增候选数)。

### 5.2 Holdout 金库
一段 loop **从未、永不**用于搜索的数据(最近 N 月滚动前推),仅晋级前**唯一一次**校验。这是唯一能戳穿「过拟合到适应度函数」的东西。**loop 自己不得触碰**。
> ✅ 已实现:`governance/holdout.py`(`boundary` 读 settings.yaml::holdout.start=2025-01-01;`assert_search_clean` 自查门;`validate_on_holdout` 唯一校验+偷看计数)。已接验真机 + island search 搜索窗截断 + **9-Gate 评估同样截 <boundary**(补「评估半边」洞:选择层不得用金库)+ 晋级前 `validate_on_holdout` 写 review 证据 + `phase4_register` `holdout_id` 登记闸(无通过记录则拒登记,向后兼容旧/手动路径)。

> ✅ **补漏(ADR-021,2026-06-22)**:此前 `workflow/phase2_backtest.py`(OOS 段硬编码 2023-2026 + 成本/相关性/decay 全用 2018-2026)与 `workflow/phase3_wf.py`(WF 测试窗到 2026)**整条 promote 验证栈都吃金库**——金库期(2025+)绩效经机械门(`annual>0`/`OOS/IS decay>0.3`/WF 正窗比)与人眼报告参与晋级判定,破坏「唯一一次」语义。已在两文件 `run()` 的 **load 后单点截到 `<boundary`**(因子/三段/成本/相关性/WF 窗全部派生自被裁面板,金库永不进入),OOS 终点改由 boundary 动态裁定;两文件已纳 `check_holdout_compliance.py` REQUIRED + 自查门 `assert_search_clean`。回归测试 `tests/test_holdout_truncation.py`。
> ✅ **边界配置锁(ADR-021)**:`holdout.start` 是软配置,改它即改金库范围。`check_holdout_compliance.py::check_boundary_lock` 把当前值 hash 钉死,任何改动 exit 1,强制走 DECISIONS(ADR)+ 同步更新 pin。
> ⚠️ **信度声明(物理限制,不可代码修复)**:金库需时间积累。当前金库 2025-01-01→今≈**1.5 年(~370 交易日)**,做年化/最大回撤/holdout DSR 的统计推断**信赖区间极宽**——真 20% 年化策略 1.5 年内出 −10%~+50% 都在 1σ 内,且这段未必含熊市。`_MIN_HOLDOUT_OBS=20` 是算 DSR 的下限,非充分。**系统对外宣称「holdout 验证通过」时必须附带此信度限制**;金库成熟前,holdout 是「未证伪」而非「已证实」。
> ✅ **boundary 迁移机制(ADR-023,已强制,非草案)**:到 2027 金库后移面临两难——后移则旧金库段(2025-26)变可搜索、既往记录失唯一性;不移则旧金库变死数据。**已机械强制**:① **只进不退**——唯一推进入口 `governance.holdout.migrate_holdout_boundary()`,后移/相等抛 `HoldoutBoundaryRegression`(复活已偷看金库段);② **append-only 历史账本** `app_config/holdout_boundary_history.jsonl`(git 跟踪,genesis=2025-01-01),active 金库 = 账本最大值,旧边界自动 `superseded` 永久留痕不删;③ **守卫** `check_holdout_compliance.py::check_boundary_monotonic` 强制账本严格递增 + `settings.holdout.start == active`(手改前进未记录 / 后退均 exit 1),叠加 ADR-021 的 hash 锁;④ **多重检验按 active 金库计**——`holdout_trials/validate_on_holdout` 的 n_trials 只数当前 boundary 的 peek,旧金库(superseded)peek 不污染新金库惩罚,同一候选可对新金库合法重校验。推进流程:`migrate_holdout_boundary()` 记账 → 改 `settings.yaml` → 更 `check_holdout_compliance.py` 的 `EXPECTED_BOUNDARY[_HASH]` pin → 记 DECISIONS。

### 5.3 容量/边际贡献感知的适应度
若适应度只看单腿夏普,loop 会进化出 50 个同质变体(本 session:illiq+size 相关 0.82,同一「小/不流动」赌注)。适应度必须 = **对当前组合的边际真 alpha × 容量**,否则找的是冗余不是分散。
> ✅ 已实现:`governance/marginal.py::marginal_alpha`(候选对在册组合残差化;高相关+残差夏普弱=判冗余)。✅ **已接**(缝④):`scheduled_factor_search` 逐候选算边际真 alpha 入 review 证据(判冗余);promote `_run_marginal` 边际定级已 holdout 截断。

### 5.4 衰减 / 遗忘
alpha 默认会失效。loop 必须主动复测 + 退役(`decay_signal`),而非只进不退。只增的台账会被僵尸策略塞满。auto-memory 记录**已证伪的 idea + 原因**,防生成器重提死路(如 [[toc-cpo-mechanism-falsified]])。
> ✅ 已实现:`governance/decay.py::decay_check`(滚动3年夏普<0.5 / Rank IC 连续4季<0 → 触发退役复核)。✅ **已接**(缝④):`decay_monitor`(周度)通用复测扫 `data_lake/version_returns` 全在册版本,decayed → `reports/research/retirement_review.json`(退役执行仍人确认,§6)。

---

## 6. 自动 vs 人/强模型在环

| 环节 | 自动化程度 |
|------|-----------|
| 生成候选 / L0-L3 廉价筛 / 验真电池 / 复测 | **全自动**(cron/workflow,便宜模型+代码) |
| 持久 trial 计数 / PBO / DSR | 全自动(代码) |
| **L2 适应度硬化(发现新自欺)** | **强模型 + 人**(铁律 L-1) |
| **触碰 holdout 金库 / 晋级在册** | **人确认**(资本/口径风险) |
| **真实下单** | **人**(第一阶段不自动下单,承 SPEC) |

---

## 6.5 LLM 可驱动边界(DeepSeek = 燃料,非发动机/方向盘)

系统已内置 DeepSeek 苦力模型(`services/agent/llm_adapter.py::get_adapter()`,`app_config/settings.yaml::ai_model`,无 key 退规则式;`run_autoresearch_island_search(use_llm=True)` 已在用)。**澄清一个致命混淆:进化的「动力」不是 LLM,是适应度函数。** LLM 只是变异算子(产生多样性),决定系统往哪进化的是选择压力 = 验真机(§4)。

**铁律 L-3:DeepSeek 只能驱动「生成」,绝不触「验真/选择/L2」。**

| Loop 环节 | DeepSeek 可驱动? | 理由 |
|------|:---:|------|
| 生成候选(变异 / 研报假设 / DSL 组合) | ✅ 能,且已在用 | 苦力归便宜模型;高方差廉价提议正合适 |
| 批量 NLP / 打标 / 候选抽取 | ✅ 能 | 同上 |
| **验真(适应度函数)** | ❌ **绝不** | 恒为确定性代码;LLM 可被叙事说服 = 自欺 |
| **选择 / 退役 / 入册门槛** | ❌ 绝不 | 防自欺判断恒为代码 |
| **L2 验证器硬化**(发现新自欺) | ❌(归 Claude + 人) | 需对抗判断;便宜模型不会怀疑自己生成的东西 |
| 节拍 / 持续运转 | ⚠️ 非 LLM,是 cron/workflow | 「自运转」动力 = 调度基础设施 |

**为什么不能让它当裁判**:便宜模型**更**易被似是而非的叙事说服。TOC 的「CPO 去 DSP 化光器件毛利爆发」正是 LLM 裁判会盖章通过、而 PnL 归因(代码)一打就碎的东西。让 LLM 判 alpha 真伪 = Goodhart 加速器 = 一键作废本文全部防自欺机制。

**可无人值守的最大范围**:`cron → DeepSeek 变异 N 候选 → 验真机(代码)L0/IC/holdout 筛 → 真 alpha 短名单`。这一圈 DeepSeek 当生成动力成立、且廉价正是其价值(高频海量提议,后有不可钻空子的适应度兜底)。但**两闸门留人/强模型**:① 晋级在册 / 碰 holdout 金库(资本与口径风险);② L2 适应度硬化(抓「照抄/择时伪装」这类 DeepSeek 看不见的新自欺)。

> 一句话:**DeepSeek 是燃料(廉价高方差生成),不是发动机(适应度函数)也不是方向盘(选择/L2)。让它喷油省钱;让它点火或掌舵,系统会以工业速度自欺。**

---

## 7. 失效模式清单(怎么知道 loop 在自欺)

- 头条夏普高但 L0 裸因子夏普≈0 → 择时伪装。
- 9-Gate 证据与别策略逐位相同 → 照抄(G1 守卫)。
- `nine_gate={}`/`passed_all=true` 但 PBO=None → 跳门(G2 守卫)。
- 压力段反成最佳年 → regime 依赖(吃特定行情)。
- 新登记 alpha 与在册组合相关 >0.7 → 冗余非分散。
- DSR 随登记数增长持续乐观 → trial 账本没在累计(§5.1 失守)。
- 机制叙事讲得通但 PnL 不落在叙事点名的链上 → 事后叙事(做 PnL 归因,见 [[toc-cpo-mechanism-falsified]])。

---

## 8. 落地优先级

1. **先地基,后编排**(否则自动搜=工业化自欺):§5.1 持久 trial 账本 + §5.2 holdout 金库。
2. §5.3 容量/边际贡献适应度(直接解决 illiq+size 冗余)。
3. 把验真机 + 守卫接成 cron 自动 L1 环(生成→验真→候选短名单),人确认晋级。
4. §5.4 衰减自动复测。

---

## 9. 本 session 已落地的零件(证据链)

- 适应度函数:`scripts/research/strategy_truth_screen.py`(L0 归因+独立IC+DSR+容量+判决)、`factor_pool_screen.py`(批量+组合)。
- 右尾/选股层度量接 canonical:`engine/metrics.py`(cvar_right/capture_spread/winner_concentration)。
- 机械守卫:`scripts/ci/check_registry_evidence.py` + `tests/test_registry_evidence_guard.py`(已接 test_all.sh)。
- §5.2 holdout 端到端接线:`scheduled_factor_search.py`(搜索+9-Gate 全程截 <boundary、晋级前 `validate_on_holdout` 写 review 证据)+ `workflow/phase4_register.py`(`holdout_id` 登记闸,向后兼容)。
- 四缝堵漏(§5 接线完整化,审接线不审存在):①trial 记账下沉搜索 chokepoint(orchestrator);②holdout 跨候选多重检验(`holdout_trials`+`deflated_sharpe` 惩罚,金库不再被规模化白嫖);③`scripts/ci/check_holdout_compliance.py` 守卫 + `portfolio/cross_asset`、promote `_run_marginal` 择优截断;④`marginal_alpha`/`decay_check` 接进 review 证据 / 周度退役复核。守卫已入 `test_all.sh`。
- L2 复盘记录:[DECISIONS.md](DECISIONS.md) ADR-017 + auto-memory(`nine-gate-evidence-self-proving`、`toc-cpo-mechanism-falsified`、`strategy-truth-screen-and-first-real-alpha`)。
- 首个通过验真的真 alpha:小盘 illiquidity(L0 夏普1.19 / IC t=3.88 / 容量~0.21亿)。

---

## 10. 一句话

> **可进化 = 不可钻空子的适应度函数(L1)+ 比寄生虫进化更快的免疫系统(L2)+ 戳穿过拟合的 holdout 金库 + 全局诚实的 trial 账本。**
> 流水线只是把这四样串起来跑;缺任一样,自动化只会让系统更快地自欺。
