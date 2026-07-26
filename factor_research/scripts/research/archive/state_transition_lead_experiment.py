# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""State-transition lead-time experiment for the v2.0 small-cap strategy.

The earlier overlay experiments used HMM risk probabilities as an exposure
multiplier. This script tests the more specific hypothesis from the LOB paper:
state-transition edges should be evaluated by lead time before stress onsets.

Stress onsets are defined from realized v2.0 equity drawdown crossings. Trigger
rules are built from upward transitions in the cached HMM risk probability. The
script reports both detection quality and the result of converting each trigger
into a temporary exposure cut.

Research-only: no production signal, registry, or scheduler files are changed.

Usage:
  /usr/bin/python3 -m scripts.research.archive.state_transition_lead_experiment
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

from engine.metrics import metrics
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
EXIT_DAILY = OUT_DIR / "hmm_exit_smallcap_daily.csv"
RAW_RISK_LABEL = "hmm_risk_raw small_market lb1260 rt60"


@dataclass(frozen=True)
class TriggerRule:
    kind: str
    threshold: float
    min_delta: float = 0.0
    suppress_days: int = 10
    cut_days: int = 10
    floor: float = 0.5

    def label(self):
        return (
            f"{self.kind}_th{self.threshold:.2f}_d{self.min_delta:.2f}"
            f"_sup{self.suppress_days}_cut{self.cut_days}_floor{self.floor:.1f}"
        )


def load_risk_series():
    usecols = ["date", "risk_prob", "label"]
    pieces = []
    for chunk in pd.read_csv(EXIT_DAILY, usecols=usecols, parse_dates=["date"], chunksize=250_000):
        part = chunk.loc[chunk["label"] == RAW_RISK_LABEL, ["date", "risk_prob"]]
        if not part.empty:
            pieces.append(part)
    if not pieces:
        raise ValueError(f"label not found in {EXIT_DAILY.name}: {RAW_RISK_LABEL}")
    return (
        pd.concat(pieces)
        .drop_duplicates("date", keep="last")
        .set_index("date")["risk_prob"]
        .astype("float64")
        .sort_index()
    )


def stress_onsets(ret, drawdown_threshold=-0.08, separation_days=20):
    nav = (1.0 + ret.fillna(0.0)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    raw = drawdown[(drawdown <= drawdown_threshold) & (drawdown.shift(1).fillna(0.0) > drawdown_threshold)].index
    onsets = []
    last_pos = -10**9
    dates = ret.index
    for dt in raw:
        pos = dates.get_loc(dt)
        if pos - last_pos >= separation_days:
            onsets.append(dt)
            last_pos = pos
    return pd.DatetimeIndex(onsets), drawdown


def make_trigger_mask(risk, rule):
    r = risk.fillna(0.0).clip(0.0, 1.0)
    diff = r.diff().fillna(0.0)
    if rule.kind == "cross":
        raw = (r >= rule.threshold) & (r.shift(1).fillna(0.0) < rule.threshold)
    elif rule.kind == "edge":
        raw = (r >= rule.threshold) & (diff >= rule.min_delta) & (diff.shift(1).fillna(0.0) <= rule.min_delta)
    elif rule.kind == "accum":
        smooth = r.rolling(3).mean()
        raw = (
            (smooth >= rule.threshold)
            & (smooth.diff().fillna(0.0) >= rule.min_delta)
            & (smooth.diff().shift(1).fillna(0.0) <= rule.min_delta)
        )
    else:
        raise ValueError(f"unknown trigger kind: {rule.kind}")

    out = pd.Series(False, index=r.index)
    last_pos = -10**9
    for pos, flag in enumerate(raw.fillna(False).values):
        if flag and pos - last_pos >= rule.suppress_days:
            out.iloc[pos] = True
            last_pos = pos
    return out


def evaluate_lead(triggers, onsets, dates, min_lead=1, max_lead=20):
    trigger_dates = pd.DatetimeIndex(triggers[triggers].index)
    covered = 0
    lead_days = []
    for onset in onsets:
        onset_pos = dates.get_loc(onset)
        lo = max(0, onset_pos - max_lead)
        hi = max(0, onset_pos - min_lead)
        window = dates[lo : hi + 1]
        hits = trigger_dates.intersection(window)
        if len(hits):
            covered += 1
            lead_days.append(onset_pos - dates.get_loc(hits[-1]))

    useful = 0
    for trigger in trigger_dates:
        pos = dates.get_loc(trigger)
        future = dates[pos + min_lead : min(len(dates), pos + max_lead + 1)]
        if len(onsets.intersection(future)):
            useful += 1

    n_triggers = int(triggers.sum())
    return {
        "stress_events": int(len(onsets)),
        "triggers": n_triggers,
        "coverage": covered / len(onsets) if len(onsets) else 0.0,
        "precision": useful / n_triggers if n_triggers else 0.0,
        "avg_lead_days": float(np.mean(lead_days)) if lead_days else 0.0,
        "median_lead_days": float(np.median(lead_days)) if lead_days else 0.0,
    }


def exposure_from_triggers(triggers, rule):
    exposure = pd.Series(1.0, index=triggers.index, dtype="float64")
    trigger_positions = np.flatnonzero(triggers.values)
    for pos in trigger_positions:
        end = min(len(exposure), pos + rule.cut_days + 1)
        exposure.iloc[pos:end] = np.minimum(exposure.iloc[pos:end], rule.floor)
    return exposure


def slice_metrics(ret, start_year):
    return metrics(ret[ret.index.year >= start_year])


def summarize_backtest(label, ret, detail, baseline_ret, timing, exposure):
    row = {"label": label}
    for year in [2018, 2023, 2010]:
        m = slice_metrics(ret, year)
        suffix = str(year)
        row[f"annual_{suffix}"] = m["annual"]
        row[f"maxdd_{suffix}"] = m["maxdd"]
        row[f"sharpe_{suffix}"] = m["sharpe"]
        row[f"calmar_{suffix}"] = m["calmar"]

    ret_2018 = ret[ret.index.year >= 2018]
    base_2018 = baseline_ret[baseline_ret.index.year >= 2018]
    detail_2018 = detail[detail.index.year >= 2018]
    row["corr_v2_2018"] = ret_2018.corr(base_2018)
    row["timing_on_rate_2018"] = float(timing[timing.index.year >= 2018].mean())
    row["overlay_exposure_2018"] = float(exposure[exposure.index.year >= 2018].mean())
    row["turnover_ann_2018"] = float(detail_2018["turnover"].mean() * 252)
    row["cost_ann_2018"] = float(detail_2018["cost"].mean() * 252)
    row["trade_days_2018"] = int((detail_2018["turnover"] > 1e-12).sum())
    return row


def build_rules():
    rules = []
    for kind in ["cross", "edge", "accum"]:
        for threshold in [0.20, 0.35, 0.50, 0.65, 0.80]:
            deltas = [0.0] if kind == "cross" else [0.03, 0.08, 0.15]
            for min_delta in deltas:
                for cut_days in [5, 10, 20]:
                    for floor in [0.0, 0.5, 0.8]:
                        rules.append(
                            TriggerRule(
                                kind=kind,
                                threshold=threshold,
                                min_delta=min_delta,
                                suppress_days=max(10, cut_days),
                                cut_days=cut_days,
                                floor=floor,
                            )
                        )
    return rules


def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    scheduled = base["scheduled_weights"]
    baseline_ret = base["returns"]
    smallcap_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    base_detail = base["detail"]

    risk = load_risk_series().reindex(close.index)
    onsets, drawdown = stress_onsets(baseline_ret, drawdown_threshold=-0.08, separation_days=20)
    print(f"Stress onsets: {len(onsets)}", flush=True)

    baseline_row = summarize_backtest(
        "v2.0 baseline",
        baseline_ret,
        base_detail,
        baseline_ret,
        smallcap_timing,
        pd.Series(1.0, index=close.index),
    )
    baseline_row.update(
        {
            "rule": "baseline",
            "stress_events": int(len(onsets)),
            "triggers": 0,
            "coverage": np.nan,
            "precision": np.nan,
            "avg_lead_days": np.nan,
            "median_lead_days": np.nan,
            "lead_objective": np.nan,
        }
    )

    rows = [baseline_row]
    trigger_frames = []
    for rule in build_rules():
        triggers = make_trigger_mask(risk, rule)
        lead = evaluate_lead(triggers, onsets, close.index, min_lead=1, max_lead=20)
        if lead["triggers"] == 0:
            continue

        exposure = exposure_from_triggers(triggers, rule)
        timing = smallcap_timing * exposure
        ret, detail = backtest_weights(close, scheduled, timing, cfg)
        row = summarize_backtest(rule.label(), ret, detail, baseline_ret, timing, exposure)
        row.update(asdict(rule))
        row.update(lead)
        row["rule"] = rule.label()
        row["lead_objective"] = (
            lead["coverage"]
            * max(lead["avg_lead_days"], 1.0)
            * min(lead["precision"] / 0.20, 1.0)
        )
        rows.append(row)
        trigger_frames.append(
            pd.DataFrame(
                {
                    "date": close.index,
                    "risk_prob": risk.values,
                    "trigger": triggers.values,
                    "overlay_exposure": exposure.values,
                    "baseline_drawdown": drawdown.reindex(close.index).values,
                    "rule": rule.label(),
                }
            )
        )

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
        ["lead_objective", "calmar_2018", "annual_2018"],
        ascending=[False, False, False],
        na_position="last",
    )
    result_path = OUT_DIR / "state_transition_lead_experiment.csv"
    triggers_path = OUT_DIR / "state_transition_lead_experiment_triggers.csv"
    summary_path = OUT_DIR / "state_transition_lead_experiment_summary.json"
    result.to_csv(result_path, index=False)
    pd.concat(trigger_frames, ignore_index=True).to_csv(triggers_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[(variants["coverage"] >= 0.25) & (variants["precision"] >= 0.15)].copy()
    if useful.empty:
        useful = variants
    summary = {
        "baseline": baseline_row,
        "stress_onset_definition": "v2.0 equity drawdown crosses below -8%, with 20 trading-day separation.",
        "lead_window": "Trigger counts as early if it fires 1-20 trading days before a stress onset.",
        "best_by_lead_objective": variants.iloc[0].to_dict(),
        "best_useful_by_calmar_2018": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_useful_by_return_2018": useful.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "notes": [
            "Risk probabilities come from the existing shifted HMM exit artifact, so the trigger avoids same-day look-ahead.",
            "The stress label is based on strategy equity drawdown, not exchange LOB labels.",
            "This tests transition/onset logic separately from continuous probability scaling.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top state-transition lead rules ===", flush=True)
    cols = [
        "label",
        "coverage",
        "precision",
        "avg_lead_days",
        "triggers",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "lead_objective",
    ]
    for _, row in result[result["label"] != "v2.0 baseline"].head(15)[cols].iterrows():
        print(
            f"{row['label'][:52]:<52} "
            f"覆盖{row['coverage']:.0%} 精度{row['precision']:.0%} "
            f"领先{row['avg_lead_days']:.1f}d 触发{int(row['triggers'])} | "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f}",
            flush=True,
        )

    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {triggers_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
