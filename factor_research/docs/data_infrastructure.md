# 数据基础设施架构

> 数据湖 + 统一加载层 + 质量校验 + 增量更新
> **接入新数据源**:必须走固定剧本 [`agent_skills/data_source_onboarding.md`](agent_skills/data_source_onboarding.md)(立项五判→探针→契约声明→回填→质量门→加载层→登记,逐步 fail-closed),不得临场拼流程。

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        数据源层                               │
│  tushare(主力)  Tencent(新股历史补全)  akshare(东财季报)      │
│  交易所直连(两融)  eastmoney push2his(ETF,❌待修)             │
│  详见 docs/data_dimensions.md §0 数据源架构                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        原始存储层                             │
│  price/daily/*.parquet  price/daily_raw/*.parquet           │
│  fundamental_batch.parquet  capital/*.parquet               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        处理层                                 │
│  lake/compact.py          (逐只→大表合并)                    │
│  lake/aggregate.py        (日线→周线/月线)                   │
│  lake/validator.py        (8层质量校验)                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        统一加载层                             │
│  load_prices()  load_fundamental_panel()  load_capital_panel()│
│  load_raw_close()  load_panel()                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                        消费层                                 │
│  core.engine.BacktestEngine  factory/evaluator.py            │
│  run_daily.py  factors/*.py  scripts/research/*.py           │
└─────────────────────────────────────────────────────────────┘
```

## 核心设计原则

1. **单一事实源**: `data_lake/` 是生产与研究的唯一数据源，旧 `data_full/data` 已清理
2. **防未来函数**: 财务按 `avail_date` 对齐，资金面 shift(1)，T 日只用 T 日前已披露数据
3. **统一接口**: 所有数据通过 `lake/load_lake.py` 加载，不直接读写 parquet
4. **增量更新**: `update_lake.py` 基于 `last_date` 只抓新增，避免全量重下
5. **质量校验**: `validate_final.py` 8 层校验，产出 `quality_report.json`

## 模块职责

| 模块 | 职责 |
|------|------|
| `lake/schema.py` | 集中式字段名、rename 映射、目录定义 |
| `lake/load_lake.py` | 统一数据加载接口（价量/财务/资金面/不复权） |
| `lake/base.py` | Fetcher 基类 + RateLimiter（所有数据源继承） |
| `lake/validator.py` | 8 层数据质量校验 |
| `lake/compact.py` | 逐只 parquet → 大表合并 |
| `lake/aggregate.py` | 日线 → 周线/月线聚合 |
| `scripts/data/update_lake.py` | 统一增量更新入口 |
| `scripts/data/build_lake.py` | 全市场日线下载 + 校验 |
| `scripts/data/build_fundamental_batch.py` | 批量财务下载 |
| `scripts/data/fetch_raw_close.py` | 不复权 OHLC 下载 |

## Schema 规范

详见 `lake/schema.py`。所有数据源原始列名 → 标准列名的映射集中定义，避免散落在各文件中。

## 更新流程

```bash
# 每日盘后
python3 scripts/data/update_lake.py --prices --fundamental

# 每周
python3 scripts/data/update_lake.py --weekly-monthly --validate

# 全量重建（首次或数据损坏时）
python3 scripts/data/build_lake.py
python3 scripts/data/build_fundamental_batch.py
python3 scripts/data/fetch_raw_close.py
python3 lake/compact.py
python3 validate_final.py
```

---

## Tushare 扩展数据层（2026-06，付费 2000 积分）

`data_lake` 价量/财务基础之上，经 tushare 补齐多维度（registry 驱动摄取 + 统一加载入口）。

### 摄取（`scripts/data/update_tushare.py`）
- **token**：环境变量 `TUSHARE_TOKEN`，绝不入库（`.mcp.json` 亦 gitignore）。
- **registry 驱动**：加维度 = 往 `INTERFACES` 加一条声明（mode/fields/store/keys），通用 `backfill()` 处理增量+resumable+flush。三种模式：`by_date`（全市场一天）/`by_stock`（逐股全史）/`by_index`/`once`。
- **并发**：tushare 限速按接口，跨接口并发安全（实测 9 进程零限速）；单 token 顺序、绝不并发抢同一接口（限速铁律）。
- **vintage**：`data_lake/tushare_manifest.json` 记录每数据集 rows/末日/股数/落库时间。

### 加载（`lake.load_lake.load_tushare_panel(dataset, trade_dates, fields, codes)`）
统一入口，**口径自动路由**（`TUSHARE_DATASETS` 注册表）：
- `by_date`：市值/资金/市场当日量 → pivot 对齐，**不 shift**（价格衍生，T 日收盘已知）。
- `anndate`：财务/事件公告 → **ann_date 公告日 ffill**（防未来，T 日只用已公告）。

### 已入库维度
| dataset | 口径 | 内容 | 规模 |
|---|---|---|---|
| daily_basic | by_date | 总/流通市值·股本·换手·PE/PB/PS·股息率 | 13.9M 行 |
| fina_indicator | anndate | ROE/ROA·资产负债率·毛利净利率·周转·yoy（杠杆/质量/成长） | 233k |
| moneyflow | by_date | 大中小单买卖额·主力净流入 | 13.7M |
| stk_limit | by_date | 每日涨跌停价 | 16.5M |
| suspend_d | by_date | 停复牌 | 467k |
| forecast / express | anndate | 业绩预告/快报（盈利惊喜） | 115k / 26k |
| stk_holdernumber | anndate | 股东户数（筹码集中度） | 473k |
| share_float | anndate | 限售解禁（供给压力） | 10.1M |
| index_daily / index_classify | by_date / once | 基准指数（沪深300/中证500/1000/2000…）/ 申万行业 | 44k / 511 |

**待补/已知问题**：limit_list_d（连板情绪，并发下网络超时，需单独限起始日重跑）；report_rc/cyq_chips 体量大需专门处理。adj_factor/income/balancesheet/cashflow/dividend 已注册待回填。

### 批量增量更新优化（不复权原始价 daily_raw 优化）
- **背景与痛点**：不复权原始 OHLC 数据（`daily_raw`，估值 PE/PB 专用）原先依赖 `mootdx`（通达信）逐股拉取历史。由于 RateLimiter 的线程锁限制，5207 只股票变成串行请求，导致每日增量更新耗时高达 **7~20分钟**，发起 5200+ 次网络请求，易超时或被封。
- **优化方案**：利用 Tushare `daily` 全市场单日批量接口替代 mootdx 逐股拉取。
  1. **单次网络请求**：每个缺失交易日仅进行 **1 次 Tushare API 请求**，直接获取全市场所有股票的原始 OHLC 价格数据，网络耗时 < 1秒。
  2. **本地内存拆分与秒级追加**：拉回的数据在本地内存中用 Pandas 进行 `groupby("code")` 拆分，然后批量以追加写 Parquet 的形式增量合并到个股文件中。
  3. **WAF 友好与本地并发**：免去了数千次外网网络握手，本地磁盘 IO 在数秒内即可完成。
  4. **降级与鲁棒保障**：系统保留了原有通达信逐股增量更新作为 Failover 备源。若 Tushare 因限流或网络失败，程序将自动捕获异常并降级回退至 `mootdx` 通道。
- **性能飞跃**：增量数据更新耗时从 **7~20分钟 降至 5~10秒**，网络请求数从 **5200+ 次降至 1 次**。

### 真 Barra Size 的应用
`daily_basic.total_mv` → `ln(total_mv)` 真市值 Size（独立于 `-log amount`），用于 `scripts/research/style_neutralization.py` 的 CNE6 风格中性化（破除 small_cap 自循环，详见 memory `cne6-style-neutralization`）。
