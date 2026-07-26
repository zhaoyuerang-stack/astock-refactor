# daily-round-3 研究总监审视:算力再分配建议

> 方向③(研究总监审视)。本文档**不判断任何 alpha 有效性**(R-LLM-001),只机械读取既有台账/审计产物/管线日志,给出算力再分配建议。所有"是否有效"的判断仍归 9-Gate/DSR/回测(确定性代码)。
> 证据来源两处:① 本轮工作分支 `claude/daily-round-3`(基于 `origin/main@acc66b86` 新建,只读 `STATUS.md`/`TASKS.md`/`strong_ai_rounds.jsonl`);② 主仓物理工作目录 `/Users/kiki/astcok`(当前 checkout 在 `codex/xiaochengxu@1bd6bc3d`,是本机唯一挂载真实 `data_lake` 且有其他 agent 实时活动的工作树)——本轮只**读**该目录未做任何写入,详见 §5 方法论说明。

## 0. 一句话结论

**全池当前 0 个"在册"策略**(`decay_status.json::no_registered=true` 机械确认;`strategy_versions.json` 30 个 family 全部落在 候选/参考/退役/已证伪/`REJECTED_BY_ADVERSARIAL_DECAY`,此前仅剩的两个 diversifier `hq-momentum-hedged`/`large-cap-growth-hedged` 已完成 TASKS.md 待办的退役复核)。同一时刻,另一个并行 agent(`codex/xiaochengxu` 分支)正**实时**在跑组合再构成(`composite-portfolio`)与跨资产腿搜索(`cross_asset_leg_search`),覆盖了 metasearch 06-23 指出的 3 个空白区中的 1 个(跨资产腿)。本轮建议:**不要重复投入组合/跨资产方向,把下一轮方向①/②算力指向唯一仍空闲且已解除阻塞的方向——基本面质量因子族体检**;同时修正一条误导性的 TASKS.md 描述、修一处正在污染研究台账信噪比的死管线认知偏差。

## 1. 证据清单

### 1.1 registry 现状:在册池清零

- `strategy_versions.json`(codex/xiaochengxu 工作树,2026-07-05 读取)含 30 个 family,**version-level status 分布**:候选 17、参考 20(部分 family 多版本混合)、退役 8、已证伪 1、`REJECTED_BY_ADVERSARIAL_DECAY` 2,**"在册" 0 个**。
- `reports/decay_status.json`(generated_at 2026-07-05T17:44:34+08:00):`"no_registered": true, "strategies": []`,`deployment_identity_error: "部署 status='paused' 非 active"`——与 registry 现状一致,非报告口径故障。
- 对照 TASKS.md 🔴 区第 22 条("`hq-momentum-hedged`、`large-cap-growth-hedged` 走退役复核")——**已执行完毕**:两 family 分别 2/2、3/4 版本转「退役」,该 TASKS 条目可标记完成。

### 1.2 并行 agent 今日实时活动:composite-portfolio + cross_asset_leg_search

- `strategy_versions.json::composite-portfolio` 今天(2026-07-05)新增两个版本 `v1.1`/`v1.2-no-mom`,均被自动化对抗审查判定 `REJECTED_BY_ADVERSARIAL_DECAY`(DSR p=0.256/0.233,衰减超限)。此前 06-30 的 `v1.0`/`size_mix_v1.0` 两版 DSR p=0.074/0.154,均未过 <0.05 门,仍是候选。
- `reports/research/cross_asset_leg_search.json`(run_date 2026-07-05)同日产出:纳指 ETF(513100,MA240)`passes_threshold=true, shadow_recommend=true`(corr_to_book=0.071、d_sharpe+0.299、d_calmar+0.172);国债 ETF(511010)两个窗口 corr_to_book 0.93~1.0,`passes_threshold=false`(与组合本体高度重合,非独立腿)。
- 这两项对应 `git status` 里 main 仓当前被修改的 `portfolio/composite_weight_runners.py`/`workflow/composite_spec.py`/`workflow/promote_composite.py`——**与本轮观测到的实时活动吻合,非巧合**。
- **结论**:metasearch 06-23 标出的 3 个信息空白区(vol_breakout / 基本面族 / 跨资产腿)中,vol_breakout 已被 round-1 证伪关闭,跨资产腿正被另一个 agent 实时挖掘,**只剩基本面族尚无人认领**。

### 1.3 基本面质量因子族探针:TASKS 阻塞条件已过期

- TASKS.md 🔴 区:"资产负债表运营质量因子族步骤 3 体检…**在有数据湖的机器上**"(阻塞原因 = 缺数据湖)。
- 实测:`data_lake/financials/balancesheet_all.parquet` 在本机(`/Users/kiki/astcok`)**真实存在且非空**(2000 积分档全量摄取,STATUS.md 2026-06-22 已记录)。`factors/fundamental_quality.py`(bargaining_power/receivable_intensity_chg/inventory_intensity_chg,anndate PIT 对齐,单测 6/6)代码早已就绪,只是从未跑过 probe-signal-source 步骤 3(正交性 + IS/OOS 体检)。
- **该阻塞条件不成立**:这是本轮找到的、风险最低、就绪度最高的下一步执行项——建议**下一次方向①/②直接执行此 probe**,而非重新设计新因子族。

### 1.4 研究台账信噪比:一条"已知问题"被误诊,持续污染 pending_review 队列

- `reports/research_ledger/index.json`(generated_at 2026-07-04):累计 427 次运行,`counts_by_decision`:`pending_review=244`、`shadow=173`、`promote=8`、`refuted=2`。抽样最近 100 条:`report_nlp_pipeline.py` 占 **68 条**,`run_nine_gates_all.py` 占 32 条(常规审计,分布合理)。
- 深挖 `report_nlp_pipeline.py`:`reports/research/report_nlp_failures.jsonl` 显示同一批 3 个 PDF 文件被**逐小时重试**(01:31/02:30/02:31/03:30/03:31/04:30/04:31…),连续多日 100% 失败,轮流报 4 类错误:① PDF 解析库时装时不装(`opendataloader-pdf/pdfplumber/pypdf` 未检测到)② `未知的研报类别: unknown` ③ `LLM 提取结构化信号失败或返回空` ④ `个股报告缺失 stock_code`。
- **TASKS.md 对该问题的描述已过期/误导**:写的是"研报-NLP 赛道 — 缺 PDF 源",但 `data_lake/research_pdf/` 实测**每日确有新 PDF 落地**(2026-06-11 至 07-04 逐日子目录,07-03 目录下有真实文件 `AP202607031826699869.pdf`)。真正卡点不是取数,是**下游解析/分类/抽取链的 4 类具体 bug**,且无失败退避机制,每小时重试同一批必败文件,把 `PENDING_REVIEW`/`HUMAN_REVIEW` 每天灌进研究台账。
- **影响**:如果 244 条 pending_review 里有类似比例是这条死管线的重复噪声(抽样显示占最近样本 68%),意味着"研究枯竭/待审"信号被严重稀释,人工复核容易对真正待决候选脱敏。这是一个"研究总监"级别的流程健康问题,不是新 alpha 发现,但直接决定后续算力/注意力该往哪分配。

### 1.5 代码库分支碎片化(结构性观察,非本轮可修)

- 本轮工作分支基于 `origin/main@acc66b86` 新建(round-1/round-2 分支的基点 `1fc555c5` 已落后 main 54 个提交,故本轮换了新基点)。
- 但主仓物理工作目录 `/Users/kiki/astcok` 当前 checkout 在 `codex/xiaochengxu@1bd6bc3d`,与 `main` 是两条独立演化的线:`git merge-base --is-ancestor 4dede961(main 上的 regime_audit read layer, ADR-033/WS6) HEAD` 在 `codex/xiaochengxu` 上返回**否**——即 `main` 上已落地的 `services/read/regime_audit.py` 在 `codex/xiaochengxu` 的实际工作树里**不存在**。反过来,`codex/xiaochengxu` 正产出的 composite-portfolio/cross_asset_leg_search 实验也尚未见于 `main`。
- **含义**:daily-research-round 系列(本轮及以后)按剧本基于"主仓 HEAD"起新分支,但"主仓 HEAD"存在歧义——是抽象的 `main` 分支尖端,还是主仓工作目录当前实际 checkout 的分支(现为 `codex/xiaochengxu`)?两者内容不同步。本轮选择前者(与 round-1/round-2 一致口径),但这意味着本轮读到的 `STATUS.md`/`TASKS.md`/registry 快照,与 `/Users/kiki/astcok` 里正在发生的实时研究活动(§1.2)存在**代码基线落差**。
- 这不是本轮能修的事(涉及多分支合并决策,超出「研究总监」审视权限),但建议 owner 知悉:多个并行 agent 可能正在互相看不到对方已验证成果的两条(或更多)分支上重复探索。

## 2. 算力再分配建议(均为搜索空间增删建议,非有效性判断)

| 方向 | 建议 | 理由 |
| --- | --- | --- |
| 跨资产腿(metasearch 空白区②) | **不再投入** daily-research-round 算力 | 已被并行 agent 实时覆盖(§1.2),重复投入 = 白算 n_trials |
| vol_breakout(metasearch 空白区①) | 维持关闭 | round-1 已证伪(既有反转簇镜像变体),无新信息出现需要重开 |
| 基本面质量因子族(metasearch 空白区③) | **下一轮方向①/②优先执行** | 代码+数据双就绪,唯一未认领的 metasearch 空白区(§1.3) |
| metasearch 刷新(factor_mi_audit/information_map) | 建议近期重跑一次 | 上次 06-23 距今 12 天,3 个空白区中 2 个已被处理(关闭/占用),继续沿用旧列表意义有限;重跑才能找到下一批真空白区 |
| 研报-NLP 管线 | **不建议本轮修复代码**(超出方向③范围),但建议:① TASKS.md 描述改为"下游解析链 4 类具体 bug"(而非"缺 PDF 源")② 给该 daily 任务加失败退避(同错误连续 N 天不再产生新 HUMAN_REVIEW 条目,只在首次/状态变化时提醒) | 现状持续稀释 research_ledger 信噪比,且现有 TASKS 描述会误导下一个接手者去查错方向 |
| 分支碎片化 | 建议 owner 决定是否需要一次 `main` ↔ `codex/xiaochengxu` 收敛窗口 | 结构性风险,非本轮/非 agent 权限范围内可解决 |

## 3. 需要人裁决的事项(needs_human)

1. **研报-NLP 管线的 4 类下游 bug 是否安排人工/下一轮修复**——已定位具体错误但未修(方向③范围外)。
2. **`main` 与 `codex/xiaochengxu` 是否需要一次分支收敛**——两条线都在产生独立成果,互相不可见。
3. **是否近期重跑 metasearch(factor_mi_audit/information_map)**——3 个已知空白区 2 个即将处理完,需要新一轮空白区列表才能持续喂给方向①。

无阻塞性/高风险裁决项(不涉及口径污染、不涉及入册/部署)。

## 4. 未做的事(明确边界)

本轮**未**:碰 `strategy_registry` 写入口、`workflow.promote`、部署清单、成本模型数值、holdout boundary、样本/shift/T+1 口径;**未**在 `/Users/kiki/astcok` 写任何文件(仅读);**未**对 composite-portfolio/cross_asset_leg_search 的候选做有效性判断(那是确定性代码/另一个 agent 的进行时工作,不是本轮职责)。

## 5. 方法论说明

本轮方向③要求"通读失败台账/experiment_log/MetaSearch 产物/regime 审计"。这些产物(`decay_status.json`、`research_ledger/`、`metasearch/`、`knowledge/findings.json`)整体命中仓库 `.gitignore`(`factor_research/reports/` 全目录忽略),因此**只存在于生成它们的物理工作目录,不进 git 历史、不随 worktree 复制**。本轮固定 worktree(`daily-research-round`)是全新 checkout,天然不含这些文件。故本轮对这些"生成型"证据一律采用**只读**方式引用主仓物理工作目录 `/Users/kiki/astcok`(与 CLAUDE.md 关于 data_lake 只读符号链接同一处理原则的延伸——只是这次未建符号链接,直接读绝对路径,因为不需要重复写入/长期持有,只需一次性审阅),本轮分支/提交完全不落地那个目录的任何文件。这一现象本身也是 §1.5 结构性观察的一部分:**跨 worktree 的"证据可发现性"依赖于知道去哪个物理路径找,而不是 git 历史**,值得未来考虑是否要把关键台账快照(如 `decay_status.json`、`research_ledger/index.json`)定期打包成非 gitignore 的归档快照。
