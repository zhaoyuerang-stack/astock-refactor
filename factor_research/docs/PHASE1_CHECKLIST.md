# Phase 1 代码清理 — 完成 Check List

> 清理回测兼容层代码，消除 legacy 实现，统一到底层 `core.engine.BacktestEngine`。

---

## ✅ 任务 1：清理 `core/backtest.py` 兼容层 → `core/engine.py`

| 检查项 | 状态 | 验证命令 |
|--------|------|----------|
| `core/backtest.py` 无独立实现，为纯转发层 | ✅ | `grep "def " core/backtest.py` 无结果 |
| 所有实现已迁移到 canonical 模块 | ✅ | 见下表 |
| `test_engine.py` 6 项测试通过 | ✅ | `python3 test_engine.py` |
| `run_daily.py --no-update` 正常输出 | ✅ | `python3 run_daily.py --no-update` |
| `strategy_lake.py` 正常输出 | ✅ | `python3 strategy_lake.py` |
| `validate_final.py` 质量校验通过 | ✅ | `python3 validate_final.py` |

### 实现迁移路径

| 原位置 | 新位置 | 说明 |
|--------|--------|------|
| `core/backtest.py::CostModel` | `core/engine.py::CostModel` | 已在 engine 中定义 |
| `core/backtest.py::StrategyConfig` | `strategies/small_cap.py::StrategyConfig` | 策略配置 |
| `core/backtest.py::load_price_panels` | `strategies/small_cap.py::load_price_panels` | 数据加载 |
| `core/backtest.py::small_cap_factor` | `factors/small_cap.py::small_cap_factor` | 因子计算 |
| `core/backtest.py::small_cap_timing` | `factors/small_cap.py::small_cap_timing` | 择时信号 |
| `core/backtest.py::build_rebalance_weights` | `strategies/small_cap.py::build_rebalance_weights` | 权重构建 |
| `core/backtest.py::backtest_weights` | `strategies/small_cap.py::backtest_weights` | 代理到 engine（deprecated） |
| `core/backtest.py::run_small_cap_strategy` | `strategies/small_cap.py::run_small_cap_strategy` | 策略主入口 |
| `core/backtest.py::latest_signal` | `strategies/small_cap.py::latest_signal` | 实盘信号 |
| `core/backtest.py::metrics` | `engine/metrics.py::metrics` | 绩效指标 |
| `core/backtest.py::yearly_returns` | `engine/metrics.py::yearly_returns` | 年度收益 |
| `core/backtest.py::safe_zscore` | `factors/utils.py::safe_zscore` | 工具函数 |
| `core/backtest.py::mad_clip` | `factors/utils.py::mad_clip` | 工具函数 |

### 新建文件

- `factors/utils.py` — 因子计算工具函数
- `factors/small_cap.py` — 小盘因子和择时
- `strategies/small_cap.py` — 策略配置、执行和实盘信号
- `engine/metrics.py` — 绩效指标计算

---

## ✅ 任务 2：清理 `engine/` 目录遗留兼容层

| 检查项 | 状态 | 验证命令 |
|--------|------|----------|
| `engine/backtest.py` 已删除 | ✅ | `test -f engine/backtest.py` → 不存在 |
| `engine/backtest.py` 无残留引用 | ✅ | `grep -r "from engine.backtest import"` 无结果 |
| `engine/portfolio.py::top_n_portfolio` 已删除 | ✅ | `grep "def top_n_portfolio" engine/portfolio.py` 无结果 |
| `engine/portfolio.py::calc_portfolio_return` 已删除 | ✅ | `grep "def calc_portfolio_return" engine/portfolio.py` 无结果 |
| `engine/__init__.py` 已更新 | ✅ | 只导出 `performance_metrics`, `to_signal` |
| `test_engine.py` 通过 | ✅ | `python3 test_engine.py` |

---

## ✅ 任务 3：清理 `factory/evaluator.py` 遗留 API

| 检查项 | 状态 | 验证命令 |
|--------|------|----------|
| `run_candidate_returns` 已删除 | ✅ | `grep "def run_candidate_returns" factory/evaluator.py` 无结果 |
| `evaluate_candidate` 已统一为 engine 路径 | ✅ | 接收 `(candidate, engine, library, baseline_result)` |
| `evaluate_candidates_with_context` 已删除 | ✅ | `grep "def evaluate_candidates_with_context"` 无结果 |
| `prepare_context` 返回 engine 路径结果 | ✅ | 返回 `(engine, library, baseline_result)` |
| `*_engine` 后缀函数已重命名 | ✅ | `run_candidate_engine` → `run_candidate`, `evaluate_candidate_engine` → `evaluate_candidate` |
| 所有调用方已更新 | ✅ | 见下表 |

### 调用方更新清单

| 文件 | 更新内容 |
|------|----------|
| `factory/islands.py` | `prepare_context` + `run_candidate` |
| `factory/incubation.py` | `prepare_context` + `run_candidate` |
| `factory/review.py` | `prepare_context` + `evaluate_candidate` |
| `factory/run_factory.py` | `prepare_context` + `evaluate_candidates` |
| `factory/timing_experiment.py` | `prepare_context` + `evaluate_candidate` |

---

## 🏁 Phase 1 完成标准验证

```bash
# 1. 测试通过
python3 test_engine.py
# 预期: 6/6 通过

# 2. 生产入口正常
python3 run_daily.py --no-update
# 预期: 输出信号文件

# 3. 回测入口正常
python3 strategy_lake.py
# 预期: 输出两段回测结果

# 4. 数据质量校验正常
python3 validate_final.py
# 预期: clean_ratio ~99.9%

# 5. 无遗留 deprecated 函数
python3 -c "
from core.backtest import *
from factory.evaluator import *
print('All imports OK')
"

# 6. 无 engine/backtest.py
ls engine/backtest.py 2>/dev/null || echo 'engine/backtest.py removed ✓'
```

---

## 📊 改动统计

| 类别 | 数量 |
|------|------|
| 新建文件 | 4 (`factors/utils.py`, `factors/small_cap.py`, `strategies/small_cap.py`, `engine/metrics.py`) |
| 重写文件 | 1 (`core/backtest.py`) |
| 删除文件 | 1 (`engine/backtest.py`) |
| 修改文件 | 9 (`engine/portfolio.py`, `engine/__init__.py`, `factory/evaluator.py`, `factory/islands.py`, `factory/incubation.py`, `factory/review.py`, `factory/run_factory.py`, `factory/timing_experiment.py`, `test_engine.py`) |
| 删除函数 | 6 (`top_n_portfolio`, `calc_portfolio_return`, `run_candidate_returns`, `evaluate_candidate` legacy, `evaluate_candidates_with_context`, `prepare_context` legacy) |
| 迁移函数 | 13 (见实现迁移路径表) |
