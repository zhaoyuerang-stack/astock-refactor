"""DeploymentManifest —— 区分「已注册策略」与「当前部署」(Task 7)。

注册表回答「哪个版本通过了完整证据门」;部署清单回答「现在到底在跑哪几条腿」。
两者解耦后,registry 退役 / spec_hash 不匹配会**机械地**让部署加载失败(fail-closed),
而不是靠代码里散落的硬编码 "LIVE" 字符串当事实源。

load_active_deployment 默认查 strategy_registry,但接受注入的 registry_lookup 以便测试。
任一腿不满足(版本不存在 / 非在册 / spec_hash 不一致)即抛 DeploymentNotReady。
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "deployments" / "production.json"

# registry 中视为「可部署」的版本状态(中文台账枚举 + 英文状态机别名,Task 15 统一)
DEPLOYABLE_STATUSES = {"在册", "REGISTERED", "DEPLOYED"}


class DeploymentNotReady(RuntimeError):
    """部署清单存在某条腿无法激活(版本缺失 / 未在册 / spec_hash 不匹配)。"""


@dataclass(frozen=True)
class DeploymentLeg:
    family: str
    version: str
    spec_hash: str
    role: str


@dataclass(frozen=True)
class Deployment:
    deployment_id: str
    environment: str
    status: str
    portfolio_policy: dict
    legs: tuple[DeploymentLeg, ...]


def _default_registry_lookup(family: str, version: str) -> dict | None:
    """默认实现:从 strategy_registry 读某 family/version 的版本记录(含 status/executable_spec)。"""
    import strategy_registry
    data = strategy_registry._load()
    fam = next((f for f in data.get("families", []) if f.get("id") == family), None)
    if fam is None:
        return None
    return next((v for v in fam.get("versions", []) if v.get("version") == version), None)


def read_declared_manifest(manifest_path: Path | str = DEFAULT_MANIFEST) -> dict | None:
    """读「声明的」部署清单原文,**不做 fail-closed 校验**。

    声明态回答「清单声称在跑什么」,不回答「能不能跑」(后者由 ``load_active_deployment``
    fail-closed 决定)。返回归一化 dict 或 None(清单文件不存在)。供「真相层」并排展示
    declared vs verified,杜绝把 manifest 里的 ``status: active`` 误读成 live。
    """
    path = Path(manifest_path)
    if not path.exists():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8"))
    legs = [
        {
            "family": str(rl.get("family", "")),
            "version": str(rl.get("version", "")),
            "spec_hash": str(rl.get("spec_hash", "")),
            "role": str(rl.get("role", "")),
        }
        for rl in (manifest.get("legs") or [])
    ]
    return {
        "deployment_id": manifest.get("deployment_id", ""),
        "environment": manifest.get("environment", ""),
        "status": manifest.get("status", ""),
        "portfolio_policy": manifest.get("portfolio_policy") or {},
        "legs": legs,
    }


def diagnose_leg(
    leg: dict,
    *,
    registry_lookup: Callable[[str, str], dict | None] | None = None,
) -> dict:
    """对单条声明腿做「非抛出式」诊断,产出结构化证据(供证据链)。

    与 ``_validate_leg`` **同源判定**(状态可部署性 + spec_hash 一致性,且判定优先级一致),
    但不抛 ``DeploymentNotReady``,而是把每一步对照结果返回,便于前端逐条展示阻断根因。
    ``blocking_reason`` 为空字符串表示该腿无阻断。
    """
    registry_lookup = registry_lookup or _default_registry_lookup
    family = str(leg.get("family", ""))
    version = str(leg.get("version", ""))
    declared_hash = str(leg.get("spec_hash", ""))
    out = {
        "family": family,
        "version": version,
        "role": str(leg.get("role", "")),
        "declared_spec_hash": declared_hash,
        "registry_found": False,
        "registry_status": "",
        "registry_spec_hash": "",
        "status_deployable": False,
        "spec_hash_match": False,
        "blocking_reason": "",
    }
    rec = registry_lookup(family, version)
    if rec is None:
        out["blocking_reason"] = f"{family}/{version} 不在注册表 —— 不能部署未注册策略"
        return out
    out["registry_found"] = True
    status = rec.get("status")
    out["registry_status"] = str(status or "")
    reg_hash = (rec.get("executable_spec") or {}).get("spec_hash") or ""
    out["registry_spec_hash"] = reg_hash
    out["status_deployable"] = status in DEPLOYABLE_STATUSES
    out["spec_hash_match"] = bool(reg_hash) and reg_hash == declared_hash
    if not out["status_deployable"]:
        out["blocking_reason"] = (
            f"{family}/{version} 注册状态={status!r} 非可部署({sorted(DEPLOYABLE_STATUSES)});"
            f"退役/候选版本不得激活")
    elif not reg_hash:
        out["blocking_reason"] = (
            f"{family}/{version} 注册记录缺少 executable_spec.spec_hash;"
            f"需先迁移到 spec 化身份(Task 19)")
    elif not out["spec_hash_match"]:
        out["blocking_reason"] = (
            f"{family}/{version} spec_hash 不匹配:清单={declared_hash[:12]} "
            f"注册={reg_hash[:12]} —— 部署与注册身份漂移")
    return out


def _validate_leg(leg: DeploymentLeg, registry_lookup: Callable[[str, str], dict | None]) -> None:
    """fail-closed 校验:复用 ``diagnose_leg`` 的同源判定,有阻断即抛。"""
    diag = diagnose_leg(
        {"family": leg.family, "version": leg.version,
         "spec_hash": leg.spec_hash, "role": leg.role},
        registry_lookup=registry_lookup,
    )
    if diag["blocking_reason"]:
        raise DeploymentNotReady(diag["blocking_reason"])


def load_active_deployment(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    *,
    registry_lookup: Callable[[str, str], dict | None] | None = None,
) -> Deployment:
    """加载并校验当前激活部署。任一腿不满足即抛 DeploymentNotReady(fail-closed)。"""
    registry_lookup = registry_lookup or _default_registry_lookup
    path = Path(manifest_path)
    if not path.exists():
        raise DeploymentNotReady(f"部署清单不存在: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))

    if manifest.get("status") != "active":
        raise DeploymentNotReady(f"部署 status={manifest.get('status')!r} 非 active")
    raw_legs = manifest.get("legs") or []
    if not raw_legs:
        raise DeploymentNotReady("部署清单没有任何腿")

    legs: list[DeploymentLeg] = []
    for i, rl in enumerate(raw_legs):
        for key in ("family", "version", "spec_hash", "role"):
            if not str(rl.get(key, "")).strip():
                raise DeploymentNotReady(f"第{i}条腿缺字段 {key!r}")
        legs.append(DeploymentLeg(
            family=rl["family"], version=rl["version"],
            spec_hash=rl["spec_hash"], role=rl["role"]))

    for leg in legs:
        _validate_leg(leg, registry_lookup)

    return Deployment(
        deployment_id=manifest.get("deployment_id", ""),
        environment=manifest.get("environment", ""),
        status=manifest["status"],
        portfolio_policy=manifest.get("portfolio_policy") or {},
        legs=tuple(legs),
    )


def load_deployed_strategy_spec(leg: DeploymentLeg):
    """Load the immutable spec referenced by one already-validated deployment leg."""
    from core.strategy_spec import ExecutableStrategySpec

    record = _default_registry_lookup(leg.family, leg.version)
    executable = (record or {}).get("executable_spec") or {}
    spec_data = executable.get("spec")
    if not spec_data:
        raise DeploymentNotReady(
            f"{leg.family}/{leg.version} registry executable spec body missing"
        )
    spec = ExecutableStrategySpec.from_dict(spec_data)
    spec.validate()
    if spec.spec_hash != leg.spec_hash:
        raise DeploymentNotReady(
            f"{leg.family}/{leg.version} executable spec body hash mismatch"
        )
    return spec


def defensive_authorization(deployment: Deployment) -> dict | None:
    """返回当前部署中独立 defensive leg 的授权身份;没有则不授权债券轮动。"""
    leg = next((item for item in deployment.legs if item.role == "defensive"), None)
    if leg is None:
        return None
    return {
        "role": leg.role,
        "family": leg.family,
        "version": leg.version,
        "spec_hash": leg.spec_hash,
    }
