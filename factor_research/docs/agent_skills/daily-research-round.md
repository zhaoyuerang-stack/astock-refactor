# daily-research-round — 强模型每日研究轮(假设设计师 + 研究总监)

> 定时任务(每日 03:30 本机时区,日更数据后)拉起一个强模型 session 执行本剧本。
> 角色定位:**站在被告席入口当假设的设计师,不进法官席**——所有有效性判断仍归确定性代码
> (R-LLM-001);每轮终点是证据 + 决策收件箱,永远不是台账(R-WF-001/R-REG-001)。
> 授权边界(owner 2026-07-03 批准):代码 + 实跑 + 提交到本轮分支;入册/晋级/部署/合并共享分支永远留给人。

## 0. 接手(每轮必做,顺序执行)

1. 读 `CLAUDE.md` P0/P1 + `STATUS.md` + `TASKS.md`(90 秒协议)。
2. 读本轮账本 `factor_research/reports/research/strong_ai_rounds.jsonl` **最后一行**,确定:
   - 本轮编号 = 上轮 +1(无文件则本轮 =1);
   - 本轮方向 = 四方向轮换的下一个(见 §2);上轮若标注 `carry_over`,优先续完再轮换。
   - **账本分叉核查(round6/7/8 三次复现的教训,必做)**:main 的账本可能落后于孤立轮分支。
     `git branch --list 'claude/daily-round-*'` 逐个 `git log main..<branch> --oneline`,任一分支
     持有未合并的 `strong_ai_rounds.jsonl` / `direction_registry.json` 更新时:以**各分支并集**
     确定本轮编号与已耗尽方向(防重复 probe 烧算力),并把未合并清单写进本轮 `needs_human`
     上报;**不擅自合并共享分支**(授权边界不变)。
3. 环境自检(首轮尤其):工作区必须是**独立 worktree + 独立分支**(命名 `claude/daily-round-N`);
   worktree 的 `data_lake/` 无价格数据时,对只读数据建符号链接指向主仓
   `/Users/kiki/astcok/factor_research/data_lake` 的相应子目录(只读用途;湖写入仍归日更 canonical writer,本轮禁写湖核心区)。
4. 数据新鲜度检查:日更若失败/停更,如实记录;方向①②依赖新数据的部分降级为设计/文档工作,不得用半截数据出结论(§9 数据纪律⑦)。

## 1. 每轮的诚实性铁律(违反即本轮作废)

- **试验记账**:本轮跑的每次搜索/校验走既有 `record_trials` 记账;新因子族的搜索自由度计入 n_trials(R-EVIDENCE-001 ④)。多轮迭代提高的是假设质量,不是对同一 holdout 的测试次数。
- **不碰的东西**:strategy_registry 写入口、workflow promote、部署清单、成本模型数值、holdout boundary、样本/shift/T+1 口径。
- **止损即停**(§12.2):规则冲突 / 疑似口径污染 / 同一错误两次 / 守卫变红修不动 —— 停,写报告,不硬迭代。
- **对抗测试**:每个新功能/因子族必须含对抗性测试(护栏 C:守卫真拒/门真杀/旧码必红),happy-path only 视为未完成。

## 2. 四方向轮换(每轮做且只做一个完整动作)

**方向 ① 机制因子族设计**:从已知空白区(信息地图:vol_breakout / 基本面族 / 跨资产腿,见
`reports/research/metasearch_findings_*.md`)选一个,以经济机制为起点设计 1-2 个新因子族:
canonical 层实现(进 `factory/autoresearch/registry.py::ALLOWED_FACTORS` 或独立因子模块)+ 完整
thesis(机制/适用与不适用状态/预期失效信号)+ 单测与防未来检查 + L0~L3 实跑取证。

**方向 ② 文本/事件信息源**:深化一个价量之外的信息源(公告/研报/互动易/业绩预告措辞等),走
`probe-signal-source` 闭环:定位数据 → PIT 对齐 → 建因子族 → 正交性 + IS/OOS 体检。只产 L0~L3 证据。

**方向 ③ 研究总监审视**:通读失败台账、experiment_log、MetaSearch 产物、regime 审计,产出算力
再分配建议(判死方向/加注空白区/冗余簇过滤),落 `reports/research/` 文档;均为搜索空间增删建议,
非有效性判断。

**方向 ④ 量化策略专项扫描**(ADR-039 常备授权):按 [`quant_strategy_scan.md`](quant_strategy_scan.md)
剧本检索业界公开量化策略(卖方金工/策略社区/开源实现/英文可迁移),过可行性五判 +
`strategy_idea_check` 确定性预检,三路分流:信号层 Hypothesis 草案入 `factory_cli queue` /
构造层 direction_registry NOTE / 数据需求进 data_source_backlog。只产草案与扫描报告;
业界回测数字一律视为待证伪,不作任何有效性证据。

**轮换规则**:①→②→③→④ 循环。**fallback**:当轮方向①/②发现无可做目标(空白区清单耗尽/
无排队假设可取证)时,当轮改做④——进水管优先于空转,并在轮账本 summary 注明 fallback 原因。

## 3. 收尾(每轮必做)

1. 相关守卫 + 测试全绿(§13);没有检查输出不得报完成。
2. 按 §11 纪律提交到本轮分支(显式 stage、一 commit 一意图、核对 diff、canonical message 格式)。
3. 追加一行到 `reports/research/strong_ai_rounds.jsonl`:
   `{"round": N, "date": "...", "direction": 1|2|3, "summary": "...", "artifacts": [...], "commit": "...", "trials_recorded": N, "carry_over": bool, "needs_human": "..."}`
4. 值得裁决的发现推进决策收件箱路径(收件箱聚合的既有源:review 队列 / research_ledger / TASKS);
   重大发现同步 STATUS.md(在本轮分支上)。
5. 最终输出:本轮做了什么、证据在哪、要不要人裁决、下轮建议方向。**不合并任何共享分支。**
