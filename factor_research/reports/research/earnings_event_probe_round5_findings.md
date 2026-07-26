# daily-round-5:业绩预告/快报事件源 probe(方向②)

> 角色边界:本轮只做假设设计与确定性 L0 取证,不判断 alpha 是否有效,不入册、不晋级、不部署。
> 数据边界:本 worktree 使用主仓只读 data_lake symlink;价格 vintage 截至 2026-06-29,manifest 检查到 2026-06-30。本轮只做历史事件源 probe,不生成任何依赖最新日更的交易结论。

## 1. 源选择

方向②要求深化价量之外的信息源。round-2 已确认互动易、公告全文、研报全文、新闻、人气榜等接口在当前 tushare 权限下被 40203 积分墙拦住;`report_rc` 有结构但 10 次/小时硬配额,批量回填不可行。

本轮选择已落库的 `event/forecast_all.parquet` 和 `event/express_all.parquet`:

- `forecast`:业绩预告,字段含 `ann_date/end_date/type/p_change_min/p_change_max/net_profit_min/net_profit_max`。
- `express`:业绩快报,字段含 `ann_date/end_date/revenue/n_income/diluted_eps/diluted_roe/yoy_net_profit`。
- PIT 口径:现有 `factors.earnings` 通过 `lake.load_lake.load_tushare_panel("forecast"/"express")` 使用 anndate 口径 ffill,即 T 日只看到公告日不晚于 T 的事件值。

已有因子:

- `factors.earnings:sue`:快报 `yoy_net_profit` 优先,缺失时回退到业绩预告净利变动幅度中点。
- `factors.earnings:earnings_forecast_surprise`:只使用业绩预告净利变动幅度中点。

## 2. 防未来与对抗检查

本轮未新增生产行为代码。复用已有对抗单测:

- `PYTHONPATH=factor_research python3 factor_research/tests/test_earnings_factors.py` → 3/3 passed。
- `PYTHONPATH=factor_research python3 factor_research/tests/test_signal_source_probe.py` → 5/5 passed。

覆盖点:

- 输出面板必须与 close 的 index×columns 对齐。
- `sue` 必须优先使用 express 实际值,缺失才回退 forecast。
- forecast-only 因子不能被 express 覆盖。
- `signal_source_probe` 的中性化能杀掉纯 control proxy,并保留正交因子。

## 3. L0 probe 结果

窗口沿用 round-2 分析师源体检口径:

- universe=`all`
- start=`2020-06-01`
- cutoff=`2023-06-30`
- end=`2024-12-31`
- probe 命令:
  - `PYTHONPATH=factor_research python3 factor_research/scripts/research/signal_source_probe.py --factor factors.earnings:sue --universe all --start 2020-06-01 --cutoff 2023-06-30 --end 2024-12-31 --json reports/research/probe_round5_earnings_sue.json`
  - `PYTHONPATH=factor_research python3 factor_research/scripts/research/signal_source_probe.py --factor factors.earnings:earnings_forecast_surprise --universe all --start 2020-06-01 --cutoff 2023-06-30 --end 2024-12-31 --json reports/research/probe_round5_earnings_forecast_surprise.json`

| factor | raw IC IS/OOS/full | residual IC IS/OOS/full | OOS/IS residual retention | style corr(size/liquidity/momentum) |
|---|---:|---:|---:|---:|
| `sue` | 0.0033 / 0.0093 / 0.0048 | 0.0043 / 0.0040 / 0.0035 | 93% | 0.439 / -0.206 / 0.006 |
| `earnings_forecast_surprise` | -0.0078 / -0.0036 / -0.0046 | 0.0022 / -0.0060 / 0.0013 | -273% | 0.173 / -0.004 / 0.037 |

Cheap-first 截止:

- `sue` 的残差 IC 留存不塌,但 IC 绝对值只有 0.0035,ICIR 只有 0.07,且 size corr=0.439,说明该源在现有全市场 long-only 月频口径下混有明显规模/覆盖度暴露。它是一个弱正交事件信号,不是可以继续 L1 的候选主腿。
- `earnings_forecast_surprise` 的残差 OOS 翻负,forecast-only 口径不应继续投算力。
- 本轮不进 L1-L3:工厂 L0 门槛文件 `factory/lines/line2_validation/gates.py` 要求至少 60 个 IC 日期,而本 probe 月频样本仅 54 个月;更关键的是 ICIR 很弱。强行包装进入 L1 会违反 cheap-first。

## 4. 结论

业绩预告/快报事件源在 PIT 对齐上可用,但本轮证据不足以把它升级为可搜索主因子族:

1. `sue` 可保留为 NOTE 级事件源观察项,未来如果改成行业内同类比较、财报季窗口、或和基本面质量族交互,可以重探。
2. 纯预告 `earnings_forecast_surprise` 在本轮口径下关闭,不建议再做窗口/归一化微调。
3. 由于价格数据不是最新日更,本轮只给历史证据,不输出当前交易结论。

## 5. 对抗性审查

最可能翻车的点与处理:

1. 把 `sue` 的 OOS 留存好看误读成 alpha。处理:报告中明确 IC 绝对值仅 0.0035、ICIR 0.07、size corr=0.439,只给 NOTE,不进白名单。
2. 把非白名单因子写进 `direction_registry.scope_factors` 造成机械降权误用。处理:方向登记测试曾抓到该问题,已改成 `scope_factors=[]`,只保留 prompt note。
3. 月频样本只有 54 个月,不足工厂 L0 的 60 个 IC 日期。处理:不包装进 L1-L3,避免绕过 cheap-first。
4. 数据新鲜度不足导致当前交易误导。处理:明示价格 vintage 截至 2026-06-29,本轮只做历史 probe。
5. 试验记账误写主仓 symlink 数据湖。处理:用 `record_trials(..., path=reports/research/daily_round5_trial_ledger.jsonl)` 记录本轮 2 次配置,不写 `data_lake/governance/trial_ledger.jsonl`。

## 6. 产物

- `factor_research/reports/research/probe_round5_earnings_sue.json`
- `factor_research/reports/research/probe_round5_earnings_forecast_surprise.json`
- `factor_research/reports/research/daily_round5_trial_ledger.jsonl`
