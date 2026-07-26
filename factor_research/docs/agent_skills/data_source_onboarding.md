# data-source-onboarding —— 新数据源接入固定剧本(工程接入,probe 之前)

> 触发:`knowledge/data_source_backlog.json` 条目**经人批准**接入 / `TASKS.md` 数据接入项 /
> owner 直接指令。系统绝不自动启动接入(外探启动须人批准,LOOP §6)。
>
> 定位:本剧本只管**「外部源 → data_lake canonical」的工程接入**。数据接进来了 ≠ 信息有价值——
> 因子价值体检归 probe-signal-source 剧本(接入完成后交棒)。本剧本不建因子、不判 alpha、不入册。
>
> 为什么存在:接入纪律散在 `data_infrastructure.md` / `data_dimensions.md` / `LESSONS.md` /
> `CLAUDE.md §9` 四处,agent 每次临场拼流程 = 每次重新踩坑(封禁/单位错/幸存者偏差/未来函数)。
> 本文把它们串成**固定流水线:每步有明确产出与停止条件,前一步不过不得进下一步(fail-closed)**。
> 直接服务的待办:TASKS「第一批新维度接入」及 backlog 全部待接条目。

## Inputs

- backlog 条目 id(含 thesis:这个源喂哪个研究决策)或等价的人工指令。
- 目标维度清单(哪些字段、什么频率、喂什么因子族)。

## Allowed Tools

- 探针读取:tushare MCP / 一次性脚本(产物只落 scratch 或 `reports/research/`,禁落湖核心区)。
- 声明注册:`scripts/data/update_tushare.py::INTERFACES`(tushare 源)、`lake/sources/*.py`
  继承 `lake/base.py::Fetcher`(非 tushare 源)、`lake/schema.py`(字段命名)。
- 回填/增量:`scripts/data/` 下 canonical 入口(受 `check_lake_writers.py` 守卫)。
- 校验:`lake/validator.py`、`validate_final.py`、自写抽查脚本(读-only)。
- 加载接入:`lake/load_lake.py`(`TUSHARE_DATASETS` 注册或等价路由)。
- 登记:`docs/data_dimensions.md`、manifest、backlog 销账。

## 固定流水线(S0→S7,逐步 fail-closed)

### S0 立项裁决 —— 接不接(不写代码)

判五件事,任一不过 = 停下报告,不进 S1:

1. **信息假设**:backlog 条目 thesis 必填——这个源预期喂哪个决策/因子族?
   对照 `knowledge/direction_registry.json`(别为已证伪方向接数据)+
   `docs/data_dimensions.md` 已入库维度 + `metasearch` information_map(别接同信息换皮源,
   同一信息算两遍只白涨 n_trials)。
2. **PIT 可得性预判**:数据有没有公告/发布时间戳?vendor 会不会回改历史(restatement)?
   没有时间戳字段的源,先想清对齐方案;判不了 PIT = 拒接或降级为参考数据(R-DATA-003)。
3. **覆盖预判**:全市场含退市股吗?历史深度 ≥ 回测窗口吗?只含存活股/头部股的源
   不得用于正式研究(R-DATA-002,幸存者偏差先例见 auto-memory survivorship-bias)。
4. **配额与封禁账**:全量回填 ≈ 多少次请求?对照积分墙/限速(先例:`report_rc` 全量 92 小时
   不实际、`cyq_perf` 5 次/天)。账算不平 = 换批量接口或升配额,不硬抓。
5. **停发风险预案**:vendor 停发/断供时下游怎么退化?(先例:北向 2024-08 停发)。
   写一句退化路径进接入记录,别让未来的失效变成静默半截数据。

产出:接/不接结论 + 上述五判的证据,一段话记入 backlog 条目或接入 PR 描述。

### S1 探针 —— 小样本核对(零落盘湖核心区)

拉极小样本(几只股 × 几段日期,必含:一只退市股、一段停牌、一个一字板日、一只 688/300),核对:

- **字段语义与单位**:元还是万元?股还是手?(先例:688 volume 单位错配 → amount 虚高 100 倍,
  容量虚估 137 倍)。打印**极值 top3** 做量纲 sanity——看 top 不看中位,是抓量纲错的诊断纪律。
- **主键唯一性**:声明的 keys 在样本内真唯一吗?重复行代表什么(更正记录?多口径?)。
- **时间戳含义**:交易日?自然日?公告日?盘中还是盘后可得?这直接决定 S2 的口径三选一。
- **退市股/停牌/涨跌停表现**:退市股有数据吗?停牌日是缺行还是填充?
- **实测限速**:单请求耗时、连续请求多少次开始异常。

产出:探针纪要(字段表 + 单位 + 主键 + 时间戳结论 + 限速实测),落 `reports/research/`。

### S2 契约声明 —— 接入 = 注册,不 = 新写一套脚本

- tushare 源:往 `INTERFACES` 加一条声明(mode / date_param / keys / store / fields),
  通用 `backfill()` 负责增量+resumable+flush,**不新写下载脚本**。
- 非 tushare 源:继承 `lake/base.py::Fetcher` + `RateLimiter`,住 `lake/sources/`,
  同样声明式配置,不自造限流/重试/断点逻辑。
- 字段重命名集中进 `lake/schema.py`;**原始存储层尽量保 vendor 原值**,单位/口径修正做在
  **读层**(可重放、自动覆盖增量,688 修正即此模式),不散落在下游消费者。
- **时间轴口径三选一(本剧本最重要的一条,强制声明)**:
  1. `by_date` 盘后可知(价格衍生量,T 日收盘已知)→ 不 shift;
  2. T 日盘后发布、次日才可用 → shift(1);
  3. 财务/公告/事件 → `anndate` 公告日 ffill。
  口径必须注册进 `lake/load_lake.py` 路由(`TUSHARE_DATASETS` 或等价),让防未来对齐
  由加载层**机械执行**,不靠每个消费者自觉。**拿不准 = 选最晚可见的口径**(宁可损失信息
  不可泄露未来,R-DATA-003)。
- token/key 走环境变量或 gitignore 配置,绝不入库。

产出:INTERFACES/sources 声明 + schema 条目 + 加载层口径路由,一个 commit 一个意图。

### S3 回填 —— 全市场、可断点、守限速

- 覆盖**全市场含退市股**、全历史;resumable,重跑不产生静默口径变化(R-ARCH-004)。
- 限速纪律(CLAUDE.md §9 铁律):单接口顺序请求、跨接口才可并发;东财类换批量/聚合接口,
  **绝不加多线程**;akshare 用 daemon 线程 + join(timeout)。
- 写入只经 canonical writer(代码必须住 `lake/` 或 `scripts/data/`,
  `check_lake_writers.py` 机械强制),并更新 manifest(vintage:rows/末日/股数/落库时间)。

### S4 质量门 —— 不过则不进加载层(fail-closed)

- **覆盖对账**:行数 × 股数 × 日期范围 vs universe 预期;抽查退市股退市前有数据。
- **PIT 抽查**(anndate 类必做):变化点必须落在披露日之后(检验法先例:茅台年报 4 月披露,
  ROE 跳变点应在 4 月,不在报告期末 12-31)。
- **量纲对账**:极值 top3 合理;抽 3-5 只股对照第二源(另一接口/交易所页面)人工 reconcile。
- **A股正常现象不误判**:停牌=孤立缺失、新股首日、一字板不是脏数据(误判会把干净率
  从 97% 假跌到 68%)。
- 任何一项不过:显式记录失败原因,**不接加载层、不挂增量、不静默用半截数据**。
  禁止为了"看起来干净"静默删行/改值——修数据须走 `scripts/repair/` 且留痕。

产出:质量抽查纪要(附对账数字)落 `reports/research/` 或 quality report。

### S5 加载层接入 —— 消费只经统一入口

- 消费方只经 `lake/load_lake.py` 统一接口;禁研究脚本直读该源 parquet(防口径分叉)。
- 若该源参与"最新交易日完整性"判断:完整性截断必须用**下游真正消费的字段**
  (先例:表面有 close 不代表 amount 能算,factor 全 NaN 事故)。

### S6 增量与登记 —— 让下一个 agent 找得到

- 需要日更的:挂 `incremental_update` / `scheduled_daily_update`;更新失败**显式记录**
  (fail-closed),新鲜度预期让 data-health 剧本可见。
- 文档登记(缺一不算完成):`docs/data_dimensions.md` 加行(已入库维度单一真相源)、
  manifest 有 vintage、backlog 条目销账/更新状态。

### S7 交棒 —— 数据健康 ≠ 信息有价值

接入完成后,信息价值体检走 **probe-signal-source** 剧本(正交性/IS-OOS 体检,阴性结论
也回写 direction_registry)。本剧本到此为止:不建因子进白名单、不宣布任何有效性。

## 雷区速查(细节见 LESSONS.md「数据源/联网」「数据正确性」章)

| 雷 | 一句话规避 |
|---|---|
| 东财逐只 40-50 只封禁 | 换批量/聚合接口;绝不多线程 |
| akshare 请求 hang 死全流程 | daemon 线程 + join(timeout),其他超时手段全无效 |
| clash 代理拦东财 push2 | 新浪源可用;或代理规则加 eastmoney DIRECT;联网需 dangerouslyDisableSandbox |
| 复权价算估值/股数 | PE/PB/成交股数/容量一律不复权价;复权价只用于收益率序列 |
| volume 手 vs 股 | 688 事故:量纲错 100 倍;新源必做极值 top3 sanity |
| 完整性看错字段 | 判"当日数据全不全"用下游消费字段,不用表面 close |
| 只回填存活股 | 幸存者偏差,回测系统性高估;退市股必含 |
| 停牌当脏数据 | A股正常现象四件套(停牌/新股/一字板/涨跌停)不误判 |
| vendor 回改/停发 | S0 记录 restatement 行为与停发预案 |
| token 入库 | 环境变量/gitignore 配置,`.mcp.json` 亦不提交 |

## Forbidden

- 绕过 canonical writer 直写湖核心区(R-ARCH-004,`check_lake_writers.py` 强制)。
- 未声明时间轴口径就接加载层;或为"信号更好"事后改口径(R-DATA-003)。
- 用存活股/头部股样本冒充全市场(R-DATA-002)。
- 质量门失败仍挂增量或让消费方读到半截数据(§9 原则⑦)。
- 为让校验变绿静默修数据/删异常行。
- 在接入的同一变更里顺手宣布"该源因子有效"或接进候选白名单(R-LLM-001 / 归 probe)。
- 多线程硬冲易封接口;绕过 RateLimiter。

## Success Criteria

- S0-S6 每步产出齐:立项五判记录、探针纪要、INTERFACES/sources 声明 + 口径路由、
  manifest vintage、质量抽查纪要、data_dimensions.md 登记、backlog 销账。
- `check_lake_writers.py` 等守卫全绿;重跑回填幂等。
- 时间轴口径由加载层机械执行(消费者拿到的面板天然防未来),而非口头约定。
- 交棒清晰:probe-signal-source 可直接从统一加载入口取数,无需知道 vendor 细节。
