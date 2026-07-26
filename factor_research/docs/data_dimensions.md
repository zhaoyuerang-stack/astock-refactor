# 数据维度清单 — Data Dimensions Inventory

> 单一真相源：本文件记录 `data_lake/` 中**已入库**的全部数据维度（时间跨度、覆盖范围、更新方式），以及**规划接入**的新维度与优先级。  
> 更新时间：2026-06-15  
> 相关文档：`docs/data_infrastructure.md`（底层设计）、`TASKS.md`（开放任务）

---

## 零、数据源架构

### 0.1 外部数据源清单

| 数据源 | 角色 | 接入方式 | 鉴权 | 限速/配额 | 状态 |
|--------|------|----------|------|-----------|------|
| **tushare** | 主力源（全市场价量+财务+资金+事件+宏观） | REST POST → `http://api.tushare.pro` | token：`data_lake/agent/tushare_config.json` 或 `TUSHARE_TOKEN` 环境变量 | 2000积分；通用接口 200次/分钟 | ✅ 生产中 |
| **Tencent fqkline** | 历史价量补全（仅新上市股票首次下载） | HTTP GET `qt.gtimg.cn/q=sh{code}` | 无需鉴权 | ~1200只后触发 WAF（HTTP 501）；单线程 | ⚠️ 半退役：日增量已切 tushare，仅保留新股初次历史下载 |
| **akshare（东财yjbb_em）** | 季度财务批量（fundamental_batch） | Python 库 `ak.stock_yjbb_em(date=)` | 无需鉴权 | 东财接口，逐报告期抓，~50只/分钟触发封禁 | ✅ 季度更新 |
| **上交所/深交所 margin** | 两融余额数据 | HTTP GET 交易所 CSV | 无需鉴权 | 无明显限速 | ✅ 日更（`lake/sources/exchange.py::MarginFetcher`） |
| **eastmoney push2his** | ETF 跨资产价格（511010/518880 等） | HTTP GET `push2his.eastmoney.com` | 无需鉴权 | clash 代理拦截 → ProxyError | ❌ 待修复，改 tushare `fund_daily` |

### 0.2 统一接入层

```
外部数据源
    │
    ├── tushare REST     → lake/sources/tushare.py::call(api_name, params)
    │       限速节流: 单线程, _MIN_INTERVAL=0.18s(≈330次/分留余量)
    │       重试策略: 6次指数退避, 软限速(每分钟)sleep 60s, 硬配额直接抛
    │
    ├── Tencent fqkline  → lake/sources/tencent.py::TencentDailyFetcher
    │       仅用于: missing_codes 新上市股票历史下载
    │       禁止: 多线程并发(铁律4: WAF封禁)
    │
    ├── akshare           → lake/sources/exchange.py + update_lake.py
    │       ak.stock_yjbb_em(date=YYYYMMDD) → fundamental_batch
    │       ak.stock_margin_*               → 两融（备用）
    │
    └── 交易所 HTTP       → lake/sources/exchange.py::MarginFetcher
```

### 0.3 数据源与维度对应关系

| 数据源 | 负责的维度 |
|--------|-----------|
| tushare | 价量日增量、复权因子、daily_basic、moneyflow、stk_limit、suspend、财务三表、fina_indicator、holdernumber、holdertrade、share_float、forecast、express、dividend、index_daily、margin、northbound(已停)、宏观(CPI/PPI/M2/shibor/北向)、调度元数据(trade_calendar/codes/list_date/st) |
| Tencent | 新股历史价量（新上市 → 完整历史一次性下载，写入 per-stock parquet 后不再用） |
| akshare | `fundamental_batch.parquet`（东财季报批量，收入/利润 YoY） |
| 交易所直连 | `capital/margin/` 两融逐日明细 |
| eastmoney push2his | `cross_asset/etf/`（❌ 待修复） |

### 0.4 tushare 积分说明

当前订阅：**2000 积分**

| 接口类别 | 配额（2000积分档） | 状态 |
|----------|-------------------|------|
| 通用价量/财务/事件 | 200次/分钟 + 100k次/天 | ✅ 绰绰有余 |
| `cyq_perf`（筹码胜率） | 5次/天 | ⚠️ 积分墙，需升档 |
| `report_rc`（卖方预测修正） | 1次/分钟 | ⚠️ 全量需92小时，不实际 |
| `limit_list_d`（连板） | 1次/小时 | ⚠️ 积分墙 |

---

## 一、已入库维度

### 1.1 价量数据

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **后复权日线 OHLCV** | `price/daily_all.parquet` + `price/daily/{code}.parquet` | 1,307 万 | 5,207 只（全市场含创业板/科创板） | 2010-01-04 ~ 今 | **日增量**：tushare `daily`+`adj_factor`，2 次 API/交易日；launchd 每个 China 交易日 15:30/16:30 触发 |
| **复权因子** | `adj_factor/adj_factor_all.parquet` | 1,466 万 | 5,793 只 | 2010-01-04 ~ 今 | 与价量同批次更新 |
| **原始未复权日线** | `price/daily_raw/{code}.parquet` | — | 部分 | — | 历史存档，不日更 |
| **周线/月线聚合** | `price/weekly/` `price/monthly/` | — | 同日线 | 同日线 | 日线更新后自动 `lake.aggregate.build_periodic()` 重聚合 |

> **口径说明**：后复权基准 = 最新日，与现有 Tencent hfq 历史口径兼容。2026-06-15 起日增量切换为 tushare；历史数据保留 Tencent 口径，接缝用 adj_factor 比值校准（无断层）。  
> **已知问题**：科创板 688 手数=1 手（vs A股100手），已在 `lake/compact.py` 修正 volume 量纲。

---

### 1.2 每日衍生基础指标

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **每日基础指标** | `daily_basic/daily_basic_all.parquet` | 1,391 万 | 5,789 只 | 2010-01-04 ~ 2026-06-12 | tushare `daily_basic`；**滞后价量 ~2 天**（积分限速），建议补入 launchd |
| **个股资金流** | `moneyflow/moneyflow_all.parquet` | 1,366 万 | 5,664 只 | 2010-01-04 ~ 2026-06-12 | tushare `moneyflow`；同上滞后 |
| **涨跌停记录** | `market/stk_limit_all.parquet` | 1,648 万 | 8,338 只（含退市） | 2010-01-04 ~ 2026-06-12 | tushare `stk_limit` |
| **停牌记录** | `market/suspend_all.parquet` | 46 万 | 4,636 只 | 2010-01-04 ~ 2026-06-12 | tushare `suspend_d` |

> `daily_basic` 含：PE/PB/PS/市值/流通市值/换手率/量比。因子库中 size/value/turnover 类因子从此表读取。

---

### 1.3 财务数据

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **利润表** | `financials/income_all.parquet` | 30.9 万 | 5,207 只 | 1990-12-31 ~ 2026Q1 | 季度批量：`update_fundamental()`，检测缺失报告期自动补 |
| **资产负债表** | `financials/balancesheet_all.parquet` | 25.5 万 | 5,207 只 | 2001-12-31 ~ 2026Q1 | 同上 |
| **现金流量表** | `financials/cashflow_all.parquet` | 27.9 万 | 5,207 只 | 2001-12-31 ~ 2026Q1 | 同上 |
| **财务指标（派生）** | `financials/fina_indicator_all.parquet` | 23.3 万 | 5,207 只 | 2002-12-31 ~ 2026Q1 | 同上 |
| **基本面批量（收入/利润YoY）** | `fundamental_batch.parquet` | 41.4 万 | — | — | akshare `yjbb_em` 季度批量 ⚠️ DQ-2026-001 |

> **防未来函数铁律**：财务数据按 `ann_date`（公告日）对齐到交易日 ffill，T 日只用 T 日前已披露数据。`avail_date = ann_date.fillna(report_date + 45天)`。

> ⚠️ **已知缺陷 DQ-2026-001(2026-07-22,OPEN)**：`fundamental_batch.parquet` 的 `ann_date/avail_date` 列 ≤2025Q1 报告期系统性偏晚约 +365 天（重建 join 错位，数值载荷未污染），另有 561 行实质早知（R-DATA-003 违规，最多提前 1036 天）。涉及 2010-2021 窗口的消费结论按 suspect 处理。登记与例外口径见 [docs/data_quality/register.md](data_quality/register.md)。

---

### 1.4 资金与持仓结构

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **两融余额** | `capital/margin_all.parquet` | 634 万 | 4,556 只 | 2010-03-31 ~ 2026-06-03 | tushare `margin`；`update_capital_margin()` |
| **北向持股（沪深港通汇总）** | `capital/northbound_all.parquet` | 67.5 万 | 774 只 | 2017-03-16 ~ **2024-08-16** | ⚠️ tushare `hk_hold` 接口已下架，**停更**；宏观层 `macro/moneyflow_hsgt.parquet` 替代汇总净买 |
| **股东人数** | `holder/holdernumber_all.parquet` | 47.3 万 | 5,207 只 | 1993-01-12 ~ 2026-06-14 | tushare `stk_holdernumber`；季报披露后触发 |
| **高管增减持** | `holder/holdertrade_all.parquet` | 10.3 万 | 4,802 只 | 1994-08-10 ~ 2026-06-15 | tushare `stk_holdertrade` |
| **解禁股** | `holder/share_float_all.parquet` | 1,010 万 | 5,200 只 | 2005-01-21 ~ 2035-10-29（含未来预告） | tushare `share_float` |

---

### 1.5 公司行动与事件

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **分红派息** | `corp_action/dividend_all.parquet` | 16.2 万 | 5,196 只 | 1991-05-02 ~ 2026-07-09 | tushare `dividend`；含未来预告 |
| **业绩预告** | `event/forecast_all.parquet` | 11.5 万 | 5,202 只 | 1999-01-09 ~ 2026-05-21 | tushare `forecast` |
| **业绩快报** | `event/express_all.parquet` | 2.6 万 | 3,842 只 | 2005-01-08 ~ 2026-05-07 | tushare `express` |

---

### 1.6 指数与分类

| 维度 | 文件路径 | 行数 | 覆盖范围 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|----------|
| **指数日线（8 条）** | `index/index_daily_all.parquet` | 4.4 万 | 沪深300/500/1000/创业板/科创50等 8 条 | 1993-07-09 ~ 2026-06-12 | tushare `index_daily` |
| **申万行业分类** | `index/sw_classify.parquet` | 511 行 | 三级分类 | 静态快照 | 每年重新拉取一次（行业调整时） |
| **ETF 日线（跨资产）** | `cross_asset/etf/{code}.parquet` | 各 3k~4k 行 | 511010(国债)/518880(黄金)/510880(红利)/513100(纳指)/159920(创业板) | 2010 ~ 2026-06-15 | 手动维护；eastmoney push2his 接口；**ProxyError 问题待修** |

---

### 1.7 宏观数据

| 维度 | 文件路径 | 频率 | 时间跨度 | 更新方式 |
|------|----------|------|----------|----------|
| **CPI** | `macro/cn_cpi.parquet` | 月 | 200108 ~ 202605 | tushare `cn_cpi`；月度手动触发 |
| **PPI** | `macro/cn_ppi.parquet` | 月 | 200108 ~ 202605 | tushare `cn_ppi` |
| **M2/M1/M0** | `macro/cn_m.parquet` | 月 | 200108 ~ 202605 | tushare `cn_m` |
| **Shibor** | `macro/shibor.parquet` | 日 | 2008-01-02 ~ 2026-06-15 | tushare `shibor` |
| **北向资金净买（沪深港通）** | `macro/moneyflow_hsgt.parquet` | 日 | 2014-11-17 ~ 2026-06-12 | tushare `moneyflow_hsgt` |

> **注**：宏观数据 month 列格式为 `YYYYMM`（整数），使用前需 `pd.to_datetime(df['month'].astype(str), format='%Y%m')`。  
> 宏观数据加入 **防未来 lag**：月频指标延迟 1 个月对齐（公告日通常在下月中旬），用 `load_macro()` 统一读取。

---

### 1.8 元数据

| 维度 | 文件路径 | 说明 |
|------|----------|------|
| **股票代码表** | `meta/codes.parquet` | 全市场有效代码列表 |
| **上市日期** | `meta/list_date.parquet` | 用于过滤新股（上市<N月排除） |
| **ST 历史** | `meta/st_history.parquet` | ST/\*ST 进出记录；做 veto 过滤 |
| **交易日历** | `meta/trade_calendar.parquet` | SSE 交易日，2010-2030 |
| **数据指纹** | `_manifest.json` | 末日+fingerprint+更新时间；漂移检测基准 |

---

## 二、数据更新全链路（现状）

```
launchd (PDT Mon-Sat 00:30 + 01:30)
    → scripts/ops/scheduled_daily_update.py
        → scripts/data/update_lake.py --prices
            → lake/sources/tushare_price.py::fetch_new_day()
                → tushare daily (全市场 OHLCV, ~5,200只)
                → tushare adj_factor (复权因子)
                → hfq重建 写入 per-stock parquet
            → lake/compact.py::compact_prices()
                → 重建 daily_all.parquet
                → lake/invariants.py 截面校验（|r|>30% 超5%只 → 拒绝）
        → update_fundamental() (季度，有新报告期才触发)
        → run_daily.py (信号生成 + 模拟盘估值)
```

**时区说明**：launchd 以本机 PDT 时间调度，PDT 00:30 = China 15:30（交易收盘后）；`expected_trade_date()` 用 -9h 偏移确保早晨运行映射到前一交易日。

**未纳入自动更新的维度**（需手动或单独脚本）：
- `daily_basic` / `moneyflow` — 滞后约 2 天，尚未接入 launchd
- 宏观月频数据 — 每月一次，手动触发
- ETF 跨资产价格 — eastmoney 接口有 ProxyError，待修
- 财务三表 — 已接入季度检测，但 launchd 未调用 `update_fundamental()`

---

## 三、规划接入的新维度

### 3.1 第一优先级：直接 alpha 候选（2000积分内，可立即接入）

| 维度 | tushare接口 | alpha逻辑 | 估算数据量 | 接入难度 |
|------|-------------|-----------|----------|----------|
| **大宗交易** | `block_trade` | 折价率 = 机构出货意愿；折价>5% 后 20 日弱 | ~200 万行 | 低：按日期批量，一次历史拉齐 |
| **龙虎榜** | `top_list` + `top_inst` | 机构净买 > 0 = 短期事件驱动；游资席位 = 情绪信号 | ~50 万行 | 低：触发条件日（异动+涨跌停）才有数据 |
| **回购记录** | `repurchase` | 管理层信心代理；与 holdertrade 合并构成 insider 综合因子 | ~5 万行 | 低 |
| **股权质押比例** | `pledge_stat` | 控股股东质押率高 = 潜在平仓风险；做风控 veto 或负向因子 | ~20 万行 | 低 |
| **指数成分权重** | `index_weight` | 沪深300/500/1000 实时权重；用于基准中性化和容量估算 | ~200 万行 | 低 |
| **概念板块成分** | `concept_detail` | 构建比申万更细的主题暴露；用于截面中性化和主题轮动 | ~100 万行 | 低 |

### 3.2 第二优先级：质量/风险信号补全

| 维度 | tushare接口 | alpha逻辑 | 接入难度 |
|------|-------------|-----------|----------|
| **审计意见** | `fina_audit` | 非标意见（保留意见/拒绝表示意见）= 财务风险硬过滤；入 veto 层 | 低 |
| **主营业务构成** | `fina_mainbz` | 收入结构 → 业务多元化因子；比申万行业更精细的行业对齐 | 低 |
| **GDP季度** | `cn_gdp` | 经济周期锚点；与 CPI/PMI 组合识别宏观状态（扩张/收缩/滞胀）| 低 |
| **PMI制造业/非制造业** | `cn_pmi` | 宏观景气信号；制造业 PMI>50 = 扩张，用于行业轮动因子 | 低 |
| **LPR利率** | `shibor_lpr` | 货币政策信号；降息周期对金融/地产/高股息板块定价影响 | 低 |

### 3.3 第三优先级：积分墙（需升积分）

| 维度 | tushare接口 | 当前限制 | alpha逻辑 | 优先级 |
|------|-------------|----------|-----------|--------|
| **筹码胜率/获利盘** | `cyq_perf` | 5次/天（需升积分） | 获利盘比例/平均成本/套牢盘 = 与价量低相关的独立 alpha；标注在 TASKS.md | ★★★★ |
| **卖方盈利预测修正** | `report_rc` | 1次/分钟（全量需92小时） | 分析师上调 = 已知强 event alpha（SUE 效应）；需升积分提速 | ★★★ |
| **连板数据** | `limit_list_d` | 1次/小时 | 连板情绪短线；与日频因子契合度低，暂不建议 | ★★ |

### 3.4 待修复的已有维度

| 维度 | 问题 | 修复方案 |
|------|------|----------|
| **北向持股个股** | `hk_hold` 接口下架，停更于 2024-08-16 | 改用 `hsgt_top10`（沪深港通十大持股）作为持仓结构代理；或放弃个股层面，保留 `moneyflow_hsgt` 汇总 |
| **ETF跨资产价格** | eastmoney push2his ProxyError（clash代理拦截） | 改用 tushare `fund_nav` 或 `etf_basic`+`fund_daily` 接口；511010/518880 均有 tushare 数据 |
| **daily_basic/moneyflow 滞后** | 目前落后价量 2 天，launchd 未调用 | 接入 `update_lake.py --all` 全量更新，或单独加入 launchd |
| **宏观月频未自动更新** | 手动触发 | 在 launchd 中加月度触发条件（每月 15 日后检查新月份） |

---

## 四、接入路线图

```
第一批（本周）：block_trade + top_list/top_inst + repurchase + pledge_stat
    → 构建机构行为因子族（大宗折价/insider买卖）
    → 写入 data_lake/institutional/

第二批（下一周）：fina_audit + index_weight + concept_detail
    → 完善风控 veto 层 + 中性化体系

第三批（宏观补全）：cn_gdp + cn_pmi + shibor_lpr
    → 宏观层从 5 维扩展到 8 维
    → 接入 load_macro() 统一接口

第四批（积分升级后）：cyq_perf
    → 独立验证：获利盘因子 IC / 多空收益
    → 通过 L0-L3 筛选后入 factory 流水线

ETF修复（并行）：切换 etf 数据源到 tushare fund_daily
    → 511010/518880/513100/510880/159920 统一日更
```

---

> **铁律提示**：任何新维度入库后，必须先过 `lake/invariants.py` 截面校验，再接入因子计算。不得绕过，上游数据质量问题会直接污染截面回归结果。
