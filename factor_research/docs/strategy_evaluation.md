# 策略评价框架

> 本文档是"策略/因子如何被打分、如何被判定在册/参考/候选/退役"的唯一说明(ADR-010 home)。
> 规则/门槛数字的权威来源仍是 [CLAUDE.md](../../CLAUDE.md);生命周期各步"谁干"见 [WORKFLOW.md](../../WORKFLOW.md) #1;
> 单条决策的"为什么"见 [DECISIONS.md](../../DECISIONS.md)。本文只整合**评价指标定义 + 判定标准 + 状态机**。

---

## 1. 核心指标定义

来源:`factor_research/engine/metrics.py::metrics()`,所有回测/审计统一用这套定义,**不允许各脚本自定义口径**。

| 指标 | 定义 | 备注 |
|---|---|---|
| `annual` | `ret.mean() * 252` | 日频收益年化(算术,非几何复利) |
| `vol` | `ret.std() * sqrt(252)` | 年化波动率 |
| `sharpe` | `annual / vol` | 无风险利率视为 0 |
| `maxdd` | `(cum / cum.cummax() - 1).min()` | 最大回撤,基于累计净值 `cum = (1+ret).cumprod()` |
| `calmar` | `annual / abs(maxdd)`(`maxdd<0` 时) | 收益回撤比 |
| `hit` | `annual > TARGET_ANNUAL and abs(maxdd) < TARGET_MAXDD` | 是否过单母策略入册门槛 |
| `n` | 样本数 | `n < 100` 时直接判定 `annual=-1, sharpe=-1, hit=False`(样本太短不可信) |

因子层面另有 `IC均值 / Rank IC / IC_IR / 多空收益 / 换手率 / 胜率` (见 `/factors` 页面规格,SPEC.md);
风格中性化的"特质 alpha"判定见 `scripts/research/style_neutralization.py`(CNE6,memory `cne6-style-neutralization`)。

---

## 2. 门槛与状态机

### 2.1 门槛数字(权威来源 CLAUDE.md,此处仅引用)

```text
单母策略入册:  年化 > 15%  &  最大回撤 < 20%   (engine/metrics.py:: TARGET_ANNUAL=0.15, TARGET_MAXDD=0.20)
项目级满意线:  年化 >= 20% & 夏普 >= 1.0
项目级卓越线:  年化 >= 28% 或 卡玛 >= 1.6
(原 35% / 15% 锚定 data_full 水分,已退役,见 ADR-001)
```

### 2.2 台账状态(`strategy_versions.json::families[].versions[].status`)

| 状态 | 含义 | 谁写 |
|---|---|---|
| `候选` | 通过 L0-L3 + 风格审计 + 边际审计,等待登记决策 | `phase4_register`(唯一入口) |
| `在册` | 已登记,过单母策略门槛,纳入组合 book | `phase4_register` |
| `参考` | 历史版本/对比基线,不参与当前组合 | `phase4_register` 或人工备注 |
| `已证伪` | 经审计判定无真实增量(NOISE/TRUE_BUT_SMALL),保留记录不删除 | `phase4_register` |
| `退役` | 曾在册,失效信号触发后退役 | `phase4_register`,标记不删除(ADR-002) |

**判定要点**:
- 进入"在册"**必须**同时满足:① 单母策略门槛(年化>15%/回撤<20%);② 边际审计显示对现有 book 有真增量(Alpha Audit: NW+RidgeCV+置换,非纯风格暴露);③ 已声明失效信号。
- "已证伪"不等于"业绩差"——可能业绩好但被证明是某个已在账的风格因子的代理(如 `small_cap` 对 Barra Size 相关 -0.70,判 `TRUE_BUT_SMALL`)。
- 状态变更只能通过 `strategy_registry.register_family/register`(`workflow/phase4_register`)写入,任何代码不得直写 `strategy_versions.json`(架构铁律)。

---

## 3. 生命周期闸门(摘自 WORKFLOW.md #1)

```
假设 → 候选生成 → 合成审计(防未来) → L0(IC扫描) → L1(快回测,回撤<40%/年化>5%)
  → L2/L3(稳健/成本敏感/样本外三段达标) → 风格审计(CNE6特质增量) → 边际审计(Alpha Audit REAL)
  → 登记(年化>15%&回撤<20%+失效信号) → LIVE(容量≥规模,准入见 §4) → 监控 → 退役(失效信号触发)
```

**铁律**:候选由便宜模型(DS)提议,**去留判断全程确定性代码**,LLM 不参与门槛判定(ADR-007)。

---

## 4. 登记 ≠ LIVE:准入决策前必问(摘自 DECISIONS.md §①)

一个策略/开关过了"登记"门槛(年化>15%&回撤<20%),**不代表自动进 LIVE 组合**。LIVE 准入额外必问:

1. **过拟合 / 幸存者偏差 / 特定行情依赖?** —— 区分"全样本结论"和"局部窗口现象"(见 ADR-011 的回撤分布修正案例)。
2. **真实成本扣了吗?**(往返≈0.47% + 融资6.5%,见 ADR-004,禁乐观值)
3. **实盘可交易吗?**(容量 / 停牌 / 涨跌停 / 投资者门槛——见 ADR-006 科创板排除案例)
4. **判断在代码还是在 LLM?** 必须代码。

**容量与收益的互斥性**(ADR-011 案例):某些"提升收益"的机制(如 regime 门控切到 large-cap)给的是收益而非容量——受宠期资金仍受限于原策略的容量上限。评估时要分别回答"这个改动提升了 alpha 还是只是换了容量曲线"。

---

## 5. 风险偏好开关 vs 默认行为

部分机制(如 ADR-011 regime 门控)经全样本验证"平均更优"，但**默认仍关闭**，因为局部窗口/尾部分布存在不确定性，且 CLAUDE.md 排序"风险可控 > 决策可解释 > 交易自动化"优先于收益最大化。

这类开关的记录方式:
- 代码:可配置项 + 默认值常量 + 单测守门(如 `REGIME_GATED_DEFAULT=False` + `test_all`)
- 文档:`DECISIONS.md` §② 记 ADR(为什么默认关、什么条件可开),§③ 记落地动作

---

## 6. 免责声明

本文档仅描述研究系统内部的评价口径,所有指标基于历史回测/模拟数据,不构成投资建议,不代表未来收益。
