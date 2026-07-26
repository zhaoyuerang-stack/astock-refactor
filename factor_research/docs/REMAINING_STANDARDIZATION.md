# 数据基础设施标准化 — 剩余收尾项

> 状态：85% 完成。以下 6 项为达到 100% 标准化所需。
> 创建日期：2026-06-05

---

## 1. 迁移 39 个 `core.backtest` 引用方

**问题**：`grep -r "from core.backtest import" --include="*.py"` 返回 39 处引用。虽然 `core/backtest.py` 现在是纯转发层，但所有调用方应该直接从 canonical 模块导入。

**目标**：所有引用方改为直接导入：
- `CostModel` → `from core.engine import CostModel`
- `StrategyConfig` / `run_small_cap_strategy` / `latest_signal` → `from strategies.small_cap import ...`
- `load_price_panels` → `from strategies.small_cap import load_price_panels` 或 `from lake.load_lake import ...`
- `small_cap_factor` / `small_cap_timing` → `from factors.small_cap import ...`
- `metrics` / `yearly_returns` → `from engine.metrics import ...`
- `safe_zscore` / `mad_clip` → `from factors.utils import ...`

**涉及文件（需逐个检查）**：
```bash
# research 脚本（约 20+ 个）
scripts/research/cost_sensitivity.py
scripts/research/decay_monitor.py
scripts/research/hmm_exit_smallcap.py
scripts/research/hmm_exit_smallcap_optimize.py
scripts/research/hmm_stress_guard_smallcap.py
scripts/research/live_readiness.py
scripts/research/microstructure_overlay_experiment.py
scripts/research/mkt_diffusion_robustness.py
scripts/research/momentum_probe.py
scripts/research/newstock_exposure.py
scripts/research/paper_replay.py
scripts/research/regime_decompose.py
scripts/research/reverify_v2.py
scripts/research/simulate_2025.py
scripts/research/state_transition_execution_explore.py
scripts/research/state_transition_lead_experiment.py
scripts/research/state_transition_signal_search.py
scripts/research/tradability.py

# 生产/工厂模块
scripts/ops/paper_trade.py
factory/incubation.py
factory/objectives.py
factory/review.py
factory/search_space.py
factory/timing.py
factory/timing_experiment.py
run_daily.py
strategy_lake.py
test_engine.py
```

**验证**：`grep -r "from core.backtest import" --include="*.py" | wc -l` → 0

**预估**：2-3h

---

## 2. `factory/` 模块接入 `app_config`

**问题**：`factory/nsga2.py` 和 `factory/self_evolution.py` 中的搜索参数仍硬编码：

```python
# factory/nsga2.py:35
TOP_N_CHOICES = [15, 20, 25, 40, 60, 80, 120]
LEVERAGE_CHOICES = [1.0, 1.25]

# factory/self_evolution.py:22
TOP_N_CHOICES = [15, 20, 25, 40, 60, 80, 120, 160]
LEVERAGE_CHOICES = [1.0, 1.15, 1.25]
```

**目标**：这些列表从 `app_config/settings.py::FactoryConfig` 读取。注意 nsga2 和 self_evolution 的选择范围不同，需要评估是否统一或保留差异。

**涉及文件**：
- `factory/nsga2.py`
- `factory/self_evolution.py`

**验证**：修改 `app_config/settings.py` 中 `FactoryConfig.top_n_choices` 后，factory 搜索范围同步变化。

**预估**：30min

---

## 3. 把 `fetch_raw_close --incremental` 纳入 `update_lake.py`

**问题**：不复权 raw 的增量更新仍是独立入口：
```bash
python3 scripts/data/fetch_raw_close.py --incremental
```

**目标**：`update_lake.py --prices` 在更新后复权 daily 后，自动调用 `fetch_raw_close.update_raw_prices()` 更新不复权 raw，然后统一 compact。

**涉及文件**：
- `scripts/data/update_lake.py`
- `scripts/data/fetch_raw_close.py`

**验证**：`python3 scripts/data/update_lake.py --prices` 后 `daily_raw_all.parquet` 的 max(date) 等于最新交易日。

**预估**：30min

---

## 4. 简化 `scheduled_*.py` 为单命令调用

**问题**：`scripts/ops/scheduled_daily_update.py` 和 `scripts/ops/scheduled_weekly_maintenance.py` 仍有自己的独立逻辑，没有完全收敛到 `update_lake.py`。

**目标**：
- `scheduled_daily_update.py` → 只调用 `update_lake.py --prices --fundamental`
- `scheduled_weekly_maintenance.py` → 只调用 `update_lake.py --weekly-monthly --validate`

**涉及文件**：
- `scripts/ops/scheduled_daily_update.py`
- `scripts/ops/scheduled_weekly_maintenance.py`

**验证**：两个文件各 < 50 行，逻辑单一。

**预估**：30min

---

## 5. 创建 `app_config/settings.yaml` 默认配置

**问题**：目前只有 Python 配置框架（`app_config/settings.py`），没有 YAML 配置文件。用户还不能通过修改配置文件调整全局参数。

**目标**：创建 `app_config/settings.yaml`：

```yaml
strategy:
  family: "small-cap-size"
  version: "v2.0"
  start: "2018-01-01"
  size_window: 60
  timing_ma: 16
  top_n: 25
  rebalance_days: 20
  leverage: 1.25

cost:
  buy_cost: 0.00225
  sell_cost: 0.00275
  financing_rate: 0.065

data:
  warmup_start: "2010-01-01"
  default_start: "2018-01-01"

factory:
  top_n_choices: [15, 20, 25, 40, 60, 80, 120]
  leverage_choices: [1.0, 1.25]
  review_corr_threshold: 0.50
```

**注意**：yaml 模块可能未安装，需要 graceful fallback（已在 `settings.py` 中处理）。

**验证**：修改 YAML 中 `strategy.top_n` 为 30，`run_daily.py` 持仓数量变化。

**预估**：15min

---

## 6. （可选）删除逐只 parquet，只保留大表

**问题**：价量数据目前两种格式并存：逐只 parquet + 大表 parquet。加载层优先读大表、fallback 到逐只，但维护两套格式增加了复杂度。

**目标**：确认大表完全可用后，删除 `data_lake/price/daily/` 和 `data_lake/price/daily_raw/` 逐只文件，只保留 `daily_all.parquet` 和 `daily_raw_all.parquet`。

**风险**：
- 增量更新逻辑需要改为直接更新大表（而非逐只更新再 compact）
- 某些工具可能直接读取逐只文件

**建议**：此项风险较高，建议在其他 5 项稳定后再执行，且需要先验证全链路。

**涉及文件**：
- `scripts/data/update_lake.py`（增量更新逻辑需改为更新大表）
- `scripts/data/build_lake.py`（全量下载逻辑需改为直接写大表）
- `lake/compact.py`（可能不再需要）

**验证**：删除逐只文件后 `run_daily.py` / `strategy_lake.py` / `test_engine.py` 全部正常。

**预估**：1h（含验证）

---

## 执行顺序建议

按依赖关系和风险从低到高：

```
5. 创建 settings.yaml          (15min, 无风险)
2. factory 接入 app_config     (30min, 低风险)
3. raw 增量纳入 update_lake    (30min, 中低风险)
4. 简化 scheduled_*.py         (30min, 低风险)
1. 迁移 39 个 core.backtest   (2-3h, 中风险, 涉及文件多)
6. 删除逐只 parquet            (1h, 高风险, 最后做)
```

**总计：约 5-6h**

---

## 当前验证命令（每次改动后运行）

```bash
# 1. 引擎测试
python3 test_engine.py

# 2. 数据层测试
python3 tests/test_data_layer.py

# 3. 端到端测试
python3 tests/test_e2e.py

# 4. 全部测试
bash scripts/test_all.sh

# 5. 生产入口
python3 run_daily.py --no-update

# 6. 数据质量
python3 validate_final.py
```
