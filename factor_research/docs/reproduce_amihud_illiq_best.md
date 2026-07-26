# AmihudIlliq 最佳策略跨平台复现说明

本文档只描述 `AmihudIlliq` 母策略当前最佳版本在其它同等数据平台上的复现方法。

当前最佳版本：

```text
family: illiquidity
version: v3.1
name: AmihudIlliq 20d + Salience Veto 30% + Band Timing + 511010 Rotation
```

台账指标：

```text
年化: +37.77%
最大回撤: -11.95%
夏普: 2.12
卡玛: 3.16
```

注意：`AmihudIlliq v3.1` 是当前最佳 AmihudIlliq 策略。若不使用 `Salience Veto 30%`，复现的是 `v3.0`，不是当前最佳版本。

## 1. 复现所需数据

必须具备：

- A 股全市场日线，含退市股、停牌股、创业板、科创板和小盘股。
- 后复权收盘价 `close`，用于收益率计算。
- 不复权收盘价 `raw_close`，用于成交额重算。
- 成交量 `volume`。本仓库口径是“手”。
- 交易日历。
- `511010` 国债 ETF 日线收盘价。

成交额口径：

```python
amount = volume_in_lots * 100 * raw_close
```

如果平台成交量单位是“股”，则：

```python
amount = volume_in_shares * raw_close
```

不要用后复权价格计算 `amount`。

## 2. AmihudIlliq 因子

Amihud 非流动性原始值：

```python
ret = close.pct_change()
illiq_daily = abs(ret) / (amount + 1)
amihud_20 = illiq_daily.rolling(20).mean()
```

截面标准化：

```python
factor = row_zscore(row_mad_clip(amihud_20, n=5)).shift(1)
```

含义：

- `factor` 越高，股票越不流动，越优先买入。
- `shift(1)` 是硬约束，防止 T 日使用 T 日收盘后才知道的数据交易 T+1 前的收益。
- 调仓日从 `factor` 最大的股票开始选。

## 3. Salience Veto 30%

`Salience Veto` 是排除规则，不是独立策略。

参数：

```text
W = 20
theta = 0.1
delta = 0.7
veto_q = 0.30
```

计算：

```python
returns = close.pct_change()
market_returns = returns.mean(axis=1)

r_diff = abs(returns - market_returns)
r_sum = abs(returns) + abs(market_returns) + theta
salience = r_diff / r_sum

# 最近 W 日 salience 逐股票排序，按 delta 衰减加权历史收益
est_return = decayed_rank_weighted_return(salience, returns, W=20, delta=0.7)
avg_return = returns.rolling(20).mean()
st_cov = est_return - avg_return

veto_score = (-st_cov).shift(1)
```

调仓日过滤：

```python
v = veto_score.loc[date].reindex(candidate_factor.index).dropna()
candidate_factor = candidate_factor[v > v.quantile(0.30)]
```

必须遵守：

- 先过滤候选池，再选 Top 25。
- 过滤后从幸存候选补满 25 只。
- 不因为 veto 降低仓位。
- 不在持仓期内踢仓。

## 4. PureTrend MA16 Band Timing

先构造小盘等权指数：

```python
ret = close.pct_change()
small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
small_ret = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
small_nav = (1 + small_ret.fillna(0)).cumprod()
ma16 = small_nav.rolling(16).mean()
dist = small_nav / ma16 - 1
```

Band 暴露：

```python
dist_lagged = dist.shift(1)
stock_exposure = ((1 + dist_lagged * 8).clip(0, 1.5) * (dist_lagged > 0)).fillna(0)
```

解释：

- `dist_lagged <= 0`: 不持股票。
- `dist_lagged > 0`: 持股票，暴露在 `0~1.5x`。
- 这里用 `dist.shift(1)`，不是当日 `dist`。

## 5. 组合构造

参数：

```text
top_n = 25
rebalance_days = 20
weighting = equal weight
execution = T signal, T+1 effective
```

调仓逻辑：

```python
weights = {}

for date in factor_dates[::20]:
    effective_date = next_trading_day(date)

    active = stocks_with_valid_close(date)
    f = factor.loc[date].reindex(active).dropna()

    v = veto_score.loc[date].reindex(f.index).dropna()
    f = f.reindex(v[v > v.quantile(0.30)].index).dropna()

    selected = f.nlargest(25).index
    weights[effective_date] = equal_weight(selected)
```

## 6. 股票回测成本

股票组合回测成本：

```text
buy_cost = 0.00225
sell_cost = 0.00275
financing_rate = 0.065
```

每日收益：

```python
gross_stock_return = weighted_next_day_return * stock_exposure
trade_cost = buy_turnover * buy_cost + sell_turnover * sell_cost
financing = max(stock_exposure - 1, 0) * financing_rate / 252
stock_return = gross_stock_return - trade_cost - financing
```

如果你的回测引擎用固定杠杆参数，应注意：v3.1 的 Band 版本是动态暴露，股票腿应按 `stock_exposure` 控制，而不是固定 1.25x。

## 7. 511010 国债 ETF 轮动

股票腿完成后，再做 regime 轮动：

```python
bond_ret = close_511010.pct_change()

if dist_lagged > 0:
    final_return = stock_return
else:
    final_return = bond_ret
```

含义：

- BULL: 买 AmihudIlliq 股票组合。
- BEAR: 买 `511010` 国债 ETF。

ETF 交易成本：

```text
etf_buy_cost = 0.0005
etf_sell_cost = 0.0005
```

若只做净值级复现，可以先用 `511010` 日收益替代 BEAR 股票收益；若做交易级模拟，需要按 ETF 换仓扣成本。

## 8. 统计窗口

推荐：

```text
data_start = 2010-01-01
stats_start = 2018-01-01
```

原因：

- 2010 起用于因子、择时和持仓状态预热。
- 2018 起作为主统计窗口。
- 压力测试可直接统计 2010 起。

指标：

```python
annual = daily_return.mean() * 252
vol = daily_return.std() * sqrt(252)
sharpe = annual / vol
nav = (1 + daily_return).cumprod()
maxdd = (nav / nav.cummax() - 1).min()
calmar = annual / abs(maxdd)
```

## 9. 最小完整伪代码

```python
close, volume, raw_close = load_a_share_data("2010-01-01")
amount = volume * 100 * raw_close
bond_ret = load_close("511010").pct_change()

# AmihudIlliq
ret = close.pct_change()
amihud = (ret.abs() / (amount + 1)).rolling(20).mean()
factor = row_zscore(row_mad_clip(amihud, 5)).shift(1)

# Salience Veto
veto_score = salience_covariance_veto(close, W=20, theta=0.1, delta=0.7).shift(1)

# PureTrend Band
small_mask = amount.rolling(20).mean().rank(axis=1, pct=True) < 0.5
small_ret = (ret * small_mask).sum(axis=1) / small_mask.sum(axis=1)
small_nav = (1 + small_ret.fillna(0)).cumprod()
dist = small_nav / small_nav.rolling(16).mean() - 1
dist_lagged = dist.shift(1)
exposure = ((1 + dist_lagged * 8).clip(0, 1.5) * (dist_lagged > 0)).fillna(0)

# Rebalance
weights = {}
for date in factor.dropna(how="all").index[::20]:
    eff = next_trading_day(date)
    f = factor.loc[date].dropna()
    v = veto_score.loc[date].reindex(f.index).dropna()
    f = f.reindex(v[v > v.quantile(0.30)].index).dropna()
    selected = f.nlargest(25).index
    weights[eff] = equal_weight(selected)

stock_ret = backtest_t_plus_1(
    close=close,
    target_weights=weights,
    exposure=exposure,
    buy_cost=0.00225,
    sell_cost=0.00275,
    financing_rate=0.065,
)

common = stock_ret.index.intersection(bond_ret.index).intersection(dist_lagged.dropna().index)
final_ret = where(dist_lagged.loc[common] > 0, stock_ret.loc[common], bond_ret.loc[common])
final_ret = final_ret.loc["2018-01-01":]
```

## 10. 复现验收

同等数据平台应大致接近：

```text
annual ≈ +37.8%
maxdd ≈ -12.0%
sharpe ≈ 2.1
calmar ≈ 3.2
```

合理误差来自数据源差异、退市股覆盖、停牌处理、`511010` 复权方式、交易日历和 ETF 成本处理。

## 11. 常见错误

以下错误会导致结果不可比：

- 股票池只有当前仍上市股票，存在幸存者偏差。
- 用后复权价格计算 `amount`。
- 忘记 `factor.shift(1)`、`veto_score.shift(1)` 或 `dist.shift(1)`。
- veto 后不补满 25 只，变成隐性降仓。
- 用固定 1.25x 复现 Band 版本。
- BEAR 阶段用现金收益代替 `511010`。
- 不扣股票成本和融资成本。
- 把 Salience Veto 当独立策略看净值。

## 12. 版本对照

```text
v1.0 = AmihudIlliq 20d + PureTrend MA16 + 固定 1.25x
v3.0 = AmihudIlliq 20d + Band Timing + 511010 Rotation
v3.1 = v3.0 + Salience Veto 30%
```

当前最佳是 `v3.1`。
