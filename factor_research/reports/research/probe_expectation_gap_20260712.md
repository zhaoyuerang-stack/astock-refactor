# Probe:隐含预期差因子族(expectation_gap)— L0 证据,非 alpha

- 日期:2026-07-12 | 剧本:probe-signal-source | 工具:`scripts/research/signal_source_probe.py`
- 因子:`factors/expectation_gap.py`(probe 前候选,未接 DSL 白名单/种子)
- 窗口:2018-01-01 → cutoff 2022-12-31 → 2024-12-31(holdout 金库 2025-01-01 起未触碰)
- 宇宙:all(全市场,R-DATA-002);月度调仓;IC = canonical `engine.factor_analysis`
- 搜索自由度诚实申报:本次共 3 个族成员 × 默认参数各 1 次 probe = **3 trials**,无参数网格;
  `discount_rate` 未搜索(它是价值/成长两腿的混合权重,若日后网格化必须计入 n_trials,R-EVIDENCE-001④)
- 动机:VOS「市场隐含预期」层的因子化;方向登记簿 `frontier-fundamental-family`(BOOST)指向基本面族空白区

## 设计

机制锚:P/E=(1+g)/(r−g) 反解 g_implied=(PE·r−1)/(PE+1)(PE 的单调变换,截面上≡价值因子)。
**族的信号设计在"差"上**:gap = PIT 已知的增速兑现/指引 − 价格隐含要求的增速。

| 成员 | 口径 | 数据(全 PIT,canonical loader) |
|------|------|------|
| `implied_growth_gap` | netprofit_yoy − 100·g_implied | daily_basic.pe_ttm(by_date) + fina_indicator(anndate) |
| `guidance_gap` | 业绩指引(快报优先/预告中点) − 100·g_implied | + forecast/express(anndate,同 earnings.py 口径) |
| `peg_inverse` | netprofit_yoy / pe_ttm(乘性参数化) | 同 implied_growth_gap |

覆盖诚实:pe_ttm≤0(亏损股)一律 NaN(盈利宇宙);指引口径覆盖有偏(预告为条件强制披露)。
单元对抗测试 9/9(`tests/test_expectation_gap.py`:退化成纯价值/纯成长的实现必挂、亏损股不编分、快报优先、缺字段真拒、池对齐)。

## 结果(L0-L3 证据,非 alpha)

| 成员 | 原始 IC IS→OOS(留存) | 残差 IC IS→OOS(留存) | 正交保留率 | 风格相关(size/liq/mom) |
|------|----------------------|----------------------|-----------|------------------------|
| `implied_growth_gap` | 0.0115 → **−0.0067(−58%)** | 0.0205 → **−0.0000(−0%)** | 231% | 0.13 / 0.06 / 0.12 |
| `guidance_gap` | 0.0229 → 0.0049(21%) | 0.0131 → 0.0089(**68%**) | 66% | **0.44** / −0.21 / 0.03 |
| `peg_inverse` | 0.0134 → **−0.0094(−70%)** | 0.0202 → 0.0014(**7%**) | 211% | 0.12 / 0.06 / 0.11 |

JSON 明细:`reports/research/probe_expectation_gap_{implied_growth_gap,guidance_gap,peg_inverse}.json`

## 诚实结论(按 skill 便宜判据,advisory,非裁决)

1. **`implied_growth_gap` / `peg_inverse`:阴性——OOS 塌缩/翻负,不泛化**。
   正交性极好(残差比原始更强,确实不是小盘/流动性代理),但 IS 残差 ICIR 0.30 在 OOS
   (2023-2024)归零或翻负,教科书式 IS-only 信号。"已兑现的 yoy"是市场早已消化的陈旧信息,
   与估值作差不产生可泛化的截面预测力。
2. **`guidance_gap`:边缘偏弱**。唯一残差流 OOS 不塌的成员(留存 68%),但:
   ① 量级 modest(OOS 残差 IC 0.0089 / ICIR 0.14)——落在已关闭的「真正交但太弱」家族
   (北向/holder/smart_div,残差 ICIR~0.2,long-only 做不成 standalone 也加不动核心)同一量级带;
   ② 原始形态 size 相关 0.44,近半是大盘暴露的伪装(正交保留率 66%),接工厂须内建中性化。
   按 skill 判据"风格相关高→已知风格代理"与"残差 OOS 不塌→有希望"冲突,取保守侧:**不接工厂**。
3. **不进入步骤 4-6**(工厂接入/island search/walk-forward):无成员同时满足"正交 + OOS 不塌 +
   量级可用"。本结论已按步骤 8 回写方向登记簿(DEPRIORITIZE + 复活条件 + 180d 到期复测)。

## 复活条件(什么变了才值得重测)

- **湖内新增分析师一致预期/预期修正数据源**:本族失败的合理归因是"预期"腿用的是已兑现(陈旧)
  信息;若有真前瞻预期(一致预期变动),gap 口径值得整族重测。
- **纯 SUE 口径(backlog `forecast-express`)独立 probe 显示强 OOS**:则 guidance_gap 的
  估值条件化变体(带内建 size 中性化)值得重测。

—— 以上全部为 L0-L3 便宜筛证据,不扣成本、无 DSR/容量/9-Gate;不构成任何"有效/无效"的最终裁决(R-LLM-001);入册通道唯一(R-WF-001)。
