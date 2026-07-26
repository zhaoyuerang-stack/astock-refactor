# 因子研究系统操作手册

这个目录是一套 A 股因子研究、数据校验和策略运行系统。当前主线已经迁移到 `data_lake/` 真实口径：

- 策略：AmihudIlliq 20d + Salience Veto 30% + PureTrend MA16 Band 动态敞口(0~1.5x) + 511010 国债ETF轮动
- 目标：单母策略入册 年化>15% / 回撤<20%；项目级满意线 年化≥20% & 夏普≥1.0，卓越线 年化≥28% 或 卡玛≥1.6(原 35%/15% 锚定 data_full 水分，已退役)
- 旧口径结果：`data_full` 年化约 40.4%，但含幸存者偏差水分
- 当前生产基线：`data_lake` 2018-2026 年化约 37.8%，最大回撤约 -12.0%
- 阶段状态：阶段 0 已关闭；当前在阶段 1 多目标工厂化
- 版本登记：`strategy_versions.json`

## Python 环境

Apple Silicon macOS 上建议先建本地 venv，再装项目依赖和开发工具：

```bash
cd /Users/kiki/astcok/factor_research
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -e '.[dev]'
```

`pyproject.toml` 集中管理 `pytest` / `ruff` / `mypy` 配置，`requirements.txt` 保留运行时兼容清单。若必须使用系统 Python，再按 `requirements.txt` 里的提示加 `--break-system-packages`。

## 目录说明

- `run_daily.py`：每日生产入口，更新数据、校验、生成信号和持仓 JSON。
- `api/`：FastAPI 只读/受控动作接口，默认本机端口 `8011`。
- `strategy_lake.py`：真实口径策略复测入口。
- `validate_final.py`：全市场日线数据质量校验入口。
- `test_load_lake.py`：轻量验证数据湖加载层。
- `strategy_registry.py`：策略版本登记和对比。
- `core/`：统一回测内核，负责 `data_lake` 加载、因子、择时、真实成本、融资成本和指标。
- `factory/`：阶段 1 策略工厂骨架，负责候选空间、多目标评估和 Pareto 排序。
- `scripts/data/`：数据构建、拉取和增量更新脚本。
- `scripts/repair/`：数据修复和重校验脚本。
- `scripts/research/`：研究验证、模拟、成本敏感性和旧研究遗留脚本。
- `data_lake/`：新版数据湖，包含日线、周线、财务、交易日历和质量报告。
- `lake/`：数据湖加载、聚合、校验和数据源模块。
- `results/`：策略配置、净值图、进化历史等结果文件。
- `signals/`：每日信号输出。
- `paper/`：自动模拟盘账户、交易流水和净值曲线；只模拟，不连接真实券商。
- `reports/`：报告和导出文件。

## 日常查看信号

在项目目录运行：

```bash
cd /Users/kiki/astcok/factor_research
python3 run_daily.py --no-update
```

输出重点看：

- `最新交易日`：确认数据日期是否符合预期。
- `小盘指数 vs MA16` / `Band exposure`：确认当前动态敞口、持仓或空仓。
- `调仓判断`：确认今日是否需要调仓。
- `操作` 和 `持仓`：用于生成当日执行参考。

结果会保存到：

```text
signals/YYYY-MM-DD.json
```

如需联网增量更新数据后再出信号：

```bash
cd /Users/kiki/astcok/factor_research
python3 run_daily.py
```

## 定时增量更新

生产定时入口不要直接裸跑 `run_daily.py`，使用包装脚本先更新数据、校验新鲜度，再生成信号：

```bash
cd /Users/kiki/astcok/factor_research
/usr/bin/python3 scripts/ops/scheduled_daily_update.py
```

dry run 不会更新数据或生成信号，只验证锁、日历、日志和报告链路：

```bash
cd /Users/kiki/astcok/factor_research
/usr/bin/python3 scripts/ops/scheduled_daily_update.py --dry-run
```

安装 macOS `launchd` 定时任务：

```bash
cd /Users/kiki/astcok/factor_research
scripts/ops/install_launchd_jobs.sh
```

已配置的任务：

- `com.astcok.daily-update`: 周一到周五本机 00:30 和 01:30 触发;脚本内部按中国时间判断,未到 16:30 自动跳过,当天成功后第二次自动跳过。
- `com.astcok.weekly-maintenance`: 周日本机 02:30 执行，重建周/月线、刷新不复权价、完整质量校验。

手动触发：

```bash
launchctl kickstart -k gui/$UID/com.astcok.daily-update
launchctl kickstart -k gui/$UID/com.astcok.weekly-maintenance
```

查看日志和报告：

```text
logs/daily_update/YYYY-MM-DD.log
logs/daily_update/launchd.out.log
logs/daily_update/launchd.err.log
reports/ops/daily_update/YYYY-MM-DD.json
```

包装脚本会在数据落后应有交易日时跳过信号生成，避免用旧数据覆盖 `signals/state.json`。
当信号生成成功时，包装脚本会继续运行 paper_trade 自动模拟盘；真实账户仍不自动下单，Web/Obsidian 输出只作为人工跟单参考。

## Web/API 看板

本机产品看板由 FastAPI + Next.js 组成：

```bash
cd /Users/kiki/astcok/factor_research
python3 -m uvicorn api.main:app --port 8011 --reload

cd /Users/kiki/astcok/web
npm run dev
```

前端默认调用 `http://127.0.0.1:8011`，页面覆盖总览、数据、风险、组合、模拟盘、实验室和设置。安装 launchd 后，`com.astcok.api` 和 `com.astcok.web` 会分别常驻 `:8011` 和 `:3000`。

## 复测真实口径策略

运行 `data_lake` 口径复测：

```bash
cd /Users/kiki/astcok/factor_research
python3 strategy_lake.py
```

输出会对比：

- 2018-2026：真实成本 + `data_lake` 口径
- 2010-2026：压力测试，包含 2015 股灾和 2017 小盘崩盘

## 校验数据质量

运行最终数据质量校验：

```bash
cd /Users/kiki/astcok/factor_research
python3 validate_final.py
```

结果会保存到：

```text
data_lake/quality_report.json
```

重点看：

- `clean_ratio`：干净数据比例。
- `issue_breakdown`：真实数据问题分布。
- `flagged`：需要排查的股票代码。

当前质量报告显示约 99.92% 数据干净，仅少数股票存在 `价格跳变>50%` 问题。

## 验证数据湖加载层

用于确认新版 `data_lake` 的价量、财务防未来函数对齐和估值自算是否正常：

```bash
cd /Users/kiki/astcok/factor_research
python3 test_load_lake.py
```

如果输出 `加载层验证通过`，说明数据湖加载层基础可用。

## 运行策略工厂

阶段 1.1 最小工厂入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_factory.py
```

结果会保存到：

```text
reports/factory_stage1_1.json
```

阶段 1.2 批量网格扫描入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_factory.py --mode grid --limit 60 --top 20
```

结果会保存到：

```text
reports/factory_stage1_2.json
```

阶段 1.3 最小 NSGA-II 搜索入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_factory.py --mode nsga2 --population 12 --generations 2 --top 20
```

结果会保存到：

```text
reports/factory_stage1_3.json
reports/factory_stage1_3_history.json
```

当前版本支持确定性候选网格、最小 NSGA-II 多目标搜索、多目标评估、基础质量门槛和 Pareto 排序。

阶段 1.4 生态位搜索入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_factory.py --mode nsga2 --niche non_size --population 8 --generations 2 --top 20
```

结果会保存到：

```text
reports/factory_stage1_4_non_size.json
reports/factory_stage1_4_non_size_history.json
reports/factory_stage1_4_non_size_review.json
```

`--niche` 可选 `all`、`non_size`、`quality_location`、`reversal_liquidity`、`defensive_liquidity`、`trend_quality`、`fundamental_quality`、`fundamental_value`、`orthogonal_fundamental`、`fundamental_industry`、`fundamental_change`、`fundamental_value_pctile`、`fundamental_regime`。`*_review.json` 只保留非纯小盘、相关性不过高、样本外非负且通过基础前沿门槛的复核候选。

fundamental niche 使用 `data_lake/fundamental_batch.parquet` 中按 `avail_date` 对齐的财务质量/成长/价值因子。估值类因子用 `price/daily_raw` 不复权价计算;如果原始价缺失才退回复权 `close`。工程化 fundamental niche 额外使用行业内排名、行业中性、财务变化率、估值时间分位和质量+价值 regime 过滤。

阶段 1.5 复核审计入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/review_shortlist.py reports/factory_stage1_4_non_size_review.json
```

如果需要对完整报告做诊断审计：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/review_shortlist.py reports/factory_stage1_4_non_size.json --include-all --out reports/factory_stage1_5_non_size_audit.json
```

审计会复测 2018 主样本、2023 样本外、2010 压力样本,并跑成本上浮 50% 敏感性;`registry_precheck=true` 才值得进入台账预审。`incubate=true` 表示低相关/有逻辑/局部有潜力,只进孵化池继续研究,不能直接入册。

阶段 1.6 岛屿模型入口：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_islands.py --smoke --population 4 --generations 2
```

正式配置去掉 `--smoke`：

```bash
cd /Users/kiki/astcok/factor_research
python3 factory/run_islands.py
```

结果会保存到：

```text
reports/islands/*/front.json
reports/islands/*/review.json
reports/islands/*/audit.json
reports/islands/candidate_batch.json
reports/islands/incubation_pool.json
reports/islands/summary.json
```

`candidate_batch.json` 只保留 `registry_precheck=true` 的最终 Pareto 候选母策略批。`incubation_pool.json` 保留 `incubate=true` 的弱候选,用于后续再校准/降频/组合研究,不进入台账预审。`--create-worktrees` 可为每个岛创建 `.worktrees/island_name` 作为代码隔离锚点;搜索仍在主工作区运行,因为 `data_lake/` 是忽略数据,新 worktree 默认没有数据湖。

阶段 1.13 孵化池自进化入口：

```bash
cd /Users/kiki/astcok/factor_research
/usr/bin/python3 factory/evolve_incubation.py \
  --input reports/islands_fundamental_1_12_parallel/incubation_pool.json \
  --out-dir reports/incubation_evolution_1_13 \
  --generations 3 \
  --population 12 \
  --survivors 6
```

这个程序从孵化池候选出发,每代做本地规则化变异(因子、权重、top_n、调仓频率、杠杆),然后重新走 2018/2023/2010 三段审计和成本上浮敏感性。它不调用 OpenAI API,不会因为本地长跑本身触发模型 429 限流。

本机当前 `/opt/homebrew/bin/python3` 缺 pandas/pyarrow;回测和 parquet 读取优先用 `/usr/bin/python3`。

## 当前最重要的下一步

阶段 0 已收束到统一 `core/` 内核、`data_lake` 口径和真实成本模型。旧 `data_full/`、`data/` 缓存已清理。当前阶段 1 已有确定性网格、NSGA-II、生态位复核、复核审计、孵化池、岛屿编排、扩展非小盘价量因子池、fundamental 正交因子池、fundamental 因子工程升级和孵化池自进化。当前小规模岛屿搜索暂无 `registry_precheck=true` 候选,验收条件尚未满足;孵化池已有非小盘弱候选。

建议顺序：

1. 用 `factory/evolve_incubation.py` 对 1.12 的 `incubation_pool.json` 做按代自进化,优先观察 `fund_profit_growth_delta`、行业 BP 价值和估值分位组合。
2. 对自进化输出的 `candidate_batch.json` 做台账预审;仍未过审的继续留在 `incubation_pool.json`。
3. 继续以 `strategy_lake.py` 和 `strategy_versions.json` 为准登记新版本。
4. 用 `run_daily.py --no-update` 验证每日信号流程。

## 常见结果文件

- `data_lake/quality_report.json`：数据湖质量报告。
- `signals/YYYY-MM-DD.json`：每日信号。
- `signals/state.json`：每日流程维护的真实持仓状态，包含当前仓位、上次调仓日和最近持仓。
- `strategy_versions.json`：策略版本登记。
- `reports/`：交易明细、执行层模拟等报告。
- `results/`：阶段 0 早期研究产物，仅作历史参考，不作为主线口径。

## 注意事项

- 本仓库已 git 化；数据湖和大体量运行产物不入库，重要阶段改动用提交固定。
- 回测结果不是投资建议，实盘前还需要做更严格的交易成本、容量、停牌、涨跌停、滑点和组合换手验证。
- 当前最新数据日期需要以 `run_daily.py` 或 `strategy_lake.py` 输出为准，运行前应确认数据是否已更新到目标日期。
