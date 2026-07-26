# 最佳策略跨平台复现说明

本文档用于在其它具备同等 A 股数据能力的平台上复现当前最佳生产策略。

当前最佳版本按本仓库台账与生产入口定义为：

```text
illiquidity v3.1
= AmihudIlliq 20d
+ Salience Veto 30%
+ PureTrend MA16 Band Timing
+ 511010 国债 ETF 熊市轮动
```

当前台账指标（`strategy_versions.json::illiquidity/v3.1`）：

```text
区间: 2010-2026 压力测试口径 / 2018 起统计
年化: +37.77%
最大回撤: -11.95%
夏普: 2.12
卡玛: 3.16
```

这些指标只在同等数据口径、同等交易成本、同等 T+1 执行和同等幸存者偏差处理下有比较意义。

## 1. 数据要求

必须具备以下数据：

- A 股全市场日频数据，包含退市股、停牌日处理、创业板 `300`、科创板 `688`、小盘股。
- 后复权收盘价 `close`，用于收益计算。
- 不复权收盘价 `raw_close`，用于成交额口径修正。
- 成交量 `volume`，单位为“手”。若平台单位是股，需要相应调整。
- 交易日历。
- 场内债券 ETF `511010` 日频收盘价。

成交额必须按不复权价格重算：

```python
amount = volume_in_lots * 100 * raw_close
```

不要直接用后复权价格计算成交额或估值类量纲，否则截面排序会被复权因子污染。

## 2. 股票选择因子

### 2.1 AmihudIlliq 20d

对每只股票：

```python
ret = close.pct_change()
illiq_raw = abs(ret) / amount
amihud_20 = illiq_raw.rolling(20).mean()
```

截面处理：

```python
factor = row_zscore(row_mad_clip(amihud_20, n=5)).shift(1)
```

解释：

- 因子越高，表示越不流动，越靠前。
- `shift(1)` 必须保留，表示 T 日选股只能使用 T-1 前已确认数据。
- 调仓时选 `factor` 最高的股票。

## 3. Salience Veto 30%

这是候选池过滤器，不是独立策略。

### 3.1 Salience Covariance

参数：

```text
W = 20
theta = 0.1
delta = 0.7
```

计算过程：

```python
returns = close.pct_change()
market_returns = returns.mean(axis=1)

r_diff = abs(returns - market_returns)
r_sum = abs(returns) + abs(market_returns) + theta
salience = r_diff / r_sum

# 对最近 W 日 salience 做衰减排序权重
# 计算 salience 加权预期收益 est_return
st_cov = est_return - returns.rolling(W).mean()
veto_score = -st_cov
veto_score = veto_score.shift(1)
```

仓位构造时，在调仓日先过滤候选池：

```python
threshold = veto_score.loc[date].quantile(0.30)
eligible = veto_score.loc[date] > threshold
candidate_factor = factor.loc[date][eligible]
holdings = candidate_factor.nlargest(25)
```

关键约束：

- 过滤发生在 `top_n` 之前。
- 剔除底部 30% 后，从幸存候选里补满 25 只。
- 不因为 veto 降仓。
- 只在调仓日生效，持仓期内不踢仓。

## 4. 择时与仓位暴露

### 4.1 PureTrend MA16

构造小盘等权指数：

```python
ret = close.pct_change()
small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
small_ret = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
small_nav = (1 + small_ret.fillna(0)).cumprod()
ma16 = small_nav.rolling(16).mean()
dist = small_nav / ma16 - 1
```

即：用 20 日均成交额截面排名最低的 50% 股票构造小盘等权指数。其它平台如果用市值、总市值、流通市值或不同小盘分位，结果会偏离。

### 4.2 Band Timing

生产信号使用动态暴露：

```python
dist_lagged = dist.shift(1)
exposure = clip(1 + dist_lagged * 8, 0, 1.5)
exposure = exposure where dist_lagged > 0 else 0
```

含义：

- `dist_lagged <= 0`: 股票仓位为 0。
- `dist_lagged > 0`: 股票仓位在 `0~1.5x` 之间动态变化。
- 这里的 `shift(1)` 是防未来函数要求，不可删除。

## 5. 调仓规则

```text
持股数: 25
调仓间隔: 20 个交易日
选股方向: factor 最大的 25 只
执行: T 日生成目标组合, T+1 生效
持仓权重: 等权
```

伪代码：

```python
for date in factor_dates[::20]:
    effective_date = next_trading_day(date)

    active = stocks_with_valid_close_on(date)
    f = factor.loc[date].reindex(active).dropna()

    v = veto_score.loc[date].reindex(f.index).dropna()
    f = f[v > v.quantile(0.30)]

    names = f.nlargest(25).index
    target_weights[effective_date] = equal_weight(names)
```

## 6. 国债 ETF 轮动

股票策略收益先按 Band exposure 得到 `r_stock`。

再根据 `dist_lagged` 切换：

```python
if dist_lagged > 0:
    daily_return = r_stock
else:
    daily_return = r_511010
```

也就是：

- BULL: 持有 AmihudIlliq 股票组合。
- BEAR: 空仓资金配置 `511010` 国债 ETF。

实盘模拟中 ETF 交易成本按单边 `0.05%` 估计。

## 7. 成本模型

股票交易成本：

```text
买入成本: 0.225%
卖出成本: 0.275%
融资利率: 6.5%/年
```

ETF 交易成本：

```text
买入成本: 0.05%
卖出成本: 0.05%
融资利率: 0
```

回测中必须按真实换手扣成本。不要用无成本或低成本结果替代。

## 8. 回测统计口径

推荐两段式：

```text
warmup_start = 2010-01-01
stats_start = 2018-01-01
```

原因：

- 2010 起加载数据用于因子预热、择时状态和压力测试连续性。
- 2018 起统计生产主指标。
- 如做压力测试，可直接统计 2010 起结果。

指标计算：

```python
annual = mean(daily_return) * 252
vol = std(daily_return) * sqrt(252)
sharpe = annual / vol
nav = (1 + daily_return).cumprod()
maxdd = min(nav / nav.cummax() - 1)
calmar = annual / abs(maxdd)
```

## 9. 最小复现伪代码

```python
close, volume, raw_close = load_full_market_data(start="2010-01-01")
amount = volume * 100 * raw_close
bond_ret = load_etf_close("511010").pct_change()

# factor
ret = close.pct_change()
amihud = (ret.abs() / amount).rolling(20).mean()
factor = zscore(mad_clip(amihud, 5)).shift(1)

# veto
veto = salience_covariance_veto(close, W=20, theta=0.1, delta=0.7).shift(1)

# timing
timing_raw, small_nav, dist = small_cap_timing(close, amount, ma_window=16)
dist_lagged = dist.shift(1)
exposure = ((1 + dist_lagged * 8).clip(0, 1.5) * (dist_lagged > 0)).fillna(0)

# weights
weights = {}
for date in rebalance_dates(factor, step=20):
    eff = next_trading_day(date)
    f = factor.loc[date].dropna()
    v = veto.loc[date].reindex(f.index).dropna()
    f = f.reindex(v[v > v.quantile(0.30)].index).dropna()
    names = f.nlargest(25).index
    weights[eff] = equal_weight(names)

# stock return with costs
r_stock = backtest_weights(
    close=close,
    target_weights=weights,
    exposure=exposure,
    buy_cost=0.00225,
    sell_cost=0.00275,
    financing_rate=0.065,
)

# bond rotation
common = r_stock.index.intersection(bond_ret.index).intersection(dist_lagged.dropna().index)
r = where(dist_lagged.loc[common] > 0, r_stock.loc[common], bond_ret.loc[common])
r = r.loc["2018-01-01":]
```

## 10. 复现校验目标

若数据平台口径足够接近，应接近本仓库台账：

```text
illiquidity v3.1:
年化约 +37.8%
最大回撤约 -12.0%
夏普约 2.1
卡玛约 3.2
```

允许偏差来源：

- 是否包含退市股。
- 停牌日和一字板处理。
- `volume` 单位是手还是股。
- 不复权价是否完整。
- `511010` ETF 数据起点和复权方式。
- T+1 执行是否严格。
- 是否误删 `shift(1)`。
- Salience Veto 排名和分位边界处理差异。

## 11. 不可接受的复现偏差

以下情况应视为复现失败：

- 使用幸存者股票池。
- 用后复权价格计算成交额。
- T 日因子直接交易 T 日收益。
- 不扣交易成本。
- veto 后不补满 25 只，导致隐性降仓。
- BEAR 状态用现金替代 `511010`，却仍声称复现 v3.1。
- 把 Salience Veto 或任何 veto 当作独立策略报告净值。

## 12. 版本备注

本仓库存在早期 v3.0 文档与配置注释，当前生产入口 `run_daily.py` 已标注为 `illiquidity v3.1`。若其它平台只复现：

```text
AmihudIlliq 20d + Band + 511010
```

而不含：

```text
Salience Veto 30%
```

则复现的是 v3.0，不是当前最佳 v3.1。
