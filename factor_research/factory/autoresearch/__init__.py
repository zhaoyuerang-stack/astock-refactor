"""Controlled Auto Factor Research Engine.

Agents may propose JSON AST candidates; this package validates, scores, logs,
and routes them to human review without touching LIVE strategy registry.
"""
from .complexity import compute_complexity
from .decision import decide_candidate
from .engine import evaluate_lite
from .fingerprint import fingerprint_ast
from .generator import generate_seed_candidates
from .models import (
    Candidate,
    CandidateDecision,
    CandidateEvaluationResult,
    CandidateStatus,
    ComplexityReport,
    LeakageReport,
    RedundancyReport,
)
from .novelty import behavior_redundancy, candidate_factor_panel, novelty_score
from .pipeline import ast_to_hypothesis, run_validation_pipeline
from .redundancy import factor_redundancy_score
from .repositories import CandidateRepository, ExperimentLog, ReviewQueue
from .validator import DSLValidationError, validate_candidate_ast
from .walkforward import WalkForwardChampion, WalkForwardResult, run_walk_forward_search

__all__ = [
    "Candidate",
    "CandidateDecision",
    "CandidateEvaluationResult",
    "CandidateRepository",
    "CandidateStatus",
    "ComplexityReport",
    "DSLValidationError",
    "ExperimentLog",
    "LeakageReport",
    "RedundancyReport",
    "ReviewQueue",
    "WalkForwardChampion",
    "WalkForwardResult",
    "ast_to_hypothesis",
    "behavior_redundancy",
    "candidate_factor_panel",
    "compute_complexity",
    "decide_candidate",
    "evaluate_lite",
    "factor_redundancy_score",
    "fingerprint_ast",
    "generate_seed_candidates",
    "novelty_score",
    "run_validation_pipeline",
    "run_walk_forward_search",
    "validate_candidate_ast",
]
