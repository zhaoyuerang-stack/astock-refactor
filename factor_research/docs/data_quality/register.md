# 数据质量登记表(canonical)

湖内数据的**质量违规与已知缺陷**唯一登记点。R-DATA-xxx 类事故的事实层在这里,
规则层(风险定义)在 CLAUDE.md,执法层(CI 守卫)在 `scripts/ci/`。

## 登记规则

- 一个事故一个条目,ID 格式 `DQ-YYYY-NNN`,条目只增不删。
- 状态机:`OPEN`(已确诊未修复)→ `MITIGATED`(已止血/有守卫,根因未除)→ `CLOSED`(根因修复+守卫例外清零)。
- 行级证据(fingerprint CSV)放本目录,文件名以条目 ID 开头;CSV 只记**违规/异常行**,
  可由条目中 repro 命令重新生成,但以提交版本为准(数据会漂移,审计要钉死现场)。
- 例外集只许缩小不许扩大:守卫对例外集之外的任何新违规则红(kill condition 见各条目)。
- 判定口径写进条目:阈值、容忍度、真值来源及其局限,不许事后调参达标。

## 条目索引

| ID | 状态 | 数据集 | 一句话 |
| --- | --- | --- | --- |
| [DQ-2026-001](#dq-2026-001) | MITIGATED | fundamental_batch.parquet | 根因已定位并修复(ingestion 源头 + 存量重铺);例外集清零;守卫绿;策略版本重跑未做,暂不转 CLOSED |

---

## DQ-2026-001

- **状态**:MITIGATED(2026-07-22 确诊 → 同日根因定位 + 存量重铺 + ingestion 源头修复;
  守卫 `check_fundamental_batch_pit.py` 现全绿、例外集清零;转 CLOSED 前还差"重跑受影响
  registry 版本"一项,见 kill condition)
- **数据集**:`data_lake/fundamental_batch.parquet`(akshare 东财 `stock_yjbb_em` 季度批量,41.4 万行)
- **规则**:R-DATA-003(禁止未来函数);同时构成 PIT 口径降级(契约 `anndate`,schema.py 注册)

### 根因(已实锤定位,非猜测——订正本条目早先"批量重建 join 错位"的猜测)

`lake/schema.py::YJBB_RENAME` 把东财 `stock_yjbb_em` 返回的"最新公告日期"列直接
改名成了 `ann_date` 消费。该列语义是**公司级、随该公司最近一次披露滚动刷新**,
从不是"本报告期专属公告日"——这是本仓 RENAME 时的语义误用,不是东财数据本身出错,
也不是某次批量重建的 join 错位。

现场复现(2026-07-23):`ak.stock_yjbb_em(date="20240630")` 今日实时查询,000001
的"最新公告日期"返回 `2025-08-23`(该公司最近一次披露日),而 2024-06-30 中报
真实公告日是 `2024-08-16`(tushare income 现网核实,`f_ann_date`)。列名从未承诺
"本期"语义,东财该接口本身就没有"首次公告日"这个专属字段。

### 事实(2026-07-22 审计,repro 见下;数字为审计当时快照,已被下方"修复"章节的
新数字取代,保留作为诊断存证)

被审列 `ann_date`(=`avail_date`,全表两列逐行相等);真值 = 三大报表
(income/balancesheet/cashflow)`ann_date` 按 (code, 报告期) 取 MIN。

1. **晚知类(PIT 降级,非未来函数)**:全表 89.99% 行 `avail_date - report_date > 180d`,
   中位 418 天。按报告期断点整齐:**≤2025Q1 各季度 diff(fb_ann − 真值)中位数稳定 364~366 天**
   (2010~2025Q1 逐年验证),**≥2025Q2 diff=0**(断点后 `update.py::update_fundamental`
   走的是同一有缺陷的 RENAME,能对上纯属巧合——见下方修复章节,已一并修正)。
2. **早知类(真实 R-DATA-003 违规)**:join 命中 250,815 行中 `fb_ann < 真值 − 2d` 的 **561 行 /
   406 股**,report_date 2010-03-31 ~ 2021-03-31,提前中位 89 天、**最多 1036 天**。
   (另 915 行 diff ∈ {-1,-2} 天为供应商日历差(东财周末披露 vs tushare 顺延),判为非违规噪音。)
3. **数值载荷未受污染**:fb.revenue 与 income 同期值相等 234,082/250,304(93.5%,余为修订/精度差),
   与次年同期值相等仅 26 行——**只有日期列错位,数值不错位**,污染限于"何时可见"。
4. **物理不可能行(ann < report_date)**:0 行。早知类仅能被真值对账捕获,物理单边规则捕获不了。

### 修复(2026-07-23,同日闭环)

**存量重铺**:`scripts/data/repair_fundamental_batch_ann.py` 按 `lake/fundamental_repair.py`
的三级优先级级联重铺全表(备份于 `data_lake/backups/fundamental_batch_ann_repair_*`):
① 主源 = income/balancesheet/cashflow 三表 `ann_date` 按 (code,end_date) 取 MIN(与守卫
真值定义逐字一致);② 补位源 = tushare income `f_ann_date` 侧车(`build_f_ann_sidecar.py`
拉取,100% 覆盖,仅在主源缺失该 (code,end_date) 时生效——**不与主源一起无差别取 MIN**,
早期实现踩过这个坑:侧车个别行早于三表 ann_date,一起取 MIN 会制造新的"早知"违规,
现级联结构从根上避免);③ 兜底 = 法定披露上限代理(报告期+30/62/31/120天,宁晚不泄),
`ann_date=NaT` 诚实未知。旧(污染)`ann_date` 列改标为 `em_last_touch_date`,仅供审计
溯源,禁作 PIT。修复结果:`cross_table_min` 262,622 行(63.4%)、`proxy_statutory`
151,318 行(36.6%,主要为北交所/退市尾/极早期报告期)。

**ingestion 源头修复**(防止未来重跑/日更再污染):
① `lake/schema.py::YJBB_RENAME` 把"最新公告日期"改名到 `em_last_touch_date`(不再是
`ann_date`);② `scripts/data/build_fundamental_batch.py`(全量重建)抓完原始数据后
调用 `lake.fundamental_repair` 重铺,不再用"报告期+45天"当主口径;③ `lake/update.py::
update_fundamental()`(日更增量)同样接入 `lake.fundamental_repair`,新增报告期与存量
一起走同一套三级级联(此前日更用的是更粗糙的"公告日缺失则+45天"兜底,即使 RENAME
修好后日更也不会自动变准,必须显式接线)。三处共享同一份 `lake/fundamental_repair.py`
核心逻辑(R-ARCH-001:`lake.` 不得 import `scripts.`,故重铺逻辑落在 lake/ 而非
scripts/data/,供 scripts 与 lake 双向消费)。单测:`tests/test_fundamental_repair.py`
10 例(含级联优先级对抗测试——验证过退回旧"无差别取 MIN"写法确实会被同一测试抓红)。

### 判定口径(冻结)

- 早知违规:`true_ann − fb_ann ≥ 3 天`(2 天容忍吸收周末/日历差);真值局限:三大报表自身
  含重述行(9.56% >180d),IPO 前报告期公告日口径特殊。
- 晚知异常:`avail_date − report_date > 180 天`;**不**按违规处理(年报法定 4 个月为最宽,
  但重述/更正公告可合法超期,三大报表同样存在),DQ-2026-001 的晚知问题在"系统性 +365d 平台"
  这一形态,不在单行超期。
- join 未命中 163,125 行(39.4%,akshare 与 tushare 覆盖不对称:退市/北交所/报告期稀疏),
  本审计无法判定,属已知盲区,兜底走法定披露上限代理。

### 消费面(污染半径)

直接读盘:`factors/fundamental.py`、`factors/hq_momentum.py`、`factors/large_cap.py`、
`strategies/industry_rotation.py`、`workflow/phase4_register.py`、`lake/load_lake.py:69`、
`lake/update.py`。

**订正(2026-07-23,曾误报)**:早先版本本条目写"13 个已登记版本受影响",取值方式是对
`strategy_versions.json` 做 `data_snapshot` 字符串匹配"是否含 fundamental_batch.parquet"
——这个判据是假的:`data_snapshot` 只记评估当时数据湖里存在哪些文件,不代表该策略真的
消费了该文件。按 `config`(DSL 候选)与策略模块源码(硬编码策略读 `load_fundamental_panel`
的字段名)逐条核实后,**真正受污染因子影响的是 11 个版本、7 个 family,且没有一个是
"在册"(active)状态**:

| family | version | status | 受污染因子 |
|---|---|---|---|
| autoresearch_2335eeab | v1.0 | 候选 | net_profit_yoy |
| autoresearch_234c8ab7 | v1.0 | 候选 | roe |
| autoresearch_8de70997 | v1.0 | 候选 | roe |
| autoresearch_e181a275 | v1.0 | 候选 | revenue_yoy |
| fundamental-momentum | v0.1 | 候选 | revenue_yoy |
| industry-neglect-rotation | v1.0–v1.3 | 参考 | roe |
| industry-neglect-rotation | v1.4 | 候选 | roe |
| size-earnings | v1.0 | 参考 | net_profit_yoy |

`ontology_industry/v1.0-shadow` 曾被 family id 字符串误匹配("industry"),核实其
`config.signal_source` 指向独立信号文件,与本条目无关,已排除。

**这些版本涉及 2010-2021 窗口的结论按 suspect 处理,重跑未做(见 kill condition)**——
但无一"在册",不构成生产/实盘层面的紧迫性,是研究记录诚实性问题,不是部署风险。

### 止血与现状(守卫口径)

CI 守卫 `scripts/ci/check_fundamental_batch_pit.py` 执法三条,**当前全绿(2026-07-23 复核,
exit=0)**:

- R1 物理零容忍:0 违规,无例外。
- R2 真值对账:`fb_ann < true_ann − 2d` → 红;**修复后 0 违规**,`DQ-2026-001_early_561.csv`
  已收紧为 0 行(原 561 行全部真实修复,不再需要例外;文件名保留原诊断数字作历史标记,
  内容已更新)。
- R3 晚知形态:`avail_date − report_date > 180d` → 红;**例外 = 报告期 ≤2025-03-31 整段豁免**
  (事故窗口,历史遗留,尚未随本次修复移除,见 kill condition)+
  [`DQ-2026-001_late_gt2025q1_403.csv`](DQ-2026-001_late_gt2025q1_403.csv) 逐行豁免(已收紧
  至 92 行——多为真实严重逾期披露的公司,兜底法定上限代理本身合理但仍 >180d,不视为
  新违规;文件名同样保留原诊断数字)。

### repro

```bash
python3 factor_research/scripts/ci/check_fundamental_batch_pit.py       # 守卫本身,exit=0 即通过
python3 factor_research/scripts/data/repair_fundamental_batch_ann.py --dry-run  # 复核重铺口径不写湖
python3 factor_research/scripts/ops/audit_fundamental_batch_pit.py      # 独立诊断脚本,打印同款汇总
```

### kill condition

③ 根因修复(**已完成**:定位语义误用根因 + ingestion 源头修复 + 从 canonical 源重铺
ann_date)后:例外集**已清零/收紧**(561→0,403→92,≤2025Q1 段豁免仍保留——该豁免属于
守卫 R3 规则本身而非例外文件,是否随本次修复一并移除需先确认 92 行残留是否都是真实
严重逾期而非其他遗留问题,留给下一次复核);**随后重跑受影响 registry 版本并回写
strategy_versions 结论**(**6/11 已完成**,5/11 因独立 bug 阻塞,精确清单见 §消费面——
均非在册,详见下方进度),该项**尚不能**转 CLOSED(还有阻塞项未解)。

**进度(2026-07-23,已完成重跑)**:移植 `strategy-path-analysis` 机制(见
`.claude/skills/strategy-path-analysis`,`strategy_registry.attach_path_metrics`)+
`strategies/ast_config_strategy.py`(DSL 候选活跑执行器,同一移植来源)。逐个
live re-run + `--persist-path-metrics`:

| family/version | 结果 | annual (旧→新) | 备注 |
|---|---|---|---|
| autoresearch_2335eeab/v1.0 | ✅ resolved | 0.2397→-0.3054 | 符号反转,与 round7 net_profit_yoy 负IC结论一致 |
| autoresearch_234c8ab7/v1.0 | ✅ resolved | 0.128→-0.3415 | 符号反转 |
| autoresearch_8de70997/v1.0 | ✅ resolved | 0.3082→-0.4371 | 符号反转 |
| autoresearch_e181a275/v1.0 | ✅ resolved | 0.266→-0.4308 | 符号反转 |
| fundamental-momentum/v0.1 | ✅ resolved | 0.2886→0.0924 | 未反转,显著恶化 |
| size-earnings/v1.0 | ✅ resolved | 0.0871→0.1202 | 改善(唯一变好的) |
| industry-neglect-rotation/v1.0-1.4(5条) | ❌ 阻塞,未解决 | — | 见下方独立发现 |

5 个符号反转/显著恶化的案例**不构成"数据修复导致策略变差"的证据**——更合理的读法是:
原先的正收益本身就是建立在污染(滞后约1年)数据上的 artifact,用干净数据后现出原形。
本次未做 `price_unit_blast_radius.py` 式的新旧数据受控 A/B 对比,不作最终归因结论,
如实记录当前差异。size-earnings 是唯一改善的,原因未知(不排除同期还有其他改动)。

**副产品发现(与 DQ-2026-001 无关的独立 bug)**:重跑 industry-neglect-rotation 时发现
`strategies.industry_rotation.run_industry_rotation_strategy` **全版本(v1.0/v1.1 已直接验证)
产出全零收益**(2044 天全部 0,sharpe=nan)。已排除是本次修复引入——v1.0 走"ETF 等权持有"
分支,选股逻辑完全不读 roe/npy 却同样复现零收益,根因在 industry-level 因子计算
(mom/vol/amt_growth)或 `aggregate_industry_data`/`load_industry_groups` 更上游,超出本次
范围未深挖。首次尝试(v1.0)曾把这个零结果误写入台账覆盖真实旧数字,已撤回并归档到
`evidence.metrics_history`(reason=`reverted_broken_rerun`),v1.1-1.4 未再重复同样错误,
直接标记阻塞。
