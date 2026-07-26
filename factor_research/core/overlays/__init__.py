"""Strategy overlays: modular risk-management layers.

Each overlay is a callable that receives the current strategy state and
returns an exposure multiplier in [0.0, 1.0]. Multiple overlays can be
chained together.
"""
from .hmm_macro_overlay import HMMMacroOverlay, OverlayConfig, OverlayMonitor
from .moving_average_overlay import MovingAverageOverlay
from .pure_trend_overlay import PureTrendOverlay

__all__ = ["HMMMacroOverlay", "OverlayMonitor", "OverlayConfig", "PureTrendOverlay", "MovingAverageOverlay"]
