"""单一 Nine-Gate 裁决策略(Task 9)。

消除「DSR 通过 = Nine-Gate 通过」的错误降维:审批只认 `passed_all is True`
(nine_gates 评估器已把全部门——含 PBO/Gate7 极端 regime——折进该标志),
对缺失 / 运行失败一律 fail-closed。registry、production readiness、trade readiness、
governance 视图全部调用本策略,不再各自用 DSR-only 推断。

说明:registry 存的是**扁平** nine_gate 摘要(passed_all/dsr_p/dsr_significant/pbo/...),
而非 per-gate 富结构。本策略据真实数据形状裁决:approval ⇔ passed_all is True。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NineGateDecision:
    code: str               # PASSED | FAILED | PENDING | RUN_FAILED
    approved: bool          # 仅 PASSED 为 True —— 唯一可授予治理批准的状态
    audited: bool           # 是否真正跑过审计(PENDING/RUN_FAILED 为 False)
    blocking_reasons: tuple

    # 中文展示映射(UI 用;业务判断只看 code/approved,禁止比较 label)
    _LABELS = {
        "PASSED": ("审计通过", True),
        "FAILED": ("审计未通过", False),
        "PENDING": ("待完整审计", None),
        "RUN_FAILED": ("审计失败", False),
    }

    @property
    def label(self) -> str:
        return self._LABELS[self.code][0]

    def as_state(self) -> dict:
        """兼容既有治理/可用性消费者的四态结构 {code,label,audited,passed}。"""
        label, passed = self._LABELS[self.code]
        return {"code": self.code, "label": label, "audited": self.audited, "passed": passed,
                "blocking_reasons": list(self.blocking_reasons)}


def decide_nine_gate(summary: dict) -> NineGateDecision:
    """唯一裁决入口。summary = registry 存的扁平 nine_gate 摘要(或 None)。

    规则(fail-closed):
      · status == FAILED_TO_RUN          → RUN_FAILED(blocked)
      · passed_all 缺失/非布尔            → PENDING(未跑完整门,不得用 DSR-only 推断通过)
      · passed_all is not True            → FAILED(blocked;附 PBO/DSR 失败线索)
      · passed_all is True                → PASSED(approved)
    """
    s = summary or {}
    if s.get("status") == "FAILED_TO_RUN":
        return NineGateDecision("RUN_FAILED", False, False, ("nine_gate_run_failed",))

    passed_all = s.get("passed_all")
    if not isinstance(passed_all, bool):
        return NineGateDecision("PENDING", False, False, ("nine_gate_incomplete",))

    if passed_all is not True:
        reasons = []
        if s.get("dsr_significant") is False:
            reasons.append("dsr_not_significant")
        pbo = s.get("pbo")
        if isinstance(pbo, (int, float)) and pbo > 0.5:
            reasons.append("pbo_high")
        reasons.append("passed_all_false")
        return NineGateDecision("FAILED", False, True, tuple(reasons))

    return NineGateDecision("PASSED", True, True, ())
