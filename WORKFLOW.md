# WORKFLOW — 系统端到端流程

> 工作怎么在系统里流动:每个 workflow 标注 **谁干每一步**(代码/DeepSeek/Claude/Codex/Antigravity)+ 闸门 + 交接。
> 与 [SPEC.md](SPEC.md)(静态架构)、[RUNBOOK.md](RUNBOOK.md)(每日操作清单)、[DECISIONS.md](DECISIONS.md)(为什么)互补——这里是**流程**。
> 角色简称:`代码`=确定性代码(判断)/`DS`=DeepSeek(苦力)/`Claude`=架构编排/`AG`=Antigravity(浏览器)/`Cx`=Codex(并行编码)。

---

## 1. 因子研究生命周期(核心闭环)
```
假设 ──▶ 候选生成 ──▶ L0 ──▶ L1 ──▶ L2/L3 ──▶ 9-Gate风险审计 ──▶ 登记 ──▶ LIVE ──▶ 监控 ──▶ 退役
```
| 步 | 谁 | 做什么 | 闸门/产物 |
|---|---|---|---|
| 假设 | Claude/人 | 提研究假设(机制驱动,非数据挖掘) | — |
| 候选生成 | **DS** | `scheduled_factor_search.py` 自动演化 + 反思播种 | 过 DSL 校验 |
| 合成审计 | **代码** | `workflow/phase1_synthetic` 防未来铁律机械执行 | 防未来过 |
| L0 | **代码** | IC 扫描(NW 校正) | ICIR 闸 |
| L1 | **代码** | 快回测 | 回撤<40%/年化>5% |
| L2/L3 | **代码** | 稳健/成本敏感/样本外滚动 | 三段达标 |
| **9-Gate风险审计** | **代码** | `nine_gates.py` 自动化 9 关风险审计 | 参见 1.1 风险审计明细 |
| 风格审计 | **代码** | `style_neutralization.py` 对 CNE6 测特质增量 | 有特质 alpha (非纯风格) |
| 边际审计 | **代码** | Alpha Audit (NW+RidgeCV+置换) 对在册 book 增量 | 真增量 REAL |
| 登记 | **代码** | `phase4_register` 写台账(唯一入口) | 年化>15% & 回撤<20%+失效信号 |
| LIVE | Claude/人 | 准入(容量与规模配比、跨资产分散) | 见 DECISIONS playbook |
| 退役 | **代码** | 失效信号触发 → 自动打标退役(不删) | — |

**铁律**: 候选 DS 提议，**去留判断全程代码** (LLM 不判)。

### 1.1 9-Gate 风险审计明细 (nine_gates.py)
新因子在进入 Review 队列前必须接受全流程审计：
*   **Gate 0: Data Audit** (空值率、溢出值监测与数值摄动未来函数测试)
*   **Gate 1: Economic Hypothesis** (机制长度及学术引文检验，拒绝对数掘纯拟合)
*   **Gate 2: Single Factor Verification** (Rank IC 均值、NW-ICIR、五分位单调性及 IC 衰减曲线)
*   **Gate 3: Neutralization Verification** (横截面 OLS 剥离 Size 与 Industry 后的残差 Alpha 留存率)
*   **Gate 4: Multiple Testing Penalty** (经过多期试错惩罚的 Deflated Sharpe Ratio / DSR p-val 检验)
*   **Gate 5: Portfolio Backtesting** (裸因子多头年化回报与最大回撤硬约束)
*   **Gate 6: Cost & Capacity Modeling** (波动率平方根模型 + 5日拆单自适应优化器，平衡冲击成本与延迟Alpha衰减)
*   **Gate 7: Out-of-Sample & Stress Testing** (OOS 样本外滚动夏普、Bull/Bear 牛熊市依赖度及极端 Regime 压力测试)
*   **Gate 8: Live Monitoring** (设定实盘每日收益/波动均值期望、风控跟踪误差及最大硬止损触发线)

---

## 2. 数据维度接入与自动搜寻
```
探通 ──▶ registry 声明 ──▶ 维度扫描与 L0 审计 ──▶ 自动注册 ──▶ 适应度扫描 ──▶ 反思播种
```
| 步 | 谁 | 做什么 | 闸门 |
|---|---|---|---|
| 探通 | Claude | 验证接口字段/物理口径(by_date/anndate) | — |
| 声明 | Claude | `schema.py` 写入 `TUSHARE_DATASETS` 声明 | — |
| **维度扫描与审计** | **代码** | `dimension_explorer.py` 自动扫描新列并执行 L0 审计 | 缺失率 <50% & 无未来穿越 |
| **自动注册** | **代码** | 自动注册进 `registry.py::ALLOWED_FACTORS` 字典 | — |
| **适应度扫描** | **代码** | 运行 Rank IC 快扫计算各新维度对 $T+20$ 的预测力 | 计算 IC/ICIR 作为适应度得分 |
| **反思播种** | **DS** | 将高分新维度反馈给 LLM 生成端进行特征交叉组合 | — |

---

## 3. 每日运行(自动 + 人工边界)
```
增量更新 ──▶ 质量校验 ──▶ 择时+持仓+调仓判断 ──▶ 自动模拟盘(T+1)──▶ 监控 ──▶ 人工解读
```
*   **全自动**：`run_daily.py` (数据→信号→`signals/<date>.json`) + `paper_trade.py` 模拟盘。
*   **红黄灯监视器**：`health_check.py` 假崩盘哨兵，`decay_monitor.py` 因子衰减监控。
*   **人工物理防线**：模拟盘与实盘边界隔离，下单确认恒为人工。
*   操作步骤详见 [RUNBOOK.md](RUNBOOK.md)。

---

## 4. 多 Agent 协作(build/acquisition-time → run-time)
```
[开发/获取期 · 订阅 Agent]               [常驻运行期 · API + 代码]
AG 浏览器抓数 ──落 data_lake/──┐
                              ├──▶ DS+代码 7×24 消费 ──▶ 信号/因子
Cx worktree 写模块 ──commit──┘
Claude 设计管线+判断代码+契约+协调 ─────────────────────▶ 全程
```
**铁律**：常驻运行系统只依赖 DS+代码，**禁止依赖订阅 Agent 实时在线**。

---

## 5. 个股因子与风控诊断 (stock_cli.py)
```
输入股票代码 ──▶ 双因子有效性回溯 ──▶ 截面分位数计算 ──▶ Salience Veto 风控判决 ──▶ 历史明细输出
```
*   **回溯机制**：若最新日期个股停牌或无交易数据，自动回溯至历史上该股 **Amihud 因子与 Veto 分数同时有效**的最晚交易日。
*   **因子诊断**：
    *   **Amihud Illiquidity**：计算 20 日滚动非流动性并输出全市场截面百分比。
    *   **Size Factor**：计算 60 日滚动成交额规模及全市场分位数。
*   **Veto 判定**：调用 [veto.py](file:///Users/kiki/astcok/factor_research/factors/veto.py)，计算该股的 faded Salience Covariance 分数，若截面百分比 $\le 30.0\%$ 则判定为 `❌ VETOED` (高气泡风险/过度博弈)，实盘调仓将强制剔除。
*   **产出**：生成 20 日历史明细表，辅助判断持仓合理性与冲击成本。

