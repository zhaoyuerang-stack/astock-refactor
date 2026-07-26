"""Decision taxonomy compatibility tests.

Taxonomy: strategy daily decision canonical name = ``latest_decision``;
``latest_signal`` is kept only as a backward-compatible wrapper.

These tests are data-free on purpose: calling the real ``latest_decision``
runs the full strategy (needs the data lake). Instead we prove the wrapper is
a faithful pass-through — it forwards the config unchanged and returns the
callee's result verbatim — by swapping ``latest_decision`` for a recorder.

Run:
    cd factor_research && python3 tests/test_decision_taxonomy.py
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# every strategy module that exposes the daily-decision entrypoint
STRATEGY_MODULES = [
    "strategies.hq_momentum",
    "strategies.d_le_sc",
    "strategies.industry_rotation",
    "strategies.small_cap",
    "strategies.size_earnings",
    "strategies.large_cap",
]


def test_every_strategy_exposes_both_names_distinctly():
    for name in STRATEGY_MODULES:
        mod = importlib.import_module(name)
        assert hasattr(mod, "latest_decision"), f"{name} missing canonical latest_decision"
        assert hasattr(mod, "latest_signal"), f"{name} missing compat latest_signal"
        assert callable(mod.latest_decision) and callable(mod.latest_signal)
        # wrapper must not BE the canonical fn — it should delegate to it
        assert mod.latest_signal is not mod.latest_decision, f"{name}: wrapper is not distinct"


def test_latest_signal_delegates_to_latest_decision():
    """Wrapper forwards config unchanged and returns callee result verbatim."""
    for name in STRATEGY_MODULES:
        mod = importlib.import_module(name)
        sentinel = object()
        captured = {}

        def recorder(config, captured=captured, sentinel=sentinel):
            captured["config"] = config
            return sentinel

        original = mod.latest_decision
        mod.latest_decision = recorder
        try:
            cfg = mod.StrategyConfig()
            out = mod.latest_signal(cfg)
            assert out is sentinel, f"{name}: wrapper did not return latest_decision result"
            assert captured["config"] is cfg, f"{name}: wrapper altered/dropped the config"
        finally:
            mod.latest_decision = original


def test_wrapper_default_arg_passes_through():
    """Calling latest_signal() with no arg must forward its own default to latest_decision."""
    for name in STRATEGY_MODULES:
        mod = importlib.import_module(name)
        captured = {}

        def recorder(config, captured=captured):
            captured["config"] = config
            return None

        original = mod.latest_decision
        mod.latest_decision = recorder
        try:
            mod.latest_signal()  # no-arg call uses the wrapper's historical default
            assert "config" in captured, f"{name}: no-arg wrapper call did not reach latest_decision"
        finally:
            mod.latest_decision = original


if __name__ == "__main__":
    test_every_strategy_exposes_both_names_distinctly()
    test_latest_signal_delegates_to_latest_decision()
    test_wrapper_default_arg_passes_through()
    print("decision taxonomy tests passed")
