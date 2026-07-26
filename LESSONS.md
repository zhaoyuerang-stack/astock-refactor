# LESSONS - 踩过的坑与关键决策

> 项目内可见的经验库(我的私有 auto-memory 作补充)。新踩的坑、新做的决策往这里加。

> **写入路由(教训知识库化纪律,2026-07-18)**:本文件是 append-only 叙事日志,**不是机器可读知识库本体**。每写一条新教训,先问"能上哪级阶梯",同时落到机器能消费的最高层:
> ① **自欺模式可机械检测** → `scripts/ci/` 确定性守卫(先在 `TASKS.md` 立项);
> ② **方向级证伪/太弱/空白**(因子族/数据源/用法粒度)→ [`factor_research/knowledge/direction_registry.json`](factor_research/knowledge/direction_registry.json)(带 evidence 指针+精确 scope+revival_condition,生成端机器消费;无证据条目会被自动忽略);
> ③ **单候选粒度验证结论** → `knowledge/findings.json` 由 `record_from_validation()` 机器自长,勿手写;
> ④ **操作性雷区**(接口/环境/流程)→ 对应 `factor_research/docs/agent_skills/` 剧本;
> ⑤ **叙事与"为什么"** → 本文件。
> 只写本文而漏掉 ①②④ 的教训 = 半知识库化:自然语言生成器不消费,算力会反复烧回死路(2026-07-18 回填了 8 条积欠方向条目,教训是别再积欠)。scope 必须精确到**用法**——Alpha101"standalone long-only 证伪"但"DSL 成分合法",划宽了误杀活方向,划窄了白烧算力。

## 因子评估 / 统计量
- **正交增量 alpha ≠ 残差均值(OLS 数学坑)**(2026-06):算「子版本对父版本的正交增量 alpha」时,把它写成 `mean(child - a - b·parent)×252` → **永远≈0**。因为带截距的 OLS 残差均值恒等于 0(最小二乘正规方程性质)。**正确 = 截距 a 的年化**(`a×252`,父版本无法解释的那部分日均收益)。修复后 illiquidity/v3.1 对 v1.3 的 incΑ 从 0.0 变 12.9%(高相关 corr=0.79 但仍有实质增量)。`lineage_pbo.py`。
- **9-Gate 大量已算指标在 `summarize()` 被默默丢弃**(2026-06):`NineGatesEvaluator` 内部 gate2 早算了 `nw_icir/monotonicity_corr/ic_decay`、gate3 算了中性化后 `neut_nw_icir/icir_retention`、gate6 算了 `cost_decay_rate/capacity_limit_aum`、gate7 算了 `bull/bear_sharpe`,但 `NineGatesReport.summarize()` 只留了 DSR/PSR/WF/CV/tail 一小撮 → 台账/前端看着像「没算」。**补字段优先查 summarize() 的留存白名单,别急着写新计算**。
- **PBO 复用家族多版本做 CSCV**(2026-06):`core/analysis/walk_forward.py::pbo_cscv` 已实现,输入 `{name: 日收益}`。把**同一家族的多版本当策略池**喂进去,直接量化「样本内最优版本是否样本外塌陷」=版本选择过拟合。需先留存每版本 gate5 日收益(`data_lake/version_returns/`,9-Gate 持久化时顺带存,零额外回测)。注意:版本高度相关(corr>0.9,如换皮/`-full` 变体)时 PBO 会偏高且意义弱化--高 PBO 本身可能就是「版本近乎重复」的信号。
- **`attach_nine_gate` 是整体替换不是合并**:`v["nine_gate"] = dict(summary)` 会覆盖整个字段。分阶段往 nine_gate 加字段(如 2A 摘要 + 2B/2C lineage)必须**先 `_load` 读现有 nine_gate → update 合并 → 再 attach**,否则后写的抹掉先写的。
- **全局平均优化的“晴天因子陷阱”与多 Regime 极小值极大化 (2026-06-30)**：以全局平均作为搜寻适应度会选出拟合牛市的“晴天因子”，在发生踩踏期等极端政权切换时面临毁灭性塌陷（例如 `revenue_yoy + alpha_015` 在 2024 年初小盘股流动性危机期间 ICIR 瞬间降到 `-0.05` 变为纯噪声，回撤达 `-31.74%`）。改用 Min-Max 极小值极大化极性 Regime 生存适应度后，引擎能自发收敛出对不同 Regime 均具备防御底线的 Champions（如 `alpha_055` 的“超跌成长股反弹”模式，在踩踏期保持 `0.46` 显著 ICIR，回撤大幅收敛），是构建多政权组合或战术影子池热备的利器。
- **防过拟合门禁自动搁置超额但长周期不显著的因子 (2026-06-30)**：尽管新搜寻产生的另类因子（如反转成长股策略）在近三年样本外（OOS）展现出了极强的爆发力（年化收益率达 `27-28%`，夏普 `0.82-0.85`），但因为 2018-2020 年“大盘动量抱团行情”下抄底逻辑的灭顶之灾（长周期亏损），其长周期 DSR 仍无法显著通过。系统门禁程序在此时起到了绝佳的自动过滤器作用，将其安全地 `"shelved"`，证明了防 P-hacking 自律的有效性。
- **small-cap-size 换手是结构性的,降换手救不动 DSR**(2026-06-22,别再试):small-cap-size/v2.0 真有 alpha(年化21.6%/回撤-17.7%/夏普1.38/净化CV过)但 DSR=0.086 差一口气。想靠降成本提净夏普压 DSR 时,试了**两个对症单假设全失败**:1 调仓 20→40 天:换手 31.8x→30.2x(几乎不动);2 持仓缓冲 keep50/75(已持有仍在 top50/75 就不卖):31.8x→29.8x。**根因**=小盘成分剧烈轮动 + MA16 二值择时全进全出(翻熊清仓、翻牛买回),不是调仓频率或成分缓冲能解决。别再走降换手死路;真要救只能「平滑择时(band 替二值)」或「换低换手新因子族」,但前者是风控非 alpha(ADR-018)。
- **救 DSR 是自缚--越救惩罚越重**(2026-06-22):DSR 只能靠提夏普(`n_trials` cannot be manually adjusted). 但「试配置救 DSR」本身是新搜索 = 新 trials. 若登记变体，n_trials 必须累积 → DSR 更差。**对症单假设失败即停，不许调到「刚好过 0.05」**。
- **反转因子的“双阀门控风控”拯救模型 (2026-06-30)**：针对反转策略（Reversal）由于逆风风格或微观踩踏而产生崩塌的问题，我们实现并验证了**价格一阶风格门控（NAV MA40 均线，防长周期无声失血）** 与 **成交量二阶加速度熔断器（Volume Acceleration，防短期资金踩踏）** 融合的双阀门控模型。在 2018-2026 样本内，该模型成功将 Reversal 因子的最大回撤从 **`-83.26%` 大幅压降至 `-54.94%`**，使收益扭亏为盈；在 2023-2026 样本外，成功将最大回撤从 **`-57.53%` 斩半至 `-30.28%`**，夏普比率（`0.49` vs `0.53`）和利润提取效率高度留存。这证明了 A 股风控的底层原则：**大级别用一阶价格风格硬控，微观层面用二阶量能导数控爆，能完美规避常规波动率控制（GARCH）的“仓位饥饿”弊端。**
- **非相关多策略组合的“回撤对消”与夏普跃升 (2026-06-30)**：当独立策略因为大周期逆风（如 Reversal 的 2018-2020 冰封段）或单策略瓶颈无法取得standalone通关时，将其纳入多策略复合组合（40%小盘非流动性 + 40%大盘动量双阀 + 20%反转双阀）表现出了极其出色的平滑效应。在 2023-2026 样本外，组合不仅跑出了 **`17.68%`** 的高额年化收益，更将最大回撤压缩至惊人的 **`-15.51%`**（甚至低于各单一子策略的自身回撤！），夏普比率跃升至 **`1.16`**。这证明了资产组合理论（MPT）的经典结论：**正交/低相关性变体在双阀风控的保护下，虽然单体不能注册，但其组合贡献的非系统性风险对冲是极具价值的，是平滑实盘净值回撤的战略力量。**



## 数据源 / 联网
- **东财封禁规律**:逐只接口下 40-50 只就封(返回空 / JSONDecodeError),降速也压不住。**解法 = 换批量/聚合接口**(按报告期 `yjbb_em` 把请求从 2万 → 几十次),**绝不加多线程**(更快触发封禁)。批量接口还白送 退市股 + 公告日 + 行业。
- **akshare hang**:某些请求卡死整个流程。唯一可靠超时 = **daemon 线程 + join(timeout)**(超时后后台自灭);ThreadPoolExecutor(shutdown 会等 hang)、socket.setdefaulttimeout、requests monkey-patch 都无效。
- **代理**:本地 clash(7897)下 **新浪源可用、东财 push2 被拦**(ProxyError/502);加 `DOMAIN-SUFFIX,eastmoney.com,DIRECT` 可恢复东财。联网需 `dangerouslyDisableSandbox`。
- **代码列表偏差**:旧 stocks.json 只有沪市主板(60开头)→ 严重样本偏差(纯蓝筹天花板仅 17%)。必须全市场(`ak.stock_info_a_code_name()`;新浪源加 sh/sz 前缀,北交所 4/8 跳过)。
- **Python 解释器**:回测/工厂脚本必须用 **`/usr/bin/python3`**(系统 Python,有 pandas/numpy);homebrew 的 `/opt/homebrew/bin/python3` 没装,会 `ModuleNotFoundError: pandas`。只用标准库的脚本(如 `strategy_registry`)两个都能跑,**容易掩盖这个坑**--跑回测一律 `/usr/bin/python3 -m ...`。
- **日更卡死排查(数据停在某天不前进)**(2026-06-22):症状 = 数据湖 last_date 停在 N 天前、前端如实显示旧数据。根因两类:1 **陈旧锁** `logs/daily_update/.scheduled_daily_update.lock` 被**已死 PID** 持有(`ps -p <pid>` 确认死)→ 后续 `scheduled_daily_update` 自跳过(`status=skipped_locked`)。注:flock 进程死会自动释放,但锁**文件**残留易误导,直接 `rm` 掉。2 子步脚本崩:`report_nlp_pipeline.py` 的 `def f()->dict|None:` 在**旧解释器**下 `TypeError: unsupported operand |`(PEP604 注解 def 期求值)→ 加 `from __future__ import annotations` 让注解惰性化,任何解释器都不崩。**解卡 = 删锁 + 修解释器 bug + `--force` 重跑**。但数据补上后信号/paper 仍可能不动--那是 readiness **正确 fail-closed**(部署腿被降级/decay red),不是日更没跑,得看 `[readiness] blocking=[...]`。

## 数据正确性
- **防未来函数**:财务用**公告日(ann_date)**对齐交易日 ffill,T 日只用 T 日前已披露。验证:ROE 变化点应落在财报披露日之后(茅台年报在 4 月)。
- **复权陷阱**:后复权价 ÷ 原始 EPS 算 PE 量纲不匹配(虚高数倍)。**估值 PE/PB 必须用不复权价**。同理**模拟盘/实盘下单股数、容量参与率也必须用不复权价**--后复权价虚高数倍(茅台后复权 8859 vs 真实 1306),按它算 `shares=预算/价//100×100` 会把小盘股也买成 0 股(2026-06 `paper_trade` 踩到:预览买 0 只;改读 `data_lake/price/daily_raw` 的 `raw_close` 后正常买满 25 只)。
- **当天信号必须用因子真正依赖的字段判数据完整**(2026-06):每日盘后增量只更后复权 `daily`,不复权 `daily_raw`(目前周度维护)滞后;而 `small_cap_factor/timing` 用的 `amount=volume×raw`,raw 缺则**最新日 amount 全 NaN → factor 全 NaN → 选不出 top25、择时值失真**(实测 06-03 close 有 4953 只但 amount=0;更早 06-04 是当天盘后只抓到 117 只)。`latest_signal` 用 `close.index[-1]` 会撞这个残缺日,今天碰巧空仓没爆,但择时值已脏(同一天 -2.99%→-3.46%→-3.89% 随口径变干净)。**修复**:`load_price_panels` 按 **amount 完整性(非 close)**截断尾部不完整日--取最近 60 日有效股数中位,截断到最后一个 ≥0.7×常态 的交易日。教训:完整性判断要用**下游真正消费的字段**,表面有 close 不代表 factor 能算。**已根治(2026-06-05)**:`fetch_raw_close` 改拉不复权 OHLC + 增量模式,`daily_update` 每日同步 `daily_raw`,amount 不再滞后。
- **模拟盘=真实盘的成交口径**(2026-06,用户要求"所有模拟按真实盘"):`paper_trade` 重构为 **T+1 开盘价成交**--T 日盘后出信号、收盘后才看到,只能次日开盘买(pending order 跨天结算);成交/估值全用**不复权** `daily_raw`(raw_open 成交、raw_close 估值);**停牌**(当日无 open)不可买卖、**一字涨停**买不进、**一字跌停**卖不出。**涨跌停价必须按分四舍五入** `round(prev_close×(1±limit), 2)`--否则 6.73×1.1=7.403 会漏判开盘 7.40 的涨停(端到端测试抓到)。板块幅度 主板10%/创业科创20%/ST5%。**回测口径(收盘撮合)不动--回测归回测、成交归真实买卖**(两套口径分离)。
- **成交额 amount 两层单位坑**(2026-06):`data_lake` 的 `amount` 是 `volume×复权close` 补出来的--1 用**复权价** → 复权因子逐股不同,**污染 `small_cap_factor`/`small_cap_timing` 的截面排序**(偏向复权因子小的次新/老股,选股+择时双中招);2 `volume` 单位是**手**(×100 股)。真实成交额 = `volume×100×不复权价`。已在 `core/backtest.py::load_price_panels` 修正--消除污染后 v2.0 选股变、全部数字要重测。凡用 amount 的截面排序(选股)或绝对值(容量)都必须用此口径。
- **质量判定**:区分真问题(OHLC 错/负价/跳变>50%)vs A股正常现象(停牌=孤立缺失/新股首日/一字板)。把停牌当问题会让干净率从 97% 假跌到 68%。交易日历用几只超级大盘股**高频交集**(非并集)。
- **波动时滞因子的数据泄露与均值回归陷阱**(2026-06-24):
  在进行产业链波动时滞因子(WDSF)测试时,踩了两个严重的**数据泄露坑**与一个**均值回归陷阱**:
  1 **金库边界泄露 (ADR-021)**:回测绝不能穿透 `2025-01-01` 的金库起点,所有测试必须在 `2024-12-31` 严格物理截断,否则会导致金库数据污染。
  2 **参数前瞻泄露 (Look-Ahead Bias)**:禁止在回测中硬编码未来周期(如2024-2026年)拟合出的固定时滞($\tau_i$)。必须在每个调仓日 $t$,仅使用 $[t-252, t]$ 的历史滚动窗口动态拟合最优时滞,确保参数估计无未来信息。
  3 **全宇宙价值陷阱与垃圾股偏差 (Trash Bias / Value Trap)**:若在无基本面过滤下将 WDSF 盲目推向全市场,算法会系统性买入长期阴跌、基本面归零的垃圾股(因为其股价 $P_i$ 极低导致 WDSF 虚高),导致回测录得 -12.96% 的年化和 -74% 的崩盘。**结论**:物理时滞因子必须绑定在人工白名单/供应链图谱(如 CPO 链)或配置严苛基本面过滤门槛(如 ROE > 6%)的宇宙中,才能稳定变现。

## 数据 bug: 科创板(688)amount 放大约 100 倍 (2026-06-14)
测门控容量时诊断发现:large-cap 持仓里 **688256 显示 4912亿/日、688981 2838亿、688111 1557亿** --物理不可能(>很多天的全市场成交)。**科创板(688xxx,可能含创业板 300)的 amount 在数据湖被放大约 100 倍**,几乎肯定是这些板块 volume 单位(股 vs 手)在 `amount=volume×100×raw` 的 `×100` 转换时错配(科创板 volume 已是股,不该再 ×100)。**影响**:任何吃科创板 amount **绝对值**的计算(large-cap 容量、科创板 illiquidity/Amihud、成交额排序里科创板的相对位置)都被污染。**小盘策略恰好免疫**(`small_cap_factor=-log(amount)` 选 amount 最低的微盘,科创板高 amount 根本进不了池),故小盘容量 ~2千万 一直可信。**诊断纪律**:capacity 脚本打印"最高3持仓 ADV"才抓到--4912亿一眼假;只看中位(746亿)会第三次报错数。
**已修 (2026-06-14)**:`lake/load_lake.py::_normalize_star_volume` 在唯一 load 点对 **688 列 volume ÷100**(股→手),使 `amount=volume×100×raw` 全市场一致。验证:寒武纪 17734亿→173亿、中芯 13194亿→133亿(均合实),茅台/比亚迪/宁德(创业板 300)**不变**,全市场已无 >2000亿/日 异常。**300 创业板 volume 本就是手,不在修正范围**。
**连带(关键)**:修复后 688 amount 不再虚高 → 它们在 `-log(amount)` 排序里可入选小盘,~27% 调仓含 7/25 只科创板。**显式决策=排除**:`StrategyConfig.exclude_star=True`(默认)在 `_drop_star` 把 688 移出 universe,**因为科创板加入不增收益还降夏普**(纳入 28.2%/1.85→**夏普 1.65**;排除 27.9%/**1.85**;2019.7后同向)--之前误判"+6pp 来自科创板"是拿纳入版对**陈旧的 22.2% 文档基线**比错了,苹果对苹果科创板≈零 alpha+加噪。50万门槛/20cm 进一步不利。纳入与否现在是 `exclude_star` 显式开关,不再靠数据 bug 隐式排除。
**另记·已对账 (2026-06-14)**:当前排除版小盘 IS 25.9~27.9%/1.69~1.85 远高于台账 v2.0 22.2%/1.38--**config 与台账完全一致**,差异来自登记之后两提交:`5b4e2a90b`(Phase-2 引擎迁移+read-only array 修复)、`053d93441`(BacktestConfig.start 实化为统计窗口语义)。688/VetoFilter **非**主因(小盘默认不走 veto)。**已重登记 (2026-06-14)**:同口径(原 v2.0 = 2010 预热切 2018)重测三段并经 `strategy_registry.register` 写回 v2.0--IS 25.9%/1.69/-19.4%、OOS 29.7%/1.90、压力 27.4%/-30.5%,hit=true(旧 22.2% 保留在 notes 供审计)。**口径教训**:start=2018 直跑会少计预热回撤报 27.9%/-13.1%,与台账 2010-切片口径不可比;重登记必须沿用原 run-start 约定。`strategy_versions.json` 暂未提交(工作树含 6 个本会话未提交的其他 family 登记,不混入本次)。
**amount 消费者 ripple 已查(contained)**:修复使 688 的 Amihud illiq ×100(看着不再虚假流动)、ADV 容量 ×100 归正。但 **illiquidity LIVE 腿持仓含 688 = 0%**(688 校正后 illiq 仍进不了最不流动 top25,无需 exclude_star);唯一持 688 的默认 LIVE 腿是小盘(已 guard)。large-cap 仅 regime-gate 用(默认关)。**完整 ripple 审计 (2026-06-14)**:吃 amount 的全部消费者逐一查--1 共享 `small_cap_timing`(所有 equity 腿用)修复前后仅翻转 **32/2047 天(1.6%)**,择时基本不受 688 影响;2 **small-cap** 持 688=27%(降夏普 1.85→1.65)已 guard;3 **illiquidity** 持 688=**0%**(免疫;include≠exclude 仅因删列扰动 zscore,**不应**加 exclude_star);4 **size-low-vol(SHADOW)** 持 688=16%(4/25,加收益但夏普持平 1.33~1.35)--tradability 口径上应排除,但未上线不阻塞,待促 LIVE 时定。**结论:LIVE 面(2 ACTIVE 腿 + timing)已全部重回测且干净;size-low-vol(SHADOW)+ 其余非 LIVE 参考 family(d_le_sc/industry_rotation/large_cap/size_earnings/hq_momentum,均吃 amount)按"用时再回测"。**
**large-cap 容量连带已重算 (2026-06-14,漏网补做)**:large-cap 选成长大盘(吃 amount 绝对值定 universe + 容量),是真受 688 虚高污染的腿。重算(`scripts/research/largecap_capacity_688_recompute.py`):修复前 688×100 → 持仓 25/25 全是 688、容量 binding 落虚高 ADV(688416 假 615亿)→ 容量虚估 **768.95亿**;修复后 → 持仓 19/25 科创板(成长大盘本就含科技龙头,合理)、binding 落真实最不流动持仓(002028 ADV 4.5亿)→ 真容量 **5.62亿**。**虚高 137 倍(-99%)**。large-cap 仅 regime-gate(默认关)用,未污染 LIVE,但旧容量数谁评估都会被骗,现钉死真值 ~5.6亿。illiquidity 容量不受影响(持 688=0%)。
**每日更新链路已查 (2026-06-14)**:1 修复在**读层**(`load_prices`→`_normalize_star_volume` 每次读都 ÷100),自动覆盖每日新增行,无需补写路径;每日更新写 688 仍是 shares(最新 2026-06-12 验证单位分裂完好)。2 **每日 LIVE 信号是 illiquidity v3.1(Amihud+Salience Veto+Band),不是小盘**;当日 would-be top25 含 **0 只 688**(veto 后池里 296 只 688 但进不了最不流动 top25)→ 日信号天然 688-免疫,`run_daily` 无需 exclude_star。**条件性风险**:`run_daily` 直接 `load_price_panels`+`build_rebalance_weights` 绕过 `run_small_cap_strategy` 的 exclude_star 守卫--若日信号换回 small-cap-size(持 27% 688)必须在 run_daily 补守卫。

## 策略 / 回测
- **`BacktestConfig.start` 是死字段(2026-06-12)**:engine.run 的 nav 跨度 = 价格面板全长,`start` 从不裁剪。做 OOS 对照必须**物理切面板**(含少量预热),只过滤 weights 起始日会把空仓期算进年化(实测 2025-2026 OOS 被稀释成 1.4%,切面板后真值 7.1%)。**已修引擎(2026-06-12)**:`start` 现在是统计窗口语义--全面板连续模拟(保留预热/持仓连续性)后把 returns/turnover/cost 切到 `>= start` 再算指标;四条 canonical 验证线本就物理切面板且 start 一致,修复对它们是 no-op(回归测试确认数字不变),只矫正"传晚 start 不切面板"的误用。
- **L1 闸门丢掉了"为什么死"这个维度(2026-06-12)**:AutoResearch 冠军 ICIR 0.5-0.6 全死 L1(年化≈0),分位解剖发现 alpha 全在空头侧(D1 超额 -1.67%/20日,D10 仅 +0.05%)--**rank IC 度量双边排序,long-only 只能变现单边;A 股不能做空 → 空头侧 alpha 不可套利所以持久**。死于"信号在不可交易一侧"的候选≠死于"没信号",正确形态是**否决器**:在册微盘策略 top25 平均 2.9 只踩死亡层,否决@10% 训练 +0.87%/年、OOS(2025-2026,干净数据) +5.40%/年。**但稳健面不过线**:分年增益 3/7 年为正(2021 抱团年 -9.5%),增益集中在妖股活跃年份--它是 regime 依赖的增强件,不是无条件 alpha,未达入册标准,记录待条件化假设。失败池按死因分流复检 = 把废料堆变矿,这个方法论结论不受影响。
- **否决器与宿主的 alpha 来源相克会自杀(2026-06-12)**:VetoFilter 机制五件套落地后,边际贡献协议在 canonical 宿主(small-cap v2.0,真实成本+择时)上把"输家端反转低波否决器"**全窗口证伪**:Δ年化 样本内 -1.24% / OOS -2.36% / 压力 -0.97%,逐年仅 4/17 为正,且自付换手 +1.5x(成本 +0.47%/年)。机制解释:**小盘宿主本身在收割"暴跌后反弹"的反转溢价,否决器按"低动量+低波=死亡层"剔除的恰好是刚跌完待反弹的票--排除规则杀掉了宿主自己的 alpha 来源**。早前弃牌堆实验的 +5.4% OOS 来自不同宿主形态(等权反转 host、冠军 DSL 因子),不可迁移。**正交性假设复测(同日)也死**:动量宿主(mom60 top25)上 Δ年化 -1.7%~-6.6% 三窗口全负 → 否决器是**结构性死亡**,不是宿主错配:死亡分位的负 alpha 是**全截面无条件**度量的,但任何宿主 top-N 自带强度筛选,top-N∩死亡分位 ≈ "强势股刚回撤"子集,在 20 日反转主导的 A 股恰好反弹;死亡分位里真正的烂票理性宿主本就不买。**空头侧 alpha 既不能做空套利,也不能靠回避套利--条件分布(选股后)≠无条件分布(全截面),分位解剖的结论不能跨这道条件化直接迁移**。通用教训:1 排除式规则的价值必须在"宿主已选中"的条件分布上重新度量,全截面 D1 负超额是必要条件远非充分;2 边际贡献协议(同引擎同成本、带/不带、只报 Δ)是否决器唯一合法评估口径,独立净值表述被结构性禁止;3 证伪也入台账(`loser_veto_reversal/v0.1-observe`,已证伪/retired),机制五件套保留、因子不启用。
- **数据湖会在交易日内被重写,同一实验同日两跑结果可以不同(2026-06-12)**:日更 pipeline 会把当日不复权价混进后复权面板(daily_all 末两日假崩盘:全市场中位 -59.6%、72% 个股 |r|>30%),当天被修复两次(13:53/20:12),17:33 日更又写坏一次--**19:00-20:05 之间所有 OOS 回测的 NAV 指标(年化/回撤)全部吃了假崩盘**(回撤 -54.8% 实为 -14.8%);rank-IC 类指标因逐日截面计算几乎免疫(±0.01)。教训:1 重要实验前先跑 validate_final 或抽查末几日截面收益分布;2 NAV 类结论必须标注数据版本/时间戳;3 **修大表不修逐只文件 = 假修复**--compact 每次日更从逐只文件重建大表,毒会复发(17:33 复发机制)。
  **根因与根治(同日)**:`tencent.py` 的 `k.get("hfqday") or k.get("day")` 在腾讯 hfq 节点缺失时**静默回退不复权口径**--已改为 hfq 缺失即跳过(宁缺数不混口径);逐只文件治愈 4324 只/6411 行(原始行备份);`lake/invariants.py` 截面不变量(末 5 日 |r|>30% 占比 >5% 拒绝落盘)强制接入 compact 写路径,manifest 如实记录拒绝事件。通用教训:**任何 `or fallback` 落在不同数据口径上都是定时炸弹,fallback 必须显式声明口径一致才允许。**
- **幸存者偏差水分**:`active=(volume>0)` 过滤剔退市股 → 高估约 8.5%(v1.0 40% → v2.0 真实 32%)。进化/回测必须含退市股。
- **回测必须预热**(2026-06):`small_cap_factor`(rolling60)、`small_cap_timing`(MA16)依赖历史窗口,从目标区间直接起跑会**冷启动虚高**(v2.0 从 2018 跑 24.2%/夏普1.53 vs 从 2010 预热再切 2018 的 **22.2%/1.38**)。正确做法:**从更早(如2010)加载、连续跑、再切目标区间统计**;factory 评估候选同样要预热,否则又是一批冷启动假候选。
- **v2.0 真身(干净 amount + 预热,2026-06)**:样本内 2018-2026 **22.2%/-20.0%/夏普1.38/卡玛1.11**(达满意线、未达卓越);压力 2010-2026 24.2%/-31.7%/1.27。**剔极端年(2015/2021/2025)常态仅 15%/夏普0.9--满意线达标全靠小盘疯牛年,常态平庸**;容量~2千万、可成交>98%。定位**组合一块、不单吊**。证伪轨迹:v1.0夏普2.06水分→污染21%/1.14→冷启动24%/1.53→真身22%/1.38。
- **PureTrend MA16 是 A 股策略的生存必需,不是可选开关(2026-06-06)** :测试 8 种策略在无择时下的表现--全部回撤 43-86%。illiquidity 无 PT 时 +31.3%/-73.4%,加 PT 后 +29.7%/-30.5%。PureTrend 用 ~2% 年化代价换 40+pp 回撤保护。没有 PT,任何 A 股日频因子都是不可投资的过山车。**结论:PureTrend MA16 是通用最优开关,在所有策略上验证,无例外。**
- **Band: PureTrend 的 dist 连续仓位缩放(2026-06-07)**:Binary PT 只用了 MA16 交叉的方向(0/1),丢弃了 dist=偏离度这个连续信号。`exposure = (1+dist×8)×I(dist>0), clamped [0,1.5]`--跌破MA空仓不变,站上MA后按 dist 强度缩放。三段验证(IS/OOS/压力)全部夏普+卡玛改善:Binary +28.4%/-14.9%/1.55 → Band +23.5%/-12.0%/1.60;压力期 DD 从 -31.4% 降到 -25.3%。WF 验证 MA 参数稳定区间 12-20,14/14年 OOS 正,Mode=14。**Band 是 Binary 的完整版--同一套 PureTrend 框架,只是不丢弃 dist 信息。** 当前 LIVE 主决策 (2026-06-07 切换)。

- **Band 收益方差 vs Binary(2026-06-07)**: **整体方差降 3.4%** (日) / **降 8.7%** (月), 7/9 年优于 Binary。但存在**"反常信号"**--正收益方差 +6%、负收益方差 +3%、峰度 +12.13 (Binary +7.52)。通俗解释:
  - Binary = 定速巡航: 60%时间固定 1.25x, 涨跌都等比例放大, 方差均匀分布
  - Band = 自适应油门: 90%时间温和暴露 0.3-0.8x (压缩波动), 10%时间火力全开 1.5x (放大两端)
  - **日常段**: Band 把随机噪声压小了 → 整体方差降
  - **极端段**: Band 1.5x > Binary 1.25x → 正/负收益方差都变大 → 峰度更高
  - **是好事**: 因为踩油门不是随机的--dist 大 = 趋势强 = 理应该更激进。Band 把方差从"随机波动"重新分配到"趋势信号强度区", 好的方差 (正收益 +6%) > 坏的方差 (负收益 +3%)
  - 类比: 路宽直踩到 90 码, 路窄弯多降到 30 码。大部分时间比 Binary 慢, 偶尔飙车时比 Binary 快得多
- **v2.2 PureTrend tw=2 偷看 bug(2026-06-06)**:`exposure = (mkt.rolling(2).sum() >= 0).astype(float)` **没有 `shift(1)`**--T 日仓位用到了 T 日 mkt 收益(含当天 close),经典未来函数。修复后 50.5%→**2.2%**,17年仅赢2年,PureTrend tw=2 无真正择时能力。教训:**任何涉及当日行情数据的择时信号必须验证 shift(1) 到位;修复后必须重跑全部数字再注册,不能用旧数字凑**。
- **成本别乐观**:佣金/融资是可谈硬费率(万0.65 / 5%),但**冲击滑点 0.2% 维持审慎**;往返 ≈0.47%。`evolve` 默认 0.15% 偏乐观、漏过户费。
- **真实成本杀伤很大**:small-cap-size 去幸存者偏差后 2018-2026 约 31.9%/-11.9%,但接入真实买卖成本+融资后降到约 **21.2%/-16.2%**;年均换手约 32x,成本拖累约 11%/年。阶段 1 必须把换手/成本作为目标,不能只优化收益。
- **T+1 开盘执行让小盘策略年化腰斩**(2026-06,`paper_replay` 历史重放实测):用真实盘口径(T+1 不复权开盘成交)重放 2024-2025,真实盘 **23.7%/-11.4%/夏普1.29** vs 回测收盘撮合 **45.1%/-10.9%/1.97**(两者都已含真实买卖成本)--**差距 21.4% 年化纯来自"信号日收盘选中 → 次日开盘买"的隔夜跳空**。小盘动量因子选的是近期强势/低成交额股,T+1 开盘往往高开,高换手(485 日 1561 笔)累积成巨大执行摩擦;受阻仅 3 笔(停牌/涨跌停),说明摩擦主体是**隔夜跳空非流动性**。教训:**这类高换手小盘策略的回测收益严重依赖"收盘成交"理想假设,真实 T+1 执行吃掉一半;回测数字必须配 T+1 真实盘重放才知道真实预期**。**全区间 2018-2026 真实盘 17.5%/-16.1%/夏普1.19 vs 回测 24.0%/-20.0%/1.41**--常态摩擦仅 6.5%(2024-2025 腰斩是小盘大年极端动量,非常态),且真实盘回撤反而更小(T+1 慢一拍平滑了部分极端点)。**结论:v2.0 真实盘 T+1 年化未达满意线 20%(仅 17.5%),但夏普 1.19、回撤 -16.1% 达标--合格但不惊艳的真实策略**。`scripts/research/paper_replay.py` 作可复用资产。
- **`regime_gate` 门控算子的牛熊非对称效应 (2026-06)**: `regime_gate` 门控算子(在市场指数跌破 MA16 时清零因子暴露)在熊市中能提供显著的下行保护,但会在牛市中带来极大拖累(经典小盘动量组合牛市年化由 82.47% 跌至 54.17%,牛市夏普由 4.08 降至 3.20)。原因是均线滞后导致熊转牛初期产生严重的入场滞后(Re-entry Lag),且牛市整理期极易触发假摔信号(Whipsaw)导致被动清仓后踏空。因此,该算子绝不能全局盲目应用,在生产组合端应默认关闭(`regime_gated=False`),通过跨资产防御腿进行不对称避险,而非在因子层进行简单粗暴的切断。
- **极端行情不可重复**:2025 +112%、2015 小盘疯牛是极端行情;回测含这些区间的高收益要单独核查、**不可外推**。
- **岛屿搜索的瓶颈在审计闸**:NSGA-II 单代评估还能接受,但 `review_shortlist` 每个候选要跑 2018/2023/2010 + 成本上浮,多岛长跑会慢。正确姿势是先让 `review_candidate` 尽量窄,只审计有希望的候选;不能为了速度跳过压力测试和成本敏感性。
- **孵化池不是入册池**:扩展流动性冷却/低 beta/趋势稳定后,非小盘弱候选能进入 `incubation_pool`;但只要 `registry_precheck=false`,就只能做降频/降杠杆/组合贡献研究,不能当有效母策略。
- **fundamental 接入边界**:`fundamental_batch.parquet` 已有 `avail_date`,可直接按公告可用日 ffill 到交易日;估值收益率类因子必须用 `price/daily_raw` 不复权价。当前批量表没有 `debt_ratio`,两融目录也未稳定落表,暂不纳入 1.9 第一批正交因子。
- **原始 fundamental 不够强**:1.10 三岛长跑 `registry_precheck=0`,弱 alpha 主要来自 `fund_bp_value`,但收益不足、压力回撤偏大、与小盘 baseline 相关约 0.7-0.8。后续 fundamental 必须做行业相对、时间分位、财务改善和 regime 过滤,不能只扩大原始 ROE/BPS/EPS 搜索。
- **fundamental/defensive 的高回撤是因子层结构性病,择时救不了,定位组合分散件**(独立择时 full sweep 验证,2026-06):剥掉共享的 `small_cap_ma16` 后 fundamental 候选真低相关(~0.4),但 2018/压力期裸奔回撤 -27%~-74%。**配独立择时(全市场趋势/vol-target/回撤止损,13 基因 × 9 候选 = 117 组合)0 过三道闸**:择时能把相关压到 0.3-0.4,却救不了回撤--fundamental 回撤与全市场 regime 不同步(价值陷阱/暴雷常在大盘平静时发生),`mkt_dd_stop` 止损反而在底部割肉、把年化打负、回撤打更深。**结论:别再给 fundamental 配择时凑达标;它们定位组合分散件(低相关小权重混入,样本外降组合回撤+提夏普),归孵化池/组合层(阶段3)。找第 2 个母策略要换思路--找本身回撤就可控的正交 alpha,而非靠择时事后救。** `factory/timing.py`(13 个独立择时基因)作可复用资产保留。
- **两融资金面也不是第 2 母策略(2026-06)**:`data_lake/capital/margin_all.parquet` 已稳定落表 2010-03-31~2026-06-03、634万行;factory 加入 `margin_balance_chg*`/`margin_buy_ratio*`/`short_balance_*` 并按 T+1 可用防未来函数。干净 amount + 2010 预热验证:margin NSGA `review_corr<0.5` 下 review=0;include-all audit 22 个候选 `registry_precheck=0`;确定性 168 网格 `hit_single=0`,最佳约 10.3%/-31.4%/corr 0.76。结论:两融弱且仍高度贴 small-cap/市场状态,只能入孵化观察,不能作为独立母策略。
- **北向也没挖出第 2 母策略(2026-06)**:东财每日批量 `stock_hsgt_stock_statistics_em` 仍返回 `9701/None`;改用 `ak.stock_hsgt_individual_em` 单股完整历史 fallback,对近 120 日成交额 top1000 低并发拉取,成功 774 只、`northbound_all.parquet` 675,072 行,覆盖 2017-03-16~2024-08-16。按 T+1 可用接入持股占比/市值/持股变化/净买入强度因子。关键口径:北向数据止于 2024-08,验证必须切到 2018~2024-08,不能让回测 2025-2026 持有陈旧北向篮子。结果:345 个北向网格 `review=0/hit_single=0`,最佳仅 0.7%/-28.3%/corr0.57;低相关组合 corr~0.47 但负收益且大回撤;top30 audit `registry_precheck=0`。结论:当前价量+财务+两融+北向基础下,稳健母策略仍只有小盘。
- **行业字段不是全覆盖**:`fundamental_batch.parquet` 有 `industry`,但缺失约 34.5%。行业内排名/行业中性只能对有行业标签的股票生效;缺失行业不应强行填充为同一类,否则会制造伪行业暴露。
- **自进化必须先证伪**:孵化池自进化只能本地规则化变异 + 三段审计 + 成本上浮,不能让 LLM 直接"脑补"好策略。长跑程序不调用 OpenAI API;若出现 429,优先查 Codex/LLM 并发请求,不是本地回测进程。
- **定时更新要先过 stale gate**:`run_daily.py` 会在更新失败后继续用旧数据出信号,生产定时不能裸跑它。包装脚本必须先更新数据、重建/检查交易日历、确认最新价量达到应有交易日,再用 `run_daily.py --no-update` 生成信号。

## 策略研究方法论(纯趋势 HMM 对比实验,2026-06)
- **简单方案先跑**:HMM(200+行/夏普2.23) < `mkt_ret.rolling(2).sum()<0`(3行/夏普3.40)。研究开始前先建最简基线;复杂方案无法显著超越则放弃。A股散户主导+政策频繁,HMM"状态识别"本身是噪声,宏观特征已隐含在 mkt_ret 中。
- **Walk-Forward 是唯一可信验证**:全样本回测支持任何结论(HMM tw=3 全样本看起来 32.4% 年化"很好")。WF 揭示真相:HMM tw=3 IS Sharpe 1.35~2.86(不稳),Pure Trend tw=2 IS Sharpe 4.25~4.96(极稳)。凡参数选择必须 WF;全样本只是起点。
- **A股压力信号天然窗口 = 2天**:WF 12年独立全选 tw=2(夏普3.40)。经济解释:散户恐慌阈值=两天连跌;1天太噪、3天太慢;匹配 T+1 结算节奏。这是市场结构告知的,非参数挖掘。
- **Overlay 视角必须与策略视角对齐**:小盘策略用等权 mkt_ret(胜率92%)不是成交额加权(胜率67%)。"最准确"的市场收益不是客观存在的,取决于你从谁的视角看。
- **成本敏感度=验证稳健性,不是验证盈亏**:好策略在成本 3x 时仍有相对优势(PT 优势 +15.8pp)。策略必须在 3~5x 成本假设下仍相对好才算稳健。
- **回测审计=发现策略边界**:不是安全清单。策略边界"≤1000万规模、成本≤1%、A股全市场"--知道何时失效比知道何时有效更有价值。
- **择时/牛熊判定指标的有效性验证四原则 (2026-06)**:择时和牛熊指标没有物理真值标签,不能用简单分类准确率衡量。必须通过四层量化规程来严格证明其有效性:1 **分段显性分离度**:牛熊子集内策略的 Sharpe 和年化收益必须产生极显著的统计学分离(如牛市 4.08 vs 熊市 -2.54),否则判定为随机噪音;2 **滚动样本外 (Walk-Forward OOS)**:用前向滚动窗口参数搜索确保决策不依赖未来信息,且在多数 OOS 年度取得正超额;3 **净化与禁运交叉验证 (Purged & Embargoed CV)**:利用 `walk_forward_windows` 物理阻断时序自相关和持有期重叠引起的信息泄露,计算 Deflated Sharpe (DSR);4 **过拟合概率 (PBO)**:通过 CSCV 对参数空间(如移动平均长度)进行扰动测试,确保低 PBO 以规避参数悬崖。
- **多策略组合均线择时参数的过拟合与普适优化实证 (2026-06)**:运行 `audit_portfolio_regime.py` 对全量 6 种策略组合(含在册 ACTIVE 与参考 SHADOW 变体)审计表明:
  1. **自适应仓位分配(方案二)具有全局普适性**:对所有选股风格子策略在年化和夏普上均带来显著的正向暴击。即便对于顺周期大亏的 `size-earnings`,其夏普也从 **1.03** 飞跃至 **1.50**,拯救了因特定熊市造成的特异性塌陷。
  2. **过拟合与统计不显著风险是普适存在的**:所有组合的 PBO 均无法通过 15% 关口(方案二 PBO 平均在 26%-46% 之间,方案一在 60%-100% 之间)。在 9 种 MA 参数的多重测试惩罚下,除了 `illiquidity` 单体勉强贴近显著边缘(DSR p-value = 0.051)外,其余所有组合 of DSR p-value 均不显著(>0.05)。
  * **结论**:在组合层做大类资产动态分配是战胜大盘的阿尔法利器,但由于牛熊切换状态样本极少,**必须强行固化择时均线窗口(如固定 16 日)**,坚决切断后续人工微调均线参数进行 p-hacking 的路径,否则样本外极易踏空和产生高昂调仓磨损(Transition friction 导致夏普衰减达 24%-38%)。




## 我们的 edge 是什么、被什么 bound 住 (2026-06-14)

"基金公司难道都买小盘吗"--不,公募买大盘白马(核心资产),**结构性地买不了小盘**:1 容量(百亿基金 vs 我们小盘策略实测容量 ~2千万);2 开放式赎回要日流动性,微盘满足不了;3 合规/持仓占流通比例硬约束;4 职业风险(买茅台亏了好交代,买微盘亏了丢饭碗)。**所以小盘/流动性溢价正是 limits-to-arbitrage(套利限制)溢价--它持续存在,恰因最大的玩家被结构性挡在门外。** 本会话测出"alpha 集中在小盘/流动性"和"基金不买小盘"是同一件事两面。
- **edge 定性**:小容量、结构性。我们能收割正因小到能钻进基金进不去的缝(微盘场 vs 大盘场,各自 alpha 互不可达)。这是个人投资者**唯一**的结构性优势,不是"比基金聪明"。
- **edge 的物理上限**:容量 ~2千万 不是工程缺陷,是 edge 本身的天花板。做到基金规模 = 变成被约束的大玩家 = edge 当场消失。**"公开 equity alpha 挖尽"准确含义 = 在小盘可及空间里挖尽**;大盘场可能还有 alpha 但对小钱不可及也无必要。
- **与 P=M·f 同源**:基金避开低流通/高冲击(M 大)的股票,M 大的股票留下的溢价就是赏给愿承受冲击约束的小钱的。用户那个公式描述的正是这个溢价来源。
- **定位推论**:系统本就该是"个人投资认知控制系统"(CLAUDE.md 开篇),不去抢百亿容量的大盘场。momentum/任何 equity 因子建腿都和小盘簇撞,也是这个原因--大家都在那一小撮微盘里挑 top-N。

## 策略哲学: 不对称收益 (2026-06-07)

整套策略的底层逻辑不是"找更高的夏普比率",而是**构建不对称收益结构**--让涨的时候涨得多,跌的时候跌得少。

### 不对称来源

| 组件 | 不对称机制 | 方向 |
|------|----------|------|
| illiquidity 因子 | 流动性风险补偿 + ST 彩票溢价 | 正收益端更厚 (ST 大涨 1.6% vs 大跌 0.9%) |
| PureTrend 择时 | 趋势跟踪天然截断左尾 | 砍掉极端下跌日 |
| Band 连续 exposure | dist 大时火力全开,dist 小时收缩 | 正收益方差 +6% vs 负收益 +3% |
| 不排除 ST | 保留彩票尾部 | 不丢 alpha 源 |

### 与传统多因子的区别

| | 传统多因子 | 我们 |
|--|----------|------|
| **目标** | 更高夏普比率 (对称优化) | 正偏收益分布 (不对称) |
| **假设** | 收益对称分布 | A 股收益结构性不对称 |
| **因子来源** | 统计上的 IC 显著 | 市场结构摩擦 (流动性/T+1/散户) |
| **风险控制** | 分散化降低波动 | 择时截断左尾 |
| **评估标准** | IC/ICIR 最大化 | 正收益端厚度 > 负收益端厚度 |

### 为什么 A 股适合不对称策略

- **散户主导**: 趋势持续性强 (追涨杀跌) → 右侧跟随有正偏
- **T+1 制度**: 隔夜信息不对称 → 持有者有溢价
- **壳资源**: ST 炒作逻辑 → 彩票式正向尾部
- **政策频繁**: 单边暴涨暴跌多 → 择时截断左尾的价值大

### 方法论含义

- 不要追求对称优化 (夏普最大化): 那是假设收益正态分布的陷阱
- 要看正/负收益方差的比值: Band 的 +6%/-3% = 2:1 是不对称的标志
- 截断左尾 > 放大右尾: PureTrend 砍掉极端下跌比任何 alpha 因子都重要
- 保留彩票尾部: 排除 ST 表面降低风险,实际砍掉了不对称收益源

## 组合管理 / 边际贡献
- **"信息→行动"断层是最贵的成本**(2026-06-07):STATUS.md 早写"4 策略组合 Sharpe 1.33 < 单 illiq 1.35"--**已知组合层负贡献,但 LIVE 集合没动一周以上**。这是 plan/工程之外的真问题:**有诊断没行动**。本质是组合管理纪律,不是技术问题。修复 = 把"边际贡献负"作为硬触发,立即 SHADOW(不删除,但停止吸纳)。教训:**只产新工程不剪冗余 = 假装在工作**。
- **2 ACTIVE > 4 LIVE 等权 (+18% Sharpe)**(2026-06-07 全样本 2018-2026 实测):
  - 当前 4 LIVE 等权 22.1% / Sharpe 1.60 / mdd -13.9% / calmar 1.60
  - 剔除 size-low-vol+size-earnings(边际-0.120/-0.277): **29.8% / 1.88 / -15.8% / 1.89**
  - 同 + risk_parity 加权: **29.3% / 1.89 / -13.7% / 2.14**(calmar 最高)
  - 边际正只 small-cap v2.0 (+0.104),其余两个全负
  - 决策:**size-low-vol v1.0/v1.1、size-earnings v1.0 全部转 SHADOW**;组合层用 risk_parity(illiq + small-cap)
  - 教训:**Portfolio Sharpe 不靠加策略提升,靠剪冗余**。多元化 ≠ 多策略,A 股权益内只是同因子换包装。
- **plan 自家流水线诚实揭示问题**(2026-06-07,工厂 L3 + marginal 双门验证):工厂 55 hypothesis 跑 L0/L1/L2/L3/marginal,**仅 7 个 small_cap 变体过 L3 + marginal LIVE_C 双门**--全是同因子,corr 0.85+。"防御档"(LIVE_D)候选 ret_zscore_cross/mom_n 被 L3 卡线刷下(avg yearly sharpe 0.49/0.50)。**实证了 STATUS.md 结论 #1 "A 股 alpha 单维度"--工厂自家流水线给出独立证据**。教训:不要拒绝自己流水线说"难"的话--这是质量保护,不是 bug。
- **卡玛能被"掺低波资产"刷出来,且加防御腿会降卡玛(2026-06-14)**:跨资产多腿组合冲卡玛 1.6 的三个硬教训。1 **risk_parity + 低波债券 = 退化解**:RP 按逆波动配权,债券腿波动极低 → 被灌满权重 → 组合变债券基金,卡玛"命中"1.84 但年化从 35% 崩到 8.9%(破满意线)。**任何"卡玛达标"必须先验证收益还活着**。2 **高收益引擎加低收益防御腿单调降卡玛**:股票本体 2014-2026 年化 35%/回撤 -26%/卡玛 1.36;混合比 100:0→60:40 卡玛 1.36→1.28,因为 Calmar=年化/|回撤|,年化弹性 > 回撤弹性(年化 ×0.67 vs 回撤 ×0.71)。防御腿提的是**夏普**(惩罚所有波动,1.75→1.93)和**尾部**(2018 -14%→-8%、回撤 -26%→-18%),**不是卡玛**。3 **追错臂**:卓越线 = 年化≥28% **或** 卡玛≥1.6(OR);小盘/流动性 book 是**高收益引擎**(35%≥28% 已达年化臂),不是高卡玛引擎,不该去够卡玛臂。**提卡玛的真路 = 降股票自身回撤(择时/sizing),不是外挂低收益腿(两头不讨好:卡玛降、年化降)**。防御腿的正确定位 = **风险偏好选项**(要 35%/-26% 的猛还是 24%/-18% 的稳),非卓越线手段。

## 连续 timing / Band
- **Band timing - 连续信号是 binary 的科学性升级**(2026-06-07):
  - 公式: `exposure = clip(1 + dist × 8, 0, 1.5) × I(dist > 0)`,**用 leverage 1.0 + timing[0,1.5]** 代替 Binary 的 leverage 1.25 + timing{0,1}
  - 本质: 把固定杠杆换成 **dist 驱动的动态杠杆**--趋势确认强时加杠杆,趋势弱时减仓,dist≤0 时空仓
  - 理论根据: Moskowitz-Ooi-Pedersen 2012 *Time-Series Momentum* 实证一致 (momentum-scaled position sizing)
  - 三段实测 (illiquidity v1.0 因子,2018-22 IS / 2023-26 OOS / 2010-17 Stress):
    - IS: Binary 28.4%/-14.9%/1.55 → **Band 23.5%/-12.0%/1.60** (sh +0.05, dd -2.9pp)
    - OOS: Binary 39.9%/-13.5%/2.23 → **Band 32.7%/-10.8%/2.29** (sh +0.06, dd -2.7pp)
    - Stress: Binary 30.5%/-31.4%/1.23 → **Band 25.2%/-25.3%/1.27** (sh +0.04, dd -6.1pp)
  - 价值不在 Sharpe (+0.05 微改) 而在 **Calmar +13% + 极端尾部保护**
  - 组合层 (illiq+small-cap risk_parity): Binary 29.3%/-13.7%/1.89/cal2.14 → Band 28.5%/-11.8%/1.86/**cal2.42**
  - 决策: **SHADOW 跟踪 (2026-06-07 起)**,signals/ 含 shadow_band_exposure 字段;30 日后 `scripts/research/band_shadow_review.py --update` 看真实 paper 差异决定是否切 LIVE
- **engine clip 陷阱**(2026-06-07):BacktestEngine `_run_weight_backtest` 原把 timing 强制 `min(max(x, 0.0), 1.0)`,boost timing > 1.0 全被吞--结果与 binary 完全相同。已加 `Signal.exposure_cap` 字段 (默认 1.0,Band 传 1.5)。教训: 引擎假设是隐藏约束,任何 timing > 1.0 设计**必须先验证 engine 不 clip**。
- **复现 timing 必须先核对 leverage**(2026-06-07):用户给 Band 公式描述时未明说 leverage 改成了 1.0,我用 1.25 跑出加杠杆型 (sharpe ↓),与用户报告的减仓型 (sharpe ↑) 方向相反--浪费了 2 小时尝试各种 mapping 直到看 `scripts/research/band_timing_test.py` 才发现 `lev=1.0`。教训: **timing + leverage 不可分离讨论**,公式描述必须含 leverage。

## 自动化质疑机制 (2026-06-07 Band 反思)

### 为什么 6 周工厂跑不出 Band,但人 30 分钟想出来

**5 层失败模式** (按隐蔽性排序):
1. **API 误导**: `small_cap_timing` 返回 `(timing, small_nav, dist)`,所有调用方 `_, _, dist = ...` 丢弃 dist。**输出位置传递重要性暗示**--dist 作为"输出 #3"被默认忽略。
2. **底层约束塑造思维**: `core/engine.py` 硬编码 `exposure = min(max(exp, 0.0), 1.0)`,boost > 1.0 全被吞--6 周里没人想过"为什么是 [0,1]"。**底层约束的隐性传播**: 工具假设某维度是常量时,那个维度永远不会被发现。
3. **搜索空间预设**: 工厂 mutate_existing.py `timing_kind ∈ {"none", "small_cap_ma16", "small_cap_ma8"}` 三选一离散。**工厂结构性地"看不见" timing 是连续变量**。
4. **强结论封顶**: "PT 通用最优,无例外" → "已解决"标签 → 关闭探索。**已解决的问题是探索的坟墓**。
5. **mental model 解耦**: leverage 在 config / timing 在 signal,binary 思维下天然分离。Band 揭示 **timing 可以同时编码 exposure 和 dynamic leverage**--一旦突破 0/1,timing 吃掉了 leverage 功能。**变量边界的"自然分类"未必是最优分类**。

**meta-lesson**: 6 周扩展工厂(加 L3 / 加 regime / 加 LIVE_D),**没花 30 分钟质疑工厂的搜索空间假设**。"扩展工具"是默认动作,"质疑工具假设"是被忽略的动作。

### 5 个自动化质疑模块设计

**Line 0 (MetaSearch)** = 在 Line 1-3 之前,质疑预设搜索空间本身。`factor_research/metasearch/` 已建。

1. **Signal Flow Tracer** (Phase 1 ✅ PoC 已跑通,定位 Band 根因)
   - AST 扫描 `a, _, b = some_call(...)` 模式
   - 自动报告"被丢弃 ≥50% 的输出"
   - 已发现: `small_cap_timing` output[2] (dist) 88% 被丢,output[1] (small_nav) 100% 被丢--**第二个 Band 候选已自动浮出**
   - `python3 -m metasearch.signal_flow_tracer` 一行命令
   - 也发现 spearmanr p-value 100% 被丢 (21 处),应该按显著性过滤 IC

2. **Constant Auditor** (Phase 2,1 周)
   - AST 找所有硬编码常量 + `.clip(low, high)` 上下界
   - 列表 "硬约束清单",每月人审
   - 如发现 engine 的 `exposure_cap=1.0`、`leverage=1.25 scalar`、`top_n=25 hardcoded` 等
   - 防止下一个"timing ∈ [0,1] 6 周没人质疑"

3. **Continuization Auto-Sweep** (Phase 3,2 周)
   - 给定 Binary 信号 `x > threshold`,自动生成 5-6 个连续版本 (linear/sigmoid/cap)
   - 每个跑 walk-forward 自动比较
   - **如果 Band 这个工具一年前存在,5 分钟就能找到**
   - 输入: 任何 `(bool, optional_continuous)` 信号

4. **Conclusion Expiry** (Phase 4,1 周)
   - LESSONS.md 强结论加 frontmatter: `expires: 3-months`
   - 每月自动跑 adversarial 实验试图证伪
   - 防止"PT 通用最优"封顶持续阻断探索

5. **Variable Coupling Detector** (Phase 5,3 周)
   - Dataflow analysis BacktestEngine
   - 找"独立但耦合"的变量对 (如 timing × leverage 出现在同一处乘积)
   - 建议合并实验
   - 发现下一个"timing 和 leverage 可合并"机会

### 工厂 vs 人的本质分工

| | 工厂(机器) | 人 | MetaSearch (新) |
|---|---|---|---|
| 擅长 | **广度搜索** - 预设空间内枚举 | **深度复用** - 重理解已有变量 | **质疑预设** - 找搜索空间外缺口 |
| Band 案例 | 6 周 55 hyp 0 实用 | 30 分钟想出 | 1 秒 PoC 定位根因 + 提示候选 |

**Phase 2 应有 1 周/月预算用于 MetaSearch,不只是扩展 Line 1 generators**。

### 信息熵框架 - small_nav 失败暴露的更深定理 (2026-06-07)

small_nav 5 实验全失败,本质是数学定理:**dist 是 small_nav 对 timing 决策的充分统计量,条件互信息 ≈ 0**。这把整个 plan 从"经验工程"提升到"信息论框架":

**框架核心 (4 LIVE 实测 MI 矩阵确认):**
- 4 策略两两 MI 1.5-1.8 bits (上限 3 bits, 共享 50%+) - 信息论独立给出与 corr 0.83 相同诊断
- 顺序 cond_mi: small-cap 1.57 → size-low-vol 0.15 → size-earnings 0.27 - 与 marginal_sharpe +0.10/-0.12/-0.28 **完美同向衰减**
- **A 股 alpha 的"独立信息预算"有限**: 受市场 beta + 行业 beta 上限约束。工厂努力多 ≠ 挤更多独立信息;要找尚未被利用的数据源 (基本面/资金流/行业/港股)

**MI auditor 的关键边界 (Band 案例暴露):**
- Band vs Binary: MI 完全相同 (2.77 bit), cond_mi=0 → REDUNDANT
- 但实测 Band Calmar +13% 真实价值
- 原因: **MI 测"两变量依赖", 不测"如何用同一份信息"**。Binary 和 Band 派生自同一 dist, 信息内容相同但与 PnL 的**函数关系不同**

**框架定位修正 - MI 是必要不充分:**
- 低 MI → 必然 REDUNDANT (放心关闭)
- 高 MI → 不保证 VALUABLE (还需测方向 + 用法)
- MetaSearch 是双层:
  - Lower (MI): 信息含量,毫秒级,过滤冗余
  - Upper (Sharpe): 信息使用,回测级,测方向

**这就是 plan 的 L-1 关 (在 L0 IC scan 之前)**: 工厂前端最便宜的过滤器。`factor_research/metasearch/mi_auditor.py` 已实现,等待集成到 L0 之前。

### Information Map 框架的关键盲点 - IC MI ≠ Returns MI (2026-06-07)

加 4 个 fundamental factor (NPY/revenue_yoy/ROE/gross_margin) 跑端到端,验证 Information Map 的预测:

**Information Map 预测 (IC 时序 MI):**
  · 4 fundamental 与 LIVE 距离 2.86-2.97 (近上限 ~3.0)
  · PNG 显示 fundamental 在完全独立的信息维度
  · 预测: 真正多元化候选

**实测 marginal eval (Returns):**
  · 4 fundamental returns vs LIVE corr **0.76-0.80** (远超 0.42 物理下限)
  · 全部 SHELVE,bear_imp 全部负值 (-4.8 ~ -13.8%)
  · marginal_sharpe -0.09 ~ -0.17

**根本原因 - 两个 MI 层次不同:**
  · IC MI 测"信号生成时机依赖" → fundamental 真独立 (不同时段不同预测方向)
  · Returns MI / corr 测"执行结果共动" → fundamental 与 LIVE 都是 long-only,**都吃市场 beta + 选股 overlap (NPY 好的也偏小盘) → returns 必然高度共动**

**plan 修正:**
  · 现 MetaSearch MI auditor 用 IC 时序作 proxy,与最终决策 (marginal_sharpe on returns) 不同口径
  · 必须建 **Returns MI auditor** (用 daily returns 而非 IC 时序),与 marginal_sharpe 同口径
  · 两层独立: IC MI 高 → 信号同源(关闭重复); Returns MI 高 → 组合冗余(真实拖累)
  · IC MI 低 **不保证** Returns MI 低 (fundamental 案例就是反例)

**A 股 long-only 框架的物理上限再次确认:**
  · LIVE 2 ACTIVE (illiquidity + small-cap) 已基本耗尽 long-only alpha
  · 任何 long-only fundamental 加入 → returns 大部分重叠
  · 真突破必须: (a) long-short 引擎去 beta,或 (b) 跨资产 (港股/债/商品),或 (c) 接受 2 ACTIVE 已是当前框架最优
  · 印证 STATUS 结论 #3 "真正多元化需跨资产"

**MetaSearch 框架价值:**
  · 1 天验证 fundamental 在 long-only 框架下无独立组合价值 (节省未来周/月反复尝试)
  · 暴露 MI 框架盲点 (IC vs Returns 分层) 并即时修正
  · 关闭分支 ≠ 失败,是 plan 设计本身的迭代

### A 股 long-only 多因子的真实 corr 上限 = 0.75-0.80 (2026-06-07)

之前 LESSONS 写"A 股长仓多因子 corr 物理下限 ~0.42",但 0.42 是个例 (微观结构 vol_breakout/mom_n 类)。完整数据资产盘点 + 大量实测后,**真实分布是 0.75-0.80**:

**实测 corr to A 股 ACTIVE (illiquidity + small-cap):**
- bp_proxy (经典价值): 0.77 (期望 contrarian 但实际持仓仍偏小盘)
- ep_proxy: ~0.77
- roe (质量): 0.77
- gross_margin: 0.76
- net_profit_yoy (成长): 0.80
- vol_breakout/mom_n (微观结构): 0.42-0.58 (个例,corr 真低)
- **HK 港股 (跨市场): 0.25-0.27** (唯一突破 0.5)

**根本原因 - A 股流动性结构强制 long-only 持仓收敛:**
- top-N 持仓必然偏小盘 (大盘股 1/top_n 权重太大,等权 long-only 在 A 股 universe 自然偏小)
- 价值股 (高 BP) 在 A 股不是大盘蓝筹,主要是次新/中小盘
- 成长股 (高 NPY/ROE) 也多小盘
- OHLC 派生信号 (close_position/high_low_breakout 等) 选趋势股,也多小盘
- → 所有 long-only 多因子的实际持仓都收敛到"小盘 + 流动性"维度

**对 plan 的影响:**
- 现 LIVE 2 ACTIVE (illiq + small-cap) Calmar 2.14 已是 A 股 long-only 框架几乎最优
- 任何 long-only fundamental / value / quality / OHLC 候选 corr 都 0.75+,组合贡献负
- 真正突破必须: (a) Long-short 引擎去 beta (corr 物理可到 0.2-0.4), 或 (b) 跨资产 (HK 已验证 corr 0.26 但单 Sharpe 弱需更好因子), 或 (c) 接受现状
- 现 MI Auditor 用 IC 时序 → 与最终 Returns 共动差异巨大,**信号空间独立 ≠ 持仓空间独立**,必须建 Returns MI 第二层

**完整数据资产清单 (已盘点 2026-06-07):**
- price/daily (close, volume 已用) + daily_raw OHLC (raw_open/high/low 之前没用,新增 close_position/high_low_breakout/amplitude_mean factor)
- fundamental_batch 16 字段 (今天已用 NPY/ROE/revenue_yoy/gross_margin/BP/EP/cfo,9/16 字段)
- capital/margin_all (2010-2026 全) - LESSONS 标弱,Returns 视角可重测
- capital/northbound_all (2017-2024-08 截止) - 浅尝
- price/hk_daily (111 只 × 8 年) - **HK 港股完全没用,corr 0.25 真低**,需更好 HK 因子工程
- price/monthly, weekly - 不知用没用

### 验证纪律 - 任何"建议使用"前必须 OOS 三关 (2026-06-08 永久记忆)

**触发条件:** 任何策略/配置/参数被推荐入 LIVE/SHADOW/生产前 (含"切换默认", "推荐配置", "新组合").

**必须三关 (in-sample 漂亮 ≠ 可投资):**
1. **Walk-Forward 参数稳健性** - 在搜索网格上做 WF, 看选出的参数 OOS 是否仍是最优, 或邻近参数 OOS 表现是否一致 (plateau ≠ spike).
2. **分段稳定性** - 按年/regime 切分独立测, 看哪些时段亏哪些时段赚, 是否依赖单一极端年.
3. **极端事件检查** - 在已知 stress 期 (2008/2015/2018/2022 等 A 股大跌) 看策略真实表现, 不能只看全样本平滑曲线.

**反例 (今天踩的坑):**
- 2026-06-07 推荐 ETF 配置 35/35/15/15, grid search 在全样本上选的最优
- 国债 MA60 是先验选的, 没在 ETF universe 做 WF
- 未分段验证 → 不知道是否依赖某些特殊年份
- 用户即时拦住要求验证 → 才发现可能 in-sample 调优

**反例 (历史):**
- v1.0 夏普 2.06 (含幸存者偏差水分)
- v2.2 PureTrend tw=2 IS Sharpe 4.25-4.96, shift(1) 修复后 2.2%
- MA16 grid 测试 plateau 不是 spike (这是正面例子 - 做了所以稳健)

**强制流程 (任何 SHADOW/LIVE 推荐前):**
```
1. 报告 in-sample 结果
2. 主动声明: "未经 OOS 三关验证, 直接切 LIVE 是冒险"
3. 给出三关验证 plan
4. 三关全过才能 SHADOW; SHADOW 30 日不出问题才 LIVE
```

**例外:** 仅当用户明确说"先 SHADOW 跟踪, 不切 LIVE", 且 SHADOW 期内会持续累计真实数据 - 才可跳过 walk-forward (因为 SHADOW 期本身就是 OOS 验证).

### 执行优化 - fill_mode 切换 close 实证 Sharpe +0.18 / ann +5.3pp (2026-06-07 Task 1.2)

LESSONS 早已写过 "T+1 开盘执行让 2024-2025 年化 45.1%→23.7%, 差 21.4pp"。Task 1.2 audit 实证:

**5 fill mode 对照 (2024-2025 重放, simplified no-cost baseline):**
- open (旧默认):     ann 40.3%, sh 2.29  (baseline)
- ohlc_mid (10:30):   ann 42.9%, sh 2.42  Δsh +0.13
- vwap_4:             ann 42.8%, sh 2.41  Δsh +0.12
- **close (14:55):**  **ann 45.6%, sh 2.47  Δsh +0.18** ⭐ 最佳
- lo_close_mid:       ann 41.6%, sh 2.25  Δsh -0.04

**根本原因:**
- 隔夜跳空诊断: 9% 显著高开 (>+1%), 95 pctile +1.6%
- open 模式在高开股上吃满隔夜冲击 → 持仓成本虚高
- close 模式等冲击消化, 实际成交价低 1-2pp

**决定: paper_trade.py 默认 FILL_PRICE_MODE = 'close'** (line 38-44)
- 环境变量覆盖: `PAPER_FILL_MODE=open` 回滚
- get_fill_price() 支持 4 mode (open/close/ohlc_mid/vwap_4)
- 涨跌停约束仍按开盘价判 (即使 close mode, 开盘涨停依然买不进)

**生产预期 (含真实成本 + 涨跌停):**
- 相对 Δ 改善 +5.3pp ann 可信; 绝对值依赖 paper_trade 真实环境
- audit baseline 40.3% vs paper_replay 真实 23.7%, 差 ~16pp = 成本 + 约束
- 切 close 真实生产期望: 23.7% → 28-30% ann (回收摩擦的 25-35%)

**与 6 大盲区路线的关系:**
- #2 [执行优化] PoC 完成 → 推荐切换已落地
- 后续若要更精确, 拉 5min K 线做 09:30/10:00/10:30 etc 精细对照
- 但 OHLC 推断已经给出明确方向, **节省 5min K 线工程约 1 周** (Task 1.2 plan 预期效果)

`scripts/research/execution_optimization_audit.py` 作可复用资产 (修改 fill_fn 即可加更多 mode).

### 多元化的数学下限 - 候选 Sharpe / 组合 Sharpe 比例 (2026-06-07)

HK 因子工程 (6 因子 × 5 config × 多 timing) 实测后发现:

**HK 最佳候选**:
  · mom252+illiq + notiming: sh 0.53, corr 0.18 to A 股 LIVE
  · all4_equal (mom+illiq+lowvol+size): sh 0.51, mdd -41% (最低), corr 0.24

**加入组合实测全部拖累**:
  · A only risk_parity: sh 1.89, cal 2.14
  · + HK_all4 (sh 0.51, corr 0.24): sh 1.54 (-0.35) ❌
  · + HK_mom252_illiq (sh 0.53, corr **0.18**): sh 1.55 (-0.33) ❌
  · **即使 corr 0.18 极低也救不了!**

**根本原因 - 数学约束:**
  · Sharpe = mean / vol
  · 加入 HK → portfolio mean 必降 (HK ann 12-19% vs A 股 30%+)
  · HK vol 高,即使 corr 低,也不显著降 portfolio vol
  · 分子降幅 > 分母降幅 → Sharpe 净降

**经验法则 (写进 plan):**
  · **candidate Sharpe / portfolio Sharpe < 50% → 必拖累 (即使 corr=0)**
  · 当前 portfolio sh 1.89,要不拖累 HK 必须 sh ≥ 0.95
  · HK long-only 单 sh 上限 ~0.5 (universe 91 只 + 港股流动性 + 机构主导长趋势)
  · 数学不可能在 long-only 框架下用 HK 改善 A 股组合

**真正可行的 cross-market 路径:**
  · (a) Long-short HK → 去 beta, vol 降一半,Sharpe 可能翻倍
  · (b) HK ETF rotation (行业/风格,不选股) → 降 vol
  · (c) 港股通跨市场统计套利 → 真 market-neutral
  · 这些都需要新引擎/数据,plan 之前没有

**结论修正 (A 股 long-only 物理上限的精确表述):**
  · 不只是"corr 0.75-0.80",还有数学约束
  · 任何 candidate Sharpe < 当前组合 Sharpe × 0.5 → 必拖累
  · 当前 A 股组合 sh 1.89 → 任何新候选必须 sh ≥ 0.95 才有意义
  · A 股 long-only 多因子的单 sh 也很难持续 ≥ 0.95 (已知最强 illiq v1.0 sh 1.78)

`scripts/research/hk_factor_grid.py` + `scripts/research/hk_v2_independent_timing.py` 作可复用资产。

### small_nav 已审计 - 无独立价值 (2026-06-07)

MetaSearch PoC 提示 `small_cap_timing` output[1] `small_nav` 100% 被丢。1-2 小时跑 5 实验:
  - V1 rolling 252d drawdown gate
  - V2 slope-driven boost (代替 dist boost)
  - V3 small_nav / mkt_nav 相对强度 gate
  - V4 adaptive exposure_cap (NAV 滚动 vol 控制)
  - V5 Binary × NAV vol-target 30%

**全部失败** (全段 Sharpe 不改善或下降)。V2 slope boost 全段 -0.4 sharpe 是最强证据: A 股小盘的 timing 信号在"相对均线位置 (dist)"层,不在"短期变化率 (slope)"层。

**结论: dist 已充分提取 small_nav 的全部时序信息,nav 自身/派生信号都被 dist 包含或对偶**。`scripts/research/small_nav_experiments.py` 作可复用资产。

**这是 MetaSearch 路径的价值证明 -- 不仅找到 Band 类升级,也 1-2 小时快速证伪 small_nav 类幻觉,关闭分支**。未来再有人想挖 small_nav,LESSONS 直接告诉他"已审计无价值"。

### 实证: PoC 输出 (2026-06-07)

```
HIGH PRIORITY - 默认被忽略的输出
  callee                       #calls  output[i]   discard%
  small_cap_timing                 40    1/2         100%   ← small_nav 全丢
  small_cap_timing                 40    2/2          88%   ← dist (Band 来源!)
  spearmanr                        21    1/1         100%   ← p-value 全丢
  load_price_panels                 9    1/2          89%   ← volume? 全丢
  backtest_weights                 72    1/1         100%   ← detail 全丢
```

下一步: 逐个审 small_nav / spearmanr p-value / backtest detail 能不能成下个 Band。

## 科学性 / 参数 robustness
- **MA16 是 plateau 不是 spike**(2026-06-07 grid 测试 2010-2026):
  - MA10-20 sharpe 1.26-1.45 都 work(plateau)
  - MA16 sharpe 1.45 是 grid winner,但 MA18 1.42 几乎等效且 calmar +0.92>0.89、mdd -29.1%<-30.5%
  - 极端 MA5 sh 0.81 / MA60 sh 0.96 → 趋势跟踪概念真实(中间区段都 work)
  - 但"16"无理论意义,是事后合理化(3 周 ≈ MA15-18 都行)
  - 教训:**概念有理论根据(time-series momentum) + 参数 plateau ≠ magic number**。MA16 不是 v2.2 tw=2 那样的偷看 bug,只是轻度 in-sample tuning。严肃科学应 walk-forward 选 MA window。

### AmihudIlliq - 比 SizeProxy 更好的因子公式 (2026-06-08)

Alpha 框架的 FactorSpace 搜索发现: Amihud 原始公式 `|ret|/amount` 优于我们一直用的 SizeProxy `-ln(avg_amount)`。

**原理**:
- SizeProxy: 只按成交额排序 → "越冷清越好"
- AmihudIlliq: 按"日波动 ÷ 成交额"排序 → "波动大但没人交易的股票"
- 后者多了一个维度: 不是所有小盘股都一样, 当天剧烈震荡却无人接盘的才是真正的流动性洼地

**实证** (2018-2026, +Band timing):

| | 年化 | 回撤 | 夏普 | 终值(100万→) |
|--|------|------|------|------|
| SizeProxy_w60 (当前) | +24.8% | -17.6% | 1.49 | 678万 |
| **AmihudIlliq_w20** | **+32.1%** | **-13.0%** | **1.66** | **1180万** |

6/9年跑赢, 年化+7.3pp, 回撤-4.6pp, 终值+74%。

**选股差异**: 两者都选极端小盘, 仅 25 只中重叠 4 只。Amihud 多选的股票: 波动大但成交额小的"事件型小盘股"(*ST皇庭、永安林业等), 这些股票存在更强的流动性折价。

**与 SizeProxy 相关性**: 日收益 corr=0.84, 共享方差 70%。不是独立策略, 是同一策略的公式升级。持仓重叠仅 13%, 但收益高度同步(小盘股内部涨跌一致性高)。

**结论**: 切换因子公式即可, 不需要改其他任何东西(Band timing/调仓频率/top_n 不变)。AmihudIlliq 捕捉的是"信息效率低"的股票, 在散户主导的A股市场中天然存在更深的错误定价。

## 我们的 raw ICIR 在 horizon=20 被高估 3-4 倍(NW 校正,2026-06-14)

借第二系统(自進化因子挖掘系統)的防自欺武器审 fund_mom,顺手暴露我们自己的大坑:
**L0 用的 raw ICIR 在 horizon>1 时因重叠样本严重虚高**--每日 IC 序列强自相关,同一信息被重复计入,分母(std)被压低。实测 horizon=20:revenue_yoy raw 0.172→NW 0.045(**3.8×**)、momentum60 raw 0.455→NW 0.133(**3.4×**)。**本会话所有 champion ICIR(0.5~0.76)都是 raw/重叠口径,NW 校正后真值 ~0.13~0.2。** 相对排序大体保留(都缩 ~3.5×),故 champion 选择没崩;但绝对量级被系统性夸大,L0 阈值是按 raw 标定的(内部自洽),对外别把"ICIR 0.5"当强信号读。**修复方向**:L0 对 horizon>1 报 NW 校正口径(Bartlett 核,max_lag 按 **IC 序列**自相关长度≈horizon 设,不按因子不变天数设--差一个数量级)。工具 port 在 `scripts/research/alpha_audit_fund_mom.py`。

## revenue_yoy 是 price-in 死重--两系统独立同证(2026-06-14)

在册 `fundamental-momentum/v0.1 = momentum(60)+revenue_yoy`。借来的 RidgeCV 联合增量+置换检验(本地重算,见 [[borrow-mechanism-not-conclusions]]):量价 base 池(small_cap/illiquidity/momentum60/vol/volume_ratio)OOS rank-ICIR=0.479,加 revenue_yoy 后 0.452,**表面增量 -0.026、置换增量 -0.000 → 真增量 -0.026 < 0.015 地板 = 噪声/price-in**(不只是零,是轻微稀释)。**对方系统在 A股500/2019-2020 判 revenue_yoy=噪声(成长被 ts_roc/amount 覆盖),我们在全市场/2018-2026 独立复算同结论**--两窗口、两方法、两数据,同一 price-in 机制。**含义(已验证修正,2026-06-14)**:**revenue_yoy 不是死重--我此前的"应简化为纯 momentum"判断错了。** 同 canonical L0→L1→L2 实测:pure momentum(60) L1 净年化 20.1%/夏普 0.58/回撤 -38.1%;fund_mom(+revenue_yoy)**31.2%/0.98/-33.2%**--revenue_yoy 加了 **+11.1pp** 净年化、提夏普、降回撤。**fund_mom 站得住,不简化。**
**为什么与审计不矛盾(关键 nuance)**:"price-in/冗余"是**相对一个因子池**说的。审计测的是 revenue_yoy 对**完整量价 pool**(illiquidity+small_cap+momentum+vol+volume_ratio)的 RidgeCV 联合增量--冗余(illiquidity/small_cap 已 price-in 成长)。但 fund_mom 只把 revenue_yoy 配 **momentum 一个**,对它**不冗余**。更细:NW ICIR 两者几乎相同(0.139 vs 0.135),但回测差 +11pp--说明 **revenue_yoy 的价值在 top-N 尾部(交易的那一端),不在全截面 IC**;它是 momentum 排名头部的质量过滤器,RidgeCV 的宽截面 IC 测不到尾部效应。**正确结论**:1 revenue_yoy 相对**在册 book**(illiquidity/small_cap)冗余 → fund_mom 对 book 的边际可能低(呼应伪多样性 corr 0.6);2 但 fund_mom **作为独立策略**,revenue_yoy 真实加值,不可简化。**元教训**:借来的 IC 级审计(RidgeCV 宽截面)和本地回测(top-N 尾部)测不同的东西;差一点就凭审计降级了一个真有价值的在册策略--[[borrow-mechanism-not-conclusions]] 本地重算这步救了场。

## P=M·f(放大因子×资金流入)思路检验--price-in,但 f 单独有真 IC (2026-06-14)

用户提 P=M·f(P 市值变化、f 资金流入、M≈1/流通股比例=放大因子)。检验:**literal M 建不了**
(数据湖无流通股/总股本,price 仅 OHLCV+amount → 这是 alt-data 前沿)。**Steelman 代理版**:
M=Amihud illiquidity(价格冲击=1/float 的经验实现,且已是 ACTIVE 腿)、f=Δ融资余额(净杠杆流入,
4556 股覆盖 44%)、M·f=illiq_rank×f。借审计武器(NW + RidgeCV 联合增量+置换)在全市场/2018-2026 测:
- **NW**:f raw 0.272→nw **0.182**、M·f 0.237→0.160、illiq 0.262→0.075。**f 单独 NW IC 0.182 比 illiq 还高--融资流入是真信号**,不是噪声。
- **RidgeCV 联合增量 vs 量价 base**:f 真增量 **+0.009**、M·f **+0.008**,均 <0.015 → **price-in**。
  且 **M·f 不优于 f**(0.008≈0.009)--**放大因子 M 对边际无贡献,用户思路的核心(float 放大)在代理形式下不成立**。
**结论**:1 P=M·f 机制正确但 price-in--M(illiquidity)已在 ACTIVE 池、f(融资)有真 IC 但量价冗余、
放大交互 M·f 加不出增量;2 与第二系统独立结论一致(margin/northbound=零增量);3 **诚实边界**:
测的是代理非 literal(M=1/float、f=主力总流入需龙虎榜/大单/分价 alt-data);代理 price-in + M≈已交易的
illiquidity + 第二系统佐证 → literal 版大概率也 price-in,但要真关死需买 float+全口径资金流数据(采购决策非技术问题)。
机制教训:f 有真 univariate IC 却量价冗余,正是"散户主导市场资金流提前被量价 price-in"的又一例。

## 因子评价框架: 截面 vs 时序 + 多周期 IC (2026-06-07)

专家审视触发: 量化交易的关键不在具体因子,而在科学的因子评价体系--将定性理解转化为定量约束函数。

### 多周期 IC 闸门实验

**假设**: 工厂 L0 只用 1d forward return ICIR 筛因子。但 A 股价格发现慢,所有因子 IC 从 1d→20d 单调递增 (增幅 76%-156%)。L0 在信号最弱的周期上设门槛,可能误杀中周期因子。

**实验**: 从 `FACTOR_MUTATION_SPECS` 生成 74 个候选因子,双闸门评分:
- 旧闸门: |ICIR_1d| > 0.03
- 新闸门: 0.1×ICIR_1d + 0.2×ICIR_5d + 0.3×ICIR_10d + 0.4×ICIR_20d > 中位数

**结果**: **0 个误杀**。旧闸门通过率 99% (73/74),几乎是个 no-op--阈值 0.03 太宽松。

**深层发现**:
- 旧闸门 L1 精度 22%,新闸门 19% - 两者都不高
- IC Score 和真实 L1 收益 **不相关甚至反向**: vol_breakout Score 最高 (0.56) 但 L1 -6.3%;illiquidity Score 中等 (0.40-0.48) 但 L1 +21-23%
- **IC 是必要条件,不是充分条件。** IC 能去掉明显噪音,但区分不了"真 alpha"和"会死的假 alpha"

**结论**: 多周期 IC 无增量--不是闸门周期的问题,是 IC 本身信息量有限。当前 L0→L1→L2→L3 串联设计合理: IC 粗筛,后续层精筛。真正改进方向: 提高旧闸门阈值 (从 0.03),而非加周期维度。

`scripts/research/experiment_multi_period_ic.py` + `scripts/research/factor_eval_framework.py` 作可复用资产。

### 时序预测 vs 截面预测

**假设**: 截面 IC 测"这只股票比别的股票好吗",时序 IC 测"这只股票比它自己过去好吗"。两者是不同信息维度,时序信号可用于仓位管理。

**实验**: 对 illiquidity 因子,在截面 top-25 基础上加入时序仓位缩放--每只股票的 illiquidity 相对自身 252 天历史的 zscore。测试正向 (选时序上升) 和反向 (选时序下降 = 流动性恢复)。

**时序 IC 基线** (20 只抽样):
- 1d forward: 均值 +0.024, 仅 10% 股票显著
- 20d forward: 均值 **+0.101**, **75%** 股票显著 - 时序信号在中周期确实有效

**回测结果**:
| 场景 | 年化 | 回撤 | 夏普 | 换手 |
|------|------|------|------|------|
| 等权 (基线) | +20.8% | -21.8% | 1.23 | 36.5x |
| 时序缩放 (正向) | +19.4% | -22.1% | 1.13 | 38.2x |
| 时序缩放 (反向) | +20.5% | -21.8% | 1.21 | 37.4x |
| 时序筛选 (正向) | +13.6% | -21.1% | 0.70 | 42.0x |
| 时序筛选 (反向) | +21.3% | **-18.5%** | 1.21 | 43.4x |

**核心发现**:
- 方向是对的: 反向 (选流动性恢复) > 等权 > 正向 (选流动性恶化)
- 但效果太小: 反向筛选仅 +0.5pp 年化、换手 +19%,不显著
- B2 缩放版本几乎和等权一样 - 时序信号只在激进筛选时有效,不是稳定的线性信号
- **截面 illiquidity 完成了 95% 的工作,时序微调只贡献边缘噪音**

**方法论教训**:
- 时序和截面是两个不同的预测视角,逻辑必须对齐
- 对 illiquidity,"截面高 = 选它"是对的;但"时序升高 = 它在恶化"有害、"时序下降 = 它在恢复"有益
- 时序信号的方向不能假设,必须和截面信号的逻辑一致
- 当前不值得将时序仓位加入系统--复杂度提升 > 收益改善

`scripts/research/experiment_ts_weighting.py` 作可复用资产。

### top_n 参数是 plateau 不是 spike (2026-06-07)

**实验**: Band timing 下跑 top_n = 10/15/20/25/30/40/50/60/80/100/120, 2018-2026 全区间 + 三段分区间验证。

**全区间结果**: 20-25 区间几乎等效:
| top_n | 年化 | 回撤 | 夏普 | 卡玛 |
|------:|------|------|------|------|
| 20 | +25.3% | -18.0% | 1.49 | 1.41 |
| 25 | +25.0% | -17.7% | 1.50 | 1.41 |

**分段验证**: 20 的微弱优势 (+0.3pp) 几乎全部来自 2024-2026 段,前两个子段 25 反而更好。20 和 25 在子段间互有胜负,差异不显著。

**结论**: **top_n=25 不变。** 20-25 是 plateau,不是 spike。和 MA 参数一样,区间内几乎等效,不值得为 0.3pp 噪声改参数。更集中 (10-15) 回撤大、更分散 (50+) 稀释 alpha。25 容量优于 20 (多 25%)。

`scripts/research/top_n_sensitivity.py` 作可复用资产。

### ST 股暴露 - 反直觉:ST 不拖累反而略好 (2026-06-07)

**发现**: illiquidity 因子选的 top-25 中,ST 股占比高达 **28.7%**。因子天然选中成交额极低的股票,和 ST 高度重叠。

**实证对比** (2018-2026, 调仓日次日收益):
| | ST 持仓 | 非 ST 持仓 |
|--|:--:|:--:|
| 占比 | 28.7% | 71.3% |
| 平均日收益 | **+0.13%** | +0.12% |
| 中位数日收益 | 0.00% | 0.00% |
| 波动率 | 2.15% | 1.85% |
| 跌停率 (≤-5%) | 0.9% | 0.8% |
| 大涨率 (≥+5%) | 1.6% | 1.3% |

**为什么 ST 没有拖累**:
- ST 已被市场定价,信息是公开的
- A 股 ST 存在投机溢价: 壳资源炒作 + 散户博重组反转 → 彩票式正向尾部
- ST 退市股票在回测中被自然剔除 (次日不交易 → 调仓排除)

**决策: 不主动排除 ST。** 排除 ST = 对抗因子自身的信号,会损失 ST 彩票溢价带来的 alpha。ST 暴露是因子逻辑的结果,不是 bug。但在真实盘需注意停牌期间无法卖出的执行风险 (回测无法完全捕捉)。

### AmihudIlliq SHORT ≠ "大盘防御" - 是半导体动量 (2026-06-10)

Sharpe 动量监控显示 AmihudIlliq SHORT 6月变化+320%,引发"是否切换到大盘策略"的讨论。分析其实际持仓后发现:

**最新选股 (2026-06-09)**:
- 100% 科创板 (688xxx)
- 80% 半导体 (寒武纪/中芯国际/海光信息/澜起科技...)
- 日波动 73%-140% 年化--极端高波

**这不是"大盘防御策略"**,是"全市场成交最活跃的股票",当前恰好是半导体。这个策略:
- 历史 9 年仅胜 1 年 (2026), 全期年化 -5.2%, 最大回撤 -82.7%
- 2020 年 -37.4%, 2023 年 -25.0%--半导体泡沫破裂时崩盘
- 2026 年赚钱是因为半导体在涨,不是"风格切换"

**教训**: Sharpe 动量 +320% 是因为从极低基数反弹 (Sharpe 0.34→1.80, 2025年是半导体最惨一年)。动量好看 ≠ 策略靠谱。行业集中度是最大的隐藏风险。不能因为 6 个月跑赢就切换--最终会栽在单一行业的周期性崩盘上。

### 当前策略持仓画像: 深主板微盘传统股 (2026-06-10)

AmihudIlliq LONG (v3.0) 最新选股 (2026-06-09):

| 维度 | 实际内容 |
|------|------|
| 板块 | 100% 深主板 (00xxxx) |
| 市值级别 | 全市场成交额后5% |
| 日均成交额 | ~3700万/只 vs 全市场2.1亿 |
| ST占比 | ~40% (*ST美丽、ST海王、ST金鸿...) |
| 典型股票 | 永安林业、广弘控股、英力特、源飞宠物、慕思股份 |
| 行业 | 林业/化工/宠物/家具 - 传统行业, 无一只科技股 |
| 股价 | 多数7-50元, 远低于半导体千元股 |

**v3.0 策略的本质**: 买全市场最被忽视、最冷门、最传统的微盘股。和 SHORT (半导体) 策略形成极端对立:
- 小盘 LONG: 深主板 + 传统 + 微盘 + ST → 年化+32%/-13%回撤
- 大盘 SHORT: 科创板 + 半导体 + 热门 + 无ST → 年化-5%/-83%回撤

A股里赚钱的不是追逐热门, 是买别人不敢买的。ST不是bug, 是feature--这正是不对称收益的来源。

### *ST 过滤实为有害 (2026-06-07)

**问题**: ST 暴露 28.7%,如果某只持仓被 *ST (退市风险警示) 后连续跌停卖不掉怎么办?

**实验**: 三场景对比 - 无过滤 vs 仅排除 *ST vs 排除全部 ST。*ST 识别通过 `codes.name` 前缀判断 (`*ST` 开头 = 退市风险,`ST` 开头非 `*ST` = 其他风险)。

**结果**:
| 场景 | 年化 | 回撤 | 夏普 | 终值 |
|------|------|------|------|------|
| 无过滤 | **+20.6%** | -20.0% | **1.23** | **484万** |
| 排除 *ST | +18.3% | -19.1% | 1.06 | 403万 |
| 排除全部 ST | +19.3% | -19.9% | 1.11 | 435万 |

排除 *ST: 年化 -2.2pp,回撤仅改善 0.9pp - **用 2.2% 年化换 0.9% 回撤改善,血亏。**

**为什么**: *ST 股是 illiquidity 最极端的股票,天然占据因子排序顶端。排除 *ST = 砍掉最强 alpha 信号源。因子通过更高的收益补偿了退市风险 (ST 彩票溢价包含了退市风险定价),排除就丢掉了这部分补偿。

**结论: 不加 *ST 过滤。** 因子自身已定价退市风险。单只 4% 仓位 + A 股退市整理期 (15 交易日可交易) = 真正的尾部风险可管理。砍 *ST 的代价远大于收益。如果未来注册制下退市潮,再重新评估。

## 架构反思: 工厂搜索的盲区 (2026-06-08)

两天的密集探索暴露了一个系统性架构问题。以下是完整的发现链和架构诊断。

### 探索节点

| 节点 | 发现 |
|------|------|
| 定价模型审视 | illiquidity 是摩擦类 alpha,非风险类;A 股截面定价是单因子主导 |
| 不对称收益框架 | 策略哲学不是"找更高夏普",而是"构建不对称收益结构" |
| 丰巢期轮动 | illiq SHORT (大盘) 在 bear regime +6.3%, 而 illiq LONG 在 bear -11.4% |
| 因子×择时配对 | MA16 是普适最优,无更好 pairing |
| 全面 bear 搜索 | 74候选×18配置,仅 illiq SHORT 在 bear 正收益 |

### 架构诊断

**当前工厂 = 串行、单因子、全时段搜索框架:**

```
候选因子 → L0(IC) → L1(回测) → L2(regime) → L3(WF) → marginal
```

隐含假设: **一个因子要在所有时间段都"不差"才能活下来。**

但 illiq SHORT 永远过不了这个流水线:

| 闸门 | illiq SHORT | 命运 |
|------|-----------|------|
| L0 IC | 全时段 IC 弱 (只在 bear 有效) | ❌ 被杀 |
| L1 回测 | 年化 12%, 回撤 -60% | ❌ 被杀 |
| L2 regime | bear 好但 bull 差 | ❌ 被杀 |

**工厂找不到它,不是因为它不存在,而是工厂不理解"只在特定 regime 激活"这个概念。**

### 工厂能搜 vs 搜不了

| 当前能搜的 | 搜不了的 |
|-----------|---------|
| 单一因子全时段 | **因子 × regime 条件激活** |
| 固定 MA16 择时 | **因子 × 择时配对** |
| long-only top-N | **双向 long-long 切换** |
| 独立候选 | **regime-aware 组合编排** |
| 对称指标 (Sharpe/IC) | **regime-conditional 评估** |

### 轮动实证

**bull → illiq LONG (小盘), bear → illiq SHORT (大盘):**

| | 年化 | 回撤 | 夏普 | 终值(100万→) |
|--|------|------|------|------|
| 基线 (LONG+Band) | +25.0% | **-17.7%** | **1.50** | 691万 |
| 轮动 | **+32.2%** | -33.2% | 1.09 | 999万 |

轮动多赚 7pp,但回撤翻倍。SHORT 腿在 bear 中 always-invested 无风控是主因。2020 年 COVID V 型反转贡献了大部分超额(+97pp),其他年份改善有限。

**方向对,裸奔不行--SHORT 腿需要自己的风控层。**

### 架构演化方向

不是推翻工厂,是**加一层"regime-aware 组合编排"**:

```
当前:  搜一个万能因子
应该:  搜一组因子,每个负责一个 regime,编排成完整策略

示例:
  bull regime  → illiq LONG × Band (进攻)
  bear regime  → illiq SHORT × 独立风控 (防御)
  chop regime  → 现金或低仓位
```

这套逻辑在当前工厂的表达能力之外--需要的是**组合编排层**,不是因子发现层。核心变化:

- 搜索单元从"因子"变成"因子 × regime × 择时 × 方向"的组合
- 评估从"全时段 Sharpe"变成"每个 regime 内的不对称性"
- 最终输出不是单一最优因子,而是**多腿 regime 编排方案**

`scripts/research/asymmetry_retrospective.py` + `scripts/research/experiment_ts_weighting.py` + `scripts/research/experiment_factor_timing_pairing.py` 作可复用资产。

### factor_research vs Personal Alpha - 两套系统对比 (2026-06-08)

Personal Alpha 是同一数据湖的"干净版本"--策略层剥离,只保留数据基础设施+通用回测内核。
但意外发现它的 `factors/` 框架设计远优于我们的:

**Personal Alpha 的因子表达层 (我们缺少的)**:
- `Factor` 抽象基类: 延迟计算图, `factor.compute(data)` 统一接口
- `TransformedFactor`: `.rolling(n).zscore().shift(1)` 链式变换
- `FactorBlend`: `factor1 + factor2 = FactorBlend`, 静态/IC动态加权
- `FactorSpace`: axis-based 网格搜索 (比我们工厂的 mutation spec 更通用)
- `FactorData`: 统一输入容器 (close/volume/amount/raw_close/industry)

**我们有但 Personal Alpha 没有的**:
- 完整生产链路 (run_daily → paper_trade → Obsidian + launchd)
- WF 验证 + Regime 引擎 + Composer
- 不对称性审计 + regime-conditional 评估
- L0-L3 工厂流水线 + marginal eval

**结论**: Personal Alpha 的因子表达层设计更优雅(延迟计算图 > 散落函数),
但缺乏验证和生产能力。理想下一步: 把它的 `factors/` 框架搬过来当表达层,
上面接我们的 Regime 引擎 + Composer + 生产链路。

Personal Alpha 路径: `/Users/kiki/Personal Alpha`

## 工程: LLM 链路静默退化让上层实验白跑 (2026-06-12)

闭环验证发现:此前所有"带 LLM"的 AutoResearch 搜索**实际都没用上 LLM**--三层静默退化叠加:1 HTTP 30s 读超时掐在真实生成耗时(20-35s)边缘,撞上即空返回;2 `adapter.complete` 捕获一切异常返回 None,真实死因不可见;3 `_llm_seeds` 整体 try,一岛失败拖垮全部播种退回确定性种子。**`seeded_by` 字段如实标注是唯一发现手段**--如果当初让它在退化时伪装成 "llm",这个洞永远查不出来,所有"LLM 没用"的结论都会是假的。教训:1 管道里每个降级路径都必须在输出里留下如实痕迹(口径透明铁律的工程版);2 外部调用的超时要按真实耗时分布定,不要用库默认值;3 吞异常的兜底层(`except Exception: return None`)必须把原因送到可观测的地方再吞。

**闭环对照结果(同日,预注册判据)**:基线(确定性种子)C1=0/5 冠军全部收敛回反转+低波吸引子(novelty≤0.43);修复后反思播种(LLM+失败台账)C1=5/5(novelty 0.68-0.88),冠军迁移到 momentum(60)+revenue_yoy / illiquidity-volatility / volume_ratio+revenue_yoy 等基本面/流动性组合,C2=4/5 OOS ICIR≥0.3 且短窗 L1 存活。**P3 反思确实改变了生成分布**;但注意 C2 的 L1 是 2025-2026 短窗(妖股友好年),不等价于长窗 L1 闸门;且语义指纹尚不识别整体取反(两个互为镜像的冠军同时入选),待补。

## 工程: 假 runner 测试掩盖铁律错配 (2026-06-11)

AutoResearch → L0-L3 的桥接代码(`factory/autoresearch/pipeline.py`)写完后,所有 pipeline 测试都注入 fake runner,结果掩盖了一个真跑必炸的 bug:`ast_to_hypothesis` 产出 `DRAFTED` 状态,而 `run_l0` 的 F-2 铁律(cheap-first)在 try 块**外**断言进 L0 必须是 `QUEUED` → 第一次接真实 runner 直接 `InvariantViolation`。

**教训**:跨模块桥接必须配**真实被调方的契约测试**--用确定性合成面板(不碰 data_lake,420 天×25 股带漂移)逐级调用真实 `run_l0..run_l3`,断言 `Experiment.result.error is None`。fake runner 只能测桥接自身的控制流,测不了契约;decision 交给真实 gate,不在断言范围。同类坑:声明了 `neutralize` 但运行时不执行 = 口径不透明,validator 现在直接拒绝未实现的声明。

## 关键决策
- **文档治理**(2026-06):CLAUDE.md 精简(操作宪法)/ SPEC.md(架构)/ STATUS.md(进度)/ LESSONS.md(本文件)。别再把设计/进度往 CLAUDE.md 堆。
- **母策略两层台账**(2026-06):口径降为版本属性;组合 vs 轮换待定(先只立分类)。
- **阶段 0 收束**(2026-06):统一 `core/` 内核 + data_lake + 真实成本;旧 `data_full/data` 约 513M 清理,旧 `evolve` 不再是主线。
- **项目级目标校准**(2026-06):原"年化 35%/回撤 15%"锚定在 v1.0 的 `data_full` 水分 40%,去水分+真实成本后真实基线仅 21%,该目标退役。校准为**双轨**:满意线 年化≥20% & 夏普≥1.0(baseline 已达),卓越线 年化≥28% 或 卡玛≥1.6。单母策略入册线 15%/20% 不变。组合路线(`scripts/research/portfolio_combo.py` 验证)能降回撤/提卡玛(压力 -33.9%→-27.8%),但难把绝对收益提到 35%--目标连同口径一起"去水分"。

## 前端: Next.js 缓存污染与全局表单暗色 Contrast 冲突 (2026-06-16)

### 1 Next.js 开发服务缓存污染导致 404/500
* **现象**:在 Next.js 开发服务(`npm run dev`)处于运行状态时,如在同目录下执行生产打包(`npm run build`),会污染 `.next` 构建缓存。当浏览器发起新请求时,开发服务会因找不到 Webpack 临时编译块文件(`*.pack.gz`)抛出 `ENOENT` 致命异常,导致除当前内存已缓存页外,其它页面全网 404/500。
* **处置**:必须物理清理缓存。步骤:1 杀掉占用 3000 端口的 Node 进程:`lsof -ti :3000 | xargs kill -9`;2 强制清空 `.next` 缓存目录:`rm -rf web/.next`;3 重启开发服务 `npm run dev`,并指引用户在浏览器端执行**硬刷新 (⌘+Shift+R 或 Ctrl+F5)** 清空前端路由缓存。

### 2 全局暗黑模式下表单控件"白底白字"冲突
* **现象**:当将网站换装为暗黑/深色主题(在 `globals.css` body 中全局声明 `color: #EFEFEF` 白字)时,如果 `<input>`, `<select>`, `<textarea>` 等基础表单元素未在组件中显示定义背景类,它们会继承 body 的白字,但保留浏览器原生的白色输入背景,产生**白底白字**这一典型暗色转换 Bug。
* **处置**:在 `globals.css` 中引入最高优先级(`!important`)的全局表单控件样式覆写,强制声明深色半透明背景及正文高亮颜色。这样无需逐一修改历史组件中零散的表单项,即可自动在全站消除对比度冲突。

### 3 常驻生产前端崩溃循环:`next start` 找不到 `BUILD_ID`(2026-06-22)
* **现象**:前端整站打不开。launchd 常驻任务 `com.astcok.web` 跑的是 `npm run start`(= `next start` **生产模式**),它必须有 `next build` 产出的 `.next/BUILD_ID`。日志反复刷 `Error: Could not find a production build in the '.next' directory`,任务崩溃即被 launchd 拉起、再崩,形成重启循环;`:3000` 因此始终无人监听。**后端 API(`com.astcok.api`)完全不受影响,持续 `200 OK`**--所以"系统被破坏"实为前端单点,虚惊一场。
* **根因**:`.next` 被污染成**残缺混合态**--同时存在 `static/development/`(dev 产物)与残缺的生产 manifest,但**缺 `BUILD_ID`**。几乎可断定是"生产 `next start` 常驻期间又跑了 `next dev`"--两者共用 `.next`,正是本节 1 与 `web/CLAUDE.md §2.1` 禁止的操作。`.next` 是 gitignore 的**可再生构建产物,不是真实数据**,故此类"删除/破坏"无源码或数据损失(`git status` 0 个 `D` 可证)。
* **损失评估铁律**:报"系统被删/破坏"时,先 `git status --short | grep '^ ?D'` 看**有无真删的已追踪文件**(本次 0 个),再分清**前端构建产物丢失 ≠ 数据/源码丢失**。API/数据湖/registry/研究代码与前端 `.next` 是隔离的。
* **处置(即 `web/CLAUDE.md §2.2` 缓存自救 + 重建生产)**:1 `launchctl bootout gui/$(id -u)/com.astcok.web` 停掉崩溃循环;2 `rm -rf web/.next web/node_modules/.cache` 清污染;3 在 `web/` 下 `npm run build` 重建生产产物(写出新 `BUILD_ID`);4 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.astcok.web.plist` 重新加载;5 `curl -s -o /dev/null -w "%{http_code}" localhost:3000/overview` 应回 `200`(`/` 回 `307` 是正常重定向到 `/overview`)。
* **教训**:常驻生产服务(launchd `next start`)下**严禁**再起 `next dev`。改完前端代码的正确刷新姿势 = `web/` 下 `npm run build` 后 `launchctl kickstart -k gui/$(id -u)/com.astcok.web`,绝不在生产常驻期跑 dev。


## Agent 个股画像:后复权价当股价展示(铁律3 复权陷阱)(2026-06-17)
* **现象**:`services/read/stocks.py::stock_profile` 直接取 `data_lake/price/daily/{code}.parquet` 的 `close` 当"最新股价"展示给用户。但该价是**后复权价**(回测口径),汇川技术(300124)后复权 1579.95,而真实股价仅 ~67 元--用户一眼看出"数据不对"。
* **根因**:`price/daily` 为回测用后复权序列;`daily_basic` 没有 `close` 列。用后复权价算收益率(比率)没问题,但当"股价"展示就违反铁律3。
* **处置**:真实股价 = `daily_basic.total_mv / total_share`(万元/万股=元),作为展示头条;后复权 OHLC 标 `close_is_adjusted` 仅供算收益、并从喂给 LLM 的 narrate 数据里**剔除**(防 DeepSeek 又把 1579 当股价讲);加 warning。估值(PE/PB/PS)、总市值、资金流均来自 `daily_basic`/`moneyflow`,本就正确。
* **教训**:任何 user-facing 的"价格"展示都要走不复权;后复权只用于回测/收益率。新增个股字段时先问"这是复权价还是真实价"。

## 工程: 演化搜索中最后一代的"串行收尾"栅栏错误 (2026-06-28)
* **现象**:进化搜索在大规模运行(如 8岛/10代)时,进入后代评估末尾时速度急剧变慢,甚至长达 20 分钟候选库计数只增加几十。但 CPU 满载率为 98.3%,没有发生挂起或死锁。
* **根因**:`islands.py` 的世代循环 `for gen in range(generations):` 结束时,系统通过变异繁衍出了最后一批种群。然而,由于循环退出,这最后一批种群(8岛 × 12 population = 96 个新因子)**完全错过了多进程并行评估(Batch Pre-evaluation)块**。随后在收尾时:
  ```python
  for i, pop in enumerate(islands):
      for c in pop:
          fitness(c, i, generations - 1)
  ```
  这 96 个因子被迫以**单线程、串行排队**的形式进行评估,导致产生严重的等待时延。此为典型的程序设计**栅栏错误 (Fencepost Error)**。
* **教训**:在世代迭代结束后、进入收尾评估排序前,必须追加一次并行的 `Batch pre-evaluate`。只要存在多进程加速的批处理流程,一定要确保循环边界外的收尾批次同样被批处理,不能留下串行漏洞。

## 架构: 多阶段无状态 Runner 导致的"三重因子重复计算"重度瓶颈 (2026-06-28)
* **现象**:即使世代寻优结束,评估 Top K 冠军因子的 L1~L3 深度审计依然极为缓慢,单个冠军的审计需要耗费 10-15 分钟以上。
* **根因**:L1 快速回测、L2 多时区回测和 L3 Walk-Forward 属于独立、无状态的验证引擎(Runner)。每个引擎在执行时都会独立调用 `fn(*args, **hyp.factor_params)` 来生成因子面板。对于复杂的 GA 冠军因子(深层 AST 公式),计算一次全量因子面板需要 5-8 分钟。由于各 Runner 之间**完全不共享计算好的因子面板**,导致同一个因子在一次审计管道中被**原封不动地在内存中重复计算了 3 遍**,使得单核 CPU 耗时拉长了 3 倍!
* **教训**:跨验证阶段(L0 -> L1 -> L2 -> L3)应该引入**上下文共享(Context Sharing)**。在管道最开头将因子面板计算并暂存在内存中,作为参数直接注入后续所有的验证 Runner,避免重复执行高开销的 AST 树矩阵计算。

## 性能: 多进程并发与大 DataFrame 磁盘 I/O 争抢 of 物理瓶颈 (2026-06-28)
* **现象**:大范围因子演化搜索(8岛屿并行)中,子进程分配充足(8核),但中后期 CPU 大量时间处于等待态,整体吞吐率低下。
* **根因**:由于 Python 的 GIL 限制,系统采用 `ProcessPoolExecutor` 物理隔离的多进程模型。每个子进程都有独立的内存空间,无法共享父进程载入的 80MB 基础数据和因子面板(如 `_BASE_FACTOR_MEM_CACHE`)。这导致 8 个进程在并发运算时,频繁、重复地从 SSD 磁盘读取相同的 80MB Parquet 文件,产生严重的磁盘 **I/O 读写风暴与通道争抢(I/O Contention)**。
* **教训**:对于百兆级以上的大 DataFrame 矩阵并发计算,多进程模型会导致严重的 I/O 瓶颈和内存冗余。后续应改用**共享内存机制(Shared Memory)**或通过 **GPU/Tensor 统一内存架构**进行硬件级并行加速,彻底消灭磁盘 I/O。

## 工程: 变异参数微调导致的"缓存击穿与过物理化写盘"惩罚 (2026-06-28)
* **现象**:在遗传算法对窗口参数进行轻微突变时(如 `volatility(20)` 变为 `volatility(21)`),系统计算速度断崖式下跌,且伴随 SSD 写入量剧增。
* **根因**:系统将不同的窗口参数视为全新的物理因子,在突变时会完整计算 15 年的全量面板,并**往磁盘上写入一个新的 80MB 物理 Parquet 文件**。这在参数抖动频繁的遗传算法中,触发了高强度的缓存击穿与写盘惩罚。
* **教训**:不要对"仅窗口参数不同"的衍生因子进行过度的物理化(Over-materialization)磁盘落库。应该只固化最底层的原始量价面板,将窗口滚动操作(如 `.rolling(N).std()`)延迟到内存回测时动态执行。

## 方法: 因子前置计算耗时导致 L0 时间预算门禁的"假阴性误杀" (2026-06-28)
* **现象**：部分逻辑清晰、预测力极佳的复杂复合因子，在 L0 阶段被系统以 `computation_time_budget exceeded (10.4s > 10.0s)` 为由无情丢弃（Discard）。
* **根因**：原版验证管道中，计算因子面板的时间被直接计入了 L0 Runner 计时器中。当遭遇树状嵌套深度极深的复杂因子时，首次冷启动计算耗时极易突破 10 秒硬闸门，触发了假阴性误杀。
* **教训**：在设计带有计算超时预算（Timeout Budget）的验证门禁时，必须将"因子生产耗时"与"门禁诊断耗时"解耦，或者通过前置预计算扣除生产成本，防止系统错误地将高预测力但高算力的"重超额收益"因子误杀。

## 全因子 IC/ICIR 全景审计 (2026-06-27)

### A 股动量全面反向——追涨杀跌是赔钱策略
对 30+ 因子做全市场 2010-2026 Rank IC 扫描，**所有动量/趋势因子 IC 均为负**：
* `momentum_60d`: ICIR=-0.445, Win=33%
* `momentum_20d_skip5`: ICIR=-0.387, Win=35%
* `volatility_20d`: ICIR=-0.403, Win=33%
* `price_to_ma60`: ICIR=-0.493, Win=32%（负 IC 最强）
* `amount.rank(pct=True)` (amount-timing 策略): ICIR=**-0.840**, Win=20%（最强反向信号）

**结论**: A 股是反转市场，任何"强者恒强"假设的因子都会稳定赔钱。所有正向 IC 的因子都是"逆向"类（size=买小的、illiq=买不流动的、short_reversal=买刚跌的）。

### size 是 A 股 alpha 的唯一锚——其余全是换皮
* `size60 (-log amount)`: ICIR=0.586, NW-ICIR=0.156, Win=73% — **绝对王者**
* `Amihud illiq 20d`: ICIR=0.474, NW-ICIR=0.127 — 第二，但与 size60 的 MI 距离仅 1.34（metasearch 证实：同一信息源的两面投影）
* `size-low-vol` 系列: ICIR=0.60-0.63 — 只比纯 size 高 0.01-0.04，lowvol 成分 ≈ 噪声
* `small_cap_factor__window{20,30,45,60,90,120,252}` 七个窗口版: 全是对同一个 size 因子切不同窗口 = 7 个换皮变体

**教训**: 22 个在册/参考策略、18 个唯一因子公式，本质绕来绕去就 3 个方向：小盘、非流动性、量价复合。且量价复合（Huaxi 11、Momentum+Quality）的 IC 远弱于纯 size。系统的因子多样性是假象。

### alpha101_006 意外亮眼——需防未来函数
全市场 ICIR=0.764 (raw) / 0.250 (NW)，Win=80%，是唯一超过 size 的因子。但 alpha101 原版部分公式使用了未来价格做截面排名，**必须逐行审计 `alpha_006` 的实现是否含 look-ahead**。ICIR_nw=0.25 保留了约 1/3 的 raw ICIR，说明 IC 自相关较弱（好信号）。若审计干净，这是系统第一个真正的 non-size alpha 候选。

### 基本面因子在 A 股集体失效
* `net_profit_yoy`: ICIR=-0.206, NW=0.056
* `roe`: ICIR=-0.077, NW=0.021
* `gross_margin`: ICIR=0.001, NW≈0
* `bp_proxy` (账面市值比代理): ICIR=0.311, NW=0.083 — 唯一勉强有信号的，但其与 size 高度共线（小盘≈低 PB）

**结论**: 财报数据的截面预测力在 A 股极弱。不是数据频率问题（季度 vs 日度），是 A 股定价对公开财报信息反应已充分（或定价主要靠非基本面因素）。

## DSR 审计：_taibook 口径与台账持久化值严重脱节 (2026-06-27)

### 事实
STATUS.md 06-18 声称 _taibook 对齐口径 DSR 审计结果：
* illiquidity v1.0 p=0.032, v1.1 p=0.043, v3.1 p=0.034, small-cap-size v2.0 p=0.017 → "4 个 standalone 扛过多重检验"

但 06-27 用 canonical 9-Gate 重跑后，台账持久化的值截然不同：
* illiquidity v1.0 p=0.341, v1.1 p=0.274, v3.1 p=0.177, small-cap-size v2.0 p=0.086

### 根因
1. **n_trials 不同**: STATUS 用逐家族迭代数（地板 3），canonical 用 trial_ledger 真实计数（illiquidity=6）
2. **数据窗口不同**: _taibook_start 对齐到各版本 data_scope.period（较短），canonical 用 2010-2026 全量
3. **结论**: STATUS 的乐观值从未持久化到 `strategy_versions.json`。canonical 重跑后全部不通过。

### 教训
**任何"口头/文档声称的审计结果"不等于台账事实。唯一真相 = `strategy_versions.json` 中 `nine_gate.dsr_p` 字段的值。** 审计必须 `--persist` 落台账，否则是空气。文档与台账脱节会制造"我们有几个通过的 alpha"的假自信。

## 13 个缺 Gate 策略批量补审——Gate 2 方法论 (2026-06-27)

### 问题
22 个在册/参考策略中，13 个完全没有 Gate 数据（无 executable_spec，无法走 `run_nine_gates_all.py`）。

### 方法
写 `scratch/batch_gate2_audit.py`，按策略声明的因子公式映射到可计算函数，只跑 Gate 2（Rank IC + NW-ICIR + IC Win% + Monotonicity），不跑 Gate 4/5（DSR/回测需要 executable_spec）。结果通过 `strategy_registry._load/_save` 写入 `nine_gate` 字段。

### 结果
* 8 个 size/illiquidity 变体：全部 PASS Gate 2（ICIR 0.47-0.63）= 换皮确认
* 2 个（amount-timing、large-cap-growth-hedged）：WARN（IC 反向或弱）
* 1 个（roc-yc）：FAIL（ICIR=0.045，NW=0.012 ≈ 无信号）
* 2 个（d-le-sc-hedged ×2）：SKIP（因子公式为空，不可复现）
* 4 个（industry-neglect ×4）：SKIP（行业级因子，需独立审计框架，不在本次范围）

### 教训
**没 executable_spec 不代表不能审——Gate 2 纯因子诊断不需要回测引擎。** 只要因子公式可映射到价量计算，就能跑 IC 扫描。批量脚本一次加载数据、依次算 IC，8 个因子约 2 分钟完成（瓶颈在 `calc_ic` 的 4000 次 Spearman 截面相关）。

## small-cap-staleness 降级——唯一在册策略清零 (2026-06-27)

### 事实
`small-cap-staleness v1.0` 是系统唯一在册策略（admission.track=diversifier，依托 portfolio margin contribution 入册），但：
* dsr_p=0.5469（严重不显著，12 次 trial）
* maxdd=-38.5%（远超 20% 入册上限）
* 0 Gate 数据（完全无审计）
* STATUS 06-22 已确认所有 5 个 diversifier 全 decayed（回撤 -50%~-96%，WF 夏普 ≈ 0）

### 行动
直接通过 `strategy_registry._load/_save` 将 status 从"在册"改为"参考"，写入 `dsr_demotion` 审计块。

### 教训
**`demote_dsr_insignificant_standalone()` 只扫 standalone 轨，diversifier 轨不会被自动降级。** 如果 diversifier 的 maxdd 超过入册上限或策略已衰减，需人工判断降级。建议给 `demote_dsr_insignificant_standalone` 加一个 `--include-diversifiers` 开关或在 `decay_monitor` 中加自动降级逻辑。

## 系统当前状态：正确拒绝所有假 alpha (2026-06-27)

审计完成后系统状态：
* **在册策略: 0**
* DSR 显著 (p<0.05): 0/22
* 最佳 DSR: small-cap-size v2.0 = 0.086（边缘，差一口气）
* 9-Gate 覆盖: 22/22（100%）
* 全在册/参考策略 Gate 2 IC 可用: 15/22

**这不是故障——是宪法在正确执行。系统宁可空仓（部署已切换为防守仓：空仓+国债 ETF 511010），也不信任假 alpha。** 审计暴露的真相：整个因子池本质是 size + illiquidity 的换皮变体，没有一个能同时扛住多重检验（DSR）和真实成本回测（Gate 5）。

## Alpha101 全量审计：32 因子完整证伪记录 (2026-06-27)

### 背景
`factors/alpha101.py` 实现了 32 个 WorldQuant 风格 alpha 公式，30 个注册在 AutoResearch 白名单。此前只有 alpha_012/013 在闭环实验中被 LLM 选中测试过（未成 standalone），其余从未被单个评估。

### 审计流程
1. **全量 IC 扫描**（32 因子，全市场 2010-2026，forward_ret=20d）
2. **防未来函数审计**（逐行审查 top 6 公式，确认无 look-ahead）
3. **9-Gate 完整管线**（top 9 因子，含 Gate 2/3/4/5/6/7）
4. **方向反转测试**（direction=-1，买底部替代买顶部）
5. **否决器测试**（alpha101 排除底部 20-50%，挂在 size60 上）
6. **复合因子测试**（size+alpha101 权重扫描 0%~100%）

### 核心发现

#### 发现 1：截面 IC 与组合收益完全脱钩
```
alpha003 ICIR=0.920 → Gate5 Sharpe=0.38  ❌
size60   ICIR=0.586 → Gate5 Sharpe=0.85  ✅ (对比 illiq v3.1 Sh=1.33)
```
**截面 IC 强 ≠ 组合能赚钱。** 这 9 个因子 Gate 2 IC 全过（NW-ICIR 0.12-0.30, Win 66-82%），Gate 3 中性化后 ICIR 反升 22-39%（确认真特质信号），但 Gate 5 真金白银回测全部惨死（年化 7-12%，Sharpe 0.26-0.38，回撤 47-63%）。

#### 发现 2：alpha 在双尾价差，不在任何一端
- LONG（买 top-25）：Sh=0.38，DD=-49~-63% → 死
- REVERSE（买 bottom-25）：DD > 95%，触发结果哨兵 → 死得更惨
- 两端的 9-Gate 全部触发回撤哨兵

**根因**：IC 度量的是全截面排序（含 Q1→Q5 多空两端），alpha 分布在双尾。long-only 只取一头，漏掉绝大部分信息。A 股不能做空 → 多空价差无法实现 → alpha 无法变现。

#### 发现 3：alpha101 与 size 正交 = 毒药，不是资产
G3 中性化保留率 122-139% 说明 alpha101 与 size 完全正交。但这在 long-only 下是坏事——alpha101 选的是**和 size 完全不同的股票**，而这些股票赚不到钱：

```
纯 size60:                    Sh=0.85
size60 + alpha040 否决器:     Sh=0.88 (边际 +0.03，基本没用)
70% size + 30% alpha040:     Sh=0.57 (掺 30% 就烂)
50% size + 50% alpha040:     Sh=0.48
纯 alpha040:                  Sh=0.38
```

**掺 alpha101 越多，Sharpe 越低。线性单调递减。** 正交不是分散化红利——是噪声注入。

#### 发现 4：高 IC = 高换手 = 高成本衰减，形成负反馈
```
alpha044: ICIR=0.76, TO=25x/年, 成本衰减 209%
alpha025: ICIR=0.45, TO=15x/年, Gate 6 PASS (唯一)
```
截面排序越强的因子，成分股变化越快，换手越高，交易成本吃掉越多 alpha。最强的 IC 因子反而是最差的组合因子。

#### 发现 5：防未来函数审计通过，但无用
Top 6 公式全部审计干净——`corr(close, volume)` 只用当日收盘数据，无 look-ahead。但在 A 股 long-only 约束下，干净也白干净。

### 所有尝试汇总

| 用法 | 结果 | 判定 |
|------|------|:--:|
| 纯 alpha101 top-25 做多 | Sh=0.26-0.38, DD=-47~-63% | ❌ 证伪 |
| 纯 alpha101 买底部 (direction=-1) | DD > 95% | ❌ 证伪 |
| alpha101 否决器挂 size60 (排 20-50%) | Sh +0.00~0.03 边际 | ❌ 无效 |
| alpha101 + size60 复合因子 (任意权重) | Sh 随 alpha 权重线性递减 | ❌ 毒药 |
| alpha101 截面 IC | ICIR=0.40-0.92, Win=66-82% | ✅ 信号真实但不可变现 |

### 教训
1. **截面 IC 是必要的但不是充分的。** Gate 2 过了只说明排序有用，Gate 5 过了才说明能赚钱。永远不要用 IC 替代回测做决策。
2. **在 long-only 市场，正交 ≠ 分散化。** 与主力因子正交的新因子，选出来的股票可能与主力因子完全不重叠——不是在分散，是在换池子。换过去的池子如果本身不能独立盈利，就是纯噪声。
3. **高 IC 因子往往高换手，形成负反馈。** 截面排序变化越快，信号越"敏锐"，但交易成本也越高。A 股 0.5% 双边成本下，年换手 >15x 的因子基本不可能净赚钱。
4. **"alpha 在空头侧"的判断要精确化。** 不是"买底部就能收割"，而是"alpha 在多空价差里，两端各自都烂"。只有能同时做多 Q5 和做空 Q1 的市场才能变现双尾 alpha。
5. **Alpha101 在 A 股：理论干净、统计显著、经济合理、无法变现。** 这是诚实研究的范本——不 p-hack、不降成本、不造假。记录为证伪结论，给后续研究节省算力。
