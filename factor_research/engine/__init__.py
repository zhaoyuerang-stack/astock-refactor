"""Engine module — factor analysis, composition, and portfolio utilities.

New canonical entry-point: ``from core.engine import BacktestEngine``.
"""
from engine.factor_analysis import calc_ic, ic_summary, stratify_return  # noqa: F401
from engine.factor_composer import (  # noqa: F401
    equal_weight_factor,
    ic_weight_factor,
    pca_factor_composite,
)
from engine.portfolio import performance_metrics, to_signal  # noqa: F401
from engine.signal_factory import factor_to_signal  # noqa: F401
