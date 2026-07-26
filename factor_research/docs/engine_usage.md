# BacktestEngine 使用指南

> 统一回测引擎 — 标准化输入/输出，打通因子研究 → 策略回测 → 绩效评估全流程。

## 快速开始

```python
from core.engine import BacktestEngine, Signal, BacktestConfig, PricePanel, CostModel
from core.backtest import load_price_panels, small_cap_factor, small_cap_timing, build_rebalance_weights

# 1. 加载数据
close, volume, amount = load_price_panels("2018-01-01")
prices = PricePanel(close=close, volume=volume, amount=amount)

# 2. 创建引擎
engine = BacktestEngine(prices=prices, config=BacktestConfig())

# 3. 构建信号
factor = small_cap_factor(amount, window=60)
timing, _, _ = small_cap_timing(close, amount, ma_window=16)
weights = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
signal = Signal(weights=weights, timing=timing)

# 4. 运行回测
result = engine.run(signal)

# 5. 查看结果
print(result.metrics)      # {'annual': 0.22, 'maxdd': -0.20, 'sharpe': 1.38, ...}
print(result.summary())    # 格式化字符串
print(result.yearly_returns)
```

---

## Signal 的三种模式

`Signal` 是统一输入，支持三种策略表达方式：

### 模式 A：预计算权重（生产环境）

适合信号已经生成、只需回测验证的场景：

```python
signal = Signal(
    weights=weight_df,          # date × code DataFrame
    timing=timing_series,       # 日度 exposure [0,1]，可选
    family="small-cap-size",
    version="v2.0",
)
result = engine.run(signal)
```

### 模式 B：原始因子值（研究环境）

适合因子研究阶段，引擎内部自动转成 top-n 权重：

```python
signal = Signal(
    factor=factor_df,           # date × code DataFrame
    top_n=25,                   # 选前25只
    direction=1,                # 1=多头 top，-1=多头 bottom
    rebalance_freq="20D",       # 每20个交易日调仓
    timing=timing_series,
)
result = engine.run(signal)
```

> 内部逻辑与 `build_rebalance_weights()` 完全一致：取因子非空日期 → 每 N 日调仓 → 生效日为下一交易日。

### 模式 C：因子构建器（工厂/自动化）

适合策略工厂，延迟构建因子：

```python
def my_factor_builder(prices, config):
    # prices: PricePanel (close, volume, amount, raw_close)
    # config: 自定义参数字典
    return -np.log(prices.amount.rolling(60).mean() + 1)

signal = Signal(
    factor_builder=my_factor_builder,
    factor_config={"window": 60},
    top_n=25,
    rebalance_freq="20D",
)
result = engine.run(signal)
```

---

## 因子合成 → 回测流水线

```python
from engine.composer import equal_weight, ic_weight, pca_composite, to_signal

# 合成多因子
factors = {
    "size": size_factor,
    "value": value_factor,
    "quality": quality_factor,
}
composite = equal_weight(factors)          # 或 ic_weight(factors, forward_ret) / pca_composite(factors)

# 直接转成 Signal 回测
signal = to_signal(composite, top_n=25, rebalance_freq="20D")
result = engine.run(signal)
print(result.metrics)
```

---

## IC 分析

```python
# 计算因子 IC
ic_result = engine.run_ic_analysis(factor, forward_days=1, method="rank")
print(ic_result["ic_summary"])
# {'IC_mean': 0.03, 'IC_std': 0.12, 'ICIR': 0.25, ...}
```

## 分层回测

```python
# 五分组分层收益
stratify = engine.run_stratify(factor, forward_days=1, n_quantile=5)
print(stratify.head())
#                  Q1        Q2        Q3        Q4        Q5
# date
# 2018-01-02  0.0012   -0.0003    0.0001   -0.0005    0.0021
```

---

## 工厂接入：新因子族

旧工厂（硬编码小盘）：

```python
# 旧代码 —— 必须改 evaluator.py 才能接新因子
from core.backtest import small_cap_factor, small_cap_timing  # 硬编码
```

新工厂（通用接口）：

```python
from factory.evaluator import prepare_context_engine, evaluate_candidate_engine
from factory.search_space import Candidate

# 准备上下文（一次）
engine, library, baseline = prepare_context_engine("2018-01-01")

# 定义候选（自带因子/择时构建器）
candidate = Candidate(
    family="my-factor",
    version="v1.0",
    desc="自定义因子",
    factors=["my_custom_factor"],   # 在 factor_library 中注册
    weights=[1.0],
    top_n=25,
    rebalance_days=20,
    leverage=1.25,
    timing="my_timing",             # 在 build_timing 中注册
)

# 评估
result_dict = evaluate_candidate_engine(candidate, engine, library, baseline)
print(result_dict["annual"], result_dict["sharpe"], result_dict["maxdd"])
```

---

## 旧 API → 新 API 对照表

| 场景 | 旧 API | 新 API |
|------|--------|--------|
| 回测（权重） | `backtest_weights(close, weights, timing)` | `engine.run(Signal(weights=weights, timing=timing))` |
| 回测（因子） | `build_rebalance_weights(factor, close, 25, 20)` + `backtest_weights(...)` | `engine.run(Signal(factor=factor, top_n=25))` |
| 绩效指标 | `metrics(ret)` | `result.metrics` |
| 年度收益 | `yearly_returns(ret)` | `result.yearly_returns` |
| IC 分析 | `calc_ic(factor, forward_ret)` | `engine.run_ic_analysis(factor)` |
| 分层回测 | `stratify_return(factor, forward_ret)` | `engine.run_stratify(factor)` |
| 因子合成回测 | `equal_weight(factors)` → 手动衔接 | `equal_weight(factors)` → `to_signal(...)` → `engine.run(...)` |
| 工厂评估 | `evaluate_candidates(candidates)` | `evaluate_candidates(candidates)`（已内部走 engine） |

---

## 性能提示

1. **PricePanel 复用**：`prepare_context_engine()` 加载一次，评估多个候选时复用同一个 `engine`。
2. **缓存**：`prepare_context` 有 `@lru_cache(maxsize=8)`，相同 start/warmup 参数会命中缓存。
3. **避免循环创建 engine**：批量评估时，先 `prepare_context_engine()` 一次，再循环调用 `evaluate_candidate_engine()`。

---

## 兼容层

所有旧 API（`core/backtest.py`、`engine/backtest.py`、`factory/evaluator.py`）保留兼容层，现有脚本**零改动**即可运行。迁移是渐进式的：新代码用 engine，旧代码逐步替换。
