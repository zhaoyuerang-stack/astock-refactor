"""System Truth Read Service.

把「声明的部署 / 已验证的部署 / 是否允许生产」三态收敛为单一只读视图,
供前端/CLI/agent 从同一入口读「当前能不能交易、为什么」。

本服务**不重算任何判定**:``readiness`` 内嵌 ``runtime.production_readiness``
既有唯一权威闸门;部署 fail-closed 仍由 ``runtime.deployment.load_active_deployment``
裁决。这里只做「声明 vs 已验证」并排 + 逐腿证据链组装。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import DeclaredLeg, LegEvidence, SystemTruthView
from runtime.deployment import (
    DEFAULT_MANIFEST,
    DeploymentNotReady,
    diagnose_leg,
    load_active_deployment,
    read_declared_manifest,
)
from runtime.production_readiness import get_production_readiness

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def get_system_truth() -> SystemTruthView:
    """组装系统真相层视图。declared ≠ verified ≠ production_allowed。"""
    declared = read_declared_manifest()
    declared_present = declared is not None
    declared_legs_raw = (declared or {}).get("legs", [])
    declared_legs = [DeclaredLeg(**leg) for leg in declared_legs_raw]

    # verified —— 通过 fail-closed 校验后真正可激活的身份
    verified = False
    verified_deployment_id = ""
    verified_legs: list[DeclaredLeg] = []
    verify_error = ""
    try:
        dep = load_active_deployment()
        verified = True
        verified_deployment_id = dep.deployment_id
        verified_legs = [
            DeclaredLeg(family=leg.family, version=leg.version,
                        spec_hash=leg.spec_hash, role=leg.role)
            for leg in dep.legs
        ]
    except DeploymentNotReady as e:
        verify_error = str(e)

    # 证据链 —— 对每条声明腿做非抛出式诊断(声明 vs 注册表逐项对照)
    evidence_chain = [LegEvidence(**diagnose_leg(leg)) for leg in declared_legs_raw]

    # readiness —— 既有唯一权威闸门(不重算);production_allowed 取其 allowed
    readiness = get_production_readiness()
    readiness_dict = (
        readiness.model_dump() if hasattr(readiness, "model_dump") else readiness.dict()
    )
    production_allowed = bool(readiness_dict.get("allowed"))

    truth_sources = {
        "deployment": str(DEFAULT_MANIFEST),
        "registry": str(ROOT / "strategy_versions.json"),
        "decay": str(ROOT / "reports" / "decay_status.json"),
        "data_issue": str(ROOT / "reports" / "data" / "data_issue_triage.json"),
        "paper": str(ROOT / "paper" / "account.json"),
    }

    return SystemTruthView(
        as_of=datetime.now(CHINA_TZ).date().isoformat(),
        production_allowed=production_allowed,
        declared_present=declared_present,
        declared_deployment_id=(declared or {}).get("deployment_id", ""),
        declared_status=(declared or {}).get("status", ""),
        declared_legs=declared_legs,
        verified=verified,
        verified_deployment_id=verified_deployment_id,
        verified_legs=verified_legs,
        verify_error=verify_error,
        blocking_reasons=list(readiness_dict.get("blocking_reasons", [])),
        evidence_chain=evidence_chain,
        truth_sources=truth_sources,
        readiness=readiness_dict,
    )
