# quant-strategy-scan —— 量化策略专项扫描剧本(daily round 方向④,ADR-039)

> 触发:daily-research-round 方向④ 按 ADR-039 **常备授权**执行(轮换命中,或 ①/② 无可做
> 目标时 fallback);或人主动发起。与 literature-scan(机制文献扫描,枯竭触发 + 人批,不变)
> 并列:本剧本扫的是**业界实际在跑/在发的完整量化策略**(股票池 + 信号 + 构造 + 风控),
> 落点 = 该策略在本仓约束下是否可行、能拆出什么待证伪假设。

## Inputs(扫描前必读,顺序执行)

- `knowledge/direction_registry.json` falsified/weak 条目——别把已证伪路换皮捡回来。
- 仓根 `LESSONS.md` 策略级教训(二值择时 whipsaw / 纯横截面动量 / 弱正交源加持仓 等)。
- `metasearch/frontier.json` 空白区 + `knowledge/data_source_backlog.json` 已知候选源。
- 可选:限定主题(如"事件驱动 / 小盘容量 / 风格轮动")。

## Allowed Tools

- WebSearch / WebFetch:检索不依赖 Bash 联网(绕开 sandbox/代理坑);登录墙后内容不追,
  公开可见部分为准。
- `services.read.strategy_idea.check_strategy_idea`(确定性策略想法预检,ADR-037;
  五判存活的每条候选必过)。
- `apps/factory_cli.py queue`(信号层 Hypothesis 草案入队,canonical 通道)。

## 检索源(A股量化策略专项)

| 类别 | 来源 | 取什么 |
| --- | --- | --- |
| 卖方金工公开部分 | 华泰(AI/多因子系列)、海通(选股因子系列)、国盛、开源证券、方正星火、招商定量 | 策略机制描述、因子构造思路 |
| 策略百科/社区 | Quantpedia、聚宽、BigQuant、果仁网、掘金 | 策略结构、宇宙/调仓设定 |
| 开源实现 | qlib benchmarks(Alpha158/360)、awesome-quant、研报复现 repo | 机制与数据需求,**不抄代码** |
| 英文可迁移 | SSRN "China A-share" 关键词、QuantConnect 社区 | 有 A 股样本或机制可迁移的 |

## 可行性五判(每条候选机械过一遍;不过即丢,记一句话理由)

1. **long-only 可投**——A股做空不可行;多空策略只取多头腿并标注"多头腿需重验"
   (本仓教训:多空溢价在 long-only 侧常消失,正交源族全灭于此)。
2. **T+1 + 涨跌停可执行**——日内/高频翻仓类直接丢。
3. **日频 PIT 数据可得**——对照 `lake/schema.py::TUSHARE_DATASETS` 数据集契约;
   不可得但值得的 → `data_source_backlog` 新条目(带文献/策略指针)。
4. **审慎成本后仍有肉**——超高换手先天出局(G6 成本/换手是在册池主要死因之一)。
5. **非已证伪路换皮**——对照 direction_registry + LESSONS;二值择时 overlay、
   纯横截面动量、弱正交源加持仓的换皮直接丢。

## 流程

1. 先读 Inputs 全部教训,再检索;每条候选记录:来源/链接/策略一句话/宇宙/调仓/所需数据。
2. 五判过滤;存活者逐条过 `check_strategy_idea` 预检(确定性,输出 precheck 级
   Evidence Envelope,`can_claim_valid` 恒 false)。
3. **三路分流**(策略 ≠ 因子,不全塞 Hypothesis 队列):
   - 信号层可拆出因子假设的 → Hypothesis 草案(thesis.citation = 出处)进
     `factory_cli queue`,供后续方向①/②轮取证;
   - 构造层想法(权重/风控/调仓结构)→ `direction_registry` NOTE 条目
     (evidence = 扫描报告路径);
   - 数据需求 → `data_source_backlog` 新条目(priority 按预期正交性)。
4. 收尾:报告落 `reports/research/strategy_scan_<date>.md`,含五判淘汰清单与理由;
   **阴性结论(无可用新策略)同样落报告**——"扫过没找到"是信息,防重复扫描。

## Forbidden(R-LLM-001 / R-WF-001 / R-DATA-003)

- **业界回测数字一律不可信**(幸存者偏差 + 成本口径全对不上):只取机制当待证伪假设,
  不得作为任何有效性证据、不得上屏为"业界已验证"。
- **不复制实现**:只提机制假设;因子实现必走本仓 canonical 层 + PIT 对齐。
- **不自动入册/不写台账**:草案只进候选队列,验真走完整 L0-L3 → 9-Gate → holdout。
- **不绕过 trial 账本**:草案进入搜索即被 chokepoint 记账,不得为"业界背书"少记 n_trials。

## Success Criteria

- 每条存活策略有出处、有一句话机制、有五判结论、有预检输出、有分流去向。
- 淘汰项有一句话理由(防下轮重复捡)。
- 扫描不产生任何台账/部署/口径变更。
