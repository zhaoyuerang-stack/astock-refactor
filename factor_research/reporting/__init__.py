"""GIPS-style Performance Attribution and Reporting module exports."""
from __future__ import annotations

from reporting.audit_pack import generate_audit_pack
from reporting.benchmark_comparison import compare_to_benchmark
from reporting.composite_return import compute_composite_return
from reporting.drawdown_report import generate_drawdown_report
from reporting.exposure_report import generate_exposure_report
from reporting.gross_net_return import calculate_gross_to_net
from reporting.performance_attribution import attribute_returns
from reporting.turnover_report import generate_turnover_report

__all__ = [
    "attribute_returns",
    "compute_composite_return",
    "calculate_gross_to_net",
    "compare_to_benchmark",
    "generate_drawdown_report",
    "generate_exposure_report",
    "generate_turnover_report",
    "generate_audit_pack",
]
