"""Trust-Calibration Read Service —— 信任校准首屏(DECISION_COCKPITS dashboard 首屏)。

决策:**用户在看 KPI(年化/夏普)前,当前策略池有多可信 / 哪里最可能是假 alpha 或已在失效?**
这是 over-trust 防护带 —— 业界 2026 共识:AI 系统成败已从「够不够聪明」转到「用户能不能
校准信任」,对量化尤致命的是 over-trust(把过拟合回测当真 alpha)。本视图把仓库里既有的
防自欺证据(DSR、9-Gate 裁决、holdout、regime)聚合成首屏一眼可读的信任裁决。

诚实护栏(与本仓防自欺内核一致):
- **不重算判定**:逐版本裁决复用 ``validation_gate.get_gate_verdicts``(权威 = decide_nine_gate)。
- **banner 只聚合不裁绿**:永不比其权威输入更绿(fail-closed);池中无一 PASSED 则禁绿。
- **holdout 只陈述事实**:边界/genesis 原样展示,完整性判定归 ``check_holdout_compliance``,不自判。
- **论点 ≠ 实时**:``decay_signal`` / ``failure_boundaries`` 是 §7.1 论点字段(该盯什么),
  实时衰减权威 = ``reports/decay_status.json``;缺失则如实标「未监控」,绝不用论点字段冒充实时。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import TrustCalibrationView, TrustSignal, TrustStrategyRow
from services.read.validation_gate import get_gate_verdicts

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")

HOLDOUT_HISTORY = ROOT / "app_config" / "holdout_boundary_history.jsonl"
DECAY_STATUS = ROOT / "reports" / "decay_status.json"

_REGISTERED = {"在册", "ACTIVE", "active"}
_ACTIVE_STAGES = _REGISTERED | {"候选", "参考", "CANDIDATE", "REGISTERED_REFERENCE", "candidate", "reference"}


def _load_registry_index() -> tuple[dict, dict]:
    """family 论点字段 + (family,version)→nine_gate,仅供描述性展示(不裁决)。"""
    import strategy_registry

    data = strategy_registry._load()
    fam_meta: dict[str, dict] = {}
    ng_index: dict[tuple[str, str], dict] = {}
    for fam in data.get("families", []):
        fid = fam.get("id") or fam.get("family_id") or ""
        fam_meta[fid] = {
            "decay_signal": fam.get("decay_signal"),
            "failure_boundaries": fam.get("failure_boundaries"),
        }
        for v in fam.get("versions", []):
            ng_index[(fid, v.get("version", ""))] = v.get("nine_gate") or {}
    return fam_meta, ng_index


def _row_note(verdict: str, audited: bool, active: bool, dsr_sig, blocker: str) -> str:
    if verdict == "PASSED" and dsr_sig:
        return "已独立验证通过(DSR 显著),可作已验证 alpha 参考"
    if verdict == "PASSED" and dsr_sig is False:
        return "通过但 DSR 不显著 = 过拟合风险,勿高估"
    if verdict in ("FAILED", "RUN_FAILED"):
        return f"9-Gate 未通过({blocker or '见验证闸门'}),不可当已验证 alpha"
    if active and not audited:
        return "在册/候选却未跑完整 9-Gate,勿据此实战(证据缺失)"
    return "待完整审计,信任度未定"


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "; ".join(str(x) for x in value)
    return str(value)


def _holdout_signal() -> TrustSignal:
    """只陈述金库边界事实;完整性判定归 check_holdout_compliance,本视图不自判。"""
    boundary = genesis = ""
    if HOLDOUT_HISTORY.exists():
        for line in HOLDOUT_HISTORY.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            boundary = rec.get("boundary", boundary)
            if rec.get("kind") == "genesis":
                genesis = rec.get("boundary", "")
    evidence = (
        f"金库边界={boundary or '未记录'}(genesis={genesis or '未记录'});"
        "完整性(只进不退+hash 锁)由 check_holdout_compliance 裁决,本视图不自判。"
    )
    return TrustSignal(
        key="holdout", label="Holdout 金库", status="info", evidence=evidence,
        authority="scripts/ci/check_holdout_compliance.py (ADR-021/023)",
    )


def _decay_signal() -> tuple[TrustSignal, bool]:
    """实时衰减权威 = reports/decay_status.json;缺失则如实标未监控(记为信任缺口)。"""
    if not DECAY_STATUS.exists():
        return (
            TrustSignal(
                key="decay_watch", label="实时衰减监控", status="attention",
                evidence="reports/decay_status.json 不存在:在册策略实时衰减未监控(信任缺口)。",
                authority="reports/decay_status.json",
            ),
            True,
        )
    try:
        data = json.loads(DECAY_STATUS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    flagged = bool(data.get("decaying")) if isinstance(data, dict) else False
    return (
        TrustSignal(
            key="decay_watch", label="实时衰减监控",
            status="attention" if flagged else "ok",
            evidence=("检测到衰减告警" if flagged else "无实时衰减告警"),
            authority="reports/decay_status.json",
        ),
        flagged,
    )


def get_trust_calibration() -> TrustCalibrationView:
    gv = get_gate_verdicts()                    # 权威逐版本裁决(复用,不重算)
    fam_meta, ng_index = _load_registry_index()

    rows: list[TrustStrategyRow] = []
    any_registered_failed = False
    any_claimed_unaudited = False
    any_audited_not_sig = False
    any_passed = False
    audited_active = 0
    total_active = 0

    for gr in gv.verdicts:
        ng = ng_index.get((gr.family, gr.version), {})
        meta = fam_meta.get(gr.family, {})
        stage = gr.stage
        active = stage in _ACTIVE_STAGES
        dsr_sig = ng.get("dsr_significant")
        if active:
            total_active += 1
            if gr.audited:
                audited_active += 1
        if gr.verdict == "PASSED":
            any_passed = True
        if gr.verdict in ("FAILED", "RUN_FAILED") and stage in _REGISTERED:
            any_registered_failed = True
        if active and not gr.audited:
            any_claimed_unaudited = True
        if gr.audited and gr.verdict == "PASSED" and dsr_sig is False:
            any_audited_not_sig = True
        rows.append(TrustStrategyRow(
            family=gr.family, version=gr.version, stage=stage,
            verdict=gr.verdict, verdict_label=gr.verdict_label, audited=gr.audited,
            dsr_p=gr.dsr_p, dsr_significant=dsr_sig,
            bull_sharpe=ng.get("bull_sharpe"), bear_sharpe=ng.get("bear_sharpe"),
            wf_sharpe=ng.get("wf_sharpe"),
            decay_thesis=_stringify(meta.get("decay_signal")),
            failure_thesis=_stringify(meta.get("failure_boundaries")),
            trust_note=_row_note(gr.verdict, gr.audited, active, dsr_sig, gr.register_blocker),
        ))

    # ── 逐维度信任信号 ─────────────────────────────────────────────
    audited_rows = [r for r in rows if r.audited]
    not_sig = [r for r in audited_rows if r.dsr_significant is False]
    if not audited_rows:
        overfit = TrustSignal(
            key="overfit_guard", label="过拟合防护(DSR)", status="attention",
            evidence="池中无任一完整审计版本,DSR 多重检验惩罚尚未生效(勿据未审计结果实战)。",
            authority="core.analysis.nine_gate_policy.decide_nine_gate / §6 G8",
        )
    elif not_sig:
        overfit = TrustSignal(
            key="overfit_guard", label="过拟合防护(DSR)", status="attention",
            evidence=f"{len(not_sig)}/{len(audited_rows)} 个已审计版本 DSR 不显著(过拟合风险)。",
            authority="core.analysis.nine_gate_policy.decide_nine_gate / §6 G8",
        )
    else:
        overfit = TrustSignal(
            key="overfit_guard", label="过拟合防护(DSR)", status="ok",
            evidence=f"{len(audited_rows)} 个已审计版本 DSR 均显著。",
            authority="core.analysis.nine_gate_policy.decide_nine_gate / §6 G8",
        )

    oos = TrustSignal(
        key="oos_regime", label="样本外/regime 稳健性", status="info",
        evidence="以自标字段 wf_sharpe / bull_sharpe / bear_sharpe 逐行呈现(非由 metrics 臆测 IS/OOS 落差)。",
        authority="nine_gate 自标字段(walk-forward / regime 拆分)",
    )

    coverage = TrustSignal(
        key="audit_coverage", label="审计覆盖",
        status="attention" if any_claimed_unaudited else "info",
        evidence=f"在册/候选/参考中 {audited_active}/{total_active} 已跑完整 9-Gate"
                 + ("(有已声明但未审计者)。" if any_claimed_unaudited else "。"),
        authority="strategy_versions.json::nine_gate 存在性",
    )

    holdout = _holdout_signal()
    decay, decay_unmonitored = _decay_signal()
    signals = [overfit, oos, coverage, holdout, decay]

    # ── 首屏裁决(qualitative + fail-closed,永不比权威输入更绿) ──────────
    if not rows:
        banner, headline, detail = "neutral", "策略池为空,无可信度可评。", "先产出候选并跑 9-Gate。"
    elif any_registered_failed:
        banner = "blocked"
        headline = "在册策略存在 9-Gate 未通过项:勿据此实战。"
        detail = "权威裁决 decide_nine_gate 判定在册版本 FAILED,须先处置(退役/重审)。"
    elif audited_active == 0:
        banner = "attention" if any_claimed_unaudited else "neutral"
        headline = "池中无完整审计版本:当前无已验证 alpha 依据。"
        detail = "在册/候选均未跑完整 9-Gate,任何 KPI 都不足以支撑信任。"
    elif any_claimed_unaudited or any_audited_not_sig or decay_unmonitored:
        banner = "attention"
        gaps = []
        if any_claimed_unaudited:
            gaps.append("有已声明但未审计版本")
        if any_audited_not_sig:
            gaps.append("有通过但 DSR 不显著版本")
        if decay_unmonitored:
            gaps.append("实时衰减未监控")
        headline = "策略池部分可信,但存在 over-trust 缺口:" + "、".join(gaps) + "。"
        detail = "先补齐缺口再据以配置;详见逐维度信号与逐策略行。"
    elif any_passed:
        banner = "ready"
        headline = "在册/候选均已完整审计且 DSR 显著,可作已验证 alpha 参考。"
        detail = "仍以 failure_boundaries / decay 为监控前提,信任非永久。"
    else:
        banner = "attention"
        headline = "信任依据不足以判绿,按需补审计。"
        detail = "无 FAILED 但亦无 PASSED 版本,fail-closed 保守呈现。"

    # 排序:失败/未审计优先置顶(最需用户注意的 over-trust 风险在前)
    order = {"FAILED": 0, "RUN_FAILED": 1, "PENDING": 2, "PASSED": 3}
    rows.sort(key=lambda r: (order.get(r.verdict, 4), 0 if not r.audited else 1))

    return TrustCalibrationView(
        as_of=datetime.now(CHINA_TZ).date().isoformat(),
        banner_status=banner,
        headline=headline,
        detail=detail,
        signals=signals,
        strategies=rows,
        truth_sources={
            "verdict_authority": "core.analysis.nine_gate_policy.decide_nine_gate",
            "verdicts_view": "services.read.validation_gate.get_gate_verdicts",
            "registry": str(ROOT / "strategy_versions.json"),
            "holdout_history": str(HOLDOUT_HISTORY),
            "holdout_authority": "scripts/ci/check_holdout_compliance.py",
            "decay_live": str(DECAY_STATUS),
        },
        honesty="本视图是 over-trust 防护带的『聚合呈现』,不做任何新判定:逐版本裁决复用权威 "
                "decide_nine_gate;banner 永不比其权威输入更绿(fail-closed);holdout 完整性归 "
                "check_holdout_compliance;decay_signal/failure_boundaries 为 §7.1 论点字段(非实时)。",
    )
