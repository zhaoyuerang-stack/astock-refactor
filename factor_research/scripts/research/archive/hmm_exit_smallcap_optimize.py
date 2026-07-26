# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Broader optimizer for the HMM small-cap exit overlay.

Research-only script. It caches HMM risk probabilities per model grid, then
evaluates many exposure transforms without retraining. No production code,
registry, daily signal, or scheduler is changed.

Usage:
  /usr/bin/python3 -m scripts.research.archive.hmm_exit_smallcap_optimize
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

from scripts.research.archive.hmm_exit_smallcap import (  # noqa: E402
    HMMGrid,
    hmm_exit_signal,
    make_features,
    row_for,
)
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ExposureRule:
    mode: str
    threshold: float = 0.5
    floor: float = 0.0
    power: float = 1.0
    hi: float = 0.8
    mid: float = 0.5

    def label(self):
        if self.mode == "binary":
            return f"binary_th{self.threshold:.2f}"
        if self.mode == "soft":
            return f"soft_floor{self.floor:.2f}_pow{self.power:.1f}"
        if self.mode == "step":
            return f"step_mid{self.mid:.2f}_floor{self.floor:.2f}_th{self.threshold:.2f}_hi{self.hi:.2f}"
        raise ValueError(f"unknown mode: {self.mode}")


def exposure_from_rule(risk, rule):
    risk = risk.clip(0.0, 1.0)
    if rule.mode == "binary":
        return (risk < rule.threshold).astype(float)
    if rule.mode == "soft":
        return rule.floor + (1.0 - rule.floor) * ((1.0 - risk) ** rule.power)
    if rule.mode == "step":
        out = pd.Series(1.0, index=risk.index, dtype="float64")
        out[risk >= rule.threshold] = rule.mid
        out[risk >= rule.hi] = rule.floor
        return out
    raise ValueError(f"unknown mode: {rule.mode}")


def model_label(grid):
    return f"{grid.feature_set}_lb{grid.lookback}_rt{grid.retrain_days}_it{grid.max_iter}"


def build_model_grids():
    grids = []
    for feature_set in ["small_market"]:
        for lookback in [1008, 1260, 1512]:
            for retrain_days in [60, 80]:
                grids.append(HMMGrid(feature_set, lookback, retrain_days, 0.5, "binary", 0.0, 1.0, 30))
    return grids


def build_exposure_rules():
    rules = []
    for threshold in np.linspace(0.05, 0.95, 10):
        rules.append(ExposureRule("binary", threshold=float(threshold)))
    for floor in [0.0, 0.25, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.99]:
        for power in [0.5, 1.0, 1.5, 2.0]:
            rules.append(ExposureRule("soft", floor=floor, power=power))
    for threshold in [0.35, 0.50, 0.65]:
        for hi in [0.75, 0.90]:
            if hi <= threshold:
                continue
            for mid in [0.50, 0.70, 0.85]:
                for floor in [0.0, 0.30, 0.50]:
                    rules.append(ExposureRule("step", threshold=threshold, hi=hi, mid=mid, floor=floor))
    return rules


def objective(row):
    """Balanced objective: prefer Sharpe/Calmar, penalize low annual return."""
    annual = row["annual_2018"]
    maxdd = abs(row["maxdd_2018"])
    sharpe = row["sharpe_2018"]
    calmar = row["calmar_2018"]
    annual_penalty = min(annual / 0.20, 1.0)
    dd_penalty = min(0.22 / max(maxdd, 1e-9), 1.2)
    return float((0.55 * sharpe + 0.45 * calmar) * annual_penalty * dd_penalty)


def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    amount = base["amount"]
    scheduled = base["scheduled_weights"]
    baseline_ret = base["returns"]
    smallcap_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)

    rows = [row_for("v2.0 baseline", baseline_ret, baseline_ret)]
    risk_frames = []
    features_cache = {}
    rules = build_exposure_rules()

    for grid in build_model_grids():
        label = model_label(grid)
        print(f"Training risk model {label}", flush=True)
        features = features_cache.get(grid.feature_set)
        if features is None:
            features = make_features(close, amount, grid.feature_set)
            features_cache[grid.feature_set] = features
        _, risk_prob, state_trace = hmm_exit_signal(features, grid)
        risk = risk_prob.reindex(close.index).fillna(1.0)
        risk_frames.append(pd.DataFrame({"date": close.index, "risk_prob": risk.values, "state": state_trace.reindex(close.index).values, "model": label}))

        for rule in rules:
            exposure = exposure_from_rule(risk, rule)
            timing = smallcap_timing * exposure
            ret, _ = backtest_weights(close, scheduled, timing, cfg)
            full_label = f"hmm_opt {label} {rule.label()}"
            active_2018 = timing[timing.index.year >= 2018]
            row = row_for(
                full_label,
                ret,
                baseline_ret,
                {
                    **asdict(grid),
                    **asdict(rule),
                    "model": label,
                    "rule": rule.label(),
                    "timing_on_rate_2018": float(active_2018.mean()),
                    "hmm_on_rate_2018": float(exposure[exposure.index.year >= 2018].mean()),
                },
            )
            row["objective"] = objective(row)
            rows.append(row)

    result = pd.DataFrame(rows)
    result["objective"] = result.apply(lambda r: objective(r) if r["label"] != "v2.0 baseline" else np.nan, axis=1)
    result = result.sort_values(["objective", "sharpe_2018", "annual_2018"], ascending=[False, False, False])

    result_path = OUT_DIR / "hmm_exit_smallcap_optimized_results.csv"
    risk_path = OUT_DIR / "hmm_exit_smallcap_optimized_risk.csv"
    summary_path = OUT_DIR / "hmm_exit_smallcap_optimized_summary.json"
    result.to_csv(result_path, index=False)
    pd.concat(risk_frames, ignore_index=True).to_csv(risk_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    summary = {
        "baseline": rows[0],
        "best_by_objective": variants.iloc[0].to_dict(),
        "best_by_sharpe_2018": variants.sort_values(["sharpe_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_calmar_2018": variants.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_annual_under_20dd": variants[variants["maxdd_2018"] >= -0.20]
        .sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False])
        .iloc[0]
        .to_dict(),
        "notes": [
            "Research-only optimizer; no production files changed.",
            "HMM state risk is learned from next-day small-cap return, and risk probabilities are shifted one day before exposure.",
            "Objective is a local research score, not a registry admission criterion.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top HMM optimized candidates ===", flush=True)
    cols = ["label", "annual_2018", "maxdd_2018", "sharpe_2018", "calmar_2018", "annual_2023", "maxdd_2023", "annual_2010", "maxdd_2010", "objective"]
    for _, row in result.head(12)[cols].iterrows():
        print(
            f"{row['label'][:82]:<82} "
            f"18年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} | "
            f"23年化{row['annual_2023']:+.1%} 回撤{row['maxdd_2023']:+.1%} | "
            f"10年化{row['annual_2010']:+.1%} 回撤{row['maxdd_2010']:+.1%} score{row['objective']:.3f}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {risk_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
