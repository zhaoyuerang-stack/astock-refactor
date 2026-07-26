"""Line 1 — Hypothesis 产生。"""
from .mutate_existing import (
    FACTOR_MUTATION_SPECS,
    generate_all_mutations,
    mutate_factor,
)

__all__ = [
    "FACTOR_MUTATION_SPECS",
    "generate_all_mutations",
    "mutate_factor",
]
