"""GET /governance —— 模型卡与风险治理视图。"""
from __future__ import annotations

from fastapi import APIRouter

from contracts.views import GateVerdictsView, GovernanceView, TrustCalibrationView
from services.read.governance import get_governance_overview
from services.read.trust_calibration import get_trust_calibration
from services.read.validation_gate import get_gate_verdicts

router = APIRouter(prefix="/governance", tags=["governance"])

@router.get("", response_model=GovernanceView)
def governance() -> GovernanceView:
    return get_governance_overview()


@router.get("/gate-verdicts", response_model=GateVerdictsView)
def gate_verdicts() -> GateVerdictsView:
    """验证闸门②:全注册表逐版本 9-Gate 裁决面(权威 verdict + 逐门诊断 + 入册卡点)。"""
    return get_gate_verdicts()


@router.get("/trust-calibration", response_model=TrustCalibrationView)
def trust_calibration() -> TrustCalibrationView:
    """信任校准首屏:StatusBanner 综合裁决 + 逐维度信号 + 逐策略行(over-trust 防护带)。

    banner_status 直接映射 web StatusBanner 的 status prop;仅聚合权威证据,不做新判定。"""
    return get_trust_calibration()
