# Data Lake

全市场、日频数据湖。所有数据通过 `lake/load_lake.py` 统一接口加载。

## 目录结构

```
data_lake/
├── price/
│   ├── daily/              # 后复权价量 (腾讯源, 逐只 parquet)
│   ├── daily_raw/          # 不复权 OHLC (通达信, 逐只 parquet)
│   ├── weekly/             # 周线聚合 (from daily)
│   └── monthly/            # 月线聚合 (from daily)
├── fundamental/
│   └── *.parquet           # 逐只财务 (已废弃, 用 fundamental_batch.parquet)
├── fundamental_batch.parquet  # 批量财务长表 (东财 yjbb, 全市场×全历史)
├── capital/
│   ├── margin_all.parquet     # 两融 (date×code)
│   └── northbound_all.parquet # 北向持股 (date×code)
└── meta/
    ├── trade_calendar.parquet   # 交易日历
    ├── codes.parquet            # 全市场代码列表
    ├── st_history.parquet       # ST 历史记录
    └── list_date.parquet        # 上市日期
```

## 数据血缘

```
数据源 → 原始存储 → 处理 → 消费

腾讯 fqkline      → price/daily/*.parquet        → load_prices()       → 因子/回测
通达信 mootdx     → price/daily_raw/*.parquet    → load_raw_close()    → 估值/模拟盘
东财 yjbb_em      → fundamental_batch.parquet    → load_fundamental_panel() → 基本面因子
交易所 + 东财     → capital/*.parquet            → load_capital_panel()    → 资金面因子
akshare           → meta/*.parquet               → 直接读取            →  Universe/停牌
```

## 字段规范

所有字段名统一定义于 `lake/schema.py`。

| 类别 | 字段 | 来源 | 更新频率 |
|------|------|------|----------|
| 价量 | close, open, high, low, volume, amount | 腾讯 | 日频 (增量) |
| 不复权 | raw_close, raw_open, raw_high, raw_low | 通达信 | 日频 (增量) |
| 财务 | roe, eps, eps_ttm, bps, revenue, net_profit, gross_margin, cfo_ps, revenue_yoy, net_profit_yoy, industry | 东财 yjbb | 季频 |
| 两融 | margin_balance, margin_buy, short_balance, short_vol | 交易所 | 日频 |
| 北向 | northbound_hold_shares, northbound_hold_value, northbound_hold_pct, ... | 东财 | 日频 (2017-2024) |

## 防未来函数设计

- **财务数据**: 按 `avail_date` (公告日) 对齐，T 日只用 T 日前已披露的财务
- **资金面**: 交易所数据 T 日盘后发布，统一 shift(1)，T 日记录 T+1 起可见
- **不复权价**: 用于估值计算 (PE/PB)，避免复权价量纲错误

## 更新入口

```bash
python3 scripts/data/update_lake.py --all    # 全部更新
python3 scripts/data/update_lake.py --prices # 仅价量
python3 scripts/data/update_lake.py --validate # 质量校验
```

更新状态记录在 `_manifest.json`。

## 质量监控

```bash
python3 validate_final.py
```

产出 `quality_report.json`，当前 clean_ratio ~99.9%。
