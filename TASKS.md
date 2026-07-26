# TASKS — 开放任务 backlog

> 单一真相源:**所有未完成的可执行任务**。其余文档(STATUS/对话)只记历史/状态,开放任务一律落这里,防遗忘。
> 完成即移到「近期完成」或删除。新任务带:来源 + 上下文 + 谁能做。

## 🔴 进行中 / 优先

### DQ-2026-001 fundamental_batch ann_date 修复收尾(2026-07-23 发现并同日修复;详见 docs/data_quality/register.md)
- [x] **根因定位** — ✅ `lake/schema.py::YJBB_RENAME` 把东财"最新公告日期"(公司级滚动字段,非本期专属公告日)误当 `ann_date` 消费,现场用 `ak.stock_yjbb_em` + tushare income 现网核实(000001.SZ 2024-06-30:真实公告日 2024-08-16,污染值 2025-08-23)。
- [x] **存量重铺** — ✅ `scripts/data/repair_fundamental_batch_ann.py` 按 `lake/fundamental_repair.py` 三级优先级级联(income/balancesheet/cashflow 三表 MIN 主源 → tushare f_ann_date 侧车补位 → 法定披露上限兜底)重铺全表,备份于 `data_lake/backups/`;守卫 `check_fundamental_batch_pit.py` 从 435 处未登记违规转为全绿(exit=0),例外集 561→0 行、403→92 行。
- [x] **ingestion 源头修复** — ✅ `build_fundamental_batch.py`(全量重建)+ `lake/update.py::update_fundamental()`(日更增量)均接入 `lake.fundamental_repair`,不再用东财"最新公告日期"直接当 ann_date、不再用粗糙"+45天"兜底当主口径。单测 `tests/test_fundamental_repair.py` 10 例(含级联优先级对抗测试)。
- [x] **移植 strategy-path-analysis 机制层** — ✅ `.claude/skills/strategy-path-analysis`(`scripts/research/strategy_path_report.py` + `strategy_registry.attach_path_metrics`)从协作方独立 clone 移植,本仓独立验证(52 例测试全过 + 实跑 size-earnings/v1.0 成功)。只搬机制,不采信对方已跑出的旧结果(不同代码/数据状态)。
- [x] **重跑受影响 registry 版本并回写 strategy_versions 结论(6/11)** — ✅ 11 个版本中 6 个已用 `strategy_path_report.py --persist-path-metrics` 活跑并诚实回写(`strategy_registry.attach_path_metrics`,`attach_data_incident` 标 resolved=true):autoresearch_2335eeab/234c8ab7/8de70997/e181a275(均符号反转正→负,与 round7 net_profit_yoy/roe 负IC结论一致)、fundamental-momentum/v0.1(显著恶化未反转)、size-earnings/v1.0(唯一改善,annual 8.71%→12.02%)。均未做新旧数据受控 A/B 对比,不作因果归因,只如实记录差异。详见 `docs/data_quality/register.md::DQ-2026-001`。
- [ ] **【待办,新发现】修复 `strategies/industry_rotation.py` 全版本零收益 bug(阻塞剩余 5/11 重跑)** — 重跑 industry-neglect-rotation 时发现 `run_industry_rotation_strategy`(v1.0/v1.1 已直接验证,繞过 strategy_path_report.py 包装层直接调用同样复现)产出全零收益(2044 天全 0,sharpe=nan)。已排除与 DQ-2026-001 相关(v1.0 的 ETF 等权持有分支完全不读 roe/npy 仍复现零收益);根因疑似在 `ind_mom`/`ind_vol`/`ind_amt_growth` industry-level 因子计算或更上游的 `aggregate_industry_data`/`load_industry_groups`,未深挖。首次误将该零结果写入台账覆盖真实旧数字后已撤回(archived 到 `evidence.metrics_history`,reason=`reverted_broken_rerun`)。剩余 industry-neglect-rotation v1.0-1.4 共 5 个版本的 DQ-2026-001 incident 保持 `resolved=false`,待此 bug 修复后才能继续重跑。谁:研究侧。
- [ ] **【待评估】R3 的"≤2025-03-31 整段豁免"是否随修复移除** — 该豁免是守卫 R3 规则本身的一部分(不是例外 CSV),当前 92 行残留(`DQ-2026-001_late_gt2025q1_403.csv`)看起来多为真实严重逾期披露的公司(兜底代理值虽 >180d 但本身合理),建议先抽查确认无遗留问题再决定是否收紧规则本身。谁:研究侧。
- [ ] **【待评估,round12 独立发现的其他 frontier-fundamental-family 影响】round7/8 六因子结论是否需用修复后数据重跑** — net_profit_yoy/revenue_yoy/roe/gross_margin/bp_proxy/ep_proxy(`direction_registry.json::frontier-fundamental-family`)此前的 weak/falsified 结论建立在污染数据上,数据滞后通常削弱而非制造虚假信号,判定大概率仍成立,但不能排除修复后数字有变化。谁:研究侧,待 owner 决定优先级。

### 【ADR-037】自然语言验证编排器 — 实施任务清单(方案 E)

> **目标**:用户 NL 验证策略想法;LLM 翻译并**选既有协议推进**;Strict tool/CLI 为唯一产品证据;Lab 默认非证据;永不宣布 alpha;不改核心约束代码。  
> **来源**:`DECISIONS.md` ADR-037 + owner 2026-07-16 产品真意。  
> **非目标**:自动入册/下单;裸 shell 当正式回测;桌面第二套验证口径;取消 CLI。  
> **依赖序**:P0 文档✅ → P1 Envelope 契约 → P2 协议注册/可达性 → P3 L0 probe 包装 → P4 确认回测 → P5 Lab 隔离 → P6 统一 Agent Service(可与 P4/P5 部分并行,但勿先于 P1)。  
> **验收总闸**(ADR-037 最低对抗集):① lab→formal 必拒 ② 无 Strict payload 绩效不得上屏为已验证 ③ mid 回测无确认不可跑 ④ Agent 路径禁写台账旁路 ⑤ `can_claim_valid` 默认 false 变异必红。

#### 现状基线(已完成,勿重做)
- [x] **P0.1 ADR-037 + CLAUDE 指针 + STATUS** — ✅ 2026-07-16 `c3c6400c`
- [x] **P0.2 idea_precheck 能力** — ✅ `strategy_idea_check` + 桌面 CLI 证据路径(`96193c1b`)

#### P1 — Evidence Envelope 契约 + 上屏拦截
- [x] **P1.1 契约模型** — ✅ `contracts/evidence.py` EvidenceEnvelope + 对抗测试
- [x] **P1.2 统一装配入口** — ✅ `services/agent/evidence.py` wrap/public_view;tools 挂 envelope
- [x] **P1.3 桌面展示拦截** — ✅ diagnosisService allowsPerformanceDisplay + 屏蔽年化/夏普;信任条 tier/protocol
- [x] **P1.4 API 透传** — ✅ envelope 在 tool JSON 内,agent_cli 原样透出(Web 大改后置)

#### P2 — Protocol 注册表 + data_gap_audit
- [x] **P2.1 Protocol 注册表** — ✅ `services/agent/protocols.py` + list_protocols tool
- [x] **P2.2 data_gap_audit** — ✅ `services/read/data_gap.py` + tool;WACC 类 missing 诚实
- [x] **P2.3 协议 skill** — ✅ Pi system prompt / strategy-precheck 走 catalog+CLI
- [ ] **P2.4 structured idea DTO** — 后置可选

#### P3 — proxy_or_signal_probe
- [x] **P3.1 受控 probe 回执 + 全量接线** — ✅ `run_signal_probe`：`full=true` 调 `signal_source_probe.probe`；`full=false` 仅回执；落 reports/research
- [x] **P3.2 holdout 截断** — ✅ end/cutoff ≥ boundary 必拒
- [x] **P3.3 桌面可调** — ✅ 工具 risk=readonly,Pi catalog 可见
- [ ] **P3.4 阴性回写 direction_registry** — 仍须人确认(不自动)

#### P4 — engine_backtest HITL
- [x] **P4.1 mid confirm-token** — ✅ agent_cli `--confirm-token` + `ASTOCK_MID_CONFIRM_TOKEN`;无 token 拒
- [x] **P4.2 回测 envelope** — ✅ run_backtest 包 engine tier
- [x] **P4.3 桌面确认 UX** — ✅ capabilityService + IPC `capability:run` + 确认弹窗 +「正式回测（需确认）」按钮；未确认不调 CLI

#### P5 — Lab 隔离
- [x] **P5.1 formal 路径对抗** — ✅ scratch/results/logs 非正式证据(测试钉死)
- [ ] **P5.2 OS 级 lab 目录沙箱** — 后置(策略层已禁 formal 洗白)
- [x] **P5.3 展示降级** — ✅ precheck 禁 perf display
- [x] **P5.4 文档** — ✅ desktop README + ADR-037

#### P6 — Protocol runner
- [x] **P6.1/P6.2 protocol_runner** — ✅ `run_protocol_step` 校验协议×tool
- [ ] **P6.3 Web 对齐** — 后置
- [ ] **P6.4 session 审计日志** — 后置

#### P7 — high 仅提案
- [x] **P7.1 propose_high_risk_action** — ✅ 永不 executed=True
- [x] **P7.2 工具描述** — ✅ 提案语义写入 tool desc

#### 里程碑建议(可按人天裁)
| 里程碑 | 包含 | 可演示 |
| --- | --- | --- |
| M1 | P1 全完成 | 无 CLI 的假年化上不了「系统事实」 |
| M2 | P1+P2 | 「WACC」类想法 → 缺字段诚实 + 协议进度 |
| M3 | +P3 | 对**已有**因子一键 L0 probe 报告(非 alpha) |
| M4 | +P4 | 确认后正式回测证据卡 |
| M5 | +P5 | Lab 与 Strict 同屏不洗白 |
| M6 | +P6/P7 | 单服务编排 + 高风险仅提案 |

#### 本清单不做(明确排除)
- 为过演示改成本/样本/holdout/shift
- 自动入册或 `force=True` promote
- 真 WACC 数据全量 onboarding(另立项,走 data_source_onboarding;本清单只保证「缺数据时诚实」)
- Web 九页大改

### 【桌面延迟】对话发送后回复偏慢 — 优化 backlog

> **问题**:每条消息等完整 Pi 回合 + 多次 `python3 agent_cli` 冷启动(本机粗测 catalog/call 冷启动 ~0.9s/次,业务 check 本身 ~75ms);体感 10–60s。  
> **约束**(ADR-037 不可拆):正式证据仍走 Strict tool;禁为快让模型散文当证据;禁为快改成本/holdout/假回测。  
> **来源**:2026-07-16/17 桌面体感 + 链路分析(冷 Pi / 模型时间 / ×N 冷 Python)。  
> **推荐序**:观测 → A2+A4 → A3 流式 → A1 常驻 → B 快路径/会话;一意图一 commit + 对抗测试。

#### 第 0 步 — 观测(先做,指导后续取舍)
- [x] **L0.1 回合计时日志** — ✅ 2026-07-20 落地(commit 84344c58,Grok 委托单,Claude 验收):`roundTiming.cjs`(fail-open 落盘 `.runtime/round_timing.jsonl`,gitignored)+ piBridge WeakMap 侧信道计时(hrtime 测 pi_ms/tool 区间,不污染返回值)+ diagnosisService 三分支(strategy|stock|agent_only)回合末落盘。对抗测试 3 例(mock 假诊断字段逐项齐全/不可写路径不阻断诊断/返回值逐位不变);验收=47/47 测试亲跑 + 活体突变复验(挖 model_est_ms 字段 3 例真红→还原复绿;破坏内层 fail-open 被外层 catch 吸收=三层冗余防护确认)。**后续**:积累若干真实回合数据后读 `.runtime/round_timing.jsonl` 回答「模型 vs CLI 谁占 70%+」,再决定先做 A1 还是 A2。

#### A 类 — 高 ROI 优先
- [ ] **A2 策略预检单 tool 纪律** — system prompt + `strategy-precheck` skill:策略类默认只调 1 次 `strategy_idea_check`(已含质量/漏斗/成本);禁止为凑证据再调 `data_quality`/`experiments`/`factors`;缺字段再 `data_gap_audit`。对抗:录一轮 tool 列表,冗余只读 ≤1(或 +data_gap)。谁:桌面。  
- [ ] **A4 模型够用即可** — 预检路径更短输出上限;可选更小/更快 `ASTOCK_PI_MODEL`;thinking 保持 low。对抗:回复长度有上限配置;证据卡仍来自 CLI envelope。谁:桌面。  
- [ ] **A3 分阶段回包(流式体感)** — Pi JSON 流已有 `tool_execution_*`/`message_end`:主进程边收边 IPC 推 UI(已调 CLI / precheck 到 / 最终叙述)。对抗:首条进度事件在终态前必达;终态仍可 envelope 校验。谁:桌面。  
- [ ] **A1 Tool 常驻 RPC** — Electron 起长期 `agent_rpc`(HTTP/Unix socket)暴露同一 `tool_registry`;桌面/Pi extension 改调 RPC,保留 `agent_cli` 调试。对抗:冷路径 N 次 tool 总 import 时间不再线性 ×N;readonly/mid confirm 语义不变。谁:研究侧+桌面。

#### B 类 — 中 ROI 第二波
- [ ] **B1 协议快路径(按钮/Skill 绑定,非主机猜意图)** — Skill=策略预检或显式按钮 → 主机直接 Strict 调 `strategy_idea_check`(及已有正式回测 confirm 路径);LLM 仅润色 envelope,禁升 tier。对抗:快路径无 CLI payload 不得展示绩效;与自由对话路径可切换。谁:桌面。  
- [ ] **B2 Pi 会话复用** — 同 thread 复用 session(弱化 `--no-session`);每条 user 消息仍强制至少一次 Strict tool(防空聊装证据)。对抗:跨 thread 不串 session;无 tool 的回合不得标 formal。谁:桌面。  
- [ ] **B3 Catalog/读层短 TTL 缓存** — extension catalog 缓存 30–60s;`data_quality`/funnel 内存短 TTL。对抗:缓存失效后仍读到新报告;mid 路径不缓存错 token。谁:桌面+研究侧。

#### C 类 — 明确不做(防快致腐)
- 去掉 CLI/Strict,改裸 shell 或 Pi 直 import 当产品证据  
- 为快跳过 Evidence Envelope / `can_claim_valid`  
- 每条消息默认全量 probe 或正式回测  
- 多线程硬冲易封接口  

#### 建议里程碑
| 里程碑 | 内容 | 体感目标 |
| --- | --- | --- |
| M0 | L0.1 计时 | 知道瓶颈在模型还是 CLI |
| M1 | A2+A4 | 少 1 轮 tool + 更短生成 |
| M2 | A3 流式 | 2–5s 内见进度/证据,不再干等终态 |
| M3 | A1 常驻 | N 次 tool 亚秒级 |
| M4 | B1 快路径 | 点「预检」约 1–3s(+可选润色) |

- [x] **【WS-D】组合再构成周度 job + top-N paper 排名持久化(ADR-034 后续)** — ✅ 2026-07-02 落地:`portfolio/recompose.py`(确定性内核:多目标排名 mean-rank(sharpe/calmar/边际残差夏普)+衰减腿强制垫底+非冗余贪心选腿+静态 inverse-vol 提案+组合自身 decay_check;口径 RANKING_VERSION 锚定,改口径须 bump+记 DECISIONS)+ `scripts/ops/scheduled_portfolio_recompose.py`(周度,读在册 version_returns → 持久化 `reports/research/portfolio_recompose.json` latest+归档,R-PROD-001)+ 决策收件箱第八源(info 级提案,过期>14天不入箱)。对抗测试 8/8(高夏普衰减腿必垫底/冗余双胞胎只留一/样本不足诚实拒判/全灭空提案/确定性)。**留白**:paper 多账户并行实测的执行侧(见下一条)。
- [x] **【WS-E】文献扫描剧本(枯竭触发的外探之二,ADR-034 后续)** — ✅ 2026-07-02 落成 `factor_research/docs/agent_skills/literature_scan.md`(人批准触发;先读方向登记簿再扫描防重发现死路;产出带出处 Hypothesis 草案走 `factory_cli queue` canonical 通道;阴性结论也落报告;不判有效不写台账);已接进收件箱枯竭事项 actions + CLAUDE.md §2 剧本路由。
- [x] **【probe 结论回写纪律】** — ✅ 2026-07-02 固化为 probe-signal-source skill 步骤 8(必做):阴性→DEPRIORITIZE/SKIP 条目(带 revival_condition+180 天 expires)、阳性→BOOST,均须证据指针(证据门控拒无证据条目);种子置顶指引同步改为登记簿 BOOST(不再硬编码 _SEEDS 顺序)。
- [x] **【WS-D 执行侧】paper 多账户并行实测绑定(代码侧)** — ✅ 2026-07-10 按 `.claude/plans/PLAN_paper_multiaccount_loop.md` T1→T5 落地(7 commits):`portfolio/paper_accounts.py` 多账户管理器(active/frozen/blocked/degraded 四态;名单唯一来源=recompose 持久化 `paper_candidates`,stale>14天 fail-closed;信号只走 `build_executable_strategy` canonical 路径,R-BT-001)+ `scripts/ops/paper_accounts_update.py` 日更旁路 + `services/read/paper_accounts.py`/`GET /paper-accounts` 读层(含实测 vs 回测同窗偏差)+ web dashboard 并排展示(顺序=后端排名,禁前端重排,mutation 测试验证)。paper_engine 参数化保 legacy 单账户 parity。验证:新增测试 26/26、pytest 18 失败=基线零新增、web tsc/lint 绿 37/38(1 既有误报)、守卫 10/10。**留白见下一条生产机验收。**
- [ ] **【WS-D 验收】paper 多账户生产机人工验收** — worktree 无数据湖/无真实 paper 状态,代码侧全部 hermetic 合成数据测试;上线前须人在生产机按 `RUNBOOK.md` §④.5 清单验收:名单健康检查 → dry-run provision(legacy account.json 字节不变)→ 正式 provision → 连续 ≥2 交易日观察 → legacy 单账户 paper 流零 diff 核对 → web 实数据目检;launchd 挂载由人决定。谁:用户(生产机)。
- [x] **【probe 执行·需数据湖】资产负债表运营质量因子族步骤 3 体检(孤岛回收①后续)** — ✅ 2026-07-12 已跑(全市场,2018→cutoff 2022→2024,worktree symlink 主仓湖):**三成员全阴性,不接工厂**——bargaining_power 正交保留率仅 17%(大半 size/流动性伪装,残差 IC≈0);receivable/inventory_intensity_chg **IS 符号与设计假设相反**(强度抬升 IS 反而跑赢,更像扩张信号)且 OOS 塌缩/翻号(留存 9%/−49%)。归因:资负表结构强行业性,全市场直接截面主要在比行业构成。已按步骤 8 回写登记簿 DEPRIORITIZE(180d;**复活条件=行业分类落湖后行业内中性化重测**,依赖产业基本面 Phase 2 `meta/industry.parquet`)。报告:`reports/research/probe_fundamental_quality_20260712.md`。
- [ ] **【诚实性·2026-06-30 对抗审查发现】composite-portfolio n_trials 诚实重审** — `composite-portfolio` v1.0 与 size_mix_v1.0 均含 3 个配比分量(illiq_sc/lc_mom/reversal)却在 `nine_gate.n_trials` 写 `1`,**低报搜索自由度**(配比网格+双阀择时熔断参数+各子策略各自的搜索均未计入),骗松 DSR 多重测试惩罚——即便如此 dsr_p 已=0.0737(v1.0)/0.154(size_mix)**不显著**。新增守卫 `check_registry_evidence.py::find_understated_trials`(机械下界:组合 n_trials≥分量数)已能拦,当前两版先入 `PENDING_REMEDIATION` 基线(响而不阻,因 status=候选/未部署)。**待办**:走 `promote_composite.py`/register 重审,如实计入真实搜索自由度后重写 `n_trials` 与 `notes`(现 notes「自动化对抗审查结果：通过」措辞误导——"通过"仅指执行衰减守卫,该版 `passed_all=False`),并从 `PENDING_REMEDIATION` 移除基线项。**绝不为过门下调 n_trials**(R-EVIDENCE-001 ④)。谁:走 workflow(人确认)。
- [ ] **【2026-09-30 复核】small-cap-size/v2.0 纸面前向实验(ADR-024)** — 06-22 启动的人工 override 纸面前向(零真金,DSR=0.086 未过门,主仓继续防守)。跟踪器 `scripts/research/paper_forward_smallcap.py` 已接进日更旁路(每日自动累积快照到 `reports/experiments/smallcap_v2_paper_forward.jsonl`)。**到 ~2026-09 底(≥3 月前向)复核**:前向兑现(夏普稳、回撤受控、未破 MA16 防守)→ 证据增强(n_periods↑,DSR 可能真过)→ 再议小额真仓 + 走正式 promote;前向塌陷/破防守 → 证否、停实验。**注:复核前绝不因「纸面好看」就绕 DSR 门登记在册(R-LLM-001/§12.3)。** 谁:用户 + 走 workflow。
- [ ] **【并行设计】冠军因子 L1~L3 审计并行化改造** — 06-28 发现演化收尾时的 Top K 冠军因子 L3 审计为串行，单线程排队 Walk-Forward 耗时 15-20 分钟成为新瓶颈。已在 `factory/autoresearch/islands.py` 完成多核并行化重构（父进程顺序写盘以防止文件锁死锁），语法编译已通过。待随下一次大规模进化寻优启动进行实跑效果与性能验证。谁:研究侧 + 走 workflow。

### 因子词表治理(2026-07-12 因子库机制分析后续;守卫 `check_factor_registry.py` 已落地为第一步)
- [ ] **【词表迁移·步骤2】94 条 legacy 手工接线迁 `@register_factor`** — 守卫已冻结存量(legacy 只减不增):DSL 46 + whitelist 46 + catalog 2(唯一名字 48 个:9 价量/基本面 + 32 alpha101 + 5 隔离岛/北向 + 2 catalog)。分批迁移,每条补 definition(口径)/evidence(searchable 者),迁一批从守卫 `LEGACY_HANDWIRED` 删一批。**迁 `illiquidity` 时必须解决口径分叉**:DSL/白名单版 = `|ret|/volume`(数学上 = Amihud×价格水平,混入价格因子),canonical 家族版 = `|ret|/amount`(经典 Amihud,`factors/alpha/builtins/illiq.py`)——改 amount 口径或改名(如 `ret_per_share_volume`),并复核依赖它的候选/台账语义。谁:研究侧,机械劳动为主。
- [ ] **【词表卫生】alpha101 退化因子扫描** — `alpha_005` 含 `-Abs(R(close-close))` 恒常子项(close-close≡0),实际退化为 `-0.5×rank(close-MA10)`,与 price_to_ma 同信息,白占搜索自由度且虚增 n_trials 分母(R-EVIDENCE-001 精神)。写一次性机械检查:对 32 个 alpha 移植扫恒常子项 + 与现有因子秩相关>0.98 者,退化者移出白名单(同 commit 更新守卫 legacy 清单)。谁:研究侧。
- [ ] **【词表治理·步骤3】factor_store 缓存治理 + 回流活性表** — ① `factors/autoresearch_dsl.py::_get_cache_path` 缓存 key 加因子 `source_hash`(registry 已产出,改实现自动失效,拆"静默复用旧因子值"的 R-DATA-001 级隐患);② GC job:`data_lake/factor_store/panels/` 现 980 面板/36GB,707 个 `_mt` 时间戳旧代无人删,策略化只留当前 mtime 代(确定性策略删除,非 agent 判断);③ 回流活性表:周维护 status 加"机械回流产物新鲜度"(redundancy_clusters.json / frontier.json / factor scores 的 mtime vs 预期周期)——07-02 建的 metasearch 月度回流至今零产出无人知晓,fail-open 必须配活性观测。谁:研究侧,②③在生产机验证。
- [ ] **【待 owner 决策】R-ARCH-006 取代性变更配对义务(入宪提案)** — 机制分析结论:修正以"新增层"落地、被取代者永生(momentum.py illiquidity → alpha 框架 → DSL 绑旧版的口径分叉即此机制产物)。提案:新模块/入口/框架与既有者职责重叠时,PR 必须在 MODULE_STATUS 或 ADR 写明旧者去向(退役/降级/并存理由),缺处置决定 = 守卫 FAIL。属 CLAUDE.md §17 架构级变更,需 owner 批准后另行实现。谁:owner 决策 → 研究侧实现。

### 引擎修复后的台账诚实性缺口(2026-06-21 发现;均待用户决定,我未碰部署清单)
- [x] **生产部署清单指向一个从未真正过闸的策略** — ✅ 2026-06-28 已将 `deployments/production.json` 从 `status=active + illiquidity/v3.1` 改为显式 `status=paused` 且 `legs=[]`。含义:当前没有合规可部署 alpha 腿,生产继续 fail-closed,但清单不再声明失效策略为 active。新增守卫测试:默认清单若声明 `active`,必须能被 `load_active_deployment()` 机械加载,防止再次出现"声明 active / 实际不可部署"。验证:`python3 -m pytest tests/test_deployment_manifest.py -q` 10/10 通过;`get_production_readiness()` 仍 `allowed=False`,阻塞原因变为 `deployment status='paused'` + `deployment_not_ready` + `paper:blocked`。
- [x] **前端债券轮动指引诚实化(2026-06-23 对话发现)** — ✅ 2026-06-28 已在当前前端真实展示点完成 fail-closed 展示修复:`components/paper/PlanCard.tsx` 对 stale 债券轮动标注「冻结历史信号/非现行可执行」并停止输出“次日买入/卖出/继续持有”等行动话术;`app/dashboard/page.tsx` 与 `app/signal-audit/page.tsx` 统一用 `!paperPlan.stale && readiness.allowed_to_trade` 才展示执行清单/候选动作/执行风险,否则显示“信号已过期/生产门禁已拦截,非现行可执行”。这是纯展示层修复,不改研究口径、不改 paper 账本。新增守卫测试 `web/lib/staleBondInstruction.test.mjs`;验证:精确测试 3/3 通过,TypeScript 与 lint 通过,排除未跟踪 `pageDataIntegrity.test.mjs` 后 web 既有测试 17/17 通过。
- [x] **防御择时(MA16+国债轮动)与已降级主腿解耦(2026-06-23 对话发现)** — ✅ 2026-06-28 已先完成执行层机械解耦:`runtime.deployment.defensive_authorization()` 只从部署清单中独立 `role=defensive` 腿导出授权;`run_daily.py` 的 `rotation` 只有拿到该授权才会写 `recommend_bond=true/511010/defensive_authorization`;`scripts/ops/paper_trade.py` 和 `portfolio/paper_engine.py` 对 legacy `bond.enabled` fail-closed,无授权不买 511010、不卖遗留债券,并写明 `defensive overlay 未授权`;`services/read/paper.py` + 前端 `PlanCard` 将未授权债券状态显示为「非现行可执行」。边界:这不是宣称 MA16 overlay 已独立通过门禁;只是阻断它继续寄生在已降级 alpha 主腿上。后续若要恢复债券轮动,必须先把 defensive overlay 作为独立腿走证据/台账/部署流程。
- [x] **发现并验证 core.engine T+1 fill 修复(5ebcdde7c,06-20 21:04)使此前所有 9-Gate 审计失真** — ✅ 逐日比对 illiquidity/v3.1 修复前后收益序列,从第1天就有差异(最大单日差5.5pp);maxdd 从旧引擎算出的 -14.7% 纠正为真实 -29.3%(发生在2019-2020 COVID段,旧序列误判到2018年末)。已用修复后引擎重跑 illiquidity 全家族(v1.0/v1.1/v1.3/v3.0/v3.1/clean-v1)9-Gate + 持久化收益序列 + `lineage_pbo` 重算:**六个版本 passed_all 全为 False**,Gate4(DSR不显著)/Gate5(回撤超-20%线,v1.0/v1.1/v1.3 甚至-41%~-42%)/Gate6(成本衰减87%~110%)三道独立检验全部不过;家族 PBO=0.92(版本选择基本是噪音);v1.0 与 clean-v1 日收益 corr=1.0(数学同一序列)。衰减监控在新序列上仍判"未衰减"(滚动3年夏普2.20,逐年走高)——alpha本身没变差,是旧引擎一直在低估风险。结果存 `research_ledger`(run_id `30e5e8ed8a7d1b46`)。
- [x] **其余 4 个家族 7 个版本(8减1,large-cap-growth-hedged/v1.0-full 状态=参考未审)用修复后引擎重审完毕** — ✅ 全部 `passed_all=False`(13个版本里离显著最近的是 small-cap-size/v2.0,DSR p=0.086)。但 `decay_check` 在新序列上揪出真问题:`hq-momentum-hedged`(v1.0/v1.0-full)与 `large-cap-growth-hedged`(v1.0除外/v1.1/v1.1-full)**decayed=True**——长历史版本滚动3年夏普从2018年起连续7年为负,回撤-85.2%~-95.9%,触发 LOOP_ENGINEERING §5.4 机械退役复核条件,不是统计证据薄弱,是真衰减/可能从未起效。`size-earnings`/v1.0 与 `small-cap-size`/v2.0 健康未衰减,后者回撤-17.7%是当前台账唯一一个全历史回撤达标(<20%)的版本。结果存 `research_ledger`(run_id `859bb3e836dc7be9`)。
- [x] **`hq-momentum-hedged`、`large-cap-growth-hedged`(对冲组合)走退役复核** — ✅ 已完成:`strategy_versions.json` 现状(daily-round-3 2026-07-05 复核确认)`hq-momentum-hedged` 2/2 版本、`large-cap-growth-hedged` 3/4 版本均已转「退役」。全池当前 0 个「在册」策略(`decay_status.json::no_registered=true`),详见 `reports/research/research_director_review_round3_findings.md` §1.1。
- [x] **周度自动化三件套修复(解释器+decay_monitor范围/调度+9-Gate覆盖)** — ✅ 根因是 `scheduled_weekly_maintenance.py` 用系统自带 `/usr/bin/python3`(3.9.6,不支持 `int|None` 运行时语法)起子进程,导致9步里6步(`decay_monitor`/`tradability`/`live_readiness`/`factor_search`/`cross_asset_leg_search`/`audit_stale`)全部在 import 阶段就崩——改成 `/opt/homebrew/bin/python3` 后4步(`decay_monitor`/`tradability`/`factor_search`/`audit_stale`)实跑确认 `ok=True`。顺带把 `decay_monitor` cron 改指向现代版(`scripts/ops/decay_monitor.py`,旧的 `scripts/research/decay_monitor.py` 范围只到部署的1条腿+自身有语法错,不再调用),范围扩到全部12个在册版本(原来 `run_active()` 结构性碰不到 `hq-momentum-hedged`/`large-cap-growth-hedged` 的 `-full` 版本);`current_deployment_identity()` 解除无保护调用(同 production_readiness.py 已有修法)。`run_nine_gates_all.py::VERSION_OVERRIDES` 补 `hq_momentum/v1.0-full`(原3个疑似漏审,核实后只有这1个是真缺口)。**意外收获**:解释器修复让脚本第一次真正跑到深处,揪出2个之前从未暴露过的问题——① `live_readiness.py`/`health_check.py`/`paper_trade.py` 三个 `decay_status.json` 老消费者用的是已废弃的单策略IC schema(`d['ic']`等),改 schema 后直接 `KeyError`,已同步改成读新的多版本 `strategies[]` 数组,已修复验证;② `cross_asset_leg_search` 暴露出真实卡在上面那条"部署清单漂移"待决项上(见上一条)。`bash scripts/test_all.sh` 全绿。
- [x] **illiquidity clean-v1 入册** — ✅ 已登记 `status=候选`(非在册,留待人确认)。**但补跑 PBO 后发现它不是"干净范本"**:`lineage_pbo` 算出 clean-v1 与 v1.0 日收益 corr=1.0(数学上同一序列,只差 leverage 标量,并非独立配方);9-Gate 全套(n_trials=6/7)passed_all=False。脚本 `factor_research/scratch/register_illiquidity_clean_v1.py`。详见下方"illiquidity 全家族重审"。
- [x] **#5 独立数据族隔离岛(股东行为+资金流)** — ✅ 已接进 autoresearch:`factors/shareholder.py`(holder_count_chg/holdertrade_net)+`factors/capital_flow.py`(large_order_net_ratio),注册进 `factory/autoresearch/registry.py::ALLOWED_FACTORS`(变异/初始化/LLM播种三路径共用同一词表,已验证可达)+ 新 LLM 主题"股东行为与资金流"(`services/actions/autoresearch_search.py::_ISLAND_THEMES`,需 islands≥5 才轮到,已把 `scheduled_factor_search.py` 的 islands 从3调到5)。**验真结论**:全市场 top25 多头下三者都不是 real_alpha,且与 illiquidity/size 簇相关0.66-0.83(疑似小盘选股漏斗,非数据无信息);收窄到大中盘(u300/u800)后独立IC反而走强(0.02→0.04)但组合夏普仍弱——信息可能真实存在但分散在横截面,top25集中持仓吃不到。结果存 `research_ledger`(run_id `e6e655401623899d`)。**下一步留白,需用户决定是否投入**:试 top50-100 更宽持仓或多空结构(不是简单调参,是新的持仓结构设计,基因组目前不支持搜索 top_n/long-short)。
- [x] **三假货台账处置(ADR-017)** — ✅ ① `illiquidity-large-cap/v1.0` 退役/重分类(独立 IC=−0.084 反向、9-Gate 证据照抄小盘);② `industry-neglect-rotation/v1.3` 补独立 9-Gate(L2 复合 DSR/PBO)+ admission 标注「裸因子年化13.8%/回撤−60% 不达标,standalone 靠 MA16 overlay」(**注:ADR-020 已因 DSR=None 把 v1.3 由在册 standalone 降为「参考」,重分类部分已落,独立 9-Gate 补审已于 06-30 补齐**);③ `ai-compute-toc/v1.0` logic chain `ai_compute_toc_bottleneck` 降级 + desc 失效标注(机制三重证否)。草稿 `scratch/{illiq_largecap_governance,toc_honest_conclusion}_DRAFT.md`。处置后从 `scripts/ci/check_registry_evidence.py::PENDING_REMEDIATION` 移除对应 key(守卫会提示)。谁:走 workflow。
- [x] **既往 phase2/3 绩效用截断后引擎重算(ADR-021 后续)** — ✅ ADR-021 把 promote 验证栈(`phase2_backtest`/`phase3_wf`)截到 `<holdout boundary`,既往在册策略的 phase2 三段(OOS 含 2025-26 金库)与 phase3 WF 数字都含金库、偏乐观。已用截断后引擎重跑在册版本的 phase2/3(包括 small-cap-size, size-earnings, large-cap-growth-hedged, hq-momentum-hedged 等 6 个主要在册与候选版本), 顶层 metrics 与 data_scope.period 已同步重算回写更新完毕。注:此为绩效诚实化,不改入册结论(入册仍以 hit+DSR+9-Gate 为准)。谁:走 workflow / 研究侧。
- [ ] **扩展 9-Gate runner 覆盖(ADR-020 §E 后续)** — `run_nine_gates_all.py` 仅支持 5 家族特定版本;30 个 nine_gate 空版本(autoresearch_*/small_cap_factor__window*/industry-neglect/size-low-vol/d-le-sc-hedged 等)**全无兼容 runner 或缺该版本 config 规格,现 0 个可审**。补审须为这些家族写配置驱动适配器(类比 `ILLIQ_SPECS`)或补 `VERSION_OVERRIDES`。优先级:低——全是候选/参考/退役,**非在册 standalone,不构成 DSR 治理缺口**(唯一要 DSR 的 status 已清零)。谁:研究侧按需。
- [x] **CLAUDE.md 扩写(宪法)** — ✅ ADR-017 的「9-Gate 证据自证」5 条已落为新宪法 P0 规则 `R-EVIDENCE-001`(并接 `check_registry_evidence.py` 守卫);文档矩阵已含 `LOOP_ENGINEERING.md` 指针。详见 ADR-019(宪法升级为规则编号治理系统)。
- [x] **防自欺地基集成 follow-ups** — ✅ ① `record_trials` 钩进 `apps/factory_cli.py::cmd_generate`(记新增候选数;factory/lines 经此入口);② island search 搜索窗截到 `<boundary`(`scheduled_factor_search.py` 载面板截断后传 close/volume/amount/forward_ret → 演化不触金库)。**残留**:promoted 候选的 9-Gate eval 仍载全样本,理想也应截 <boundary(NineGatesEvaluator 自带 OOS 逻辑,深改,留续办)。
- [x] **Loop 地基续建(§5.3/§5.4)+ 接线** — ✅ §5.3 `governance/marginal.py::marginal_alpha`;§5.4 `governance/decay.py::decay_check`。**已接**:① marginal_alpha 残差法接进 `workflow/promote.py::_run_marginal`(补 line3 raw-corr 方法的洞,根因#2;残差判冗余即建议 SHADOW);② `scripts/ops/decay_monitor.py` 定时衰减复测(实跑在册4策略全健康,illiq 滚动3年夏普2.276)。**待办**:decay_monitor 接周度 cron/launchd(命令给用户,不自动装系统任务)。

### 执行现实核对清单落地缺口(2026-06-30 发现;源 `回测执行现实核对清单.md` 机械化测试 `tests/test_execution_reality.py`)
- [ ] **【P0·台账诚实性】canonical 数据湖是幸存者偏差面板,不含退市股(2026-06-30 实测证否)** — 开工"退市归零"时实测发现**根因比原设想更深**:不是引擎 drop 持仓退市股,而是**采集层只取 list_status='L' 存活股,退市股从未入库**。证据(主仓 `data_lake`):① `price/daily_all.parquet` 5207 只全部交易到 ~2026-06-30,仅 6 只序列提前终止且全在 2026;② 8/8 已知退市股(乐视网300104/中弘退000979/华锐风电601558…)在面板与 `meta/codes.parquet`(5524)中**全缺失**——元数据层都不认识退市股;③ 全库无任何退市命名 parquet,`meta/delisted_codes.parquet` 不存在。**台账诚实性违规(本节主题)**:`small-cap-size/v2.0`(status=在册)`data_scope.survivorship_bias=False` **经验上为假**;同 T+1-fill 修复那次,这是又一个让**已登记数字失真**的数据缺陷。**严重度校准**:方向确定(收益被高估),量级**未量化且在补数据前不可量化**——勿把 v1.0/data_full 的 8.5% 移植到 v2.0;退市集中于小盘/ST,正是 small-cap/illiquidity 选股域,但收益核心**本已知不可部署**(G5回撤/G6成本,见 [[project_registry_gate_reality]]),此发现是"历史数字再添高估"而非"好策略其实是假"。**次生发现**:R-DATA-002 的执行点 `phase1_synthetic.py` check5(退市股覆盖)在 `delisted_codes.parquet` 缺失时降级为 WARN(`:464`)→ P0 不变量被静默降为警告,从不触发。**重构后的任务链(a→b 闸住 c)**:(a)数据层摄取退市股全历史(含退市整理期价格,东财封禁/tushare 退市价口径约束,大活)→(b)phase1 守卫改 fail-closed + 修台账假声明(R-REG-001,只能走 register 入口,**待用户决定**)→(c)引擎归零/清算逻辑此时才相关、可测。**边界**:不擅自翻 registry survivorship 标志、不擅自起摄取作业,均待用户拍板。谁:数据层 + 用户决策。
- [ ] **【引擎/口径·高优先】按确定的 T+1 收盘口径复测旧 gap 数** — ✅ **契约已钉死**:官方成交口径 = **T+1 收盘**,已落 `core/engine.py:115` 权威注释 + `SPEC.md §统一回测内核·执行契约`(2026-06-30);`PricePanel.raw_open` 死脚手架已删(从不构造/从不读取,删后零数值影响,引擎测试全绿)。**残留(研究侧)**:`回测执行现实核对清单.md` C 组及此前 gap 研究的 reality 层按 T+1 开盘/VWAP,与确定口径差一档 → 前面执行 gap 量级数(含下方 ΔSh≈-0.01)须用 **T+1 收盘**口径重测后才能用于实盘决策。若未来真要支持开盘成交,须新增 `T_PLUS_1_OPEN` 分支 + 立 ADR + 同步 SPEC。谁:研究侧。
- [x] **【小杂务】OrderSimulator 删/deprecate + 修撒谎 docstring** — ✅ 2026-06-30 已按 R-ARCH-005 三要件 deprecate:`git mv` → `execution/_deprecated_order_simulator.py`(路径标记)+ 从 `execution/__init__.py` 撤出再导出(包命名空间已无 `OrderSimulator`)+ docstring 据实改写(点明它是单步无状态拒单过滤器,**无 T+1/无顺延/无退市清算**,非旧 docstring 宣称的 "Simulates T+1 settlement")+ 实例化发 `DeprecationWarning`。真实契约钉死在 `tests/test_execution_reality.py::TestExecutionRealityGaps`。验证:受影响 2 测试(execution_reality 7+1skip、institutional 5)+ 分层/控制路径/测试发现 3 守卫全绿。
- [ ] **【暂缓】封板(涨跌停)/停牌冻结接入 canonical 引擎** — 清单 A/B 的封板顺延+累计顺延损失、停牌持仓冻结目前未接引擎(唯一实现 OrderSimulator 已 deprecate)。**暂缓,证据注记:ΔSh≈-0.01,对当前策略类(低换手 top-25 小盘/illiquidity)不动针,换高换手/事件驱动策略再议**。注:此结论须在上面「口径对齐」敲定后复核——若 gap 数口径失真,ΔSh 也要重测。谁:换策略类时再议。

### 根因分析修复(用户提供的 6 项修复顺序;多 agent 共享工作树,我只取独立文件避冲突)
- [x] **#1 禁 force-promote 硬闸** — ✅ `scripts/ops/bulk_promote.py` 改硬闸(`force=False`+`run_nine_gate=True`+`run_marginal=True`,blanket auto-approve 默认关须 `BULK_AUTO_APPROVE=1`)+ 防回归守卫 `scripts/ci/check_no_force_promote.py`(AST 扫自动晋级脚本禁 force=True/run_marginal=False,接 test_all)。注:**未运行 bulk_promote**(写台账,运行须 workflow+用户)。
- [x] **#4 alpha/overlay 分账** — ✅ `governance/alpha_overlay.py::split_alpha_overlay`(裸因子=唯一 alpha 记账;overlay=full−bare 只记风险贡献;裸≈0但完整高→判 overlay 造假拒)。已接验真机 `strategy_truth_screen` 每行输出 `alpha_overlay_split`。21/21 测试绿。
- [ ] **#2 适应度方向归一 + 扣市场/book residual** — 🔧 部分:并行 agent 已修方向错位(novelty 在 direction-applied 面板算)+ 用 L0 edge + 加 corr_to_book 罚(默认关 corr_weight=0)。**残差法**(扣市场/book beta)已有 `governance.marginal_alpha` 并接进 workflow 准入,但**搜索 fitness 的 corr 罚仍是 raw 相关**(corr_weight=0 时不激活);要激活须用残差相关,待。
- [x] **#3a NW-ICIR 参与筛选** — ✅ `islands.py` fitness edge 从 raw `ICIR` 换成 `ICIR_nw`(NW 重叠校正,~3.5x 诚实量级;raw 仅留报告)。修「raw ICIR 淹没 novelty/turnover 项」。**注**:edge 量级变了 ~3.5x,fitness 权重(novelty 0.25/turnover 0.15)需经 **#6 A/B 重验**;在此之前搜索探索效率可能偏移,但产出仍过 L0-L3/9-Gate/holdout 硬闸,不会让垃圾入册。
- [ ] **#3b 真元级 walk-forward** — 周度调度仍走 `run_autoresearch_island_search`,非 `run_autoresearch_walk_forward`(演化只见 ≤cutoff、冠军 (cutoff,oos_end] 一次性 OOS)。现搜索窗已 holdout 截断(止血),但真元级 WF 待切换。
- [x] **#5 按独立数据族建隔离岛(真出路)** — ✅ 已接股东行为+资金流(详见上方"防自欺治理"节同名条目,含验真结论与下一步)。report_rc 卖方预测修正仍卡限速(1次/分钟,全量92小时)未碰。
- [ ] **#6 固定预算 A/B(旧 vs 修正后适应度,同 trial 数,比真样本外 residual Sharpe)** — 待 #2 完成后做;若仍无候选=确认当前 DSL 信息空间耗尽。

- [x] **产业基本面子系统 Phase 1 缺数据:补摄取资负表科目** — ✅ 已用内置 token(`data_lake/agent/tushare_config.json`)全量重取 balancesheet(5207 股/255097 行,新增 应收/应付/存货/应收票据/应付票据 5 列,旧列与行数零变化)。途中修了 `lake/sources/tushare.py::call` 限速重试 bug(40203「频率超限(200次/分钟)」未被「每分钟」分支匹配 → 误当致命崩;现匹配「频率超限」/「分钟」→ sleep 60s 重试,所有接口受益)。并在 `services/read/fundamentals.py` 修了"存量÷季度累计流量未年化 → 周转天数虚高 4 倍"(按报告期 ×12/月份年化)。汇川 300124 实测:BPI +0.20、CCC -12 天、DSO/DIO/DPO 135/118/265 天、定价权 0.73。注:2000 积分档 balancesheet 必填 ts_code,不支持 period 批量(vip),只能 by_stock。
- [ ] **产业基本面子系统 Phase 2** — 接入行业分类(`index_classify`/`stock_company` → `data_lake/meta/industry.parquet`);扩 `report_nlp_pipeline.py` 让 DeepSeek 把研报拆成本体因果链 `TransmissionNode`(SUPPLY/DEMAND/PRICE/MARGIN…)写 `research_signals/`;搬迁修复 `core/analysis/{analyst_framework,industry_ontology,bom_chain,data_quality}`(4 个坏原型,模块级 `Path` 未导入 + industry_ontology 违反 core→factory 铁律)到 `factory/{fundamental,ontology}/`;届时把 `check_layer_deps` 扩到覆盖 `core/analysis/`。行业景气预测 + 预期差机会榜接入 Agent/页面。
- [ ] **产业基本面子系统 Phase 3** — 按 `scripts/research/incubation_policy.py` 设计:本体预测分/预期差注册为 SHADOW 因子(不参与实盘权重)→ 影子 NAV → 9-Gate + DSR 审计 → 达标走 workflow 入册。

## 🧱 积分墙(per-interface 权限,非通用额度)
> 2000 积分通用额度(200/分钟 + 100k/天 per API)对 17 个核心维度绰绰有余;但下列是 **per-interface 更高积分要求**的特色接口,2000 档只给试用配额。
- [ ] **cyq_perf(筹码胜率)= 试用 5次/天** — **较值得升积分**:获利盘/平均成本/胜率与量价/size 低相关,真候选分散源。需按接口文档升档(通常 5000)。
- [ ] **limit_list_d(连板)= 试用 1次/小时** — 较不值得:连板情绪短线/噪声,与日频因子契合度低。
- `call()` 已对硬配额(X次/天/小时)fail-fast(不再白等 6×60s)。决策:**为 cyq_perf 升积分,还是两个都放弃**。
- [ ] **report_rc(卖方盈利预测修正)** — 强 event alpha,但 **1次/分钟**:全量 5524 股 ≈ 92 小时(4 天),2123 不实际。升积分提速,或只抓近 1-2 年。

## 🟡 待决策(等用户拍板)
- [ ] **size-low-vol 是否加 exclude_star** — SHADOW 策略持 4/25 科创板;tradability 口径上该排除,但 688 在它身上加收益、夏普持平(不像小盘降夏普)。建议:缓到促 LIVE 时一起处理。来源:CNE6/688 ripple 审计。

## 🟢 规划中 / 下一批
- [x] **数据日更全链路收敛(基础设施建设)** — 目标：每个 China 交易日收盘后 30 分钟内，全市场 5200+ 只股票价量数据完整入库，信号/模拟盘自动更新，web 自动反映。✅①价量源已换 tushare(5082只/日，2 API calls)；✅②时区修复(`expected_trade_date` -9h 偏移 + `update_prices` china_now)；✅③launchd plist 已补 Weekday=1(周一)；✅④监控告警落地(`scripts/ops/notify.py`：日更 failed/partial_ok 推 Obsidian[`30.output/2.[A]inbox/ai_data`，按月滚动]+桌面通知+可插拔 Bark/邮件，per-day 去重+失败恢复报平安；接在 `scheduled_daily_update.py::maybe_alert` finally；开关走 `settings.yaml::notify`，密钥走 gitignored `data_lake/agent/notify_config.json`)。
- [ ] **本体驱动重构(ontology-driven refactor)** — 据 `factor_research/docs/ontology_glossary.md` 命名冲突清单(veto/composer/signal/zscore/regime/timing/filter等),设计概念→命名 taxonomy 并逐步重命名/拆分模块。当前仅完成术语基线确认,本任务为后续工作。
- [ ] **研报-NLP 赛道(daily-round-3 2026-07-05 更正)** — **原描述"缺 PDF 源"已过期**:实测 `data_lake/research_pdf/` 每日确有新 PDF 落地(2026-06-11 至 07-04 逐日子目录均有文件)。真正卡点是下游 `scripts/research/report_nlp_pipeline.py` 解析/分类/抽取链的 4 类具体 bug(PDF 解析库时装时不装/研报类别 unknown/LLM 结构化抽取返回空/个股报告缺 stock_code),且无失败退避——同一批必败文件被逐小时重试,持续把 `PENDING_REVIEW`/`HUMAN_REVIEW` 灌入 `research_ledger`(抽样最近 100 条运行,68 条为该管线噪声),稀释研究台账信噪比。详见 `reports/research/research_director_review_round3_findings.md` §1.4。建议:① 修 4 类下游 bug(非取数问题,取数已通) ② 加失败退避(同错误连续 N 天不再产生新 HUMAN_REVIEW)。原「Antigravity 取数」建议已过期(取数已在正常工作)。谁:研究侧。
- [ ] **社融 cn_sf** — 接口名错(非 cn_sf),待查正确名补进宏观层。
- [ ] **第一批新维度接入** — `block_trade`(大宗折价)+`top_list/top_inst`(龙虎榜机构净买)+`repurchase`(回购)+`pledge_stat`(质押率)；写入 `data_lake/institutional/`；详见 `docs/data_dimensions.md` §3.1
- [ ] **ETF 价格源修复** — `cross_asset/etf/` 现用 eastmoney push2his(clash 代理拦截)；改为 tushare `fund_daily`；5 只 ETF 统一日更接入 launchd
- [ ] **北向个股持仓替代方案** — `hk_hold` 已下架(停更 2024-08-16)；评估 `hsgt_top10`(沪深港通十大)作为持仓结构代理，或直接放弃个股层保留 `moneyflow_hsgt` 汇总
- [ ] **daily_basic/moneyflow 接入 launchd** — 当前滞后价量 2 天；加入 `scheduled_daily_update.py` 自动触发

## ⚪ 已推迟 / 阻塞
- [ ] **income/balancesheet/cashflow 派生因子** — 三表已入库(anndate),但派生 Leverage/Quality 因子(真 B/P、债务/权益)尚未建因子。fina_indicator 已覆盖大部分比率,优先级低。

---

## 近期完成(可清理)
- [x] **⚡ 行动桌面三大独立看板激活与重组 (Ops Desk Dashboards)** — 激活并独立重构了`/candidates`、`/signals`和`/trade-plans`页面。支持展示 25 只因子推荐候选股与泡沫过滤说明、展示底层趋势偏离与 Regimes 择时指标、交易员签名授权委托锁定、CSV 批量委托单导出，并重构了总览页以保持台面极简化。
- [x] **P2 Python 工程化收敛(pyproject/测试入口/依赖锁)** — ⚙️ 新增 `factor_research/pyproject.toml` 统一 `pytest`/`ruff`/`mypy` 配置,加入 Apple Silicon venv 安装说明,并把 `requirements.txt` 运行时 direct deps 全部 pin 到当前版本。验证:`python3 -c "import tomllib; ..."`、`python3 tests/test_services_phase0.py`、`python3 tests/test_risk_phase3.py`。
- [x] **P2 风控页有效杠杆口径统一** — `services/read/risk.py` 改为优先读取最新 `signals/YYYY-MM-DD.json` 的 `band_exposure/leverage`,无信号时回退静态配置并在 note 中说明;新增单测覆盖 latest signal → risk current。验证:`python3 tests/test_risk_phase3.py`、`python3 tests/test_api_contracts.py`、`python3 tests/test_services_phase0.py`。
- [x] **测试 warning 噪音清理** — `engine/factor_analysis.py::calc_ic` 对常量截面先返回 NaN,避免 `ConstantInputWarning`;`tests/test_action_jobs_phase7.py` 改用 `httpx.ASGITransport` 直连 ASGI app,避免 Starlette `TestClient` 的 `httpx2` deprecation warning。验证:`python3 test_engine.py`、`python3 tests/test_action_jobs_phase7.py`、`bash scripts/test_all.sh`。
- [x] **P1 回测固定 `leverage` 参数口径修复** — `/backtest/run`、Web 回测页和生产默认展示移除可调固定杠杆;v3.1 明确为 PureTrend MA16 Band 动态敞口(0~1.5x),内部 `BacktestConfig.leverage=1.0` 只作为引擎基准。验证:`npm test`、`python3 tests/test_api_contracts.py`、`python3 tests/test_services_phase0.py`。
- [x] **P1 前后端契约 smoke 自动化** — 新增 `factor_research/tests/test_api_contracts.py`,校验 `/backtest/run` 不暴露 `leverage` 且 AutoResearch action endpoints 返回 `ActionJobView`;`scripts/test_all.sh` 已纳入该 smoke。验证:`python3 tests/test_api_contracts.py`。
- [x] **P0 前端写/重任务 Action Token 缺失修复** — `web/lib/api.ts` 增加 `/settings/action-token` 读取与 protected POST;Settings/AutoResearch 写接口统一带 `X-Action-Token`;新增 `web/lib/api.test.mjs` 覆盖 token 不进 body。验证:`npm test`、`npm run lint`、`npm run build`、`python3 tests/test_action_jobs_phase7.py`、`python3 tests/test_settings_phase6.py`。
- [x] **P0 AutoResearch 异步 Job 契约错配修复** — `web/lib/types.ts` 增加 `ActionJobView`;AutoResearch run/promote/search 前端改为提交 job 后轮询 `/experiments/jobs/{job_id}` 并按 `job.result` 渲染。验证:`npm test`、`npm run build`、`python3 tests/test_action_jobs_phase7.py`。
- [x] **价量数据日增量源切换 Tencent→Tushare** — `lake/sources/tushare_price.py` + `update_lake.py::update_prices()` 完成；token 落 `data_lake/agent/tushare_config.json` + plist env；实测 5082 只/日，launchd 周一补齐，06-15 全量 5188 只验证通过
- [x] tushare 17 维度入库(daily_basic/财务/资金/市场/事件/股东/指数,71M 行)
- [x] 科创板 688 volume 修复 + 小盘 exclude_star + v2.0 重登记
- [x] CNE6 风格中性化审计(真 Barra Size)
- [x] 便宜模型干苦力(DeepSeek)接通 + 实测 1/5 漏斗
- [x] 多 agent 系统级分工(MULTI_AGENT.md)+ 文档矩阵正交化(ADR-010)
- [x] 宏观时序层(cn_cpi/ppi/m + shibor + moneyflow_hsgt,防未来 lag,`load_macro`)
- [x] large-cap 容量 688 连带重算:虚高 768亿→真 5.62亿(-99%,137x),漏网补做
