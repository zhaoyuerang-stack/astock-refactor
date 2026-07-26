# literature-scan —— 文献扫描剧本(枯竭触发的外探之二,ADR-034/WS-E)

> 触发:决策收件箱「研究枯竭」事项(`services.read.research_exhaustion` 判 exhausted)
> 经**人批准**后执行;或人主动发起。系统**绝不**自动启动本剧本(LOOP §6:外探是
> 生成端扩张,启动须人批准)。与之并列的外探之一 = 数据 scouting
> (probe-signal-source + `knowledge/data_source_backlog.json`)。

## Inputs

- 枯竭事项的证据(哪些方向已搜尽:见 `knowledge/direction_registry.json` 活跃条目
  + `metasearch/frontier.json` 空白区)。
- 可选:限定主题(如"微观结构/事件驱动/中国 A 股异象")。

## Allowed Tools

- WebSearch / WebFetch:SSRN、arXiv q-fin、学术索引、业界公开金工研究。
- `knowledge/directions.py`(读活跃方向教训,**扫描前先读**,别提已证伪方向)。
- `apps/factory_cli.py queue`(Hypothesis 草案入队,canonical 通道)。

## 流程

1. **先读教训再扫描**:`knowledge/direction_registry.json` 的 falsified/weak 条目 +
   `data_source_backlog` 已知候选源——扫描目标是**登记簿之外**的新机制,不是重发现死路。
2. **检索**:优先近 3 年、有中国 A 股样本或可迁移机制的研究;每条记录:标题/作者/年份/
   链接/核心机制一句话/所需数据/与现有池的预期正交性。
3. **过滤(便宜,advisory)**:丢弃①机制依赖本仓不可得数据(高频/逐笔/另类付费源,
   除非值得进 data_source_backlog);②本质是 size/流动性/动量换皮(对照方向登记簿
   NOTE 条目);③无法用日频 PIT 数据复现的。
4. **产出 Hypothesis 草案**(每条带出处,thesis.citation = 论文引用):走 canonical
   通道 `factory/ontology.Hypothesis` → `apps/factory_cli.py queue`,或所需数据未落盘时
   转 `knowledge/data_source_backlog.json` 新条目(带文献指针,priority 按预期正交性)。
5. **收尾回写**:扫描结论(含"本主题无可用新机制"的阴性结论)记
   `reports/research/literature_scan_<date>.md`;值得算力倾斜的方向加
   `direction_registry.json` BOOST/NOTE 条目(evidence = 文献链接 + 扫描报告路径)。

## Forbidden(R-LLM-001 / R-WF-001)

- **不判有效**:文献声称的收益/显著性一律视为"待证伪假设",不得作为任何有效性证据。
- **不自动入册/不写台账**:草案只进候选队列,验真走完整 L0-L3 → 9-Gate → holdout。
- **不复制实现**:只提机制假设;因子实现必须走本仓 canonical 层(DSL 白名单/factors/),
  且 PIT 对齐(R-DATA-003)。
- **不绕过 trial 账本**:草案进入搜索即被 chokepoint 记账,不得为"文献背书"少记 n_trials。

## Success Criteria

- 每条草案有出处、有机制一句话、有所需数据清单、有预期正交性论据。
- 阴性结论(无可用新机制)同样落报告——"扫过没找到"是信息,防重复扫描。
- 扫描不产生任何台账/部署/口径变更。
