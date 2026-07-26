# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Explore execution responses to state-transition early warnings.

The lead-time experiment showed that upward HMM risk transitions can fire
roughly 8-9 trading days before some drawdown stress onsets. This script tests
execution-layer responses that are more realistic than continuously scaling
exposure:

* mild de-risking
* freezing scheduled rebalances
* sell-only rebalances
* no-new-buys caps
* combinations of the above

Research-only: no production signal, registry, or scheduler files are changed.

Usage:
  /usr/bin/python3 -m scripts.research.archive.state_transition_execution_explore
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
from engine.metrics import metrics
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    TriggerRule,
    evaluate_lead,
    load_risk_series,
    make_trigger_mask,
    stress_onsets,
)
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


@dataclass(frozen=True)
class ExecutionRule:
    mode: str
    floor: float = 1.0

    def label(self):
        if self.floor < 1.0:
            return f"{self.mode}_floor{self.floor:.1f}"
        return self.mode


def cooldown_mask(triggers, days):
    active = pd.Series(False, index=triggers.index)
    positions = np.flatnonzero(triggers.values)
    for pos in positions:
        end = min(len(active), pos + days + 1)
        active.iloc[pos:end] = True
    return active


def normalize_selected(selected):
    if selected is None or len(selected) == 0:
        return pd.Series(dtype=float)
    selected = selected[selected > 0].copy()
    total = float(selected.sum())
    if total <= 0:
        return pd.Series(dtype=float)
    return selected / total


def backtest_execution_overlay(close, scheduled_weights, base_timing, triggers, trigger_rule, exec_rule, config):
    daily_ret = (
        close.pct_change(fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
    )
    dates = list(close.index)
    cols = list(close.columns)
    col_idx = {c: i for i, c in enumerate(cols)}
    active = cooldown_mask(triggers.reindex(close.index).fillna(False), trigger_rule.cut_days)

    current_selected = pd.Series(dtype=float)
    current_weight = np.zeros(len(cols), dtype="float64")
    out = np.full(len(dates), np.nan)
    turnover = np.zeros(len(dates), dtype="float64")
    cost_paid = np.zeros(len(dates), dtype="float64")
    mode_counts = {"cooldown_days": int(active.sum()), "trigger_count": int(triggers.sum())}

    for i, dt in enumerate(dates):
        if i == 0:
            continue
        in_cooldown = bool(active.iloc[i])
        scheduled = scheduled_weights.get(dt)

        if scheduled is not None:
            scheduled = normalize_selected(scheduled)
            if in_cooldown and exec_rule.mode in {"freeze", "freeze_floor"}:
                pass
            elif in_cooldown and exec_rule.mode in {"sell_only", "sell_only_floor"}:
                keep = current_selected.index.intersection(scheduled.index)
                current_selected = normalize_selected(scheduled.reindex(keep).dropna())
            else:
                current_selected = scheduled

        base_exposure = float(base_timing.reindex([dt]).fillna(0.0).iloc[0])
        base_exposure = min(max(base_exposure, 0.0), 1.0)
        exposure = base_exposure
        if in_cooldown and exec_rule.mode in {
            "floor",
            "freeze_floor",
            "sell_only_floor",
            "no_new_buys_floor",
        }:
            exposure *= exec_rule.floor

        desired = np.zeros(len(cols), dtype="float64")
        if exposure > 0 and len(current_selected):
            for code, weight in current_selected.items():
                j = col_idx.get(code)
                if j is not None:
                    desired[j] = weight * exposure

        if in_cooldown and exec_rule.mode in {"no_new_buys", "no_new_buys_floor"}:
            desired = np.minimum(desired, current_weight)

        delta = desired - current_weight
        buy_turnover = float(delta[delta > 0].sum())
        sell_turnover = float((-delta[delta < 0]).sum())
        trade_cost = (
            buy_turnover * config.cost.buy_cost
            + sell_turnover * config.cost.sell_cost
        ) * config.leverage

        day_ret = np.asarray(daily_ret.iloc[i].values, dtype="float64")
        day_ret[~np.isfinite(day_ret)] = 0.0
        day_ret = np.clip(day_ret, -1.0, 10.0)
        held = desired != 0
        gross_ret = float((day_ret[held] * desired[held]).sum()) * config.leverage
        financing = 0.0
        if desired.sum() > 0 and config.leverage > 1:
            financing = (config.leverage - 1.0) * config.cost.financing_rate / 252.0

        out[i] = gross_ret - trade_cost - financing
        turnover[i] = buy_turnover + sell_turnover
        cost_paid[i] = trade_cost + financing
        current_weight = desired

    ret = pd.Series(out, index=dates).dropna()
    detail = pd.DataFrame(
        {
            "ret": ret,
            "turnover": turnover[1:],
            "cost": cost_paid[1:],
            "cooldown": active.iloc[1:].astype(float).values,
        },
        index=ret.index,
    )
    return ret, detail, mode_counts


def slice_metrics(ret, start_year):
    return metrics(ret[ret.index.year >= start_year])


def summarize(label, ret, detail, baseline_ret, extra):
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
    row["turnover_ann_2018"] = float(detail_2018["turnover"].mean() * 252)
    row["cost_ann_2018"] = float(detail_2018["cost"].mean() * 252)
    row["trade_days_2018"] = int((detail_2018["turnover"] > 1e-12).sum())
    row["cooldown_rate_2018"] = float(detail_2018["cooldown"].mean())
    row.update(extra)
    return row


def trigger_rules():
    rules = []
    for threshold in [0.20, 0.35, 0.50, 0.65]:
        for cut_days in [5, 10, 20, 30]:
            rules.append(
                TriggerRule(
                    kind="cross",
                    threshold=threshold,
                    min_delta=0.0,
                    suppress_days=max(10, cut_days),
                    cut_days=cut_days,
                    floor=0.8,
                )
            )
    for threshold in [0.20, 0.35, 0.50]:
        for min_delta in [0.08, 0.15]:
            for cut_days in [10, 20]:
                rules.append(
                    TriggerRule(
                        kind="edge",
                        threshold=threshold,
                        min_delta=min_delta,
                        suppress_days=max(10, cut_days),
                        cut_days=cut_days,
                        floor=0.8,
                    )
                )
    return rules


def execution_rules():
    return [
        ExecutionRule("floor", 0.8),
        ExecutionRule("floor", 0.5),
        ExecutionRule("freeze", 1.0),
        ExecutionRule("freeze_floor", 0.8),
        ExecutionRule("sell_only", 1.0),
        ExecutionRule("sell_only_floor", 0.8),
        ExecutionRule("no_new_buys", 1.0),
        ExecutionRule("no_new_buys_floor", 0.8),
    ]


def objective(row):
    annual_penalty = min(max(row["annual_2018"], 0.0) / 0.20, 1.0)
    dd_bonus = min(0.20 / max(abs(row["maxdd_2018"]), 1e-9), 1.4)
    lead_bonus = 1.0 + min(row.get("avg_lead_days", 0.0), 10.0) / 50.0
    return float((0.50 * row["sharpe_2018"] + 0.50 * row["calmar_2018"]) * annual_penalty * dd_bonus * lead_bonus)


def main():
    cfg = StrategyConfig(start="2010-01-01", cost=CostModel())
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    scheduled = base["scheduled_weights"]
    base_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    baseline_ret = base["returns"]
    baseline_detail = base["detail"].copy()
    baseline_detail["cooldown"] = 0.0

    risk = load_risk_series().reindex(close.index)
    onsets, _ = stress_onsets(baseline_ret, drawdown_threshold=-0.08, separation_days=20)
    rows = [
        summarize(
            "v2.0 baseline",
            baseline_ret,
            baseline_detail,
            baseline_ret,
            {
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

    for trig_rule in trigger_rules():
        triggers = make_trigger_mask(risk, trig_rule)
        lead = evaluate_lead(triggers, onsets, close.index, min_lead=1, max_lead=20)
        if lead["triggers"] == 0:
            continue
        for exec_rule in execution_rules():
            label = f"{trig_rule.label()}__{exec_rule.label()}"
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
    result_path = OUT_DIR / "state_transition_execution_explore.csv"
    summary_path = OUT_DIR / "state_transition_execution_explore_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[
        (variants["coverage"] >= 0.25)
        & (variants["precision"] >= 0.25)
        & (variants["annual_2018"] >= 0.18)
    ].copy()
    if useful.empty:
        useful = variants
    summary = {
        "baseline": rows[0],
        "best_objective": variants.iloc[0].to_dict(),
        "best_useful_calmar": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_useful_return": useful.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_low_turnover": useful.sort_values(["turnover_ann_2018", "calmar_2018"], ascending=[True, False]).iloc[0].to_dict(),
        "notes": [
            "Execution overlays are event-driven off upward HMM risk transitions.",
            "freeze means scheduled rebalance is ignored during cooldown.",
            "sell_only keeps only names already held when a scheduled rebalance arrives during cooldown.",
            "no_new_buys caps target weights at current weights during cooldown.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Top execution-layer transition responses ===", flush=True)
    cols = [
        "label",
        "coverage",
        "precision",
        "avg_lead_days",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "turnover_ann_2018",
        "cost_ann_2018",
        "trade_days_2018",
    ]
    for _, row in result[result["label"] != "v2.0 baseline"].head(16)[cols].iterrows():
        print(
            f"{row['label'][:72]:<72} "
            f"覆盖{row['coverage']:.0%} 精度{row['precision']:.0%} 领先{row['avg_lead_days']:.1f}d | "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
            f"换手{row['turnover_ann_2018']:.1f} 成本{row['cost_ann_2018']:.1%} "
            f"交易日{int(row['trade_days_2018'])}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
