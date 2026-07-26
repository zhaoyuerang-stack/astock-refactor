# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Second-pass discovery of broader state-transition signals.

The first search found market breadth deterioration useful. This pass explores
more transition-shaped signals rather than only adverse state levels:

* breadth erosion over 3/5/10/20 trading days
* breadth drawdown from rolling highs
* erosion acceleration
* liquidity/risk-appetite erosion
* small-cap vs market divergence
* sparse MAX composites across these families

Signals are evaluated by lead-time first, then replayed with fixed execution
responses (`freeze` and `freeze_floor0.8`) for the best lead candidates.

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.state_transition_signal_discovery_v2
"""
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from core.engine import CostModel
from scripts.research.archive.hmm_exit_smallcap import make_features  # noqa: E402
from scripts.research.archive.hmm_stress_guard_smallcap import build_market_features  # noqa: E402
from scripts.research.archive.state_transition_execution_explore import (  # noqa: E402
    ExecutionRule,
    backtest_execution_overlay,
    objective,
    summarize,
)
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    TriggerRule,
    evaluate_lead,
    make_trigger_mask,
    stress_onsets,
)
from scripts.research.archive.state_transition_signal_search import rolling_percentile  # noqa: E402
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


def pct_score(s, window=252):
    return rolling_percentile(s.replace([np.inf, -np.inf], np.nan), window=window).shift(1).clip(0.0, 1.0)


def positive(x):
    return x.clip(lower=0.0)


def build_scores(close, amount):
    market = build_market_features(close, amount).reindex(close.index)
    small = make_features(close, amount, "small_market").reindex(close.index)

    scores = {}
    families = {}

    breadth = market["ma_diffusion"].astype(float)
    low_breadth = 1.0 - breadth
    scores["breadth_low_level"] = pct_score(low_breadth)
    families["breadth_low_level"] = "breadth_level"

    for n in [3, 5, 10, 20]:
        drop = positive(breadth.shift(n) - breadth)
        scores[f"breadth_drop_{n}d"] = pct_score(drop)
        families[f"breadth_drop_{n}d"] = "breadth_erosion"

        drop_rate = drop / max(n, 1)
        scores[f"breadth_drop_rate_{n}d"] = pct_score(drop_rate)
        families[f"breadth_drop_rate_{n}d"] = "breadth_erosion"

    for n in [20, 60, 120]:
        dd = positive(breadth.rolling(n).max() - breadth)
        scores[f"breadth_dd_{n}d"] = pct_score(dd)
        families[f"breadth_dd_{n}d"] = "breadth_drawdown"

    drop5 = positive(breadth.shift(5) - breadth)
    drop10 = positive(breadth.shift(10) - breadth)
    scores["breadth_accel_5v10"] = pct_score(positive(drop5 - drop10.shift(5) / 2.0))
    families["breadth_accel_5v10"] = "breadth_acceleration"

    risk = market["risk_appetite"].astype(float)
    liquidity = market["liquidity"].astype(float)
    vol = market["volatility"].astype(float)
    for n in [5, 10, 20]:
        scores[f"risk_appetite_drop_{n}d"] = pct_score(positive(risk.shift(n) - risk))
        families[f"risk_appetite_drop_{n}d"] = "risk_appetite"
        scores[f"liquidity_drop_{n}d"] = pct_score(positive(liquidity.shift(n) - liquidity))
        families[f"liquidity_drop_{n}d"] = "liquidity"
        scores[f"vol_jump_{n}d"] = pct_score(positive(vol - vol.shift(n)))
        families[f"vol_jump_{n}d"] = "volatility"

    small_ret5 = small["small_ret_5d"].astype(float)
    mkt_ret5 = small["mkt_ret_5d"].astype(float)
    small_dd = small["small_dd_60d"].astype(float)
    mkt_dd = small["mkt_dd_60d"].astype(float)
    scores["small_bad_5d"] = pct_score(-small_ret5)
    families["small_bad_5d"] = "small_pressure"
    scores["small_vs_mkt_underperform"] = pct_score(positive(mkt_ret5 - small_ret5))
    families["small_vs_mkt_underperform"] = "small_divergence"
    scores["small_dd_vs_mkt_dd"] = pct_score(positive((-small_dd) - (-mkt_dd)))
    families["small_dd_vs_mkt_dd"] = "small_divergence"

    # Sparse MAX composites: allow one strong channel to trigger.
    composite_sets = {
        "breadth_erosion_max": [k for k, v in families.items() if v in {"breadth_erosion", "breadth_acceleration"}],
        "breadth_all_max": [k for k, v in families.items() if v.startswith("breadth")],
        "market_internal_max": [
            k
            for k, v in families.items()
            if v in {"breadth_erosion", "breadth_drawdown", "risk_appetite", "liquidity", "volatility"}
        ],
        "small_divergence_max": [k for k, v in families.items() if v in {"small_pressure", "small_divergence"}],
        "all_transition_max": list(scores.keys()),
    }
    for name, keys in composite_sets.items():
        scores[name] = pd.concat([scores[k] for k in keys], axis=1).max(axis=1)
        families[name] = "max_composite"

    return scores, families


def trigger_grid(family):
    thresholds = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]
    if family == "max_composite":
        thresholds = [0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    kinds = [("edge", 0.02), ("edge", 0.05), ("edge", 0.08), ("accum", 0.02), ("accum", 0.05), ("cross", 0.0)]
    cooldowns = [5, 10, 15, 20, 30]
    return thresholds, kinds, cooldowns


def json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def main():
    cfg = StrategyConfig(start="2010-01-01", cost=CostModel())
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    amount = base["amount"]
    scheduled = base["scheduled_weights"]
    base_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    baseline_ret = base["returns"]
    baseline_detail = base["detail"].copy()
    baseline_detail["cooldown"] = 0.0
    onsets, _ = stress_onsets(baseline_ret, drawdown_threshold=-0.08, separation_days=20)

    print("Building transition scores...", flush=True)
    scores, families = build_scores(close, amount)

    lead_rows = []
    candidates = []
    for signal, score in scores.items():
        family = families[signal]
        print(f"Lead scan {signal} ({family})...", flush=True)
        thresholds, kinds, cooldowns = trigger_grid(family)
        s = score.reindex(close.index).fillna(0.0).clip(0.0, 1.0)
        for threshold in thresholds:
            for kind, delta in kinds:
                for cooldown in cooldowns:
                    rule = TriggerRule(kind, threshold, delta, cooldown, cooldown, 0.8)
                    triggers = make_trigger_mask(s, rule)
                    lead = evaluate_lead(triggers, onsets, close.index, min_lead=1, max_lead=20)
                    if lead["triggers"] < 8:
                        continue
                    lead_score = (
                        lead["coverage"]
                        * max(lead["avg_lead_days"], 1.0)
                        * min(lead["precision"] / 0.25, 1.2)
                        * min(120.0 / max(lead["triggers"], 1), 1.2)
                    )
                    lead_row = {
                        "signal": signal,
                        "family": family,
                        "trigger_rule": rule.label(),
                        **asdict(rule),
                        **lead,
                        "lead_score": lead_score,
                    }
                    lead_rows.append(lead_row)
                    if lead["coverage"] >= 0.20 and lead["precision"] >= 0.15:
                        candidates.append((lead_score, signal, family, rule, triggers, lead))

    candidates = sorted(candidates, key=lambda x: x[0], reverse=True)[:160]
    print(f"Replay candidates: {len(candidates)}", flush=True)

    rows = [
        summarize(
            "v2.0 baseline",
            baseline_ret,
            baseline_detail,
            baseline_ret,
            {
                "signal": "baseline",
                "family": "baseline",
                "trigger_rule": "baseline",
                "execution_rule": "baseline",
                "stress_events": int(len(onsets)),
                "triggers": 0,
                "coverage": np.nan,
                "precision": np.nan,
                "avg_lead_days": np.nan,
                "lead_score": np.nan,
                "objective": np.nan,
            },
        )
    ]
    exec_rules = [ExecutionRule("freeze", 1.0), ExecutionRule("freeze_floor", 0.8)]
    for lead_score, signal, family, rule, triggers, lead in candidates:
        for exec_rule in exec_rules:
            label = f"{signal}__{rule.label()}__{exec_rule.label()}"
            ret, detail, counts = backtest_execution_overlay(
                close,
                scheduled,
                base_timing,
                triggers,
                rule,
                exec_rule,
                cfg,
            )
            row = summarize(
                label,
                ret,
                detail,
                baseline_ret,
                {
                    "signal": signal,
                    "family": family,
                    "trigger_rule": rule.label(),
                    "execution_rule": exec_rule.label(),
                    **asdict(rule),
                    "exec_mode": exec_rule.mode,
                    "exec_floor": exec_rule.floor,
                    **lead,
                    **counts,
                    "lead_score": lead_score,
                },
            )
            row["objective"] = objective(row)
            rows.append(row)

    result = pd.DataFrame(rows)
    base_row = result[result["label"] == "v2.0 baseline"].iloc[0]
    for col in [
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "turnover_ann_2018",
        "cost_ann_2018",
        "trade_days_2018",
    ]:
        result[f"delta_{col}"] = result[col] - base_row[col]

    result = result.sort_values(
        ["objective", "calmar_2018", "annual_2018"],
        ascending=[False, False, False],
        na_position="last",
    )
    lead_df = pd.DataFrame(lead_rows).sort_values(["lead_score", "coverage", "precision"], ascending=False)

    result_path = OUT_DIR / "state_transition_signal_discovery_v2.csv"
    lead_path = OUT_DIR / "state_transition_signal_discovery_v2_leads.csv"
    summary_path = OUT_DIR / "state_transition_signal_discovery_v2_summary.json"
    result.to_csv(result_path, index=False)
    lead_df.to_csv(lead_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[
        (variants["coverage"] >= 0.25)
        & (variants["precision"] >= 0.18)
        & (variants["annual_2018"] >= 0.20)
    ].copy()
    if useful.empty:
        useful = variants
    summary = {
        "baseline": rows[0],
        "signal_count": len(scores),
        "candidate_count": len(candidates),
        "best_objective": variants.iloc[0].to_dict(),
        "best_useful_calmar": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_useful_return": useful.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_family": {
            fam: group.sort_values(["objective", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict()
            for fam, group in variants.groupby("family")
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print("\n=== Top transition signal discovery v2 ===", flush=True)
    cols = [
        "signal",
        "family",
        "trigger_rule",
        "execution_rule",
        "coverage",
        "precision",
        "avg_lead_days",
        "triggers",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
    ]
    for _, row in variants.head(20)[cols].iterrows():
        print(
            f"{row['signal'][:28]:<28} {row['execution_rule']:<18} "
            f"覆盖{row['coverage']:.0%} 精度{row['precision']:.0%} 领先{row['avg_lead_days']:.1f}d "
            f"触发{int(row['triggers'])} | 年化{row['annual_2018']:+.1%} "
            f"回撤{row['maxdd_2018']:+.1%} 夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {lead_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
