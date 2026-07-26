"""Composite redundancy score.

MI is useful, but not enough on its own. The score intentionally blends
correlation, overlap, and exposure similarity.
"""
from __future__ import annotations

from .models import RedundancyReport

WEIGHTS = {
    "spearman_corr": 0.3,
    "normalized_mi": 0.2,
    "holding_overlap": 0.2,
    "return_corr": 0.2,
    "exposure_similarity": 0.1,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def factor_redundancy_score(**inputs: float) -> RedundancyReport:
    components: dict[str, float] = {}
    score = 0.0
    for name, weight in WEIGHTS.items():
        raw = abs(float(inputs.get(name, 0.0)))
        value = _clamp01(raw)
        components[name] = value
        score += weight * value
    return RedundancyReport(score=round(score, 4), components=components)
