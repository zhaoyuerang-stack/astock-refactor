# 被丢信号处置结论(metasearch signal_flow_tracer 06-23 遗留,孤岛回收④)

> 日期:2026-07-03。来源:`metasearch/unused_signals.json`(06-23 全量重跑,333 个丢弃事件,
> 3 个 Band 式候选立「待查」后未销账)。本文逐个机械核查现状并给处置——
> **全部为披露/编排结论,不含有效性判断**(R-LLM-001;涉及 alpha 的验证走 probe/9-Gate)。

## 1. salience 族(`compute_salience_factors` 首输出) — ✅ 已接,销账

核查:`factors/illiquidity_components.py::salience_covariance_score` 在产;
`salience_covariance_veto` 是 `scheduled_factor_search` 审计/holdout 权重的 canonical veto。
06-23 事件源是 research 脚本(`salience_neutralized_backtest.py` 等)丢弃首输出,
属研究脚本局部用法,非生产信息损失。**处置:销账,无后续。**

## 2. `hmm_stress_probability` 后两输出(state_trace / stress_state_trace) — 诊断量,不立 probe

核查(`factors/market_stress.py:208-248`):三返回值 = (压力态后验概率,
逐日 argmax 三态轨迹, 各 refit 段被判定为"压力态"的状态编号)。消费者
(`verify_timing_scenarios.py`)只取 prob。被丢的两个:

- `state_trace`(三态 regime 标签):**市场级时序信号,非截面因子**,不可作 alpha probe。
  其潜在用途 = 与 WS6 `services/read/regime_audit.py` 的四维 regime 标签做**对照输入**
  (两套独立方法给出的 regime 分歧本身是有信息的审计量)。属可选增强,非缺口——
  regime 判定已有 canonical 权威(RegimeEngine),不应并行第二套判定口径。
- `stress_state_trace`(refit 间压力态编号轨迹):**模型稳定性诊断**——编号在相邻
  refit 间漂移 = HMM 状态识别不稳,是择时信号可信度的负面证据。建议:任何使用
  hmm prob 的择时研究报告应披露该轨迹的漂移率;不是信号本身。

**处置:结论落本文;不立 probe 任务(非截面 alpha 候选);若未来 regime 对照审计
立项,以本文为指针。**

## 3. `pg.pricing_gap` 本体(gap 连续值) — 06-23 结论部分陈旧;截面因子化归 Phase 3,不重复立项

核查:`factory/fundamental/pricing_efficiency.py::pricing_gap` 返回 (gap, state);
`services/read/fundamentals.py:193` **两者都消费**(逐股画像),06-23「本体丢」的事件源
是 `tests/test_fundamentals.py` 丢弃 gap——测试局部用法,生产画像未丢。**真实缺口**是
另一层:gap 只有逐股标量画像,从未**截面因子化**(预期差 = 基本面分 − 市场定价分,
天然是截面排序候选)。这正是 TASKS「产业基本面子系统 Phase 3」的既有范围
(本体预测分/预期差注册为 SHADOW 因子 → 影子 NAV → 9-Gate),**不重复立项**,
本文作为该项的补充证据指针。

## 4. 连带产物:资产负债表运营质量因子族(孤岛回收①,probe 就绪)

`factors/fundamental_quality.py` 已建(bargaining_power / receivable_intensity_chg /
inventory_intensity_chg,anndate PIT,对抗测试 6/6),**尚未接 DSL 白名单**——
按 probe-signal-source 纪律,须先在有数据湖的机器上跑步骤 3 体检:

```bash
python scripts/research/signal_source_probe.py \
  --factor factors.fundamental_quality:bargaining_power \
  --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31
```

probe 结论(含阴性)按步骤 8 回写 `knowledge/direction_registry.json`。
