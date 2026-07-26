# daily-round-8:基本面原始比率族 probe 补完(承接 round7 明确留白)

> 角色边界:本轮只做假设设计与确定性 L0 取证,不判断 alpha 是否有效,不入册、不晋级、不部署。
> 承接关系:本文档是 `fundamental_raw_ratio_probe_round7_findings.md`(commit `1da3c323`)的直接续作,不是独立新方向——round7 明确留白 "revenue_yoy/ep_proxy 本轮未测,移出 scope_factors,状态诚实留白",本轮把这两个因子补测完,使 `frontier-fundamental-family` 全部 6 个成员(roe / net_profit_yoy / bp_proxy / gross_margin / revenue_yoy / ep_proxy)取证完整。

## 0. 一次真实的并发写入(需 owner 关注,详见文末 needs_human)

本轮开始时,`.claude/worktrees/daily-research-round` 这个固定工作区里已经有一个**同名但独立**的 round-7 会话正在运行(`Claude-Session: daily-round-7`,与本会话初始误判的"中断残留"不同,实际是同一时段内两个并发实例)。它已经完成了 gross_margin 注册、roe/net_profit_yoy/bp_proxy/gross_margin 四个因子的 probe、direction_registry.json 首版回写,并在本轮取证过程中(约 08:00-08:02)提交了 3 个 commit(`bc90967a`/`1da3c323`/`50ef8b07`)到 `claude/daily-round-7` 分支。

本会话独立复跑 `gross_margin` probe 的数值与它逐位一致(IS IC=-0.0021、OOS IC=-0.0151),确认两次运行同源同因子无口径分歧。本轮据此不重复 gross_margin/roe/net_profit_yoy/bp_proxy 的工作,只补齐它明确留白的 revenue_yoy/ep_proxy,并把三对(成长/价值/质量)的证据合并进 `direction_registry.json` 统一改写,避免登记簿出现"revenue_yoy/ep_proxy 状态未知"这句过期陈述。

## 1. revenue_yoy 独立 probe(成长对第②员)

```
python scripts/research/signal_source_probe.py --factor factors.fundamental:revenue_yoy \
  --universe all --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31
```

| | IS(60月) | OOS(23月) | full(83月) |
|---|---|---|---|
| 原始 IC | -0.0086 (ICIR 0.11) | **-0.0315** (ICIR 0.44) | -0.0150 |
| 残差 IC(去 size/流动性) | -0.0030 (ICIR 0.04) | -0.0289 (ICIR 0.48) | -0.0102 |

正交保留率(残差/原始)= 68%;风格相关 size=0.064 / liquidity=0.045 / momentum=-0.038,均低,判定真正交(非风格伪装)。

**与 net_profit_yoy(round7 已测)同符号、同形态**:原始与残差 IC 全程稳定为负、OOS 比 IS 更负、低风格相关。两个独立的成长口径(净利润同比 / 营收同比)方向完全一致,构成一个内部自洽的"成长反转"读数,而不是孤立噪声——这加强了 round7 提出的疑点:该负向读数与 `factors/fundamental.py` 文档标注的"net_profit_yoy — size_earnings v1.0 LIVE 实证基本面动量"(隐含正向贡献假设)方向相反,需要人核对两者口径/组合权重是否指向同一现象。

## 2. ep_proxy 独立 probe(价值对第②员)

```
python scripts/research/signal_source_probe.py --factor factors.fundamental:ep_proxy \
  --universe all --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31
```

| | IS(60月) | OOS(23月) | full(83月) |
|---|---|---|---|
| 原始 IC | 0.0270 (ICIR 0.31) | 0.0358 (ICIR 0.22) | 0.0294 |
| 残差 IC(去 size/流动性) | 0.0084 (ICIR **0.10**) | 0.0273 (ICIR **0.20**) | 0.0137 |

正交保留率 = 47%;风格相关 size=0.16 / **liquidity=-0.242** / momentum=-0.088。

**这是本轮最关键的判别检验**:round7 对 bp_proxy 的判读是"IS 残差极弱(ICIR 0.08)、OOS 残差骤强(ICIR 0.59)的不对称,疑似 2023-2024 价值风格窗口特有的运气,而非结构性正交 alpha"。ep_proxy 作为**独立的第二个价值口径**(1/PE vs bp_proxy 的 1/PB),在同一 OOS 窗口上重复了几乎相同的形态——IS 残差弱(ICIR 0.10)、OOS 残差明显更强(ICIR 0.20),且同样对 liquidity 呈负相关(-0.242,方向与 bp_proxy 的 -0.339 一致)。

两个数学上不同构的价值代理(市净率倒数 / 市盈率倒数)在同一 23 个月窗口上**同步**由弱转强、且同步与流动性负相关,这种协同性本身就是证据:更符合"一段 A 股价值风格占优 regime 的共同贝塔"读数,而不是两个因子各自独立地在样本外变成了结构性 alpha。round7 的猜测在本轮被加强,不是被证伪。

## 3. 三对因子的完整读数(收尾)

| 组 | 成员 | 原始 IC(IS→OOS) | 残差 IC(IS→OOS) | 判读 |
|---|---|---|---|---|
| 成长对 | net_profit_yoy | -0.0115→-0.0182 | -0.0085→-0.0154 | 稳定负向,真正交,方向与文档假设矛盾(needs_human) |
| 成长对 | revenue_yoy | -0.0086→-0.0315 | -0.0030→-0.0289 | 同上,两员互证 |
| 价值对 | bp_proxy | 0.0501→0.0928 | 0.0136→0.0592 | IS弱/OOS强不对称,疑似 regime 贝塔 |
| 价值对 | ep_proxy | 0.0270→0.0358 | 0.0084→0.0273 | 同一不对称形态,两员互证 |
| 质量对 | roe | -0.0053→-0.0190 | +0.0116→-0.0133 | 符号反转,不泛化,falsified |
| 质量对 | gross_margin | -0.0021→-0.0151 | +0.0031→-0.0138 | 量级/符号均在噪声区,真空 |

三组各自内部一致(每组两个独立口径互相印证同一现象),说明这不是随机噪声,而是三个真实存在但性质不同的现象:质量对是真空,成长对是稳定但方向存疑的负向信号,价值对是疑似 regime 依赖而非结构性的信号。这正是 `direction_registry.json::fundamental-raw-ratio-mixed-signals` 条目存在的意义——三者都不该被简单地记成"BOOST"或"falsified"。

## 4. direction_registry.json 改动

- `fundamental-raw-ratio-mixed-signals`:scope_factors 从 `[net_profit_yoy, bp_proxy]` 扩为 `[net_profit_yoy, revenue_yoy, bp_proxy, ep_proxy]`,evidence 补 revenue_yoy/ep_proxy 两条,prompt_note 按本文 §3 的三组框架重写。
- `frontier-fundamental-family`:prompt_note 里"revenue_yoy/ep_proxy 本轮未测,状态仍为未知"这句过期陈述被替换为准确指向 `fundamental-raw-ratio-mixed-signals` 条目的引用;direction 文案改为明确"质量对(roe+gross_margin)真空"。scope_factors 不变(仍只 `[roe]`,gross_margin 不进白名单证据门)。

## 5. 边界声明

以上全部是 L0 证据(正交性 + IS/OOS 线性 rank-IC),不扣成本、无 DSR/PBO/容量/9-Gate,不构成"alpha 已验证"的结论。net_profit_yoy/bp_proxy/revenue_yoy/ep_proxy 本就在 `ALLOWED_FACTORS`(legacy 手工接入),本轮不改变其可搜索状态,只是补齐 standalone 体检证据、写入 direction_registry.json 供后续生成器 steering 参考。gross_margin 仍 `searchable=False`,不进白名单。roe 维持 falsified/DEPRIORITIZE。
