# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Market-liquidity HMM stress guard for the small-cap strategy.

This follows the "HMM Stress Guard" idea: infer hidden market stress states
from observable broad-market environment features, then block buys/sell out
when stress probability is above a threshold.

Research-only script. It reads data_lake/core helpers and writes artifacts
under reports/research. It does not change production signals or schedulers.

Usage:
  /usr/bin/python3 -m scripts.research.archive.hmm_stress_guard_smallcap
"""
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.market_stress import (  # noqa: E402
    HMMStressConfig,
    build_market_features,
    hmm_stress_probability,
)
from factors.market_stress import (
    guard_exposure as market_stress_guard_exposure,
)
from scripts.research.archive.hmm_exit_smallcap import row_for  # noqa: E402
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class StressGuardConfig(HMMStressConfig):
    lookback: int = 1260
    retrain_days: int = 60
    threshold: float = 0.15
    max_iter: int = 35
    filter_days: int = 60
    mode: str = "binary"
    stress_floor: float = 0.0


def guard_exposure(prob, cfg):
    return market_stress_guard_exposure(prob, cfg.threshold, cfg.mode, cfg.stress_floor)


def fmt(row):
    return (
        f"2018年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
        f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} | "
        f"2023年化{row['annual_2023']:+.1%} 回撤{row['maxdd_2023']:+.1%} | "
        f"2010年化{row['annual_2010']:+.1%} 回撤{row['maxdd_2010']:+.1%}"
    )


def main():
    cfg0 = StrategyConfig(start="2010-01-01")
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg0)
    close, amount = base["close"], base["amount"]
    scheduled = base["scheduled_weights"]
    baseline_ret = base["returns"]
    smallcap_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)

    print("Building market stress features...", flush=True)
    features = build_market_features(close, amount)

    model_cfgs = [
        StressGuardConfig(lookback=756, retrain_days=60, threshold=0.15),
        StressGuardConfig(lookback=1008, retrain_days=60, threshold=0.15),
        StressGuardConfig(lookback=1260, retrain_days=60, threshold=0.15),
    ]
    threshold_grid = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    floor_grid = [0.0, 0.3, 0.5, 0.7]

    rows = [row_for("v2.0 baseline", baseline_ret, baseline_ret)]
    daily_frames = []
    for model_cfg in model_cfgs:
        print(f"Training HMM stress model {model_cfg}", flush=True)
        prob, state, stress_state = hmm_stress_probability(features, model_cfg)
        prob = prob.reindex(close.index)
        for threshold in threshold_grid:
            for mode, floor in [("binary", 0.0)] + [("floor", x) for x in floor_grid if x > 0]:
                cfg = StressGuardConfig(
                    lookback=model_cfg.lookback,
                    retrain_days=model_cfg.retrain_days,
                    threshold=threshold,
                    max_iter=model_cfg.max_iter,
                    mode=mode,
                    stress_floor=floor,
                )
                exposure = guard_exposure(prob, cfg).reindex(close.index).fillna(0.0)
                timing = smallcap_timing * exposure
                ret, _ = backtest_weights(close, scheduled, timing, cfg0)
                label = (
                    f"hmm_stress {mode} lb{cfg.lookback} rt{cfg.retrain_days} "
                    f"th{threshold:.2f} floor{floor:.1f}"
                )
                active_2018 = timing[timing.index.year >= 2018]
                rows.append(
                    row_for(
                        label,
                        ret,
                        baseline_ret,
                        {
                            **asdict(cfg),
                            "stress_prob_mean_2018": float(prob[prob.index.year >= 2018].mean()),
                            "stress_prob_p90_2018": float(prob[prob.index.year >= 2018].quantile(0.90)),
                            "guard_exposure_2018": float(exposure[exposure.index.year >= 2018].mean()),
                            "timing_on_rate_2018": float(active_2018.mean()),
                        },
                    )
                )
                daily_frames.append(
                    pd.DataFrame(
                        {
                            "date": close.index,
                            "prob_stress": prob.reindex(close.index).values,
                            "state": state.reindex(close.index).values,
                            "stress_state": stress_state.reindex(close.index).values,
                            "guard_exposure": exposure.values,
                            "combined_timing": timing.values,
                            "ret": ret.reindex(close.index).values,
                            "label": label,
                        }
                    )
                )

    result = pd.DataFrame(rows)
    variants = result[result["label"] != "v2.0 baseline"].copy()
    variants["objective"] = (
        0.5 * variants["sharpe_2018"]
        + 0.5 * variants["calmar_2018"]
    ) * np.minimum(variants["annual_2018"] / 0.20, 1.0)
    result = pd.concat([result[result["label"] == "v2.0 baseline"], variants], ignore_index=True)
    result = result.sort_values(["objective", "sharpe_2018", "annual_2018"], ascending=[False, False, False], na_position="last")

    result_path = OUT_DIR / "hmm_stress_guard_smallcap_results.csv"
    daily_path = OUT_DIR / "hmm_stress_guard_smallcap_daily.csv"
    summary_path = OUT_DIR / "hmm_stress_guard_smallcap_summary.json"
    result.to_csv(result_path, index=False)
    pd.concat(daily_frames, ignore_index=True).to_csv(daily_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    summary = {
        "baseline": rows[0],
        "best_by_objective": variants.iloc[0].to_dict(),
        "best_by_sharpe_2018": variants.sort_values(["sharpe_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_calmar_2018": variants.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "notes": [
            "Stress features: risk_appetite, volatility, liquidity, ma_diffusion.",
            "Stress probability is shifted one trading day before exposure changes.",
            "Research-only script; no production files changed.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== HMM Stress Guard results ===", flush=True)
    for _, row in result.head(14).iterrows():
        print(f"{row['label']:<48} {fmt(row)}", flush=True)
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {daily_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
