"""Audit classic Alpha101 factors using the 9-Gate Research-to-Production pipeline.

Usage:
  python3 scripts/research/audit_classic_alphas.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from core.analysis.nine_gates import NineGatesEvaluator, NineGatesReport
from core.engine import PricePanel, Signal
from strategies.small_cap import _drop_star, build_rebalance_weights, load_price_panels


# --- Shared Operators ---
def R(x): return x.rank(axis=1, pct=True)
def C(x, y, d): return x.rolling(d).corr(y)
def O(x, d): return x.shift(d)

def preprocess(f):
    """Normalize and shift factor by 1 day to align with T+1 execution and prevent look-ahead."""
    return f.rank(axis=1, pct=True).shift(1)

# --- Factor Builders ---
def build_a003_factor(p: PricePanel) -> pd.DataFrame:
    """Alpha003: -C(R(close), R(volume), 10)"""
    c = p.close
    v = p.volume
    raw = -C(R(c), R(v), 10)
    return preprocess(raw)

def build_a055_factor(p: PricePanel) -> pd.DataFrame:
    """Alpha055: -C(R((close - close.rolling(12).min()) / (close.rolling(12).max() - close.rolling(12).min() + 1e-6)), R(volume), 6)"""
    c = p.close
    v = p.volume
    stoch = (c - c.rolling(12).min()) / (c.rolling(12).max() - c.rolling(12).min() + 1e-6)
    raw = -C(R(stoch), R(v), 6)
    return preprocess(raw)

def main():
    print("=" * 80)
    print("  Running 9-Gate Audit for Classic Alpha101 Factors (a003 & a055)")
    print("=" * 80)

    # 1. Load data
    print("\n[Step 1] Loading data lake panels (2018-01-01)...", flush=True)
    close, volume, amount = load_price_panels("2018-01-01")
    # Drop STAR market columns (688) as in standard small_cap strategy
    close, volume, amount = _drop_star(close, volume, amount)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    print(f"  Loaded {close.shape[1]} stocks x {close.shape[0]} dates (ex-STAR).")

    # 2. Audit alpha003
    print("\n[Step 2] Auditing Alpha003...", flush=True)
    factor_a003 = build_a003_factor(prices)

    thesis_a003 = {
        "mechanism": "量价背离效应：股票价格上涨但成交量下降，或价格下跌但成交量上升，反映了资金流向的异动与博弈失衡。量价负相关的股票往往预示着随后的均值反转或超额收益回报。",
        "citation": "Alpha101: Alpha#3; Price-Volume divergence anomaly."
    }

    scheduled_weights_a003 = build_rebalance_weights(
        factor_a003,
        close,
        top_n=25,
        rebalance_days=20,
    )

    signal_a003 = Signal(
        weights=scheduled_weights_a003,
        family="alpha101_a003",
        version="v1.0"
    )

    evaluator_a003 = NineGatesEvaluator(
        prices=prices,
        factor_df=factor_a003,
        factor_builder=build_a003_factor,
        thesis=thesis_a003,
        n_trials=15,  # Standard multiple testing adjustment
        forward_days=20
    )

    reports_a003 = evaluator_a003.evaluate_all(signal_a003, start="2018-01-01")
    passed_a003 = all(r.passed for r in reports_a003)

    report_a003 = NineGatesReport(
        factor_name="Alpha101_a003_v1.0",
        run_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        passed_all=passed_a003,
        reports=reports_a003
    )

    # Write report
    report_dir = ROOT / "reports" / "research"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path_a003 = report_dir / "alpha101_a003_9_gates_report.md"
    report_path_a003.write_text(report_a003.to_markdown(), encoding="utf-8")
    print(f"  Alpha003 audit complete. Passed={passed_a003}. Report saved to: {report_path_a003}")

    # 3. Audit alpha055
    print("\n[Step 3] Auditing Alpha055...", flush=True)
    factor_a055 = build_a055_factor(prices)

    thesis_a055 = {
        "mechanism": "量能辅助的动量竭尽：在超买或超卖位置（Stochastic位置接近1或0）时，价格变化与成交量变化的相关性减弱或反向，代表动量在极端区间缺乏成交量支撑，预示价格动量的枯竭与反转。",
        "citation": "Alpha101: Alpha#55; Volume-assisted momentum exhaustion anomaly."
    }

    scheduled_weights_a055 = build_rebalance_weights(
        factor_a055,
        close,
        top_n=25,
        rebalance_days=20,
    )

    signal_a055 = Signal(
        weights=scheduled_weights_a055,
        family="alpha101_a055",
        version="v1.0"
    )

    evaluator_a055 = NineGatesEvaluator(
        prices=prices,
        factor_df=factor_a055,
        factor_builder=build_a055_factor,
        thesis=thesis_a055,
        n_trials=15,
        forward_days=20
    )

    reports_a055 = evaluator_a055.evaluate_all(signal_a055, start="2018-01-01")
    passed_a055 = all(r.passed for r in reports_a055)

    report_a055 = NineGatesReport(
        factor_name="Alpha101_a055_v1.0",
        run_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        passed_all=passed_a055,
        reports=reports_a055
    )

    # Write report
    report_path_a055 = report_dir / "alpha101_a055_9_gates_report.md"
    report_path_a055.write_text(report_a055.to_markdown(), encoding="utf-8")
    print(f"  Alpha055 audit complete. Passed={passed_a055}. Report saved to: {report_path_a055}")

    # 4. Ledger entry (optional but good for tracking)
    try:
        from scripts.research.run_nine_gates_all import record_nine_gate_research_run
        record_nine_gate_research_run(
            strategy_name="alpha101_a003",
            version="v1.0",
            summary=report_a003.summarize(),
            report_path=report_path_a003
        )
        record_nine_gate_research_run(
            strategy_name="alpha101_a055",
            version="v1.0",
            summary=report_a055.summarize(),
            report_path=report_path_a055
        )
        print("  Recorded both runs in the research ledger.")
    except Exception as e:
        print(f"  Skipped ledger registration: {e}")

if __name__ == "__main__":
    main()
