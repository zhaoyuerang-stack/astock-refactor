"""Strategy research and control-rule validation toolkit.

This package is intentionally host-agnostic. It models policy artifacts such as
VetoFilter, evaluates marginal contribution against a host, triages failed
factory candidates, and audits any factor/strategy for honest marginal alpha
(Alpha Audit) — all without turning control rules into standalone strategies.
"""
from .alpha_audit import (
    AuditReport,
    Verdict,
    audit_factor,
    corrected_icir,
    newey_west_icir,
    ridge_joint_increment,
)
from .artifacts import ArtifactType, ControlArtifact, HostSpec
from .marginal import MarginalReport, compute_marginal_report
from .policy import apply_veto_filter
from .triage import TriageDecision, route_failed_candidate

__all__ = [
    # 控制规则 / 边际
    "ArtifactType",
    "ControlArtifact",
    "HostSpec",
    "MarginalReport",
    "TriageDecision",
    "apply_veto_filter",
    "compute_marginal_report",
    "route_failed_candidate",
    # Alpha Audit(因子测谎机)
    "AuditReport",
    "Verdict",
    "audit_factor",
    "corrected_icir",
    "newey_west_icir",
    "ridge_joint_increment",
]
