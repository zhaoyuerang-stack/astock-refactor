"""Validation-Gate Read Service —— 验证闸门(DECISION_COCKPITS 驾驶舱②)。

决策:**这个版本能否独立验证通过 → 入册?在册策略是否仍合规?**
把全注册表逐版本的 9-Gate R2P 裁决呈现为一次验证决策。

诚实护栏:
- **权威裁决 = ``core.analysis.nine_gate_policy.decide_nine_gate``**(只认 passed_all)。
- 逐门诊断复用 ``promotion_readiness._derive_gates``(诊断·非裁决,source_field 可追溯),只定位卡点。
- 未审计(nine_gate=={})如实标「需先跑 9-Gate」,不臆造通过。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import GateVerdict, GateVerdictsView
from services.read.promotion_readiness import _derive_gates

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _register_blocker(verdict: str, ng: dict, gates, reasons) -> str:
    """阻挡入册的唯一根因:权威 reasons 优先,否则取第一道未过门。"""
    if verdict == "PASSED":
        return ""
    if not ng:
        return "未审计:需先跑完整 9-Gate"
    if verdict == "RUN_FAILED":
        return "9-Gate 运行失败"
    if verdict == "PENDING":
        return "9-Gate 未跑完整(passed_all 未定)"
    if "dsr_not_significant" in reasons:
        return "G8 DSR 多重检验不显著"
    if "pbo_high" in reasons:
        return "G7 PBO 过高(过拟合风险)"
    # 优先报"明确失败"的门;无则诚实说扁平字段未暴露具体失败门
    failed = [g for g in gates if g.status == "failed"]
    if failed:
        g = failed[0]
        return f"{g.gate} {g.name}(未过)"
    return "passed_all=False(综合未过;逐门扁平字段未定位具体失败门)"


def get_gate_verdicts() -> GateVerdictsView:
    import strategy_registry
    from core.analysis.nine_gate_policy import decide_nine_gate

    data = strategy_registry._load()
    rows: list[GateVerdict] = []
    for fam in data.get("families", []):
        fid = fam.get("id") or fam.get("family_id")
        for v in fam.get("versions", []):
            ng = v.get("nine_gate") or {}
            decision = decide_nine_gate(ng)
            gates = _derive_gates(ng, v)
            rows.append(GateVerdict(
                family=fid, version=v.get("version", ""), stage=v.get("status", ""),
                verdict=decision.code, verdict_label=decision.label, audited=decision.audited,
                register_blocker=_register_blocker(decision.code, ng, gates, decision.blocking_reasons),
                gate_diag=gates,
                dsr_p=ng.get("dsr_p"), pbo=ng.get("pbo"), n_trials=ng.get("n_trials"),
            ))

    # 排序:已通过 → 待审 → 失败,便于"哪些过了验证、哪些卡住"一眼看清
    order = {"PASSED": 0, "PENDING": 1, "RUN_FAILED": 2, "FAILED": 3}
    rows.sort(key=lambda r: (order.get(r.verdict, 4), r.dsr_p if r.dsr_p is not None else 9.9))

    counts = Counter(r.verdict for r in rows)
    summary = {
        "total": len(rows),
        "audited": sum(1 for r in rows if r.audited),
        "PASSED": counts.get("PASSED", 0),
        "FAILED": counts.get("FAILED", 0),
        "PENDING": counts.get("PENDING", 0),
        "RUN_FAILED": counts.get("RUN_FAILED", 0),
    }
    return GateVerdictsView(
        as_of=datetime.now(CHINA_TZ).date().isoformat(),
        summary=summary,
        verdicts=rows,
        truth_sources={
            "registry": str(ROOT / "strategy_versions.json"),
            "verdict_authority": "core.analysis.nine_gate_policy.decide_nine_gate",
        },
    )
