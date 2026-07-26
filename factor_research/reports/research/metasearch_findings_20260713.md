# MetaSearch 重跑发现 — 2026-07-13

> 来源:`metasearch/` 三组件全量重跑(相对 2026-06-23 上一版快照,20 天)。
> MetaSearch 定位 = Line 0「质疑预设搜索空间本身」,不产 hypothesis,产「扩展/收缩搜索空间的建议」。
> 复现命令见文末。所有数值由本次运行机械产出,未人工编辑。承接 [`research_director_review_round6_findings.md`](research_director_review_round6_findings.md) §2 的重跑建议。

## 0. 一句话

**06-23 标出的 3 个信息空白区(vol_breakout / 基本面族 / 跨资产腿)在过去 20 天里被处理了 2 个半**(vol_breakout 被 round1 证伪关闭;跨资产腿由其他分支持续挖掘;基本面族的"运营质量比率变化"子分支被 round4+07-12 两次独立证伪),**但本次重跑显示基本面族的原始比率本身(roe/net_profit_yoy/bp_proxy/gross_margin)从未被真正 probe 过,且依然是当前 LIVE 策略池信息距离最远的频段之一(距离 2.95-2.96,满量程 2.97)——这才是唯一仍然开放、且证据最充分的下一步**。另外发现:`vol_breakout__short5_long10` 与 `mom_n__n60_skip0` 在纯 MI 距离排序里仍排前列,但两者均已被 direction_registry.json 标记证伪/关闭——**MI 距离本身不知道方向登记簿的状态,消费者必须交叉核对,不能直接把距离排序当候选清单用**。

---

## 1. factor_mi_audit — 因子信息冗余审计

池里 14 个候选(相对 06-23 的 21 个候选变少,因 06-23 行动项 #1 已把 `small_cap_factor` 6 窗口簇收窄到 3 窗口、`illiquidity.n` 5 档收窄到 4 档)算 IC 时间序列后两两互信息(MI,bits,bins=8 上限 3.0)。

**本次重跑过程中发现并修复一个 worktree 环境问题**:首次运行时基本面 4 个因子(net_profit_yoy/roe/gross_margin/bp_proxy)全部报 `ValueError: empty fundamental panel field`——根因是本轮工作 worktree 建立只读符号链接时只 glob 了 `data_lake/*/`(目录),漏掉了顶层文件 `fundamental_batch.parquet`,导致基本面数据实际不可读却静默通过(不是数据湖真的缺失)。补链接后 14/14 候选全部算出 IC,数字附后。**此问题只影响本次重跑所在的临时 worktree,不影响主仓/生产数据湖本身。**

### 1.1 当前信息冗余簇(MI > 2.0 = 同信息源)

| 冗余簇 | 成员 | 距离 |
| --- | --- | --- |
| Cluster 1 | `amplitude_mean__n60` / `volatility__n60` | 0.52 |
| Cluster 2 | `illiquidity__n40` / `illiquidity__n60` | 0.53(06-23 行动项已把 n40/n60 保留为相邻档,符合生产口径,未进一步收窄) |
| Cluster 3 | `high_low_breakout__n20` / `price_position__n20` | 0.42(同 06-23 观测,未处理) |

14 候选 → **11 个独立信息簇,21% 算力可省**(06-23 时是 21→13,38%;当前池已比 06-23 更紧凑,冗余占比下降符合预期)。

---

## 2. information_map — 空白区(下一步往哪挖)

以当前 6 个 LIVE/SHADOW 锚点(small-cap-size.v2.0、illiquidity.v1.0、size-low-vol.v1.0、size-earnings.v1.0、gov_bond_etf_511010.MA60、gold_etf_518880.MA60)为参照,MI-distance frontier top-8(`metasearch/frontier.json`):

| 排名 | 因子 | 距离 | 交叉核对 direction_registry.json 现状 |
| --- | --- | --- | --- |
| 1 | `vol_breakout__short5_long10` | 2.962 | **已证伪关闭**(round1:是既有反转簇镜像变体) |
| 2 | `gross_margin` | 2.960 | 未接入 ALLOWED_FACTORS,从未被单独 probe |
| 3 | `roe` | 2.956 | `frontier-fundamental-family` scope_factors 内,从未被单独 probe(只测过其衍生运营质量比率) |
| 4 | `net_profit_yoy` | 2.953 | 同上 |
| 5 | `mom_n__n60_skip0` | 2.950 | **已证伪关闭**(momentum-fullmarket-standalone-null) |
| 6 | `volatility` | 2.944 | 未标记,已在生产因子池内(非空白区) |
| 7 | `close_position__n5` | 2.944 | 与 `price_position__n20`/`high_low_breakout__n20` 同簇(§1.1),非独立新方向 |
| 8 | `bp_proxy` | 2.943 | `frontier-fundamental-family` scope_factors 内,从未被单独 probe |

**结论**:剔除已证伪(#1、#5)和非独立(#6、#7 已在池内或已聚簇)后,**真正开放的空白区收窄为基本面原始比率:gross_margin(新发现,未在 direction_registry 任何条目内)、roe、net_profit_yoy、bp_proxy**。这 4 个此前只以"族"的名义被 `frontier-fundamental-family` 条目笼统 BOOST(创建于 2026-07-02),但从未真正走过 probe-signal-source 步骤 3——此前两次基本面 probe(round4 07-06、main 07-12)测的都是这些原始比率的**衍生运营质量指标**(应收/应付/存货强度变化),不是 roe/net_profit_yoy/bp_proxy/gross_margin 本身。

---

## 3. signal_flow_tracer — 被丢弃的输出

callee 50 个,339 个丢弃事件(06-23 是 47/333,规模相近,代码库 20 天内新增少量丢弃模式,非质变)。06-23 标出的 3 个"像 Band 之于 dist"的候选**原样存在、20 天内无人处理**:

| 被丢函数 | 丢弃模式 | 现状 |
| --- | --- | --- |
| `hmm_stress_probability` | 3 输出丢后 2(100%) | 未变,仍未查 |
| `compute_salience_factors` | 第 1 输出 100% 丢 | 未变,仍未查 |
| `pg.pricing_gap` | index 0 丢(100%) | 未变,仍未查 |

完整报告:`metasearch/unused_signals.json`(本次刷新)。

---

## 4. 行动建议(优先级排序)

1. **下一轮方向①/②优先 probe 基本面原始比率(roe/net_profit_yoy/bp_proxy/gross_margin)**——不是重新设计因子族,是直接对 `factors/fundamental.py` 已有的 4 个实现(net_profit_yoy/roe/gross_margin/bp_proxy)跑 probe-signal-source 步骤 3(正交性 + IS/OOS 体检)。这与此前两次已证伪的"运营质量比率变化"子分支是不同的因子,不受那两次阴性结论约束。gross_margin 需先补 `@register_factor` 接入 `ALLOWED_FACTORS`(当前未接)。
2. **消费方向登记簿时必须交叉核对 MI 距离排序**——本次验证 vol_breakout/momentum 两个已关闭方向仍排在 frontier top-5,纯距离数字会误导新会话重新提案。建议未来给 `metasearch.information_map` 的输出加一步"读 direction_registry.json 过滤已 SKIP/falsified 条目"(本轮未做,超出"重跑并读结果"范围,留 TASKS)。
3. **3 个信噪比高的被丢信号(hmm_stress_probability/compute_salience_factors/pg.pricing_gap)连续两次重跑(06-23、07-13)都指向同一批、20 天无人处理**——不是本轮范围,但值得下一次有算力盈余时查一次,尤其 `hmm_stress_probability` 调用 10 次、100% 丢弃两个输出,量级不小。
4. **`high_low_breakout__n20`/`price_position__n20`(0.42)与 `amplitude_mean__n60`/`volatility__n60`(0.52)两组因子内冗余待裁**——06-23 已提出未处理,本次重跑确认冗余关系未变,仍需人工判断"删整个因子还是留一个代表窗口"。

> 注:以上均为「扩展/收缩搜索空间」的建议,非有效性判断。任何候选仍须走 candidates → L0-L3 → workflow/promote → phase1 防未来 → phase2/3 → phase4 入册(R-WF-001),DSR<0.05 方可 standalone 准入(R-OBJECTIVE-001 / R-EVIDENCE-001)。

## 复现命令

```bash
cd factor_research
python3 -m metasearch.factor_mi_audit --json
python3 -m metasearch.information_map --json
cd ..
PYTHONPATH=factor_research python3 -m metasearch.signal_flow_tracer
```

> 注:`factor_mi_audit`/`information_map` 内部用相对路径读数据湖(如 `data_lake/price/daily_raw_all.parquet`),须在 `factor_research/` 目录下执行;`signal_flow_tracer` 是纯 AST 静态扫描,须在仓库根目录执行(`SCAN_DIRS` 相对仓库根)。两者 cwd 要求不同,混用会报 `FileNotFoundError`。
