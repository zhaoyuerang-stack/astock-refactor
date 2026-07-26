# STATUS — 当前进度

> 更新:2026-07-25。任何 AI 进来先读 本文件 + [CLAUDE.md](CLAUDE.md)。
>
> **本文只写「当前状态」,不写历史。** 逐日进度条目滚出后 append 到
> [`docs/archive/STATUS_chronicle_2026H1.md`](docs/archive/STATUS_chronicle_2026H1.md) 并从本文移除;
> 归档区的绩效数字**一律不得作为有效性证据**(产自幸存者偏差与 DQ-2026-001 修复之前)。
> 本文行数上限由 `scripts/ci/check_doc_map.py` 机械约束(≤200 行),超限即 CI 红。

## 台账实况(机械读自 `factor_research/strategy_versions.json`,2026-07-25)

```
families 30 | versions 50 | 参考 23 / 候选 19 / 退役 7 / 已证伪 1
在册 / LIVE = 0
```

**当前没有任何在册策略,生产不跑任何策略信号做实盘决策。**
这不是故障,是 ADR-020(standalone 准入强制 DSR 显著性)清零在册轨后的必然结果 ——
现有候选池全部卡在 DSR 多重测试惩罚或 G5 压力 / G6 成本门上(详见 `TASKS.md` 与 `DECISIONS.md`)。
任何文档、报告、对话里出现"某策略 LIVE / 当前生产 / 年化 xx%"的说法,
先用上面这段机械读数核对;对不上即为过期认知,按 `CLAUDE.md` R-EVIDENCE-001 处理。

## 一句话

**2026-07-23(DQ-2026-001:fundamental_batch.parquet ann_date 系统性错位,发现即修复)**:
  · **发现**:构造新因子(accruals)前核对三表能否合并,发现 `fundamental_batch.parquet` 的
    `ann_date`/`avail_date` 与真实披露日系统性不一致(≤2025Q1 段中位数偏晚约365天)。
  · **根因定位(现场复现,非猜测)**:`lake/schema.py::YJBB_RENAME` 把东财"最新公告日期"
    (公司级、随最近一次披露滚动刷新的字段)误当"本期公告日"直接改名成 `ann_date`。
    `ak.stock_yjbb_em(date="20240630")` 今日查询验证:000001 该列返回其最近一次披露日
    2025-08-23,而 2024-06-30 中报真实公告日是 2024-08-16(tushare 现网核实)。
  · **修复(同日闭环)**:存量按 `lake/fundamental_repair.py` 三级优先级级联(三大报表
    ann_date MIN 主源 → tushare f_ann_date 侧车补位 → 法定披露上限兜底)重铺
    `fundamental_batch.parquet`(备份于 `data_lake/backups/`);ingestion 源头
    (`build_fundamental_batch.py` 全量重建 + `lake/update.py::update_fundamental()`
    日更增量)一并接入同一套逻辑,防止未来重跑/日更再污染。守卫
    `check_fundamental_batch_pit.py` 从 435 处未登记违规转全绿,例外集 561→0 行、
    403→92 行。新增 `tests/test_fundamental_repair.py`(10 例,含级联优先级对抗测试)。
  · **受污染范围**:`factors/fundamental.py` 全部 6 因子
    (net_profit_yoy/revenue_yoy/roe/gross_margin/bp_proxy/ep_proxy)。**范围订正
    (2026-07-23)**:早先按 `data_snapshot` 字符串匹配报"13 个已登记版本受影响",
    该判据是假的(只说明评估时数据湖里存在该文件,不代表消费了它);按 `config`/
    策略源码逐条核实后,真正受影响是 **11 个版本、7 个 family**,且**没有一个是
    "在册"状态**(6 候选 + 5 参考:5 个 `autoresearch_*`/`fundamental-momentum` 候选、
    `industry-neglect-rotation` v1.0-1.4、`size-earnings/v1.0`),非生产紧迫。
  · **机制层已就绪**:移植协作方(grok 独立 clone)的 `strategy-path-analysis` 机制
    (`.claude/skills/strategy-path-analysis` + `strategy_registry.attach_path_metrics` +
    `strategies/ast_config_strategy.py`)——按"可复用工作 skill 化 + 不自欺"原则,只搬
    机制、不采信对方旧结果;本仓独立验证(52 例测试全过)。
  · **重跑收尾(6/11 完成)**:autoresearch_2335eeab/234c8ab7/8de70997/e181a275 四个
    候选活跑后年化符号**正转负**,与 round7 已确认的 net_profit_yoy/roe standalone 负IC
    结论一致(原正收益疑似污染数据 artifact,未做受控 A/B 不下最终归因);
    fundamental-momentum/v0.1 显著恶化未反转;size-earnings/v1.0 唯一改善
    (8.71%→12.02%)。均已 `attach_path_metrics` 诚实回写 + incident 标 resolved=true。
  · **副产品发现**:重跑 industry-neglect-rotation 时揪出一个与本次修复**无关**的独立
    bug——`run_industry_rotation_strategy` 全版本产出全零收益(v1.0 ETF 分支不读
    roe/npy 仍复现,排除因果关联)。已把一次误写入的零结果撤回归档,v1.0-1.4 共 5 个
    版本保持未解决,待该 bug 修复后再重跑。已入 TASKS.md。
  · **残留**:上述独立 bug 未修(非本次范围)+ `docs/data_quality/register.md::
    DQ-2026-001` 因此暂不能转 CLOSED。
  · 详见 `docs/data_quality/register.md::DQ-2026-001`(完整事实/修复口径/repro)。

**2026-07-20(三层分工编排轮:Grok/Sonnet5/Claude 各按判断密度接单,round7/8 needs_human 双闭环)**:
  · **共享树修复(Claude)**:解掉主仓遗留的知识库化单合并冲突(STATUS.md UU,3d10e614);daily-research-round 剧本 §0 加**账本分叉核查**纪律(0b6e1fd7,终结 round6/7/8 三次点名的孤立分支重复 probe 风险,纯纪律行不新增治理工具)。
  · **needs_human① 闭环(Claude,判断类)**:net_profit_yoy 符号疑点核对(7d795b7c)——**无真矛盾**:台账 size-earnings v1.0 正 ic_mean=0.051 是 size 腿主导的混合因子读数,与 standalone 负 IC(raw IS -0.0115/OOS -0.0182)是两个测量对象;且该版 status=参考、passed_all=false、dsr_p=0.603。fundamental.py『LIVE 实证』过期夸大标注已修正,结论回写 direction_registry(成长对若利用须按反向候选专项 probe)。
  · **needs_human② 闭环(Sonnet 5 执行+Claude 验收,2e21e7ad)**:价值对 bp/ep 跨周期反例检验——口径逐函数复用 signal_source_probe 且 round7/8 数字复算逐位一致;分年拆解:2019/2020 残差 IC 双双为负,『OOS 骤强』几乎全由 **2023 单年**贡献(bp 残差 ICIR 1.28),2024 已回落≈0,七年两口径符号完全同步。按预登记判据:**regime 共同贝塔怀疑被加强**。登记簿收紧:结构性论点+非2023年份独立复现齐备前不提『价值对接工厂』。
  · **桌面延迟 L0.1 落地(Grok 委托+Claude 验收,84344c58)**:诊断回合计时日志(roundTiming.cjs fail-open + piBridge WeakMap 侧信道 + 三分支落盘 .runtime/round_timing.jsonl);对抗测试 3 例;验收=47/47 亲跑+活体突变(挖字段 3 例真红/破内层 fail-open 被外层吸收=三层冗余确认)。首次实测:**grok 派单命中权限白名单坑**($(cat) 命令替换破坏前缀匹配→改短 -p 指令引用任务书文件)。
  · **移交 owner**:round-9(限售解禁 probe)合回 main 被本会话权限分类器拒(合并类命令不放行)——改动面已核实与 main 零冲突,命令:`git merge claude/daily-round-9 --no-edit`。round-10 worktree 有未提交在途改动(疑今晨定时轮中断遗留),未触碰。

  · **背景**:生成端活方向清零(`direction_registry` 11 条 = 6 falsified / 4 weak / 1 mixed,0 active);自动环最近一轮 83 候选 0 过 L3;round7/8 连续在同一空白区打转——内部空白区趋于耗尽,假设进水管成为瓶颈;literature-scan 被"枯竭形式判定+逐次人批"锁死(判定器窗口样本 1/4 不足),事实枯竭先于形式枯竭。
  · **交付**:新剧本 [`quant_strategy_scan.md`](factor_research/docs/agent_skills/quant_strategy_scan.md)——检索源四类(卖方金工公开/策略社区/开源实现/英文可迁移)、**可行性五判**(long-only 可投/T+1 涨跌停/日频 PIT 可得/审慎成本后有肉/非证伪路换皮)、**三路分流**(信号层 Hypothesis 入 factory queue / 构造层 direction_registry NOTE / 数据需求 data_source_backlog),五判存活候选必过 `strategy_idea_check` 确定性预检(ADR-037);daily-research-round §2 扩**四方向轮换** + ①/②无目标时 fallback ④;定时任务 SKILL.md(~/.claude/scheduled-tasks/daily-research-round)同步方向枚举;ADR-039 常备授权(外探门仅此局部放宽,literature-scan 维持枯竭触发+人批不变)。
  · **红线不变**:业界/社区回测数字一律待证伪不作证据(R-LLM-001);只产草案/报告不碰台账(R-WF-001);进搜索即记 n_trials;不复制实现、PIT 对齐(R-DATA-003)。


## 各层状态

> 本表描述**模块能力就绪度**(代码是否可用),**不代表有在册策略** —— 在册数以上文台账实况为准。

| 层 | 状态 | 说明 |
|----|------|------|
| 数据基础设施 | ✅ | data_lake 全市场+全历史+含退市股(2026-07-04 回补 253 只) |
| 回测内核 | ✅ | BacktestEngine 统一接口(R-BT-001 唯一权威) |
| Regime 引擎 | ✅ | engine/regime.py 多维分类(trend/vol/liquidity/breadth) |
| 不对称性审计 | ✅ | factory/analysis/asymmetry_audit.py |
| Composer | ✅ | engine/strategy_composer.py regime 编排自动化搜索 |
| Alpha 因子框架 | ✅ | factors/alpha/ (Base+Blend+Search+Transforms) |
| DSR+PBO 验证 | ✅ | factory/analysis/wf_validator.py |
| 策略发现 | ✅ | workflow/ Phase1-4 + FactorSpace 搜索 |
| 策略库 | ⚠️ | 机制就绪,**在册 = 0**(见台账实况) |
| 生产入口 | ✅ | run_daily.py 链路可跑;当前无在册策略可发信号 |
| 模拟盘 | ✅ | paper_trade.py 多账户并行(R-PROD-001:永不自动真实下单) |
| 失效监控 | ✅ | decay_monitor.py |
| 健康检查 | ✅ | health_check.py + 桌面通知 + Obsidian |
| 调度层 | ✅ | launchd 四件套:daily-update / weekly-maintenance / api(:8011) / web(:3000) |
| 跨资产轮动 | ✅ | paper_engine 债券腿已接;实盘人工 |

## workflow/ 发现流水线

```
Phase 1  合成数据数值穿越    5 项检查, 秒级, 8 核并行
Phase 2  不重叠三段回测      3 段+成本+相关性, 分钟级, 4 核并行
Phase 3  Walk-Forward       12 窗口滚动, 小时级, 顺序
Phase 4  自动注册+教训回流   去重机制, 可复现元信息
```

## 生产入口

```bash
python3 run_daily.py --no-update          # 当日信号(在册为 0 时不产实盘决策)
python3 scripts/ops/paper_trade.py        # 模拟盘 T+1 执行
python3 scripts/ops/health_check.py       # 健康检查 + 通知
python3 scripts/research/decay_monitor.py # 失效监控
python3 workflow/explore.py               # 并行探索新策略
python3 apps/portfolio_cli.py --analyze   # 组合分析
```

## 历史档案

* 逐日进度条目(2026-07-18 及更早)+ 2026-06 专章(核心结论 / 策略库 / 各类审计)
  → [`docs/archive/STATUS_chronicle_2026H1.md`](docs/archive/STATUS_chronicle_2026H1.md)(冻结,只读,数字不作证据)
* 踩坑与教训 → [`LESSONS.md`](LESSONS.md);决策记录 → [`DECISIONS.md`](DECISIONS.md);开放任务 → [`TASKS.md`](TASKS.md)
