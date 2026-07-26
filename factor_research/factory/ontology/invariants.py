"""Factory invariants F-1..F-10.

Phase 1 只实现 F-1（经济论证）、F-2（Cheap-First 顺序）。
后续 Phase 补完。
"""
from .experiment import ExperimentProtocol
from .hypothesis import Hypothesis, HypothesisStatus


class InvariantViolation(Exception):
    """F-i 不变量违反 — 拒绝继续。"""


# F-1: 经济论证必填
def check_f1_economic_thesis(hyp: Hypothesis) -> None:
    if hyp.thesis is None or not hyp.thesis.is_valid():
        raise InvariantViolation(
            f"F-1 violated: Hypothesis '{hyp.name}' missing EconomicThesis.mechanism"
        )


# F-2: Cheap-First 顺序
_PROTOCOL_REQUIRES: dict[ExperimentProtocol, HypothesisStatus] = {
    ExperimentProtocol.L0_IC_SCAN: HypothesisStatus.QUEUED,
    ExperimentProtocol.L1_QUICK_BT: HypothesisStatus.L0_PASSED,
    ExperimentProtocol.L2_MULTI_REGIME: HypothesisStatus.L1_PASSED,
    ExperimentProtocol.L3_WALK_FORWARD: HypothesisStatus.L2_PASSED,
}


def check_f2_cheap_first(
    current_status: HypothesisStatus,
    target_protocol: ExperimentProtocol,
) -> None:
    expected = _PROTOCOL_REQUIRES[target_protocol]
    if current_status != expected:
        raise InvariantViolation(
            f"F-2 violated: {target_protocol.value} requires status={expected.value}, "
            f"got {current_status.value}"
        )
