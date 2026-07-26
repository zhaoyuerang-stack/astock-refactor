# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Combination-trigger experiments for breadth_dd_20d.

Goal:
  Keep the drawdown benefit of breadth_dd_20d while reducing drag in trend years
  such as 2013, 2016, and 2021.

Main trigger:
  breadth_dd_20d edge threshold=0.70, delta=0.02.

Confirmation / tiering candidates:
  liquidity_drop_20d
  risk_appetite_drop_10d / 20d

Execution candidates:
  * confirmed only: only trigger when confirmation is active
  * tiered: unconfirmed triggers freeze only, confirmed triggers freeze+floor0.8
  * tiered strict: unconfirmed triggers freeze for a shorter window

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.breadth_dd20_combo_trigger
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
from engine.metrics import metrics, yearly_returns
from scripts.research.archive.breadth_dd20_mainline_validation import (  # noqa: E402
    FIXED_TRIGGER,
    build_breadth_dd20_score,
)
from scripts.research.archive.hmm_stress_guard_smallcap import build_market_features  # noqa: E402
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    evaluate_lead,
    make_trigger_mask,
    stress_onsets,
)
from scripts.research.archive.state_transition_signal_search import rolling_percentile  # noqa: E402
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


@dataclass(frozen=True)
class ComboRule:
    confirm_signal: str
    confirm_threshold: float
    confirm_lookback: int
    mode: str
    weak_cut_days: int = 10
    strong_cut_days: int = 20
    weak_floor: float = 1.0
    strong_floor: float = 0.8

    def label(self):
        return (
            f"{self.mode}_{self.confirm_signal}_th{self.confirm_threshold:.2f}"
            f"_lb{self.confirm_lookback}_weak{self.weak_cut_days}f{self.weak_floor:.1f}"
            f"_strong{self.strong_cut_days}f{self.strong_floor:.1f}"
        )


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


def pct_score(s):
    return rolling_percentile(s.replace([np.inf, -np.inf], np.nan), window=252).shift(1).clip(0.0, 1.0)


def build_confirm_scores(close, amount):
    market = build_market_features(close, amount).reindex(close.index)
    risk = market["risk_appetite"].astype(float)
    liquidity = market["liquidity"].astype(float)
    scores = pd.DataFrame(index=close.index)
    for n in [10, 20]:
        scores[f"risk_appetite_drop_{n}d"] = pct_score((risk.shift(n) - risk).clip(lower=0.0))
    for n in [10, 20]:
        scores[f"liquidity_drop_{n}d"] = pct_score((liquidity.shift(n) - liquidity).clip(lower=0.0))
    scores["confirm_max"] = scores.max(axis=1)
    return scores


def rolling_confirm(score, threshold, lookback):
    ok = score.fillna(0.0) >= threshold
    if lookback <= 0:
        return ok
    return ok.rolling(lookback + 1, min_periods=1).max().astype(bool)


def cooldown_exposure(triggers, cut_days, floor):
    exposure = pd.Series(1.0, index=triggers.index, dtype="float64")
    for pos in np.flatnonzero(triggers.values):
        end = min(len(exposure), pos + cut_days + 1)
        exposure.iloc[pos:end] = np.minimum(exposure.iloc[pos:end], floor)
    return exposure


def make_combo_exposure(main_triggers, confirm, rule):
    strong = main_triggers & confirm.reindex(main_triggers.index).fillna(False)
    weak = main_triggers & ~strong
    if rule.mode == "confirmed_only":
        return cooldown_exposure(strong, rule.strong_cut_days, rule.strong_floor), strong, weak
    if rule.mode == "tiered":
        weak_exp = cooldown_exposure(weak, rule.weak_cut_days, rule.weak_floor)
        strong_exp = cooldown_exposure(strong, rule.strong_cut_days, rule.strong_floor)
        return pd.concat([weak_exp, strong_exp], axis=1).min(axis=1), strong, weak
    if rule.mode == "strict_tiered":
        weak_exp = cooldown_exposure(weak, rule.weak_cut_days, rule.weak_floor)
        strong_exp = cooldown_exposure(strong, rule.strong_cut_days, rule.strong_floor)
        return pd.concat([weak_exp, strong_exp], axis=1).min(axis=1), strong, weak
    raise ValueError(f"unknown mode: {rule.mode}")


def apply_freeze_overlay(close, scheduled_weights, base_timing, triggers, exposure, config):
    """Freeze scheduled rebalances while exposure cooldown is active."""
    active = exposure < 0.999999
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    dates = list(close.index)
    cols = list(close.columns)
    col_idx = {c: i for i, c in enumerate(cols)}
    current_selected = pd.Series(dtype=float)
    current_weight = np.zeros(len(cols), dtype="float64")
    out = np.full(len(dates), np.nan)
    turnover = np.zeros(len(dates), dtype="float64")
    cost_paid = np.zeros(len(dates), dtype="float64")

    for i, dt in enumerate(dates):
        if i == 0:
            continue
        scheduled = scheduled_weights.get(dt)
        if scheduled is not None and not bool(active.iloc[i]):
            selected = scheduled[scheduled > 0].copy()
            total = float(selected.sum())
            current_selected = selected / total if total > 0 else pd.Series(dtype=float)

        base_exp = float(base_timing.reindex([dt]).fillna(0.0).iloc[0])
        exp = min(max(base_exp * float(exposure.iloc[i]), 0.0), 1.0)
        desired = np.zeros(len(cols), dtype="float64")
        if exp > 0 and len(current_selected):
            for code, weight in current_selected.items():
                j = col_idx.get(code)
                if j is not None:
                    desired[j] = weight * exp

        delta = desired - current_weight
        buy_turnover = float(delta[delta > 0].sum())
        sell_turnover = float((-delta[delta < 0]).sum())
        trade_cost = (buy_turnover * config.cost.buy_cost + sell_turnover * config.cost.sell_cost) * config.leverage
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
    return ret, detail


def row_for(label, ret, detail, baseline_ret, triggers, strong, weak, exposure):
    row = {"label": label}
    for year in [2018, 2023, 2010]:
        m = metrics(ret[ret.index.year >= year])
        row[f"annual_{year}"] = m["annual"]
        row[f"maxdd_{year}"] = m["maxdd"]
        row[f"sharpe_{year}"] = m["sharpe"]
        row[f"calmar_{year}"] = m["calmar"]
    detail_2018 = detail[detail.index.year >= 2018]
    row.update(
        {
            "turnover_ann_2018": float(detail_2018["turnover"].mean() * 252),
            "cost_ann_2018": float(detail_2018["cost"].mean() * 252),
            "cooldown_rate_2018": float(detail_2018["cooldown"].mean()),
            "trigger_count": int(triggers.sum()),
            "strong_count": int(strong.sum()),
            "weak_count": int(weak.sum()),
            "avg_exposure_2018": float(exposure[exposure.index.year >= 2018].mean()),
        }
    )
    onsets, _ = stress_onsets(baseline_ret, drawdown_threshold=-0.08, separation_days=20)
    row.update(evaluate_lead(triggers, onsets, baseline_ret.index, min_lead=1, max_lead=20))
    return row


def yearly_drag_score(base_ret, overlay_ret):
    base_y = yearly_returns(base_ret)
    over_y = yearly_returns(overlay_ret)
    years = sorted(set(base_y.index).intersection(over_y.index))
    deltas = pd.Series({y: float(over_y.loc[y] - base_y.loc[y]) for y in years})
    drag_years = [2013, 2016, 2021]
    return {
        "winning_years": int((deltas > 0).sum()),
        "losing_years": int((deltas <= 0).sum()),
        "drag_2013_2016_2021": float(deltas.reindex(drag_years).sum()),
        "delta_2019": float(deltas.get(2019, np.nan)),
        "delta_2018": float(deltas.get(2018, np.nan)),
        "delta_2022_2026": float(deltas[deltas.index >= 2022].sum()),
        "worst_year": int(deltas.idxmin()),
        "worst_delta": float(deltas.min()),
    }


def combo_rules():
    rules = []
    for signal in ["liquidity_drop_20d", "risk_appetite_drop_10d", "confirm_max"]:
        for threshold in [0.70, 0.80, 0.90]:
            for lookback in [0, 3]:
                rules.append(ComboRule(signal, threshold, lookback, "confirmed_only", 0, 20, 1.0, 0.8))
                for weak_cut in [5]:
                    rules.append(ComboRule(signal, threshold, lookback, "tiered", weak_cut, 20, 1.0, 0.8))
    return rules


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
    print("Loading baseline and breadth_dd20 trigger...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    score, _ = build_breadth_dd20_score(close, base["amount"])
    main_triggers = make_trigger_mask(score, FIXED_TRIGGER)
    confirm_scores = build_confirm_scores(close, base["amount"])

    base_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    baseline_ret = base["returns"]
    baseline_detail = base["detail"].copy()
    baseline_detail["cooldown"] = 0.0
    baseline_row = row_for(
        "v2.0 baseline",
        baseline_ret,
        baseline_detail,
        baseline_ret,
        pd.Series(False, index=close.index),
        pd.Series(False, index=close.index),
        pd.Series(False, index=close.index),
        pd.Series(1.0, index=close.index),
    )
    baseline_row.update({"rule": "baseline", "mode": "baseline", "objective": np.nan})

    rows = [baseline_row]
    print(f"Main triggers: {int(main_triggers.sum())}", flush=True)
    for rule in combo_rules():
        confirm = rolling_confirm(confirm_scores[rule.confirm_signal], rule.confirm_threshold, rule.confirm_lookback)
        exposure, strong, weak = make_combo_exposure(main_triggers, confirm, rule)
        if strong.sum() == 0 and rule.mode == "confirmed_only":
            continue
        ret, detail = apply_freeze_overlay(close, base["scheduled_weights"], base_timing, main_triggers, exposure, cfg)
        row = row_for(rule.label(), ret, detail, baseline_ret, main_triggers, strong, weak, exposure)
        row.update(asdict(rule))
        row.update(yearly_drag_score(baseline_ret, ret))
        # Balanced objective: keep headline quality but reward reduced drag in known bad trend years.
        annual_penalty = min(max(row["annual_2018"], 0.0) / 0.22, 1.1)
        dd_bonus = min(0.20 / max(abs(row["maxdd_2018"]), 1e-9), 1.5)
        drag_bonus = 1.0 + min(max(row["drag_2013_2016_2021"], -0.20), 0.10)
        row["objective"] = float((0.45 * row["sharpe_2018"] + 0.55 * row["calmar_2018"]) * annual_penalty * dd_bonus * drag_bonus)
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
    ]:
        result[f"delta_{col}"] = result[col] - base_row[col]

    result = result.sort_values(
        ["objective", "calmar_2018", "annual_2018"],
        ascending=[False, False, False],
        na_position="last",
    )
    result_path = OUT_DIR / "breadth_dd20_combo_trigger.csv"
    summary_path = OUT_DIR / "breadth_dd20_combo_trigger_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    useful = variants[(variants["annual_2018"] >= 0.22) & (variants["maxdd_2018"] >= -0.15)].copy()
    if useful.empty:
        useful = variants
    summary = {
        "baseline": baseline_row,
        "main_trigger_count": int(main_triggers.sum()),
        "best_objective": variants.iloc[0].to_dict(),
        "best_useful_calmar": useful.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_drag_reduction": variants.sort_values(["drag_2013_2016_2021", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_mode": {
            mode: group.sort_values(["objective", "calmar_2018"], ascending=[False, False]).iloc[0].to_dict()
            for mode, group in variants.groupby("mode")
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print("\n=== Top breadth_dd20 combo triggers ===", flush=True)
    cols = [
        "label",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "strong_count",
        "weak_count",
        "drag_2013_2016_2021",
        "delta_2019",
        "winning_years",
        "losing_years",
    ]
    for _, row in variants.head(18)[cols].iterrows():
        print(
            f"{row['label'][:64]:<64} "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
            f"强{int(row['strong_count'])} 弱{int(row['weak_count'])} "
            f"拖累组{row['drag_2013_2016_2021']:+.1%} 2019{row['delta_2019']:+.1%} "
            f"胜负{int(row['winning_years'])}/{int(row['losing_years'])}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
