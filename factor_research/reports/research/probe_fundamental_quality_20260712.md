# Probe:资产负债表运营质量因子族(fundamental_quality)— L0 证据,非 alpha

- 日期:2026-07-12 | 剧本:probe-signal-source | 工具:`scripts/research/signal_source_probe.py`
- 因子:`factors/fundamental_quality.py`(probe 前候选,孤岛回收①;TASKS「probe 执行·需数据湖」项)
- 窗口:2018-01-01 → cutoff 2022-12-31 → 2024-12-31(holdout 金库 2025-01-01 起未触碰)
- 宇宙:all(全市场,R-DATA-002);月度调仓;IC = canonical `engine.factor_analysis`
- 搜索自由度诚实申报:3 成员 × 默认参数各 1 次 = **3 trials**,无参数网格

## 结果(L0-L3 证据,非 alpha)

| 成员 | 原始 IC IS→OOS(留存) | 残差 IC IS→OOS(留存) | 正交保留率 | 风格相关(size/liq/mom) |
|------|----------------------|----------------------|-----------|------------------------|
| `bargaining_power` | 0.0077 → 0.0013(17%) | 0.0015 → −0.0004(**−27%**) | **17%** | 0.21 / −0.16 / 0.02 |
| `receivable_intensity_chg` | **−0.0056** → −0.0005(9%) | −0.0041 → 0.0003(**−7%**) | 69% | −0.05 / 0.03 / −0.03 |
| `inventory_intensity_chg` | **−0.0088** → +0.0043(**−49%**) | −0.0037 → +0.0052(**−141%**) | 23% | −0.04 / 0.03 / −0.01 |

JSON 明细:`reports/research/probe_fundamental_quality_{bargaining_power,receivable_intensity_chg,inventory_intensity_chg}.json`

## 诚实结论(按 skill 便宜判据,advisory,非裁决)

1. **三成员全阴性,不接工厂**(不进入步骤 4-6)。
2. `bargaining_power`:正交保留率仅 17% —— 大半是 size/流动性暴露的伪装(size 相关 0.21),
   去掉后残差 IC≈0(0.0015/ICIR 0.02),没有独立信息。
3. `receivable_intensity_chg` / `inventory_intensity_chg`:**IS 符号与设计假设相反**
   (设计:强度改善=高分=应正 IC;实测 IS IC 为负,即占款/存货强度**抬升**的股票 IS 反而
   跑赢——更像扩张/成长信号而非恶化信号),且 OOS 塌缩或翻号(留存 9%/−49%,残差 −7%/−141%),
   两个方向都不稳定,无可用信号。
4. 合理归因(供复活参考):资负表占款/存货结构**强行业性**(制造业 vs 服务业的应收/存货
   基线完全不同),全市场直接截面比较主要在比行业构成,行业内的真信息被淹没。probe 的
   中性化只去 size/流动性,不含行业。

## 复活条件(什么变了才值得重测)

- **行业分类落湖后行业内中性化重测**:TASKS 产业基本面 Phase 2(`index_classify` →
  `data_lake/meta/industry.parquet`)完成后,本族应以行业内 z-score 重新 probe 一次。

—— 以上全部为 L0-L3 便宜筛证据,不扣成本、无 DSR/容量/9-Gate;不构成任何"有效/无效"的最终裁决(R-LLM-001);入册通道唯一(R-WF-001)。
