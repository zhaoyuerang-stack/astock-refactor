"""Generic live runner for ledger versions driven by AutoResearch AST / formula config.

Covers:
  - fundamental-momentum/*
  - autoresearch_*/*
  - alternative-flow-shareholder/* (formula string → AST)

Canonical path: compute_dsl_factor → shift(1) → build_rebalance_weights → MA16 timing → BacktestEngine.
Does not write registry.
"""
from __future__ import annotations

import ast as py_ast
from typing import Any

import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.autoresearch_dsl import compute_dsl_factor
from factors.small_cap import small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels

DSL_FAMILIES = frozenset({
    "fundamental-momentum",
    "alternative-flow-shareholder",
})


def is_dsl_family(family: str) -> bool:
    if family in DSL_FAMILIES:
        return True
    if family.startswith("autoresearch_"):
        return True
    return False


def extract_ast_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve AST from config.ast / factor_params.ast / formula string."""
    if not cfg:
        raise ValueError("config 为空，无法解析 AST")
    if isinstance(cfg.get("ast"), dict):
        return dict(cfg["ast"])
    fp = cfg.get("factor_params")
    if isinstance(fp, dict) and isinstance(fp.get("ast"), dict):
        return dict(fp["ast"])
    formula = cfg.get("formula")
    if formula is not None:
        if isinstance(formula, dict):
            return dict(formula)
        if isinstance(formula, str):
            s = formula.strip()
            try:
                obj = py_ast.literal_eval(s)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(f"formula 无法 literal_eval: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError("formula 解析结果不是 dict")
            return obj
    raise ValueError("config 中无 ast / factor_params.ast / formula")


def _rebalance_days_from_cfg(cfg: dict[str, Any], ast: dict[str, Any]) -> int:
    if cfg.get("rebalance_days") is not None:
        try:
            return int(cfg["rebalance_days"])
        except (TypeError, ValueError):
            pass
    if cfg.get("rebal_days") is not None:
        try:
            return int(cfg["rebal_days"])
        except (TypeError, ValueError):
            pass
    execution = ast.get("execution")
    exec_b: dict[str, Any] = execution if isinstance(execution, dict) else {}
    freq = str(exec_b.get("rebalance_freq") or "")
    # e.g. "20D" / "40D"
    if freq.endswith("D") and freq[:-1].isdigit():
        return int(freq[:-1])
    return 20


def run_ast_config_strategy(
    *,
    family: str,
    version: str,
    start: str = "2018-01-01",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Live re-run one AST/config-driven strategy; returns engine bundle."""
    cfg = dict(config or {})
    ast = extract_ast_from_config(cfg)
    execution = ast.get("execution")
    exec_b: dict[str, Any] = execution if isinstance(execution, dict) else {}
    try:
        top_n = int(cfg.get("top_n") or exec_b.get("portfolio_size") or 25)
    except (TypeError, ValueError):
        top_n = 25
    rebal = _rebalance_days_from_cfg(cfg, ast)
    try:
        lev = float(cfg.get("leverage") or 1.25)
    except (TypeError, ValueError):
        lev = 1.25
    try:
        timing_ma = int(cfg.get("timing_ma") or 16)
    except (TypeError, ValueError):
        timing_ma = 16

    from app_config.settings import get_settings

    warmup = get_settings().data.warmup_start
    ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
    close, volume, amount = load_price_panels(ds)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    # DSL factor; T+1 via shift(1) then engine execution timing
    factor = compute_dsl_factor(close, volume, ast=ast, cache_mode="disk").shift(1)
    timing = small_cap_timing(close, amount, ma_window=timing_ma)[0].astype(float)
    scheduled = build_rebalance_weights(
        factor, close, top_n=top_n, rebalance_days=rebal
    )
    cost = CostModel()

    signal = Signal(
        weights=scheduled,
        timing=timing,
        family=family,
        version=version,
    )
    result = BacktestEngine(
        prices=prices,
        config=BacktestConfig(start=start, cost=cost, leverage=lev),
    ).run(signal)
    return {
        "engine_result": result,
        "returns": result.returns.astype(float).sort_index(),
        "close": close,
        "volume": volume,
        "amount": amount,
        "factor": factor,
        "scheduled_weights": scheduled,
        "timing": timing,
        "ast": ast,
        "config_snapshot": {
            "top_n": top_n,
            "rebalance_days": rebal,
            "leverage": lev,
            "timing_ma": timing_ma,
            "direction": ast.get("direction"),
            "n_terms": len(ast.get("terms") or []),
        },
    }
