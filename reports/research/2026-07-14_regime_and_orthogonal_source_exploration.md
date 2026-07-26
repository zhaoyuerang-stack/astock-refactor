# 研究报告：市场状态识别 + 正交截面源 + 龙虎榜短线探索

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-07-13 ~ 2026-07-15 |
| 分支 | `codex/xiaochengxu`（探索时） |
| 数据湖 | 本机 `factor_research/data_lake`（FULL；价量约至 2026-07-10；institutional 约至 2026-07-03） |
| 证据级别 | **L0–L1 便宜筛 + 事件研究**；**非 alpha、未入册、未改生产** |
| 成本口径 | 组合书：`CostModel` 买 22.5bp / 卖 27.5bp + 融资 6.5%；指数/事件 overlay：切换或往返 **30bp** 简化税 |
| 执行口径 | 信号 `shift(1)` / 榜单 T 盘后 → 最早 T+1；回测权威 = `BacktestEngine`（策略书） |
| `shift(1)` / T+1 | 因子与择时一律防未来；龙虎榜按盘后披露处理 |

---

## 0. 探索动机

1. 能否用**行业市值中位归一化偏离**、**交易资金/换手**、**广度**等判断牛熊并改进 MA16？
2. 主开关若仍是 MA16，算力是否应转向**正交截面新源**（institutional / 股东 / 事件）？
3. 龙虎榜能否做 **T+1/T+2** 事件组合？叠加量与业绩是否翻盘？
4. 是否应对标 Liu et al.（2019）**删除市值底部 30%**？

全程遵守：参数事先冻结（禁网格搜均线/阈值）、只算+报、LLM 不裁决有效性、不写 registry。

---

## 1. 关键发现总表

| # | 发现 | 证据强度 | 行动含义 |
| --- | --- | --- | --- |
| **F1** | **MA16（小盘 PureTrend）仍是多头腿最稳日频总开关** | 跨小盘三腿 untimed 书 + 多类 overlay WF | **生产/研究默认保留 MA16**；停止再挖第 N 个二值牛熊开关 |
| **F2** | 行业 log-市值热度（IMV）偏**弱均值回归**（fwd20 ρ≈−0.13）；资金/换手偏**弱顺周期** | 特征相关 + regime probe | 二者**不能**同一套「热→减仓」规则硬并联 |
| **F3** | **硬 AND 确认**（MA16∧换手、∧广度、∧A/D）全样本偶好看，**WF/holdout 不稳或更差** | 多 probe 对照表 | 禁止用确认门替换 MA16 |
| **F4** | **MA16 × 连续 IMV 预算**（κ=0.25, floor=0.5）WF 夏普仅 **+0.03~0.05**，maxdd 不恶化；holdout 少赚牛市 | `probe_ma16_continuous_budget.py` | 最多作可选风险预算；**不替换 MA16**；波动预算失败 |
| **F5** | 稀疏事件因子上 `mad_clip` 在「绝大多数为 0」时 **MAD=0 抹掉全部信号** | top_inst 全 nan IC → 修复后可测 | 已加 `sparse_event_zscore`；稀疏源必须特殊处理 |
| **F6** | institutional 等新源**湖已进、截面大多未成 long alpha** | P0/P1 批量 L0 | 见 §3；仅少数 L0 弱线索，L1 组合多翻车 |
| **F7** | **龙虎榜上榜后 hold1 无条件期望为负**（约 −0.4%/次毛、−0.7% 扣 30bp） | 11.9 万事件 | 默认「见榜做多 T+1」不可行；更宜拥挤/回避标签 |
| **F8** | 龙虎榜叠加**量+业绩**后，**无 IS/OOS 双过且显著的多头规则**；仅「净买+低温换手」OOS 微正不显著 | `probe_top_list_vol_earn_t1t2.py` | 过滤「过热」有意义；「追业绩」救不了榜后反转 |
| **F9** | **top_list 残差 IC 月频好看 → top25 扣成本书换手爆炸、打不过 size**（与 low_inst 同型） | L0→L1 | 稀疏源≠可部署月频多头腿 |
| **F10** | LSY 删市值底部 30% 服务**定价干净**；本仓主 edge 在小盘/流动性，**默认不应删 30%** | 文献 + 池重叠约 45% | 用分层/对照宇宙，不改生产默认 |

---

## 2. 市场状态 / 择时线

### 2.1 指标与脚本

| 主题 | 脚本（均在 `factor_research/scripts/research/`） | JSON/产物 |
| --- | --- | --- |
| 行业市值偏离（早期） | `scratch/industry_mv_deviation.py` | `scratch/industry_mv_deviation.png` |
| 干净 IMV + vs MA16 WF | `probe_industry_mv_regime.py` | `scratch/industry_mv_regime_probe.json` |
| 交易资金归一化 | `probe_market_trading_capital.py` | `scratch/market_trading_capital_probe.json` |
| MA16 × 换手 AND | `probe_ma16_turnover_confirm.py` | `scratch/ma16_turnover_confirm_probe.json` |
| %above MA60 + A/D | `probe_breadth_ad_ma16.py` | `scratch/breadth_ad_ma16_probe.json` |
| 多策略 untimed × overlay | `probe_regime_multi_strategy.py` | `scratch/regime_multi_strategy_probe.json` |
| MA16 × 连续预算 | `probe_ma16_continuous_budget.py` | `scratch/ma16_continuous_budget_probe.json` |

### 2.2 关键数字（摘要）

**特征与未来 20 日 EW 收益相关（research）：**

- IMV mean_z ≈ **−0.13**（贵→弱）
- turn_z / 成交活跃 ≈ **+0.12~0.16**（热→略强，顺周期）
- %above MA60 / A/D 斜率 ≈ **−0.05**（弱）

**多头三腿 untimed（small-cap / illiq / size-low-vol）× MA16_sc：**

- 相对 always_on：Δ Sharpe 约 **+0.9~1.3**，maxdd 大幅收敛（如 illiq −61%→−15% 量级）
- 广度/换手 AND 相对 MA16：**多数年份不赢**

**MA16 × g_imv（连续预算）：**

- 跨策略 WF 均值夏普略高于纯 MA16（约 1.60 vs 1.56）
- Holdout 2025：年化明显让出（小盘书约 +44%→+34% 量级）——贵时降仓的代价

**择时结论（写入纪律）：**

```text
多头腿主开关 = 固定 MA16_sc（禁网格）
中性/对冲腿 = 默认不套市场 regime（旧审计）
IMV 连续预算 = 可选二级；硬 AND 确认 = 停
```

---

## 3. 正交截面 / 新数据源线

### 3.1 数据源扫描（2026-07）

**相对价量主链较新、已落盘：**

- `data_lake/institutional/`：大宗、龙虎榜 top_list / top_inst、回购、质押（canonical）
- `holder/top10_holders_all.parquet`
- `global/*`（跨资产/宏观，偏组合非选股主菜）
- `cyq/`、`fund/` 空或不可用；北向个股约 **2024-08 停更**

### 3.2 新增/修补代码（探索用）

| 模块 | 作用 |
| --- | --- |
| `factors/utils.py`：`sparse_event_zscore` + `mad_clip` MAD=0 保护 | 稀疏事件可测 |
| `factors/block_trade.py` | 大宗折价/活跃 |
| `factors/repurchase.py` | 回购强度/事件宇宙 |
| `factors/top10_holders.py` | 集中度 / 机构占比 / low_inst 翻转 |
| `factors/share_float.py` | 解禁压力 |
| `factors/margin_leverage.py` | 两融 |
| `factors/top_list.py` | 龙虎榜净买强度 |
| `factors/dividend_pit.py` | 分红公告事件 |
| `factors/top_inst.py` | 接 sparse_event_zscore |

### 3.3 L0 截面 probe 口径

```bash
cd factor_research
python3 scripts/research/signal_source_probe.py \
  --factor <mod:fn> --param ... \
  --universe all --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31
```

批量入口：`probe_untested_pit_batch.py` → `scratch/probe_untested_pit_batch_summary.json`

### 3.4 截面源结论矩阵

| 因子/源 | L0 残差 OOS | 风格 | L1 扣成本 top25 | 结论 |
| --- | --- | --- | --- | --- |
| top_inst_intensity | 翻负 | 淡 | — | 停 long |
| block_discount / amount | 翻或残差反向 | 淡~弱 size | — | 停 long；amount 残差或作 veto 研究 |
| repurchase 金额/次数 | 残差负 | 弱 | — | 停 long |
| top10 集中度 | 塌 | size≈−0.15 | — | size 代理 |
| **low_inst_hold_ratio** | 弱正、稳 | 淡 | **远逊 size**（sh 0.04 vs 0.41） | L0 弱过 → **L1 证伪 long 腿** |
| sue / 预告惊喜 | 弱/翻 | **size 大** | — | SIZE_PROXY |
| holder_count_chg | 残差负 | 弱 | — | raw 好看无效 |
| large_order_net_ratio | 负 | 弱 | — | NO_GO |
| share_float_* | 弱/残差不稳 | 淡 | — | NO_GO |
| margin_balance_ratio | 不稳 | **size/换手强** | — | SIZE_PROXY |
| **top_list_net_intensity** | **残差 IS/OOS 约 +0.06** | 淡 | **换手 25x、maxdd−80%、不赢 size** | L0 过 → **L1 证伪 long 腿** |
| div_cash_ann | 残差 OOS 极端 | 淡 | — | 稀疏失真，慎 |

**阶段收束：** 扫了一圈新 PIT 源，**没有「L0 正交 OOS 不塌 且 L1 扣成本能打 size」的新 long 主因子。**  
主书仍应落在已有小盘/流动性簇；新源更适合事件/veto/低频状态，而非复制 top25 模板。

---

## 4. 龙虎榜 T+1 / T+2

### 4.1 脚本

| 脚本 | 内容 |
| --- | --- |
| `probe_top_list_t1t2_event.py` | 冻结规则事件研究 |
| `probe_top_list_vol_earn_t1t2.py` | 叠加量（ADV 比、换手）+ 业绩（YoY/ROE，avail_date PIT） |
| `probe_top_list_l1.py` | 月频强度因子 L1 书 |

### 4.2 关键数字

- 匹配事件约 **11.9 万**；hold1 全样本毛约 **−0.4%/次**，扣 30bp 约 **−0.7%**，胜率约 **44%**。
- 净买、强净买、追涨净买：IS 偶正，**OOS 转负**。
- 过热/高换手上榜：**最差档**（OOS net 约 −1.1%~−1.3%）。
- 量+业绩最优外观：`净买 + 换手<15` OOS net 仅 **+0.08%**（t≈1.1）；再叠高增长+ROE 时 **IS +2% / OOS ≈0** → 过拟合外观。
- **短名单（IS&OOS net>0、t>1.5、n≥150）：空。**

### 4.3 用法定位

```text
龙虎榜 → 拥挤/过热/回避标签（veto 或风控）
      ↛ T+1 多头提款机
      ↛ 月频 top25 主因子
```

---

## 5. 市值底部 30% 与更科学分桶

### 5.1 Liu et al.（2019）要点

- 文：**Size and Value in China**（Liu, Stambaugh, Yuan），JFE 2019；主样本 **2000–2016**。
- 删市值最小 **30%** 因壳价值污染（借壳）；价值用 **EP**；**CH-3**。
- 样本止于 2016，**客观上避开 2017+ 小盘叙事转弱**——方法论仍有价值，样本外不可照抄「size 永续」。

### 5.2 本仓是否默认删 30%

| 决策 | 理由 |
| --- | --- |
| **生产/小盘主书：不删** | edge 在小盘/低流动；约 45% 小盘池与 mcap 底部 30% 重叠，一刀切=放弃主战场 |
| **对照宇宙：要** | `ex_mcap_p10` / `ex_mcap_p30` / 分层 IC，写报告自证 |
| **更科学过滤** | ESP/壳概率（Lee–Qu–Shen 线）> 裸 30%；交易宇宙优先 **可交易+流动性分位**；市值只做分层 |

---

## 6. 失败模式（供后续避免）

1. **确认门幻觉**：全样本 AND 好看 → WF 踏空。  
2. **裸 IC 幻觉**：raw OOS 正、残差翻负（回购/大宗活跃等）→ 风格通道。  
3. **L0→L1 断崖**：残差 IC 月频可看 → top25 高换手+成本后归零（龙虎榜、low_inst）。  
4. **稀疏 mad_clip**：零截面 MAD=0 抹信号。  
5. **用 LSY 2016 前样本论证 2018+ 可部署 size**。  
6. **中性腿硬套多头 regime**（旧审计已否）。

---

## 7. 建议的后续优先级（非本报告已完成）

| 优先级 | 事项 |
| --- | --- |
| P0 | 维持 MA16 多头开关；停止牛熊开关网格 |
| P1 | 宇宙 `full \| ex_mcap_p10 \| ex_mcap_p30 \| liquid` 对照（不改默认） |
| P2 | 龙虎榜/过热作 **持仓 veto** 而非选股引擎（规则冻结后测） |
| P3 | 新源：大中盘 + 更宽持仓/事件短持有；勿再套同一 top25 模板 |
| 不做 | 再扫同类稀疏 long 强度因子直到有新数据机制 |

---

## 8. 复现命令索引

```bash
cd factor_research

# 择时 / regime
python3 scripts/research/probe_ma16_continuous_budget.py
python3 scripts/research/probe_regime_multi_strategy.py
python3 scripts/research/probe_breadth_ad_ma16.py

# 截面新源批量 L0
python3 scripts/research/probe_untested_pit_batch.py
python3 scripts/research/probe_low_inst_hold_l1.py
python3 scripts/research/probe_top_list_l1.py

# 龙虎榜事件
python3 scripts/research/probe_top_list_t1t2_event.py
python3 scripts/research/probe_top_list_vol_earn_t1t2.py
```

JSON 证据默认在 `factor_research/scratch/probe_*.json`（运行产物，默认不入库；本报告为可审计结论入口）。

---

## 9. 一句话收束

> **择时：MA16 仍赢。**  
> **截面新源：扫了一圈，无 L1 可部署 long 新腿。**  
> **龙虎榜：榜后偏弱，量+业绩救不了 T+1 多头；当拥挤标签。**  
> **壳/30%：学术对照可以，生产小盘默认不删。**

---

*本报告仅记录探索边界与失败/弱通过证据，不构成策略入册或生产启用依据。入册须走 canonical workflow + 9-Gate + holdout。*
