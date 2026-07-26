"""Run 9-Gate evaluation for the production Amihud Illiquidity factor.

Loads the data lake panels, computes the factor and weights, runs the 9-Gate suite,
and writes a comprehensive report to reports/research/amihud_9_gates_report.md.
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
from factors.alpha import transforms  # noqa: F401 —— 副作用注册 DSL 变换(zscore/mad_clip/shift 等)
from factors.alpha.builtins.illiq import AmihudIlliq
from factors.veto import salience_covariance_veto
from strategies.small_cap import build_rebalance_weights, load_price_panels


def main():
    print("=" * 80)
    print("  Running Institutional-Grade 9-Gate Strategy Evaluation Pipeline")
    print("=" * 80)
    
    # 1. Load data
    print("\n[Step 1] Loading data lake panels (2018-2026)...", flush=True)
    close, volume, amount = load_price_panels("2018-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    print(f"  Loaded {close.shape[1]} stocks x {close.shape[0]} dates.")
    
    # 2. Build factor
    print("\n[Step 2] Building Amihud Illiquidity v3.1 Factor...", flush=True)
    from factors.alpha.base import FactorData
    data = FactorData(close=close, volume=volume, amount=amount)
    factor = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(data)
    
    # 3. Build economic thesis
    thesis = {
        "mechanism": "A股市场以个人投资者为主，散户具有高频博弈特征；非流动性股票面临更高的执行冲击成本，需要流动性折价补偿。该非流动性因子反映了这一流动性溢价效应，且与个股未来超额收益强正相关。",
        "citation": "Amihud (2002) Illiquidity and stock returns; CNE6 Style Neutralization Audit 2026"
    }
    
    # 4. Build portfolio signal
    print("\n[Step 3] Constructing portfolio rebalance weights...", flush=True)
    veto = salience_covariance_veto(close).shift(1)
    scheduled_weights = build_rebalance_weights(
        factor,
        close,
        top_n=25,
        rebalance_days=20,
        veto_factor=veto,
        veto_q=0.30
    )
    
    signal = Signal(
        weights=scheduled_weights,
        family="illiquidity",
        version="v3.1"
    )
    
    # 5. Initialize and run NineGatesEvaluator
    # We set n_trials=15 to reflect typical backtest overfitting adjustments
    print("\n[Step 4] Initializing 9-Gate Evaluator & running audits...", flush=True)
    evaluator = NineGatesEvaluator(
        prices=prices,
        factor_df=factor,
        factor_builder=lambda p: AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(FactorData(close=p.close, volume=p.volume, amount=p.amount)),
        thesis=thesis,
        n_trials=15,
        forward_days=20
    )
    
    # Evaluate all gates
    reports = evaluator.evaluate_all(signal, start="2018-01-01")
    
    # Determine overall passed
    passed_all = all(r.passed for r in reports)
    
    # Generate consolidated report
    report = NineGatesReport(
        factor_name="AmihudIlliq_v3.1",
        run_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        passed_all=passed_all,
        reports=reports
    )
    
    # 6. Output to console and file
    markdown_content = report.to_markdown()
    
    report_dir = ROOT / "reports" / "research"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "amihud_9_gates_report.md"
    
    report_path.write_text(markdown_content, encoding="utf-8")
    
    print("\n" + "=" * 80)
    print(f"9-Gate Evaluation Completed! Report saved to:\n{report_path}")
    print("=" * 80)
    
    # Print summary to console
    print("\nExecutive Summary:")
    print(markdown_content.split("## Detailed Gate Findings")[0].strip())


if __name__ == "__main__":
    main()
