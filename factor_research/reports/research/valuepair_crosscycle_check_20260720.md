# 价值对(bp_proxy / ep_proxy)跨周期反例检验 — 分日历年残差 IC(2026-07-20)

> 角色边界:本文只产 L0 描述性统计(原始/残差 rank-IC、NW ICIR、风格相关),不扣成本、
> 无 DSR/PBO/容量/9-Gate,**不下"有效/无效"结论**(R-LLM-001)。判断入册/晋级归确定性
> 门禁 + workflow(R-WF-001)。承接 daily-round-7/8 needs_human② 的建议:对价值对做覆盖
> 2018-2020(round8 措辞:成长占优期)的跨周期反例检验。

## 0. 任务背景

round7(`probe_round7_fundamental_bp_proxy.json`)与 round8
(`fundamental_raw_ratio_probe_round8_addendum.md`、`probe_round7_fundamental_ep_proxy.json`)
发现两个数学上不同构的价值代理(bp_proxy = bps/raw_close,ep_proxy = eps_ttm/raw_close)
呈现同一不对称形态:IS(2018-2022)残差 ICIR 弱(0.08 / 0.10)、OOS(2023-2024)残差
ICIR 骤强(0.59 / 0.20),且同步与流动性负相关(-0.339 / -0.242)。round8 怀疑这是
2023-2024 A股价值风格占优 regime 的**共同贝塔**,而非各自独立的结构性正交 alpha。
本检验把同一口径的残差 IC 拆到 2018→2024 每个日历年,看正残差 IC 是否集中于 2023-2024。

## 1. 方法与口径一致性声明

- 脚本:`factor_research/scripts/research/valuepair_crosscycle_check.py`(本轮新建,可复现:
  `python3 factor_research/scripts/research/valuepair_crosscycle_check.py --json <path>`)。
- **口径一致性(与 round7/8 逐位可比)**:脚本**逐函数原样 import**
  `scripts/research/signal_source_probe.py` 的 `_load_close / _load_controls /
  _monthly_rebalance / _forward_returns / _neutralize / _xcorr / _seg_ic`,零改写、零改参数。
  残差化定义不变:controls = size(log circ_mv)+ liquidity(turnover_rate),逐截面
  标准化 OLS lstsq 残差;IC = 月频 rank-IC(`engine.factor_analysis.calc_ic`),
  ICIR = Newey-West(`newey_west_icir`)。唯一新增逻辑 = 把"IS/OOS 两段切分"换成
  "逐日历年对同一 `_seg_ic` 调用",因子实现用 canonical `factors/fundamental.py`(未改)。
- universe = all(全市场),月末调仓、次月末前向收益,与 round7/8 一致。
- **口径一致性机械核验**:脚本同时复算了 round7/8 定义下的 IS(2018-2022)/OOS(2023-2024)
  段(见 JSON `cross_check_vs_round78`),与 `probe_round7_fundamental_bp_proxy.json` /
  `probe_round7_fundamental_ep_proxy.json` **逐位一致**(bp_proxy 残差 IS 0.0136/ICIR 0.08、
  OOS 0.0592/ICIR 0.59;ep_proxy 残差 IS 0.0084/ICIR 0.10、OOS 0.0273/ICIR 0.20),
  证明本检验与 round7/8 同一口径,分年数字可直接对话。
- **holdout 红线**:`app_config/settings.yaml::holdout.start = 2025-01-01`。脚本对
  close/因子/前向收益/controls 逐一在加载后立即截断到 ≤2024-12-31 并以断言机械强制
  (`_enforce_holdout_boundary`);已做对抗性验证——含 2025-01-02 行的数据被截掉、
  纯 2025 数据触发 AssertionError 拒绝(guard 真拒,非 happy-path)。本检验未触及金库。

## 2. 预登记证伪判据(先于看结果写定,未按结果调整)

| 判据 | 观察形态 | 对"2023-2024 regime 共同贝塔"怀疑的含义 |
|---|---|---|
| (a) | 正残差 IC 集中在 2023-2024;2018-2020 各年 ≤0 或明显转弱 | 怀疑被**加强** |
| (b) | 2018-2024 各年残差 IC 均匀为正,无 2018-2020 塌陷 | 怀疑被**削弱** |

两种结果都如实报告,不做取舍;该映射只是读数规则,最终解读归人工/确定性门禁。

## 3. 分年结果

说明:ICIR 为 Newey-West 口径,分母用长期方差、分子取 |mean|,**恒为非负**——符号一律看
IC 列。2024 年为 11 个月:12 月末调仓的前向收益需要下一个调仓日(2025-01),已被 holdout
截断机械丢弃,与 round7/8 OOS=23 月同因,非数据缺口。

### 3.1 bp_proxy(1/PB)

| 年份 | 原始 IC | 原始 ICIR | 残差 IC | 残差 ICIR | 流动性相关 | 月数 |
|---|---|---|---|---|---|---|
| 2018 | +0.0554 | 0.33 | +0.0188 | 0.14 | -0.368 | 12 |
| 2019 | -0.0153 | 0.15 | **-0.0459** | 0.46 | -0.336 | 12 |
| 2020 | +0.0281 | 0.17 | **-0.0148** | 0.10 | -0.411 | 12 |
| 2021 | +0.1163 | 0.51 | +0.0766 | 0.38 | -0.373 | 12 |
| 2022 | +0.0661 | 0.36 | +0.0334 | 0.18 | -0.285 | 12 |
| 2023 | +0.1389 | 1.25 | **+0.1116** | **1.28** | -0.325 | 12 |
| 2024 | +0.0424 | 0.31 | +0.0020 | 0.02 | -0.268 | 11 |

### 3.2 ep_proxy(1/PE)

| 年份 | 原始 IC | 原始 ICIR | 残差 IC | 残差 ICIR | 流动性相关 | 月数 |
|---|---|---|---|---|---|---|
| 2018 | +0.0531 | 0.84 | +0.0373 | 0.85 | -0.164 | 12 |
| 2019 | -0.0033 | 0.04 | **-0.0187** | 0.32 | -0.234 | 12 |
| 2020 | +0.0304 | 0.46 | **-0.0051** | 0.08 | -0.277 | 12 |
| 2021 | +0.0361 | 0.25 | +0.0280 | 0.21 | -0.264 | 12 |
| 2022 | +0.0187 | 0.17 | +0.0006 | 0.01 | -0.235 | 12 |
| 2023 | +0.0457 | 0.44 | **+0.0472** | **0.66** | -0.262 | 12 |
| 2024 | +0.0249 | 0.12 | +0.0056 | 0.03 | -0.257 | 11 |

## 4. 预登记判据比对(如实描述,不下结论)

**观察到的形态更接近判据 (a),且比 (a) 的表述更尖锐**:

1. **2018-2020 不是均匀为正**:两因子在 2019、2020 残差 IC 均为负
   (bp_proxy -0.0459/-0.0148;ep_proxy -0.0187/-0.0051),两口径同符号同步。
   2018 为正但量级中等(+0.0188/+0.0373)。判据 (a) 的"2018-2020 ≤0 或明显转弱"
   在 2019-2020 成立、2018 部分成立(为正但远弱于 2023)。
2. **正残差 IC 并非集中于整个 2023-2024,而是几乎全部集中于 2023 单一年份**:
   bp_proxy 2023 残差 IC +0.1116(ICIR 1.28)vs 2024 仅 +0.0020(ICIR 0.02);
   ep_proxy 2023 +0.0472(ICIR 0.66)vs 2024 仅 +0.0056(ICIR 0.03)。
   round7/8 报告的"OOS(23 月)残差骤强"(0.59/0.20)按年拆开后,其量级几乎完全由
   2023 贡献,2024 已回落到与 2019-2020 噪声区同一量级。
3. **两口径逐年同步**:七个年份中两因子残差 IC 符号完全一致
   (2018 +/+,2019 -/-,2020 -/-,2021 +/+,2022 +/≈0,2023 +/+,2024 ≈0/≈0),
   与 round8 "协同性本身就是证据"的读数一致——同涨同落更符合共同 regime 暴露,
   而非两个独立的结构性信号。
4. **判据 (b) 不成立**:各年残差 IC 不是均匀为正(7 年中 2 年为负、2 年 ≈0)。

按 §2 预登记的映射规则,本证据使"2023-2024(实为 2023)A股价值风格占优 regime 的
共同贝塔"怀疑**被加强**,且给出一个 round7/8 未见的补充事实:**该形态在 2024 年内已经
消退**(两因子残差 IC 双双 ≈0)。同时如实记录反向证据:2021-2022 残差 IC 亦为正
(bp_proxy +0.0766/+0.0334),正读数并非只存在于 2023,只是量级/稳定度显著更低——
逐年符号翻转(+,-,-,+,+,+,≈0)的序列本身即不满足"结构性稳定正交信号"的直观形态。
是否据此在 direction_registry / 搜索空间上做任何增删,归人工与确定性门禁裁决,
本文不做该判断。

## 5. 附带如实观察(非结论)

- 两因子对流动性的负相关在**全部七个年份**稳定存在(bp_proxy -0.27~-0.41,
  ep_proxy -0.16~-0.28),即"低流动性倾斜"是价值对的常在风格暴露,并非 2023-2024
  特有;残差化已按 round7/8 同一口径将其线性成分去除。
- 2019 年两因子残差 IC 为负而原始 IC 接近 0/负——残差化后更负,说明当年正向的
  size/流动性暴露部分掩盖了价值本身的逆风。
- 数据无缺口:2018-2024 各年均 12 个月(仅 2024 为 11 个月,机械性前向收益边界所致,
  见 §3 说明)。

## 6. 试验记账

- canonical 账本:`governance/trial_ledger.py::record_trials` append 1 条,
  scope=`valuepair_crosscycle_check`,n_configs=2(2 因子 × 1 套固定分年窗口;
  分年切分为**全量报告式**再切分,无任何"选最好窗口"的自由度,计数口径与 round7/8
  对同一 probe harness 记 1 因子 1 config 一致)。
- 轮内副本:`reports/research/valuepair_crosscycle_trial_ledger.jsonl`(同内容,
  沿 round7/8 的轮账本模式)。

## 7. 产物清单

- `factor_research/scripts/research/valuepair_crosscycle_check.py`(可复现脚本,新建)
- `factor_research/reports/research/valuepair_crosscycle_check_20260720.json`(机器可读分年数据,新建)
- `factor_research/reports/research/valuepair_crosscycle_check_20260720.md`(本文,新建)
- `factor_research/reports/research/valuepair_crosscycle_trial_ledger.jsonl`(轮内记账副本,新建)
- `data_lake/governance/trial_ledger.jsonl`(canonical append 1 行,唯一既有文件写入,经 `record_trials` 入口)
