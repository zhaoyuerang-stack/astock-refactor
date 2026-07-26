# factor_store 分区：panels cache vs 资产

`data_lake/factor_store/` 分两层语义（湖目录本身不入库，本说明在源码侧）：

| 子目录 | 语义 | 可否 GC | 可否作证据 |
| --- | --- | --- | --- |
| `panels/` | **cache 区**：AutoResearch DSL 因子面板磁盘缓存 | ✅ 可删 | ❌ |
| `manifests/` | **资产区**：正式登记的因子 manifest | ❌ 需审计 | ✅ |
| `scores/` | **资产区**：IC/ICIR 等评分 | ❌ 需审计 | ✅ |

## panels/ 缓存契约

- 写入方：`factors/autoresearch_dsl.py`（`cache_mode != "memory"`）
- 缓存 key：`name + params + data_signature + 源码 hash(_src…) + 数据 mtime(_mt…) [+ feature_mask (_fm…)]`
- 改因子实现 → 源码 hash 变 → 自动 miss，不静默复用旧值
- **feature mask（ADR-040 默认强制, 上游污染卫生）**：
  - **默认 on**（`compute_dsl_factor` / 搜索入口）：close/volume 先按涨跌停+停牌 mask 再 rolling；路径带 `_fmfeat_tradable_v1`（或 `vol_floor_v1`）
  - 显式 dirty（仅测试）：`feature_mask=\"off\"` 或 env `ASTOCK_FEATURE_MASK=off` → **不写** `_fm*` 后缀
  - clean 与 dirty 绝不可共享同一 parquet
  - `factor_store.build_factor_id` / `save_factor_panel` 同样接收 `feature_mask_version`，写入 manifest.params
  - 成交侧另见 `BacktestConfig.enforce_fill_tradable`（默认 True，与 feature mask 独立）
- GC：`python3 scripts/ops/gc_factor_panel_cache.py`（默认 dry-run；`--apply` 删非当前 mtime 代）

2026-07-12 dry-run 本机示例：keep ≈ 350 文件 / 18GB，delete ≈ 630 文件 / 21GB（旧 mtime 代）。
