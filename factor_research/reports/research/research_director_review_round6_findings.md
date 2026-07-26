# daily-round-6 研究总监审视:算力再分配建议

> 方向③(研究总监审视)。本文档**不判断任何 alpha 有效性**(R-LLM-001),只机械读取既有台账/审计产物/分支历史,给出算力再分配建议。所有"是否有效"的判断仍归 9-Gate/DSR/回测(确定性代码)。
> 承接 [`research_director_review_round3_findings.md`](research_director_review_round3_findings.md)(2026-07-05,8 天前)——本轮核实其结论现状、发现其预判的风险已经真实发生一次,并新增一项本轮修复。
> 证据来源两处:① 本轮工作分支 `claude/daily-round-6-review`(基于 `main@f66cd419` 新建);② 主仓物理工作目录 `/Users/kiki/astcok`(当前 checkout 在 `codex/xiaochengxu`,只读引用,未做任何写入)。

## 0. 一句话结论

**round3(2026-07-05)警告的"多分支互相看不到对方成果,可能重复探索"风险,已经在 8 天内真实发生一次**:round4(2026-07-06)对资产负债表运营质量三因子(bargaining_power/receivable_intensity_chg/inventory_intensity_chg)做的 probe-signal-source 体检,因分支从未合并主线而对后续工作不可见——2026-07-12,另一次独立研究(在 `main` 分支上)重新对**同样三个因子**跑了几乎相同的 probe,产出几乎相同的阴性结论(bargaining_power 正交保留率 17% vs round4 的 17%;两个 Δ 强度因子 IS→OOS 符号反转)。这是一次真实发生、可精确定位日期与文件的重复算力浪费,不是假设风险。本轮已把 round1/round4/round5 三个孤立分支里、从未传播到 `main` 的方向级教训补记入 `direction_registry.json`(2 条新增,round5/round4 的既有条目在 07-12 已有更晚版本覆盖),并**修复了 `main` 当前处于失败状态的 `test_shipped_registry_is_valid_and_evidence_backed`**(2 个 07-12 新增条目的 `scope_factors` 引用了未进 `ALLOWED_FACTORS` 白名单的因子名,导致该测试在 `main` HEAD 上是红的)。

## 1. 证据清单

### 1.0 轮次编号说明(本轮编号取自分支惯例,非 main 已 track 的 ledger 文件)

`strong_ai_rounds.jsonl` 在 `main` 上实际只 track 到 **round=3**(round4/round5 的分支各自在本地 jsonl 追加过 round4/round5 行,但只有 round4 分支这一行的追加从未随任何 commit 落进 main;round5 的 commit `40cbd17e` 只碰了 `direction_registry.json`,没碰这个 jsonl 文件)。若严格按剧本"读最后一行 +1"会得到 round=4,但 `claude/daily-round-4`/`claude/daily-round-5` 分支已经用 4/5 自我识别(分支名、commit message、direction_registry 证据指针里的 `round=4`/`round=5` 引用),用 4 会造成两个不同内容的"round4"共存混淆。本轮沿用分支命名的既有实际编号(6),不重新按 ledger 文件字面值编号——这本身正是 §1.2 分支碎片化问题的又一体现,记录在此供 owner 判断是否要处理 ledger 文件本身的编号方式。

### 1.1 台账现状:仍是 0 在册,与 round3 结论一致

- `strategy_versions.json`(`codex/xiaochengxu` 工作树,2026-07-13 读取)含 31 个 family(round3 时 30 个),version-level status:参考 30、候选 14、退役 7、`REJECTED_BY_ADVERSARIAL_DECAY` 3(round3 时 2)、已证伪 1,**"在册" 仍 0 个**。核心结论 8 天未变。
- `reports/decay_status.json`(generated_at 2026-07-12T17:51:13):`no_registered=true, strategies=[]`,与上同。

### 1.2 分支碎片化代价实证:round4 的方向级发现被孤立分支吞掉,6 天后被独立重复

这是本轮最核心的发现,给出精确证据链(均为可复核的 git 只读操作,未修改任何历史):

- **round4(2026-07-06)** 的 commit `1cd57635` 在其自身分支 `claude/daily-round-4` 上对三因子跑了 probe-signal-source 步骤 3,写入该分支自己的 `direction_registry.json`(entry id=`fundamental-quality-balancesheet-weak`):bargaining_power 正交保留率 17%(被 size corr=0.206/流动性解释)、receivable_intensity_chg IC 符号与 thesis 相反、inventory_intensity_chg IS→OOS 符号反转(留存 -49%)。
- **该分支从未合并进 `main`**(`git merge-base --is-ancestor claude/daily-round-4 main` → 否),其配套因子代码 `factors/fundamental_quality.py` 至今在 `main` 与 `codex/xiaochengxu` 两条活跃线上都**不存在**(`git show main:factor_research/factors/fundamental_quality.py` → 路径不存在)。
- **2026-07-12**(round4 提交 6 天后),`main` 分支的 `direction_registry.json` 出现了一条新条目 `balancesheet-operational-quality-weak`,证据文件 `probe_fundamental_quality_20260712.md` + 3 个同名 probe JSON——**范围因子完全相同**(bargaining_power/receivable_intensity_chg/inventory_intensity_chg),**结论方向完全相同**(bargaining_power 正交保留率 17%、两个 Δ 强度因子 IS→OOS 翻号)。这不是巧合式的相似结论,是对同一组因子做了第二次几乎相同的全市场 probe 跑批(数据加载、IS/OOS 切分、残差正交化、trial 记账全部重新执行一遍)。
- **含义**:如果 round4 的分支在完成后走一次"仅 `direction_registry.json` 快速合并"(不需要合并整个分支、不涉及任何 registry/promote/成本口径),这次重复 probe 完全可以避免。这把 round3 §1.5 的"结构性观察"从推测变成了有日期、有文件名、可审计的真实浪费案例。

### 1.3 main 当前 `test_direction_registry.py` 处于红灯状态(本轮已修复)

- 在本轮工作分支(基于 `main@f66cd419`)运行 `PYTHONPATH=factor_research python3 factor_research/tests/test_direction_registry.py`,`test_shipped_registry_is_valid_and_evidence_backed` **失败**:07-12 新增的 3 条 `direction_registry.json` 条目(`expectation-gap-trailing-oos-collapse`/`expectation-gap-guidance-weak`/`balancesheet-operational-quality-weak`)把 `implied_growth_gap`/`peg_inverse`/`guidance_gap`/`bargaining_power`/`receivable_intensity_chg`/`inventory_intensity_chg` 写进了 `scope_factors`,但这些因子从未经 `@register_factor` 接入 `factory/autoresearch/registry.py::ALLOWED_FACTORS`(`factors/expectation_gap.py`/曾经的 `fundamental_quality.py` 都是独立 probe 代码,未接白名单)——违反测试对 `scope_factors` 的白名单存在性断言。
- 同一测试在 `codex/xiaochengxu` 工作树(`/Users/kiki/astcok` 当前 checkout)跑是**绿的**——因为那条分支的 `direction_registry.json` 内容完全不同(只有 5 条,含 `main` 没有的 `illiquidity-amount-not-volume`,没有 07-12 新增的 3 条),这是分支分叉的又一实证:两条线的方向教训台账互不相交。
- **本轮修复**:把这 3 条的 `scope_factors` 清空为 `[]`(与 round4 建立的既有惯例一致——"因子从未进白名单,scope_factors 留空以满足测试断言"),不改变任何证据/结论文本。修复后本地 `test_direction_registry.py` 12/12 通过,`test_knowledge.py` 9/9 通过,`check_layer_deps.py` 通过。**这是文档/台账一致性修复,不涉及任何 alpha 有效性判断。**

### 1.4 round1/round2 的方向级教训此前从未回写登记簿(本轮已补记)

- `claude/daily-round-1`(2026-07-03)证伪 `price_channel_breakout`(与既有反转簇相关 -0.54~-0.59),`claude/daily-round-2`(2026-07-04)证伪 `analyst_recommend_breadth`(size 代理,IS→OOS 翻负)——两轮结论都完整写在 `main` 已 track 的 `strong_ai_rounds.jsonl` 里,但**从未写入** `direction_registry.json`(该文件是生成器实际消费的机器可读知识源,ledger jsonl 不是)。`probe-signal-source` 步骤 8"结论回写纪律"是 2026-07-02 才固化的规范,round1/round2 的执行时间点上该步骤可能尚未被严格套用到方向①的场景。
- 本轮已补记两条(`price-channel-breakout-reversal-mirror` / `broker-recommend-breadth-size-proxy`),`scope_factors` 留空(因子代码同样只存在于孤立分支),证据指针指向 `main` 已 track 的 ledger 条目 + 孤立分支的 commit hash。

### 1.5 metasearch 信息地图已 20 天未刷新

- `reports/research/metasearch_findings_20260623.md` 仍是最新版本(mtime 06-23)。round3(07-05)已建议重跑,当时是"距今 12 天";现在是 **20 天**,且 3 个原始空白区中 vol_breakout(round1 关闭)、跨资产腿(其他 agent 已挖掘,见 §1.6)均已处理,基本面族(round4→07-12 重复 probe→已关闭核心三因子)也接近穷尽。**当前 `direction_registry.json` 里唯一仍是 `BOOST` 状态的 `frontier-fundamental-family` 条目已经名不副实**(其 `scope_factors` 里的 5 个因子 net_profit_yoy/roe/bp_proxy/revenue_yoy/ep_proxy 尚未被针对性 probe,但"运营质量"这条子路径已经被 round4+07-12 两次证伪关闭)——继续沿用旧地图定位下一个真空白区,边际价值正在降低。

### 1.6 cross_asset_leg_search / composite-portfolio 仍在其他分支活跃

- `reports/research/cross_asset_leg_search.json` 的 `run_date=2026-07-12`,是最近一天。round3 标出的"跨资产腿已被并行 agent 覆盖,不要重复投入"依然成立,且证据更新到了昨天——建议维持不重复。

### 1.7 研报-NLP 管线:状态不明,不能定性为"已修复"

- `research_ledger` 显示 `report_nlp_pipeline.py` 在 2026-07-12 06:59 的 3 次运行 `failed=0`(此前 07-03~07-11 逐日必有 3-11 条失败,模式高度一致)。但 `data_lake/research_pdf/` 最新日期子目录停在 **2026-07-11**,07-12/07-13 均无新 PDF 落地——"0 失败"更可能是"当天没有待处理文件可失败",而不是下游 4 类 bug 被修复。**不确认为已解决**,按 R-DATA 纪律(§9 原则⑦:数据更新失败必须显式记录,不得静默使用半截数据)如实记录为"待核实":需确认是研报 PDF 抓取本身停更(新问题),还是下游 bug 真的被修了但恰好这两天没有新输入可验证。

## 2. 算力再分配建议(均为搜索空间增删建议,非有效性判断)

| 方向 | 建议 | 理由 |
| --- | --- | --- |
| 基本面运营质量三因子(bargaining_power 等) | **不再投入**,已被 round4+07-12 两次独立证伪关闭 | §1.2/§1.3,重复概率已验证发生过一次 |
| 跨资产腿 / composite-portfolio | **不再投入** daily-research-round 算力 | 其他分支仍在活跃产出(§1.6) |
| price_channel_breakout / analyst_recommend_breadth | 维持关闭 | round1/round2 已证伪,本轮已补记登记簿防遗忘 |
| metasearch 刷新 | **建议尽快重跑一次**(factor_mi_audit/information_map) | 现有地图 20 天未更新,3 个原始空白区已全部处理完,继续沿用意义有限,是下一轮方向①/②最大的产能瓶颈 |
| `frontier-fundamental-family` BOOST 条目 | 建议下次重跑 metasearch 时一并复核其 `scope_factors`(净利增速/ROE/BP 等)是否仍是真空白,还是该并入下一批新地图 | 该条目创建于 07-02,尚未过期(2026-12-29),但其相邻子路径已被两次证伪 |
| 研报-NLP 管线 | **不建议本轮修复代码**(超出方向③范围),但建议下一轮/人工核实 `data_lake/research_pdf/` 07-12 起为何无新文件落地 | §1.7,不能把"0 失败"误读为"已修复"而放松关注 |
| 分支碎片化 | 建议 owner 考虑一个轻量级机制:**daily-research-round 分支完成后,至少把 `direction_registry.json` 的新增条目单独快速合并到 main**(不需要合并整个分支/因子代码),因为这是唯一的机器可读防重复知识源,合并成本极低(纯 JSON 追加,已有证据门控兜底) | §1.2 的重复浪费已实证发生,该文件专门设计用来防止这类浪费,但它本身如果留在孤立分支上就完全失效 |

## 3. 需要人裁决的事项(needs_human)

1. **是否建立"direction_registry.json 优先快速合并"机制**——§1.2 已证实至少一次真实重复浪费(round4 三因子 07-06 vs 07-12 重复 probe),该文件是防止此类浪费的专用机制,但只要留在孤立分支上就不生效。不涉及策略入册/成本口径,风险低。
2. **`data_lake/research_pdf/` 07-12 起无新文件落地是否为新问题**——不确定是数据源停更还是下游 bug 修复后恰好无新输入;需要用真实新 PDF 验证下游链路是否真的不再报错。
3. **metasearch 是否近期重跑**——20 天未更新,3 个原空白区基本处理完,直接影响下一轮方向①/②有没有新目标可选。

无阻塞性/高风险裁决项(不涉及口径污染、不涉及入册/部署;本轮唯一的代码级改动是 `direction_registry.json` 3 处 `scope_factors` 清空+2 条新增,均已过 `test_direction_registry.py`/`test_knowledge.py`/`check_layer_deps.py`)。

## 4. 未做的事(明确边界)

本轮**未**:碰 `strategy_registry` 写入口、`workflow.promote`、部署清单、成本模型数值、holdout boundary、样本/shift/T+1 口径、`ALLOWED_FACTORS` 白名单本身(选择清空 `scope_factors` 而非给未验证因子开白名单);**未**修复研报-NLP 管线代码(超出方向③范围,且证据不足以定性问题性质);**未**尝试合并 `claude/daily-round-1/2/4` 到 `main`(超出单 agent 权限,§3 needs_human ①留给 owner);**未**对 `codex/xiaochengxu` 分支做任何写入(只读引用其物理工作目录)。
