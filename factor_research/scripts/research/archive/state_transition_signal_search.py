# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Search broader state-transition early-warning signals.

This expands beyond a single HMM risk probability. Candidate transition scores
include:

* cached HMM exit risk models
* cached HMM market-stress probabilities
* market breadth / volatility / liquidity pressure percentiles
* small-cap drawdown and volatility pressure percentiles
* MAX composites inspired by the LOB paper's sparse-channel aggregation

Execution is intentionally fixed to the previously promising responses
(`freeze` and `freeze_floor0.8`) so the search focuses on the transition signal.

Research-only: no production signal, registry, or scheduler files are changed.

Usage:
  /usr/bin/python3 -m scripts.research.archive.state_transition_signal_search
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
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
EXIT_DAILY = OUT_DIR / "hmm_exit_smallcap_daily.csv"
STRESS_DAILY = OUT_DIR / "hmm_stress_guard_smallcap_daily.csv"


@dataclass(frozen=True)
class SignalSpec:
    name: str
    family: str
    source: str


def rolling_percentile(s, window=252):
    def pct(x):
        last = x[-1]
        return float(np.mean(x <= last))

    return s.rolling(window, min_periods=max(60, window // 4)).apply(pct, raw=True)


def load_labeled_series(path, value_col, label_prefix=None, labels=None):
    usecols = ["date", value_col, "label"]
    found = {}
    wanted = set(labels or [])
    for chunk in pd.read_csv(path, usecols=usecols, parse_dates=["date"], chunksize=300_000):
        if labels is not None:
            chunk = chunk[chunk["label"].isin(wanted)]
        elif label_prefix is not None:
            chunk = chunk[chunk["label"].str.startswith(label_prefix)]
        if chunk.empty:
            continue
        for label, part in chunk.groupby("label"):
            found.setdefault(label, []).append(part[["date", value_col]])

    out = {}
    for label, pieces in found.items():
        s = (
            pd.concat(pieces)
            .drop_duplicates("date", keep="last")
            .set_index("date")[value_col]
            .astype("float64")
            .sort_index()
        )
        out[label] = s
    return out


def pick_stress_labels():
    labels = []
    for lookback in [756, 1008, 1260]:
        labels.append(f"hmm_stress binary lb{lookback} rt60 th0.05 floor0.0")
    return labels


def build_candidate_scores(close, amount):
    scores = {}
    specs = {}

    exit_risks = load_labeled_series(EXIT_DAILY, "risk_prob", label_prefix="hmm_risk_raw")
    for label, s in exit_risks.items():
        short = label.replace("hmm_risk_raw ", "exit_").replace(" ", "_")
        scores[short] = s
        specs[short] = SignalSpec(short, "hmm_exit", label)

    stress_probs = load_labeled_series(STRESS_DAILY, "prob_stress", labels=pick_stress_labels())
    for label, s in stress_probs.items():
        short = label.split(" th")[0].replace("hmm_stress binary ", "stress_").replace(" ", "_")
        scores[short] = s
        specs[short] = SignalSpec(short, "hmm_stress", label)

    market = build_market_features(close, amount).reindex(close.index)
    market_channels = pd.DataFrame(index=close.index)
    market_channels["mkt_low_risk_appetite"] = rolling_percentile(1.0 - market["risk_appetite"]).shift(1)
    market_channels["mkt_high_volatility"] = rolling_percentile(market["volatility"]).shift(1)
    market_channels["mkt_low_liquidity"] = rolling_percentile(1.0 / market["liquidity"].replace(0, np.nan)).shift(1)
    market_channels["mkt_low_ma_diffusion"] = rolling_percentile(1.0 - market["ma_diffusion"]).shift(1)
    for col in market_channels:
        scores[col] = market_channels[col]
        specs[col] = SignalSpec(col, "market_channel", col)
    scores["market_max_pressure"] = market_channels.max(axis=1)
    specs["market_max_pressure"] = SignalSpec("market_max_pressure", "max_composite", "market channels")

    small = make_features(close, amount, "small_market").reindex(close.index)
    small_channels = pd.DataFrame(index=close.index)
    small_channels["small_high_vol"] = rolling_percentile(small["small_vol_20d"]).shift(1)
    small_channels["small_deep_dd"] = rolling_percentile(-small["small_dd_60d"]).shift(1)
    small_channels["small_bad_5d"] = rolling_percentile(-small["small_ret_5d"]).shift(1)
    small_channels["mkt_deep_dd"] = rolling_percentile(-small["mkt_dd_60d"]).shift(1)
    small_channels["mkt_bad_5d"] = rolling_percentile(-small["mkt_ret_5d"]).shift(1)
    for col in small_channels:
        scores[col] = small_channels[col]
        specs[col] = SignalSpec(col, "small_market_channel", col)
    scores["small_market_max_pressure"] = small_channels.max(axis=1)
    specs["small_market_max_pressure"] = SignalSpec("small_market_max_pressure", "max_composite", "small/market channels")

    all_channels = pd.concat([market_channels, small_channels], axis=1)
    scores["all_max_pressure"] = all_channels.max(axis=1)
    specs["all_max_pressure"] = SignalSpec("all_max_pressure", "max_composite", "all handcrafted channels")

    return scores, specs


def trigger_from_score(score, threshold, min_delta, suppress_days, kind):
    rule = TriggerRule(
        kind=kind,
        threshold=threshold,
        min_delta=min_delta,
        suppress_days=suppress_days,
        cut_days=suppress_days,
        floor=0.8,
    )
    return make_trigger_mask(score, rule), rule


def search_grid_for_family(family):
    if family.startswith("hmm"):
        thresholds = [0.20, 0.35, 0.50, 0.65, 0.80]
    else:
        thresholds = [0.60, 0.70, 0.80, 0.90, 0.95]
    kinds = [("cross", 0.0), ("edge", 0.05), ("edge", 0.10), ("accum", 0.03), ("accum", 0.08)]
    suppress = [5, 10, 20]
    return thresholds, kinds, suppress


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

    print("Building candidate transition scores...", flush=True)
    scores, specs = build_candidate_scores(close, amount)
    exec_rules = [ExecutionRule("freeze", 1.0), ExecutionRule("freeze_floor", 0.8)]

    rows = [
        summarize(
            "v2.0 baseline",
            baseline_ret,
            baseline_detail,
            baseline_ret,
            {
                "signal": "baseline",
                "signal_family": "baseline",
                "source": "baseline",
                "trigger_rule": "baseline",
                "execution_rule": "baseline",
                "stress_events": int(len(onsets)),
                "triggers": 0,
                "coverage": np.nan,
                "precision": np.nan,
                "avg_lead_days": np.nan,
                "objective": np.nan,
            },
        )
    ]

    for name, raw_score in scores.items():
        spec = specs[name]
        score = raw_score.reindex(close.index).astype("float64").clip(0.0, 1.0)
        thresholds, kinds, suppress_values = search_grid_for_family(spec.family)
        print(f"Searching {name} ({spec.family})...", flush=True)
        for threshold in thresholds:
            for kind, min_delta in kinds:
                for suppress_days in suppress_values:
                    triggers, trig_rule = trigger_from_score(score, threshold, min_delta, suppress_days, kind)
                    lead = evaluate_lead(triggers, onsets, close.index, min_lead=1, max_lead=20)
                    if lead["triggers"] < 5:
                        continue
                    # Cheap screen before full replay.
                    if lead["coverage"] < 0.15 or lead["precision"] < 0.15:
                        continue
                    for exec_rule in exec_rules:
                        trig_rule = TriggerRule(
                            kind=kind,
                            threshold=threshold,
                            min_delta=min_delta,
                            suppress_days=suppress_days,
                            cut_days=suppress_days,
                            floor=0.8,
                        )
                        label = f"{name}__{trig_rule.label()}__{exec_rule.label()}"
                        ret, detail, counts = backtest_execution_overlay(
                            close,
                            scheduled,
                            base_timing,
                            triggers,
                            trig_rule,
                            exec_rule,
                            cfg,
                        )
                        row = summarize(
                            label,
                            ret,
                            detail,
                            baseline_ret,
                            {
                                "signal": name,
                                "signal_family": spec.family,
                                "source": spec.source,
                                "trigger_rule": trig_rule.label(),
                                "execution_rule": exec_rule.label(),
                                **asdict(trig_rule),
                                "exec_mode": exec_rule.mode,
                                "exec_floor": exec_rule.floor,
                                **lead,
                                **counts,
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
    result_path = OUT_DIR / "state_transition_signal_search.csv"
    summary_path = OUT_DIR / "state_transition_signal_search_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[
        (variants["coverage"] >= 0.25)
        & (variants["precision"] >= 0.25)
        & (variants["annual_2018"] >= 0.20)
    ].copy()
    if useful.empty:
        useful = variants
    summary = {
        "baseline": rows[0],
        "candidate_signal_count": len(scores),
        "stress_onset_count": int(len(onsets)),
        "best_objective": variants.iloc[0].to_dict(),
        "best_useful_calmar": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_useful_return": useful.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_signal_family": {
            fam: group.sort_values(["objective", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict()
            for fam, group in variants.groupby("signal_family")
        },
        "notes": [
            "Handcrafted channel percentiles are shifted one day before trigger evaluation.",
            "HMM cached risks are already shifted upstream.",
            "Execution search is fixed to freeze/freeze_floor to isolate signal quality.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top state-transition signal search results ===", flush=True)
    cols = [
        "signal",
        "signal_family",
        "trigger_rule",
        "execution_rule",
        "coverage",
        "precision",
        "avg_lead_days",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "objective",
    ]
    for _, row in result[result["label"] != "v2.0 baseline"].head(20)[cols].iterrows():
        print(
            f"{row['signal'][:28]:<28} {row['execution_rule']:<18} "
            f"覆盖{row['coverage']:.0%} 精度{row['precision']:.0%} 领先{row['avg_lead_days']:.1f}d | "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
