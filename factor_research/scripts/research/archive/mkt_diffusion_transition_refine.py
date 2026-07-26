# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Refine the market-breadth deterioration transition signal.

Focus signal:
  mkt_low_ma_diffusion = rolling percentile of (1 - market MA20 diffusion)

The broad signal search found this transition useful. This refinement sweeps:

* edge/cross/accum trigger thresholds around the winning region
* cooldown/cut windows
* freeze vs freeze_floor execution
* optional confirmation channels to improve precision

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.mkt_diffusion_transition_refine
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
from scripts.research.archive.state_transition_signal_search import rolling_percentile  # noqa: E402
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


@dataclass(frozen=True)
class ConfirmRule:
    name: str
    threshold: float = 0.0
    lookback: int = 0

    def label(self):
        if self.name == "none":
            return "confirm_none"
        return f"confirm_{self.name}_th{self.threshold:.2f}_lb{self.lookback}"


def build_channels(close, amount):
    market = build_market_features(close, amount).reindex(close.index)
    small = make_features(close, amount, "small_market").reindex(close.index)

    channels = pd.DataFrame(index=close.index)
    channels["mkt_low_ma_diffusion"] = rolling_percentile(1.0 - market["ma_diffusion"]).shift(1)
    channels["mkt_low_liquidity"] = rolling_percentile(1.0 / market["liquidity"].replace(0, np.nan)).shift(1)
    channels["mkt_high_volatility"] = rolling_percentile(market["volatility"]).shift(1)
    channels["mkt_low_risk_appetite"] = rolling_percentile(1.0 - market["risk_appetite"]).shift(1)
    channels["small_bad_5d"] = rolling_percentile(-small["small_ret_5d"]).shift(1)
    channels["small_deep_dd"] = rolling_percentile(-small["small_dd_60d"]).shift(1)
    channels["confirm_max"] = channels[
        ["mkt_low_liquidity", "mkt_high_volatility", "mkt_low_risk_appetite", "small_bad_5d"]
    ].max(axis=1)
    return channels.clip(0.0, 1.0)


def apply_confirm(triggers, channels, confirm):
    if confirm.name == "none":
        return triggers
    series = channels[confirm.name].fillna(0.0)
    if confirm.lookback <= 0:
        ok = series >= confirm.threshold
    else:
        ok = series.rolling(confirm.lookback + 1, min_periods=1).max() >= confirm.threshold
    return triggers & ok.reindex(triggers.index).fillna(False)


def trigger_rules():
    rules = []
    for kind in ["edge", "accum"]:
        for threshold in np.arange(0.50, 0.86, 0.05):
            for min_delta in [0.02, 0.04, 0.05, 0.07, 0.10, 0.15]:
                for cut_days in [5, 10, 15, 20, 30]:
                    rules.append(
                        TriggerRule(
                            kind=kind,
                            threshold=float(threshold),
                            min_delta=float(min_delta),
                            suppress_days=cut_days,
                            cut_days=cut_days,
                            floor=0.8,
                        )
                    )
    for threshold in np.arange(0.50, 0.91, 0.05):
        for cut_days in [5, 10, 15, 20, 30]:
            rules.append(
                TriggerRule(
                    kind="cross",
                    threshold=float(threshold),
                    min_delta=0.0,
                    suppress_days=cut_days,
                    cut_days=cut_days,
                    floor=0.8,
                )
            )
    return rules


def confirm_rules():
    rules = [ConfirmRule("none")]
    for name in [
        "mkt_low_liquidity",
        "mkt_high_volatility",
        "mkt_low_risk_appetite",
        "small_bad_5d",
        "confirm_max",
    ]:
        for threshold in [0.60, 0.70, 0.80, 0.90]:
            for lookback in [0, 3, 5]:
                rules.append(ConfirmRule(name, threshold, lookback))
    return rules


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

    print("Building breadth/confirmation channels...", flush=True)
    channels = build_channels(close, amount)
    score = channels["mkt_low_ma_diffusion"]
    exec_rules = [
        ExecutionRule("freeze", 1.0),
        ExecutionRule("freeze_floor", 0.8),
    ]

    rows = [
        summarize(
            "v2.0 baseline",
            baseline_ret,
            baseline_detail,
            baseline_ret,
            {
                "trigger_rule": "baseline",
                "confirm_rule": "baseline",
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

    trigger_cache = {}
    for trig_rule in trigger_rules():
        raw_triggers = make_trigger_mask(score, trig_rule)
        if raw_triggers.sum() < 5:
            continue
        trigger_cache[trig_rule.label()] = raw_triggers

    print(f"Trigger candidates: {len(trigger_cache)}", flush=True)
    lead_candidates = []
    for trig_rule in trigger_rules():
        raw_triggers = trigger_cache.get(trig_rule.label())
        if raw_triggers is None:
            continue
        for confirm in confirm_rules():
            triggers = apply_confirm(raw_triggers, channels, confirm)
            lead = evaluate_lead(triggers, onsets, close.index, min_lead=1, max_lead=20)
            if lead["triggers"] < 8:
                continue
            if lead["coverage"] < 0.20 or lead["precision"] < 0.15:
                continue
            lead_score = (
                lead["coverage"]
                * max(lead["avg_lead_days"], 1.0)
                * min(lead["precision"] / 0.25, 1.2)
                * min(120.0 / max(lead["triggers"], 1), 1.2)
            )
            lead_candidates.append(
                {
                    "trig_rule": trig_rule,
                    "confirm": confirm,
                    "triggers": triggers,
                    "lead": lead,
                    "lead_score": lead_score,
                }
            )

    lead_candidates = sorted(
        lead_candidates,
        key=lambda x: (
            x["lead_score"],
            x["lead"]["coverage"],
            x["lead"]["precision"],
            x["lead"]["avg_lead_days"],
        ),
        reverse=True,
    )[:120]
    print(f"Replay candidates after lead screen: {len(lead_candidates)}", flush=True)

    for candidate in lead_candidates:
        trig_rule = candidate["trig_rule"]
        confirm = candidate["confirm"]
        triggers = candidate["triggers"]
        lead = candidate["lead"]
        for exec_rule in exec_rules:
            label = f"{trig_rule.label()}__{confirm.label()}__{exec_rule.label()}"
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
                    "signal": "mkt_low_ma_diffusion",
                    "trigger_rule": trig_rule.label(),
                    "confirm_rule": confirm.label(),
                    "execution_rule": exec_rule.label(),
                    **asdict(trig_rule),
                    "confirm_name": confirm.name,
                    "confirm_threshold": confirm.threshold,
                    "confirm_lookback": confirm.lookback,
                    "exec_mode": exec_rule.mode,
                    "exec_floor": exec_rule.floor,
                    **lead,
                    **counts,
                    "lead_score": candidate["lead_score"],
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
    result_path = OUT_DIR / "mkt_diffusion_transition_refine.csv"
    summary_path = OUT_DIR / "mkt_diffusion_transition_refine_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[
        (variants["coverage"] >= 0.30)
        & (variants["precision"] >= 0.18)
        & (variants["annual_2018"] >= 0.20)
    ].copy()
    if useful.empty:
        useful = variants
    higher_precision = variants[
        (variants["precision"] >= 0.25)
        & (variants["annual_2018"] >= 0.20)
    ].copy()
    if higher_precision.empty:
        higher_precision = useful

    summary = {
        "baseline": rows[0],
        "stress_onset_count": int(len(onsets)),
        "rows": int(len(result)),
        "best_objective": variants.iloc[0].to_dict(),
        "best_useful_calmar": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_useful_return": useful.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_higher_precision": higher_precision.sort_values(["objective", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_confirm": {
            name: group.sort_values(["objective", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict()
            for name, group in variants.groupby("confirm_name")
        },
        "notes": [
            "Primary score is shifted rolling percentile of market breadth deterioration.",
            "Confirmation channels are also shifted and may use recent lookback max.",
            "This is a focused refinement of the best signal from state_transition_signal_search.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top mkt_low_ma_diffusion refinements ===", flush=True)
    cols = [
        "trigger_rule",
        "confirm_rule",
        "execution_rule",
        "coverage",
        "precision",
        "avg_lead_days",
        "triggers",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "turnover_ann_2018",
        "cost_ann_2018",
    ]
    for _, row in variants.head(20)[cols].iterrows():
        print(
            f"{row['trigger_rule'][:34]:<34} {row['confirm_rule'][:34]:<34} "
            f"{row['execution_rule']:<18} 覆盖{row['coverage']:.0%} 精度{row['precision']:.0%} "
            f"领先{row['avg_lead_days']:.1f}d 触发{int(row['triggers'])} | "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
