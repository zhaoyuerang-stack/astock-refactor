# 信任校准首屏 · StatusBanner 规格草案 (DRAFT)

> **状态**:草案(draft),供评审。**不覆盖** canonical `WEB_DESIGN.md` / `DECISION_COCKPITS.md`;
> 评审通过后再并入对应页面职责章节。
> **归属页面**:DECISION_COCKPITS 的 **dashboard 首屏**(用户进入系统的第一屏)。按 CLAUDE.md 文档地图,
> `DECISION_COCKPITS.md` 约束 `WEB_DESIGN.md` 的页面职责,故本组件的最终落位以 dashboard 决策职责为准。

## 1. 决策与动机

**这一屏回答**:用户在看年化/夏普等 KPI **之前**,当前策略池「有多可信 / 哪里最可能是假 alpha 或已在失效」。

业界 2026 共识(见调研):AI 系统成败的瓶颈已从「模型够不够聪明」转到「用户能不能**校准信任**」;
对量化系统最致命的失败模式是 **over-trust**——把一个过拟合/幸存者偏差的漂亮回测当成真 alpha。
本仓整部宪法(防未来、DSR 惩罚、9-Gate、holdout 金库)就是在防这件事,但这些防自欺证据此前**只在后端**;
本组件把它们**前置到首屏**,让"该不该信"先于"赚多少"出现——这正是 UX 层承接防自欺内核的杠杆点。

## 2. 数据来源(唯一)

- **API**:`GET /governance/trust-calibration` → `TrustCalibrationView`(`contracts/views.py`)。
- **服务**:`services.read.trust_calibration.get_trust_calibration()`。
- **定位**:over-trust 防护带的**聚合呈现**,**不做任何新判定**——逐版本裁决复用权威
  `validation_gate.get_gate_verdicts`(权威 = `core.analysis.nine_gate_policy.decide_nine_gate`)。

## 3. StatusBanner 映射(复用 `web/components/ui/StatusBanner.tsx`)

`TrustCalibrationView` 字段**直接**对上 StatusBanner 的既有 props(无需新组件):

| View 字段          | StatusBanner prop | 说明 |
| ---------------- | ----------------- | ---- |
| `banner_status`  | `status`          | 值域一致:`ready` / `attention` / `blocked` / `neutral` |
| `headline`       | `title`           | 一句话信任裁决 |
| `detail`         | `detail`          | 一句话支撑 |

色调沿用 StatusBanner 既有 TONE:`ready`=ok 绿 / `attention`=warn 黄 / `blocked`=danger 红 / `neutral`=line 灰。

**裁绿铁律(fail-closed,永不比权威输入更绿)**——在服务端已固化,前端**不得**在展示层重算或上调:

- 在册版本存在权威 `FAILED` → `blocked`;
- 有「已声明未审计」/「通过但 DSR 不显著」/「实时衰减未监控」任一 → 最多 `attention`;
- 仅当在册/候选**全部**已完整审计且 DSR 显著且存在 `PASSED` → `ready`;
- 池空或无一审计 → `neutral`(诚实的"暂无信任依据",禁绿)。

## 4. 渐进式披露(三层)

首屏只出 StatusBanner(第 1 层);其余按需展开,避免一次铺满。

1. **第 1 层 · 综合裁决**:StatusBanner(`banner_status` + `headline` + `detail`)。
2. **第 2 层 · 逐维度信号**(`signals[]` → `TrustSignal`):5 条,每条 `label` + `status` 徽标 + `evidence` + `authority`。
   - `overfit_guard` 过拟合防护(DSR)、`oos_regime` 样本外/regime 稳健性、`audit_coverage` 审计覆盖、
     `holdout` Holdout 金库、`decay_watch` 实时衰减监控。
   - `status=info` 的信号(如 holdout)只陈述事实、**不参与裁绿**,UI 用中性徽标。
3. **第 3 层 · 逐策略行**(`strategies[]` → `TrustStrategyRow`):默认**风险优先置顶**(FAILED/未审计在前)。
   列建议:`stage` / `family` / `version` / `verdict_label` / `audited` / `dsr_p` / `dsr_significant` /
   `bull_sharpe` / `bear_sharpe` / `wf_sharpe` / `trust_note`。点行可深链到验证闸门②(`/governance/gate-verdicts`)对应版本。

## 5. 字段出处(每个信任信号 = 复用既有字段,不新造口径)

| 信任维度 | 复用字段 | 出处 / 权威 |
| ------ | ------ | --------- |
| 过拟合(DSR) | `nine_gate.dsr_p` / `dsr_significant` | `decide_nine_gate`(§6 G8) |
| 权威裁决 | `verdict` / `audited` | `validation_gate.get_gate_verdicts` |
| regime 稳健性 | `nine_gate.wf_sharpe` / `bull_sharpe` / `bear_sharpe` | nine_gate 自标字段(非由 `metrics` 臆测 IS/OOS 落差) |
| Holdout | `app_config/holdout_boundary_history.jsonl`(边界/genesis) | 完整性判定归 `check_holdout_compliance`(ADR-021/023),**本视图只陈述事实** |
| 实时衰减 | `reports/decay_status.json` | 缺失则如实标「未监控」,**绝不**用 §7.1 论点字段冒充实时 |
| 失效论点 | `family.decay_signal` / `failure_boundaries` | §7.1 **论点字段**(该盯什么),标注为"论点·非实时" |

## 6. 诚实/防 over-trust 展示铁律

- 展示层**不得**把 `status=info`(如 holdout 事实)渲染成"通过/健康"。
- `decay_signal` / `failure_boundaries` 必须带"论点·非实时"标注,不得渲染成"当前正在衰减/已破位"。
- StatusBanner **不得**比 `banner_status` 更绿;前端不重算裁决。
- 未审计(`audited=false`)行不得因 KPI 好看而弱化提示;`trust_note` 原样呈现。

## 7. 验收

- 后端:`tests/test_trust_calibration_view.py`(裁绿 fail-closed、holdout 不自判、decay 诚实未监控、复用权威裁决)。
- 前端(评审并入后补):StatusBanner 快照对 `banner_status` 的四态渲染;逐维度信号 `info` 徽标中性;
  逐策略行风险优先排序。

## 8. 现状快照(2026-07-01 真实数据,示意)

`banner_status = attention` — “策略池部分可信,但存在 over-trust 缺口:有已声明但未审计版本、实时衰减未监控。”
其中 `overfit_guard = attention`:**18/24 个已审计版本 DSR 不显著**(过拟合风险),`audit_coverage = 18/41`,
`holdout` 金库边界 2025-01-01(事实),`decay_watch` 未监控。这与 STATUS 既有结论"现池不能直接实战"一致——
即首屏诚实地把 over-trust 风险摆在了用户看 KPI 之前。
