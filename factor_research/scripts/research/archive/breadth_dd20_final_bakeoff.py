# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Final bake-off for the two breadth_dd20 combo candidates.

Candidates:
* liquidity_confirm: breadth_dd20 trigger confirmed by liquidity_drop_20d >= 0.70
* confirm_max: breadth_dd20 trigger confirmed by max(liquidity/risk appetite) >= 0.70

Both use confirmed-only freeze+floor0.8 for 20 trading days.

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.breadth_dd20_final_bakeoff
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from core.engine import CostModel
from engine.metrics import metrics, yearly_returns
from scripts.research.archive.breadth_dd20_combo_trigger import (  # noqa: E402
    ComboRule,
    apply_freeze_overlay,
    build_confirm_scores,
    cooldown_exposure,
    rolling_confirm,
    yearly_drag_score,
)
from scripts.research.archive.breadth_dd20_mainline_validation import (  # noqa: E402
    FIXED_TRIGGER,
    build_breadth_dd20_score,
)
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    evaluate_lead,
    make_trigger_mask,
    stress_onsets,
)
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
CHART_DIR = OUT_DIR / "breadth_dd20_final_bakeoff_charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Candidate:
    key: str
    confirm_signal: str
    threshold: float
    lookback: int = 0

    def rule(self):
        return ComboRule(
            confirm_signal=self.confirm_signal,
            confirm_threshold=self.threshold,
            confirm_lookback=self.lookback,
            mode="confirmed_only",
            weak_cut_days=0,
            strong_cut_days=20,
            weak_floor=1.0,
            strong_floor=0.8,
        )


CANDIDATES = [
    Candidate("liquidity_confirm", "liquidity_drop_20d", 0.70, 0),
    Candidate("confirm_max", "confirm_max", 0.70, 0),
]


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


def build_candidate_paths(base):
    close = base["close"]
    amount = base["amount"]
    score, channels = build_breadth_dd20_score(close, amount)
    main_triggers = make_trigger_mask(score, FIXED_TRIGGER)
    confirm_scores = build_confirm_scores(close, amount)
    paths = {}
    for candidate in CANDIDATES:
        confirm = rolling_confirm(confirm_scores[candidate.confirm_signal], candidate.threshold, candidate.lookback)
        action_triggers = main_triggers & confirm.reindex(main_triggers.index).fillna(False)
        exposure = cooldown_exposure(action_triggers, 20, 0.8)
        paths[candidate.key] = {
            "candidate": candidate,
            "action_triggers": action_triggers,
            "main_triggers": main_triggers,
            "exposure": exposure,
            "channels": channels,
            "confirm_scores": confirm_scores,
        }
    return paths


def replay_candidate(base, path, cfg):
    return apply_freeze_overlay(
        base["close"],
        base["scheduled_weights"],
        base["timing"].astype(float).reindex(base["close"].index).fillna(0.0),
        path["main_triggers"],
        path["exposure"],
        cfg,
    )


def headline_row(label, ret, detail, baseline_ret, triggers, exposure):
    row = {"label": label}
    for start_year in [2018, 2023, 2010]:
        m = metrics(ret[ret.index.year >= start_year])
        row[f"annual_{start_year}"] = m["annual"]
        row[f"maxdd_{start_year}"] = m["maxdd"]
        row[f"sharpe_{start_year}"] = m["sharpe"]
        row[f"calmar_{start_year}"] = m["calmar"]
    d2018 = detail[detail.index.year >= 2018]
    row["turnover_ann_2018"] = float(d2018["turnover"].mean() * 252)
    row["cost_ann_2018"] = float(d2018["cost"].mean() * 252)
    row["cooldown_rate_2018"] = float(d2018["cooldown"].mean()) if "cooldown" in d2018 else 0.0
    row["trigger_count"] = int(triggers.sum())
    row["avg_exposure_2018"] = float(exposure[exposure.index.year >= 2018].mean())
    onsets, _ = stress_onsets(baseline_ret, drawdown_threshold=-0.08, separation_days=20)
    row.update({f"lead_{k}": v for k, v in evaluate_lead(triggers, onsets, baseline_ret.index, 1, 20).items()})
    row.update(yearly_drag_score(baseline_ret, ret))
    return row


def yearly_rows(baseline_ret, candidate_returns):
    base_y = yearly_returns(baseline_ret)
    rows = []
    for key, ret in candidate_returns.items():
        over_y = yearly_returns(ret)
        for year in sorted(set(base_y.index).intersection(over_y.index)):
            rows.append(
                {
                    "candidate": key,
                    "year": int(year),
                    "baseline_return": float(base_y.loc[year]),
                    "overlay_return": float(over_y.loc[year]),
                    "delta_return": float(over_y.loc[year] - base_y.loc[year]),
                }
            )
    return pd.DataFrame(rows)


def segment_rows(baseline_ret, candidate_returns, candidate_triggers):
    segments = [
        ("warmup_2010_2017", "2010-01-01", "2017-12-31"),
        ("validation_2018_2021", "2018-01-01", "2021-12-31"),
        ("validation_2022_2026", "2022-01-01", "2026-12-31"),
        ("stress_2018", "2018-01-01", "2018-12-31"),
        ("drag_2019", "2019-01-01", "2019-12-31"),
        ("recent_2023_2026", "2023-01-01", "2026-12-31"),
    ]
    rows = []
    for key, ret in candidate_returns.items():
        triggers = candidate_triggers[key]
        for segment, start, end in segments:
            b = baseline_ret.loc[pd.Timestamp(start) : pd.Timestamp(end)]
            o = ret.loc[pd.Timestamp(start) : pd.Timestamp(end)]
            t = triggers.loc[pd.Timestamp(start) : pd.Timestamp(end)]
            if len(b) < 100 or len(o) < 100:
                continue
            bm = metrics(b)
            om = metrics(o)
            onsets, _ = stress_onsets(b, drawdown_threshold=-0.08, separation_days=20)
            lead = evaluate_lead(t, onsets, b.index, 1, 20)
            rows.append(
                {
                    "candidate": key,
                    "segment": segment,
                    "days": int(len(b)),
                    "trigger_count": int(t.sum()),
                    "baseline_annual": bm["annual"],
                    "overlay_annual": om["annual"],
                    "delta_annual": om["annual"] - bm["annual"],
                    "baseline_maxdd": bm["maxdd"],
                    "overlay_maxdd": om["maxdd"],
                    "delta_maxdd": om["maxdd"] - bm["maxdd"],
                    "baseline_sharpe": bm["sharpe"],
                    "overlay_sharpe": om["sharpe"],
                    "delta_sharpe": om["sharpe"] - bm["sharpe"],
                    "baseline_calmar": bm["calmar"],
                    "overlay_calmar": om["calmar"],
                    "delta_calmar": om["calmar"] - bm["calmar"],
                    **{f"lead_{k}": v for k, v in lead.items()},
                }
            )
    return pd.DataFrame(rows)


def event_rows(baseline_ret, candidate_returns, candidate_triggers):
    rows = []
    for key, ret in candidate_returns.items():
        triggers = candidate_triggers[key]
        for dt in triggers[triggers].index:
            if dt not in baseline_ret.index:
                continue
            pos = baseline_ret.index.get_loc(dt)
            row = {"candidate": key, "date": dt.date().isoformat(), "year": int(dt.year)}
            for horizon in [5, 10, 20, 30]:
                end_pos = min(len(baseline_ret) - 1, pos + horizon)
                window = baseline_ret.index[pos : end_pos + 1]
                if len(window) <= 1:
                    continue
                b = float((1.0 + baseline_ret.loc[window[1:]]).prod() - 1.0)
                o = float((1.0 + ret.loc[window[1:]]).prod() - 1.0)
                row[f"baseline_fwd_{horizon}d"] = b
                row[f"overlay_fwd_{horizon}d"] = o
                row[f"delta_fwd_{horizon}d"] = o - b
            rows.append(row)
    return pd.DataFrame(rows)


def cost_rows():
    scenarios = [
        ("half_cost", CostModel(buy_cost=0.001125, sell_cost=0.001375, financing_rate=0.065)),
        ("base_cost", CostModel()),
        ("high_cost", CostModel(buy_cost=0.0035, sell_cost=0.0040, financing_rate=0.08)),
    ]
    rows = []
    for scenario, cost in scenarios:
        cfg = StrategyConfig(start="2010-01-01", cost=cost)
        base = run_small_cap_strategy(cfg)
        paths = build_candidate_paths(base)
        b = metrics(base["returns"][base["returns"].index.year >= 2018])
        for key, path in paths.items():
            ret, detail = replay_candidate(base, path, cfg)
            o = metrics(ret[ret.index.year >= 2018])
            d2018 = detail[detail.index.year >= 2018]
            rows.append(
                {
                    "scenario": scenario,
                    "candidate": key,
                    "baseline_annual": b["annual"],
                    "overlay_annual": o["annual"],
                    "delta_annual": o["annual"] - b["annual"],
                    "baseline_maxdd": b["maxdd"],
                    "overlay_maxdd": o["maxdd"],
                    "delta_maxdd": o["maxdd"] - b["maxdd"],
                    "baseline_sharpe": b["sharpe"],
                    "overlay_sharpe": o["sharpe"],
                    "delta_sharpe": o["sharpe"] - b["sharpe"],
                    "baseline_calmar": b["calmar"],
                    "overlay_calmar": o["calmar"],
                    "delta_calmar": o["calmar"] - b["calmar"],
                    "overlay_cost_ann": float(d2018["cost"].mean() * 252),
                }
            )
    return pd.DataFrame(rows)


def plot_year(year, baseline_ret, candidate_returns, candidate_triggers, channels):
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year}-12-31")
    b = baseline_ret.loc[start:end]
    if b.empty:
        return None
    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    b_nav = (1.0 + b.fillna(0.0)).cumprod()
    axes[0].plot(b_nav.index, b_nav.values, label="baseline", lw=1.3)
    for key, ret in candidate_returns.items():
        o = ret.loc[start:end]
        if o.empty:
            continue
        nav = (1.0 + o.fillna(0.0)).cumprod()
        axes[0].plot(nav.index, nav.values, label=key, lw=1.2)
    axes[0].set_title(f"{year} bake-off NAV")
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    b_dd = b_nav / b_nav.cummax() - 1.0
    axes[1].fill_between(b_dd.index, b_dd.values, 0, color="tab:red", alpha=0.22, label="baseline dd")
    for key, ret in candidate_returns.items():
        o = ret.loc[start:end]
        nav = (1.0 + o.fillna(0.0)).cumprod()
        dd = nav / nav.cummax() - 1.0
        axes[1].plot(dd.index, dd.values, lw=1.1, label=key)
    axes[1].set_title("Drawdown")
    axes[1].legend()
    axes[1].grid(alpha=0.25)

    score = channels["breadth_dd_20d"].loc[start:end]
    axes[2].plot(score.index, score.values, label="breadth_dd20", lw=1.2)
    axes[2].axhline(0.70, color="gray", ls="--", lw=0.8)
    axes[2].set_ylim(-0.02, 1.02)
    axes[2].set_title("Trigger score")
    axes[2].legend()
    axes[2].grid(alpha=0.25)

    colors = {"liquidity_confirm": "crimson", "confirm_max": "purple"}
    for ax in axes:
        for key, triggers in candidate_triggers.items():
            for dt in triggers.loc[start:end][triggers.loc[start:end]].index:
                ax.axvline(dt, color=colors.get(key, "gray"), alpha=0.20, lw=0.7)
    fig.tight_layout()
    path = CHART_DIR / f"breadth_dd20_bakeoff_{year}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def main():
    cfg = StrategyConfig(start="2010-01-01", cost=CostModel())
    print("Running final bake-off base scenario...", flush=True)
    base = run_small_cap_strategy(cfg)
    paths = build_candidate_paths(base)
    candidate_returns = {}
    candidate_details = {}
    candidate_triggers = {}
    headline = []
    baseline_detail = base["detail"].copy()
    baseline_detail["cooldown"] = 0.0
    headline.append(headline_row("baseline", base["returns"], baseline_detail, base["returns"], pd.Series(False, index=base["close"].index), pd.Series(1.0, index=base["close"].index)))
    for key, path in paths.items():
        ret, detail = replay_candidate(base, path, cfg)
        candidate_returns[key] = ret
        candidate_details[key] = detail
        candidate_triggers[key] = path["action_triggers"]
        headline.append(headline_row(key, ret, detail, base["returns"], path["action_triggers"], path["exposure"]))

    yearly = yearly_rows(base["returns"], candidate_returns)
    segments = segment_rows(base["returns"], candidate_returns, candidate_triggers)
    events = event_rows(base["returns"], candidate_returns, candidate_triggers)
    print("Running cost scenarios...", flush=True)
    costs = cost_rows()

    charts = []
    any_channels = next(iter(paths.values()))["channels"]
    for year in [2018, 2019, 2021, 2024, 2025]:
        path = plot_year(year, base["returns"], candidate_returns, candidate_triggers, any_channels)
        if path:
            charts.append(str(path))

    outputs = {
        "headline": OUT_DIR / "breadth_dd20_final_bakeoff_headline.csv",
        "yearly": OUT_DIR / "breadth_dd20_final_bakeoff_yearly.csv",
        "segments": OUT_DIR / "breadth_dd20_final_bakeoff_segments.csv",
        "events": OUT_DIR / "breadth_dd20_final_bakeoff_events.csv",
        "costs": OUT_DIR / "breadth_dd20_final_bakeoff_costs.csv",
        "summary": OUT_DIR / "breadth_dd20_final_bakeoff_summary.json",
    }
    pd.DataFrame(headline).to_csv(outputs["headline"], index=False)
    yearly.to_csv(outputs["yearly"], index=False)
    segments.to_csv(outputs["segments"], index=False)
    events.to_csv(outputs["events"], index=False)
    costs.to_csv(outputs["costs"], index=False)

    headline_df = pd.DataFrame(headline)
    summary = {
        "candidates": [candidate.__dict__ for candidate in CANDIDATES],
        "headline": headline_df.to_dict(orient="records"),
        "event_summary": events.groupby("candidate")["delta_fwd_20d"].agg(["count", "mean", "median"]).reset_index().to_dict(orient="records"),
        "charts": charts,
        "outputs": {k: str(v) for k, v in outputs.items() if k != "summary"},
        "recommendation": (
            "liquidity_confirm is the stability candidate; confirm_max is the headline-return candidate."
        ),
    }
    outputs["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print("\n=== Final bake-off headline ===", flush=True)
    for _, row in headline_df.iterrows():
        print(
            f"{row['label']:<18} 年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
            f"胜负{int(row.get('winning_years', 0))}/{int(row.get('losing_years', 0))} "
            f"触发{int(row['trigger_count'])}",
            flush=True,
        )
    print(f"Charts: {CHART_DIR}", flush=True)
    for key, path in outputs.items():
        print(f"Wrote: {path}", flush=True)


if __name__ == "__main__":
    main()
