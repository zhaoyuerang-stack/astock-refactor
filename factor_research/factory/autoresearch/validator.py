"""JSON AST validation for autonomous factor candidates."""
from __future__ import annotations

from datetime import date
from typing import Any

from .fingerprint import fingerprint_ast
from .models import Candidate
from .registry import (
    ALLOWED_DIRECTIONS,
    ALLOWED_FACTORS,
    ALLOWED_NEUTRALIZE,
    ALLOWED_TRANSFORMS,
    ALLOWED_TYPES,
)


class DSLValidationError(ValueError):
    """Raised when a candidate AST is outside the safe DSL."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DSLValidationError(message)


def _validate_params(factor: str, params: dict[str, Any], allow_experimental_factors: bool) -> None:
    if factor not in ALLOWED_FACTORS:
        if allow_experimental_factors:
            return
        raise DSLValidationError(f"unknown factor: {factor}")
    spec = ALLOWED_FACTORS[factor]
    unknown = set(params) - set(spec.params)
    _require(not unknown, f"unknown params for {factor}: {sorted(unknown)}")
    for name, (lo, hi) in spec.params.items():
        _require(name in params, f"missing required param for {factor}: {name}")
        value = params[name]
        _require(isinstance(value, (int, float)), f"param {factor}.{name} must be numeric")
        _require(lo <= value <= hi, f"param {factor}.{name}={value} outside [{lo}, {hi}]")


def validate_candidate_ast(ast: dict[str, Any], *, allow_experimental_factors: bool = False) -> Candidate:
    """Validate a JSON AST and return a canonical Candidate wrapper."""
    _require(isinstance(ast, dict), "candidate must be a JSON AST object")
    _require("expr" not in ast, "free-form expr is not allowed; submit a JSON AST")
    _require(ast.get("type") in ALLOWED_TYPES, f"unsupported AST type: {ast.get('type')}")

    terms = ast.get("terms")
    _require(isinstance(terms, list) and len(terms) > 0, "linear_combo requires non-empty terms")

    for idx, term in enumerate(terms):
        _require(isinstance(term, dict), f"term {idx} must be an object")
        factor = term.get("factor")
        _require(isinstance(factor, str) and factor, f"term {idx} requires factor")

        params = term.get("params", {})
        _require(isinstance(params, dict), f"term {idx} params must be object")
        _validate_params(factor, params, allow_experimental_factors)

        transforms = term.get("transforms", [])
        _require(isinstance(transforms, list), f"term {idx} transforms must be list")
        unknown_transforms = [t for t in transforms if t not in ALLOWED_TRANSFORMS]
        _require(not unknown_transforms, f"unknown transform(s): {unknown_transforms}")

        weight = term.get("weight", 1.0)
        _require(isinstance(weight, (int, float)), f"term {idx} weight must be numeric")
        _require(abs(float(weight)) <= 2.0, f"term {idx} weight magnitude too large")

    root_transforms = ast.get("transforms", [])
    _require(isinstance(root_transforms, list), "root transforms must be list")
    unknown_root_transforms = [t for t in root_transforms if t not in ALLOWED_TRANSFORMS]
    _require(not unknown_root_transforms, f"unknown root transform(s): {unknown_root_transforms}")

    neutralize = ast.get("neutralize", [])
    _require(isinstance(neutralize, list), "neutralize must be list")
    unknown_neutralize = [n for n in neutralize if n not in ALLOWED_NEUTRALIZE]
    _require(not unknown_neutralize, f"unknown neutralize option(s): {unknown_neutralize}")
    # neutralize is now fully implemented in compute_dsl_factor runtime.

    direction = ast.get("direction", "positive")
    _require(direction in ALLOWED_DIRECTIONS, f"unknown direction: {direction}")

    thesis = ast.get("thesis", {})
    _require(isinstance(thesis, dict), "thesis must be object")
    _require(bool(str(thesis.get("mechanism", "")).strip()), "thesis.mechanism is required")

    return Candidate(
        fingerprint=fingerprint_ast(ast),
        ast=ast,
        created_at=date.today().isoformat(),
    )
