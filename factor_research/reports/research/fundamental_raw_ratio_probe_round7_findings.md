# daily-round-7:基本面原始比率族独立 probe(方向①)

> 角色边界:本轮只做假设设计与确定性 L0 取证,不判断 alpha 是否有效,不入册、不晋级、不部署。
> 数据边界:本 worktree 使用主仓只读 data_lake symlink(含顶层 `fundamental_batch.parquet`,已显式加载校验非空,413940 行,ann_date/avail_date 最新到 2026-07-09)。价格数据实测覆盖到 2026-07-17(约 5100+ 只个股,close 非空),manifest `last_check` 字段本身滞后(2026-06-30)但底层 parquet 更新,本轮按当前有效证据呈报,非纯历史回溯。

## 1. 背景与方向选择

2026-07-13 daily-round-6(方向③研究总监审视)重跑 metasearch information_map,发现:

- 06-23 标出的 3 个信息空白区(vol_breakout / 基本面族 / 跨资产腿)中,vol_breakout 已被 round1 证伪关闭,跨资产腿由其他分支持续挖掘。
- **基本面族的原始比率本身(roe/net_profit_yoy/bp_proxy/gross_margin)从未被真正单独 probe 过**——此前两次基本面 probe(round4 2026-07-06、main 2026-07-12)测的都是这 4 个原始比率的**衍生**运营质量指标(应收/应付/存货强度变化),已被证伪(`balancesheet-operational-quality-weak` 条目),不是这 4 个原始比率本身。
- 这 4 个因子在当前信息地图 MI-distance frontier 排名前 8 里占了 3 席(gross_margin 距离 2.960 排第 2、roe 2.956 排第 3、bp_proxy 2.943 排第 8;net_profit_yoy 未进 top8 展示但同族),接近满量程 2.97,是信息地图当前唯一仍然开放、证据最充分的下一步。

round7 编号取自 `strong_ai_rounds.jsonl` 上一条(round=6,2026-07-13,方向③),三方向轮换 3→1,本轮 = 方向①(机制因子族设计)。

## 2. 现状核查与代码改动

`factors/fundamental.py` 已实现 6 个基本面因子函数,但**全部未接入 `@register_factor` 单点注册系统**(该模块此前不在 `factors/registry.py::_MODULES` 内)。三处手工接线现状(`factory/autoresearch/registry.py::ALLOWED_FACTORS`):

- `roe` / `net_profit_yoy` / `revenue_yoy` / `bp_proxy` / `ep_proxy`:已手工接入(legacy,`check_factor_registry.py` C1 冻结清单内),**已进搜索白名单**,但从未走过独立 L0 probe。
- `gross_margin`:函数已实现,但**完全未接入任何白名单**——既不在手工三面接线,也不在 `FACTOR_REGISTRY`。

按 `factors/registry.py` canonical 规则"legacy 只减不增,新因子必走 `@register_factor`",本轮补齐:

1. `factors/fundamental.py::gross_margin` 加 `@register_factor("gross_margin", definition=..., data=("fundamental/gross_margin",), searchable=False)`——`searchable=False` 因为尚无 probe 证据,不得声称已验证。
2. `factors/registry.py::_MODULES` 加入 `"factors.fundamental"`,触发 eager import 完成注册。

`gross_margin` 注册后**不进 `ALLOWED_FACTORS`**(`searchable=False` 时自动接线不写入),已用测试钉死。

## 3. 对抗性测试

新增 `tests/test_factor_fundamental_registration.py`(6 项):

- `test_gross_margin_registered_via_register_factor`:注册表里存在,definition/data 非空。
- `test_gross_margin_not_searchable_without_evidence`:`searchable=False` 且不在 `ALLOWED_FACTORS`。
- `test_promoting_gross_margin_without_evidence_fails`(**对抗**):试图把 `gross_margin` 提升为 `searchable=True` 但不带 evidence,必须 `ValueError`。
- `test_handwiring_gross_margin_manually_would_be_rejected`(**对抗**):模拟绕过 `@register_factor` 手工把 `gross_margin` 塞进 `ALLOWED_FACTORS` 字面量,`check_factor_registry.py` C1 守卫必须报错拒绝。
- `test_gross_margin_computes_on_real_lake_data`(happy path):真实数据湖计算,输出与 `close` 同形状,非空率 >30%,截面均值近似 0(z-score 产出应有形态)。
- `test_live_repo_guard_passes_with_gross_margin_registered`:真实仓库 `check_factor_registry.check()` 全绿。

复用既有 `tests/test_factor_registry_guard.py`(17 项,含 C1-C4 全部对抗断言)与 `tests/test_direction_registry.py`(12 项,含 `test_shipped_registry_is_valid_and_evidence_backed` —— 逐条 `scope_factors` 必须在 `ALLOWED_FACTORS` 白名单内,这正是本轮把 `gross_margin` 排除在任何 `scope_factors` 之外的机械约束来源)。

## 4. L0 probe 结果

- `signal_source_probe.py`:universe=`all`,start=`2018-01-01`,cutoff=`2022-12-31`,end=`2024-12-31`(沿用 round4/round6 07-12 probe 的窗口口径,end < holdout boundary 2025-01-01,不碰金库)。
- 因单进程重复加载 2010-2030 全量基本面面板(`_load_fundamental_cache` lru_cache)较慢(单因子 CLI 调用约 3 分钟),roe 单独跑,net_profit_yoy/bp_proxy/gross_margin 合并成一个进程共享缓存跑。

| factor | raw IC IS/OOS/full | residual IC(去size/流动性) IS/OOS/full | 正交保留率(残差/原始,full) | 风格相关 size/liquidity/momentum |
|---|---:|---:|---:|---:|
| `roe` | -0.0053 / -0.0190 / -0.0091 | +0.0116 / -0.0133 / +0.0047 | -52%(符号不一致,数字非有效量级) | 0.103 / -0.0 / -0.034 |
| `net_profit_yoy` | -0.0115 / -0.0182 / -0.0134 | -0.0085 / -0.0154 / -0.0104 | 78% | 0.084 / 0.003 / -0.033 |
| `bp_proxy` | 0.0501 / 0.0928 / 0.0619 | 0.0136 / 0.0592 / 0.0263 | 42% | 0.082 / -0.339 / -0.098 |
| `gross_margin` | -0.0021 / -0.0151 / -0.0057 | 0.0031 / -0.0138 / -0.0016 | 28%(符号不一致) | -0.017 / 0.065 / -0.017 |

诚实边界提醒:当 IS/OOS 或原始/残差跨过零轴符号反转时,"留存率/保留率"是除以一个接近零的分母算出的数字,不代表有意义的量级——结论应看 IS/OOS 符号是否一致,而不是这个比率数字本身(roe、gross_margin 两行的百分比因此标注"数字非有效量级")。

### 4.1 逐因子结论(仅 L0 证据,非 alpha)

- **roe**:原始 IC 全程为负但极弱(ICIR 0.05-0.12);残差 IC **IS→OOS 符号反转**(+0.0116 → -0.0133)——标准化线性单因子在这个窗口不稳定,不泛化。**falsified/weak**。
- **gross_margin**:与 roe 同一模式,原始与残差 IC 都很弱且 IS→OOS 符号翻转(残差 +0.0031 → -0.0138)。**falsified/weak**,但因 `searchable=False` 不进任何 `scope_factors`。
- **net_profit_yoy**:原始与残差 IC **全程稳定为负**(IS -0.0085 → OOS -0.0154,同号,ICIR 0.16-0.29),正交保留率 78%(风格相关低,非 size/流动性伪装)——这是本轮唯一"符号稳定 + 真正交"的结果,但**方向是负的**,与 `factors/fundamental.py` 模块 docstring 标注的"net_profit_yoy(NPY,净利润同比增长 — size_earnings v1.0 实证)"隐含的正向贡献假设相反。本轮不判断谁对谁错(R-LLM-001 边界),已作为 `needs_human` 事项提出。
- **bp_proxy**:本轮信号最强(OOS 原始 IC 0.0928,ICIR 0.99),但呈现 **IS 弱(残差 ICIR 0.08)、OOS 强(残差 ICIR 0.59)** 的不对称模式,且与 liquidity 的风格相关达 -0.339(明显高于其他三个因子)。23 个月的 OOS 窗口恰好覆盖 2023-2024 A 股价值风格占优期,不能排除是该窗口特有的风格轮动运气,而非稳定结构性正交信号。

## 5. 方向登记簿回写

`knowledge/direction_registry.json` 改动两处:

1. **原地更新 `frontier-fundamental-family`**(而非留旧 BOOST 条目 + 新开矛盾的 DEPRIORITIZE 条目并存——round6 的教训是分支孤立导致重复浪费,同一登记簿内自相矛盾的 BOOST/DEPRIORITIZE 并存会持续错误引导算力):status 从 `frontier/BOOST` 改为 `falsified/DEPRIORITIZE`,`scope_factors` 收窄到仅 `["roe"]`(唯一同时满足"已证伪"且"在白名单内"的成员;`gross_margin` 证据同样弱但因 `searchable=False` 不得进 scope_factors)。`revenue_yoy`/`ep_proxy` 本轮未测,从 scope_factors 中移除,不作任何判断(既不 BOOST 也不 DEPRIORITIZE,状态诚实地留空白)。
2. **新增 `fundamental-raw-ratio-mixed-signals`**:`status=mixed, action=NOTE`,`scope_factors=["net_profit_yoy","bp_proxy"]`,记录这两个因子"不是简单证伪也不是简单确认"的混合结果,`prompt_note` 明确写出 net_profit_yoy 符号疑点与 bp_proxy 的 regime 依赖风险,提醒下一次接触者不要仅凭这批数字就下"已验证"结论。

## 6. 对抗性审查

1. **把 bp_proxy 的 OOS ICIR=0.99 误读成 alpha**。处理:报告与登记簿均明确标注"IS 弱/OOS 强不对称,疑似 2023-2024 单一价值轮动窗口的运气",action=NOTE 而非 BOOST,不写入任何搜索白名单变更。
2. **把 net_profit_yoy 的负号当作错误直接"修正"或吞掉**。处理:如实记录符号,不擅自判断哪个假设(本 probe vs size_earnings.py 既有正向假设)正确——这正是 R-LLM-001 边界,留 `needs_human`。
3. **把 roe/gross_margin 的『符号反转』百分比当真实留存率解读**。处理:报告正文单独加"诚实边界提醒",标注这两行百分比不是有效量级。
4. **把 gross_margin 直接手工塞进 ALLOWED_FACTORS 图省事**。处理:走 `@register_factor(searchable=False)`,并用对抗测试证明手工塞入会被 C1 守卫拒绝。
5. **留旧 BOOST 条目不动,另开新 DEPRIORITIZE 条目**,导致登记簿自相矛盾(round6 教训:分支孤立已造成一次真实重复浪费)。处理:原地更新 `frontier-fundamental-family`,不留冲突条目。
6. **reports/research/ 被 `.gitignore` 排除,产物磁盘易失**(round5 的 needs_human 点名过)。处理:收尾提交时对本轮全部 4 个 JSON + 2 个 md + trial ledger 显式 `git add -f`。

## 7. 产物

- `factor_research/reports/research/probe_round7_fundamental_roe.json`
- `factor_research/reports/research/probe_round7_fundamental_net_profit_yoy.json`
- `factor_research/reports/research/probe_round7_fundamental_bp_proxy.json`
- `factor_research/reports/research/probe_round7_fundamental_gross_margin.json`
- `factor_research/reports/research/daily_round7_trial_ledger.jsonl`
- `factor_research/tests/test_factor_fundamental_registration.py`
- `factor_research/knowledge/direction_registry.json`(改)
