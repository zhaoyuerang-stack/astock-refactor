# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Robustness checks for the refined market-diffusion transition rule.

Fixed rule under test:
  main score: mkt_low_ma_diffusion
  trigger: edge threshold=0.60, delta=0.02, cooldown=30d
  confirm: mkt_low_risk_appetite >= 0.80 over the last 3 days
  execution: freeze scheduled rebalances during cooldown

Checks:
* year-by-year performance
* market regime slices
* lead-time sensitivity to stress-onset definitions
* execution-cost sensitivity

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.mkt_diffusion_robustness
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
from engine.metrics import metrics, yearly_returns
from scripts.research.archive.mkt_diffusion_transition_refine import (  # noqa: E402
    ConfirmRule,
    apply_confirm,
    build_channels,
)
from scripts.research.archive.state_transition_execution_explore import (  # noqa: E402
    ExecutionRule,
    backtest_execution_overlay,
)
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    TriggerRule,
    evaluate_lead,
    make_trigger_mask,
    stress_onsets,
)
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"


FIXED_TRIGGER = TriggerRule(
    kind="edge",
    threshold=0.60,
    min_delta=0.02,
    suppress_days=30,
    cut_days=30,
    floor=0.8,
)
FIXED_CONFIRM = ConfirmRule("mkt_low_risk_appetite", threshold=0.80, lookback=3)
FIXED_EXEC = ExecutionRule("freeze", 1.0)


def build_fixed_triggers(close, amount):
    channels = build_channels(close, amount)
    raw = make_trigger_mask(channels["mkt_low_ma_diffusion"], FIXED_TRIGGER)
    return apply_confirm(raw, channels, FIXED_CONFIRM), channels


def replay_with_cost(cost):
    cfg = StrategyConfig(start="2010-01-01", cost=cost)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    triggers, channels = build_fixed_triggers(close, base["amount"])
    ret, detail, _ = backtest_execution_overlay(
        close,
        base["scheduled_weights"],
        base["timing"].astype(float).reindex(close.index).fillna(0.0),
        triggers,
        FIXED_TRIGGER,
        FIXED_EXEC,
        cfg,
    )
    return base, ret, detail, triggers, channels


def metrics_row(label, ret):
    m = metrics(ret)
    return {
        "label": label,
        "annual": m["annual"],
        "maxdd": m["maxdd"],
        "sharpe": m["sharpe"],
        "calmar": m["calmar"],
        "n": m["n"],
    }


def yearly_table(base_ret, overlay_ret):
    base_year = yearly_returns(base_ret)
    over_year = yearly_returns(overlay_ret)
    years = sorted(set(base_year.index).intersection(over_year.index))
    rows = []
    for year in years:
        rows.append(
            {
                "year": int(year),
                "baseline_return": float(base_year.loc[year]),
                "overlay_return": float(over_year.loc[year]),
                "delta_return": float(over_year.loc[year] - base_year.loc[year]),
                "baseline_days": int((base_ret.index.year == year).sum()),
                "overlay_days": int((overlay_ret.index.year == year).sum()),
            }
        )
    return pd.DataFrame(rows)


def regime_masks(close, amount, channels):
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    market_ret = ret.mean(axis=1).fillna(0.0)
    market_nav = (1.0 + market_ret).cumprod()
    trend = market_nav > market_nav.rolling(120).mean()
    breadth_ok = channels["mkt_low_ma_diffusion"] < 0.60
    high_vol = channels["mkt_high_volatility"] >= 0.80
    return {
        "trend_on": trend.shift(1).reindex(close.index).fillna(False),
        "trend_off": (~trend.shift(1).reindex(close.index).fillna(False)),
        "breadth_ok": breadth_ok.reindex(close.index).fillna(False),
        "breadth_bad": (~breadth_ok.reindex(close.index).fillna(False)),
        "high_vol": high_vol.reindex(close.index).fillna(False),
        "normal_vol": (~high_vol.reindex(close.index).fillna(False)),
    }


def regime_table(base_ret, overlay_ret, masks):
    rows = []
    for name, mask in masks.items():
        mask = mask.reindex(base_ret.index).fillna(False)
        b = base_ret[mask]
        o = overlay_ret.reindex(base_ret.index)[mask].dropna()
        if len(b) < 100 or len(o) < 100:
            continue
        b_row = metrics_row("baseline", b)
        o_row = metrics_row("overlay", o)
        rows.append(
            {
                "regime": name,
                "days": int(mask.sum()),
                "baseline_annual": b_row["annual"],
                "overlay_annual": o_row["annual"],
                "delta_annual": o_row["annual"] - b_row["annual"],
                "baseline_maxdd": b_row["maxdd"],
                "overlay_maxdd": o_row["maxdd"],
                "delta_maxdd": o_row["maxdd"] - b_row["maxdd"],
                "baseline_sharpe": b_row["sharpe"],
                "overlay_sharpe": o_row["sharpe"],
                "delta_sharpe": o_row["sharpe"] - b_row["sharpe"],
                "baseline_calmar": b_row["calmar"],
                "overlay_calmar": o_row["calmar"],
                "delta_calmar": o_row["calmar"] - b_row["calmar"],
            }
        )
    return pd.DataFrame(rows)


def onset_sensitivity(base_ret, triggers):
    rows = []
    for dd_threshold in [-0.05, -0.08, -0.10, -0.12, -0.15]:
        for separation in [10, 20, 40]:
            onsets, _ = stress_onsets(base_ret, drawdown_threshold=dd_threshold, separation_days=separation)
            for max_lead in [10, 20, 30]:
                lead = evaluate_lead(triggers, onsets, base_ret.index, min_lead=1, max_lead=max_lead)
                rows.append(
                    {
                        "drawdown_threshold": dd_threshold,
                        "separation_days": separation,
                        "max_lead_days": max_lead,
                        **lead,
                    }
                )
    return pd.DataFrame(rows)


def cost_sensitivity():
    scenarios = [
        ("half_cost", CostModel(buy_cost=0.001125, sell_cost=0.001375, financing_rate=0.065)),
        ("base_cost", CostModel()),
        ("high_cost", CostModel(buy_cost=0.0035, sell_cost=0.0040, financing_rate=0.08)),
        ("very_high_cost", CostModel(buy_cost=0.0050, sell_cost=0.0055, financing_rate=0.10)),
    ]
    rows = []
    for name, cost in scenarios:
        base, overlay_ret, detail, triggers, _ = replay_with_cost(cost)
        base_ret = base["returns"]
        b = metrics(base_ret[base_ret.index.year >= 2018])
        o = metrics(overlay_ret[overlay_ret.index.year >= 2018])
        detail_2018 = detail[detail.index.year >= 2018]
        rows.append(
            {
                "scenario": name,
                "buy_cost": cost.buy_cost,
                "sell_cost": cost.sell_cost,
                "financing_rate": cost.financing_rate,
                "trigger_count": int(triggers.sum()),
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
                "overlay_turnover_ann": float(detail_2018["turnover"].mean() * 252),
                "overlay_cost_ann": float(detail_2018["cost"].mean() * 252),
            }
        )
    return pd.DataFrame(rows)


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
    print("Running fixed-rule baseline replay...", flush=True)
    base, overlay_ret, detail, triggers, channels = replay_with_cost(CostModel())
    base_ret = base["returns"]

    annual = yearly_table(base_ret, overlay_ret)
    regimes = regime_table(base_ret, overlay_ret, regime_masks(base["close"], base["amount"], channels))
    onsets = onset_sensitivity(base_ret, triggers)
    costs = cost_sensitivity()

    annual_path = OUT_DIR / "mkt_diffusion_robustness_yearly.csv"
    regime_path = OUT_DIR / "mkt_diffusion_robustness_regimes.csv"
    onset_path = OUT_DIR / "mkt_diffusion_robustness_onsets.csv"
    cost_path = OUT_DIR / "mkt_diffusion_robustness_costs.csv"
    summary_path = OUT_DIR / "mkt_diffusion_robustness_summary.json"

    annual.to_csv(annual_path, index=False)
    regimes.to_csv(regime_path, index=False)
    onsets.to_csv(onset_path, index=False)
    costs.to_csv(cost_path, index=False)

    b2018 = metrics(base_ret[base_ret.index.year >= 2018])
    o2018 = metrics(overlay_ret[overlay_ret.index.year >= 2018])
    winning_years = int((annual["delta_return"] > 0).sum())
    losing_years = int((annual["delta_return"] <= 0).sum())
    base_onsets, _ = stress_onsets(base_ret, drawdown_threshold=-0.08, separation_days=20)
    lead = evaluate_lead(triggers, base_onsets, base_ret.index, min_lead=1, max_lead=20)

    summary = {
        "fixed_rule": {
            "trigger": asdict(FIXED_TRIGGER),
            "confirm": {
                "name": FIXED_CONFIRM.name,
                "threshold": FIXED_CONFIRM.threshold,
                "lookback": FIXED_CONFIRM.lookback,
            },
            "execution": {
                "mode": FIXED_EXEC.mode,
                "floor": FIXED_EXEC.floor,
            },
        },
        "baseline_2018": b2018,
        "overlay_2018": o2018,
        "delta_2018": {
            "annual": o2018["annual"] - b2018["annual"],
            "maxdd": o2018["maxdd"] - b2018["maxdd"],
            "sharpe": o2018["sharpe"] - b2018["sharpe"],
            "calmar": o2018["calmar"] - b2018["calmar"],
        },
        "lead_default": lead,
        "yearly_delta": {
            "winning_years": winning_years,
            "losing_years": losing_years,
            "worst_delta_year": annual.loc[annual["delta_return"].idxmin()].to_dict(),
            "best_delta_year": annual.loc[annual["delta_return"].idxmax()].to_dict(),
        },
        "paths": {
            "annual": str(annual_path),
            "regimes": str(regime_path),
            "onsets": str(onset_path),
            "costs": str(cost_path),
        },
        "notes": [
            "Performance metrics are recomputed without changing the fixed rule.",
            "Regime slices are conditional daily-return subsets, not standalone tradable backtests.",
            "Onset checks vary drawdown threshold, separation, and lead window.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print("\n=== Fixed-rule 2018+ robustness headline ===", flush=True)
    print(
        f"baseline 年化{b2018['annual']:+.1%} 回撤{b2018['maxdd']:+.1%} "
        f"夏普{b2018['sharpe']:.2f} 卡玛{b2018['calmar']:.2f}",
        flush=True,
    )
    print(
        f"overlay  年化{o2018['annual']:+.1%} 回撤{o2018['maxdd']:+.1%} "
        f"夏普{o2018['sharpe']:.2f} 卡玛{o2018['calmar']:.2f}",
        flush=True,
    )
    print(
        f"lead 默认: 覆盖{lead['coverage']:.0%} 精度{lead['precision']:.0%} "
        f"领先{lead['avg_lead_days']:.1f}d 触发{lead['triggers']}",
        flush=True,
    )
    print(f"年度胜/负: {winning_years}/{losing_years}", flush=True)
    print(f"Wrote: {annual_path}", flush=True)
    print(f"Wrote: {regime_path}", flush=True)
    print(f"Wrote: {onset_path}", flush=True)
    print(f"Wrote: {cost_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
