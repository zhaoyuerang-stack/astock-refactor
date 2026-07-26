# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Mainline validation for the breadth drawdown transition rule.

Fixed rule under test:
  score: breadth_dd_20d = percentile of (MA20 diffusion rolling-20 high - current diffusion)
  trigger: edge threshold=0.70, delta=0.02, cooldown=20d
  execution: freeze scheduled rebalances and scale exposure to 80% during cooldown

This mirrors the mkt_low_ma_diffusion validation so the two candidates are
directly comparable.

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.breadth_dd20_mainline_validation
"""
import json
import os
import sys
from dataclasses import asdict
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
from scripts.research.archive.hmm_stress_guard_smallcap import build_market_features  # noqa: E402
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
from scripts.research.archive.state_transition_signal_search import rolling_percentile  # noqa: E402
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
CHART_DIR = OUT_DIR / "breadth_dd20_charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)


FIXED_TRIGGER = TriggerRule(
    kind="edge",
    threshold=0.70,
    min_delta=0.02,
    suppress_days=20,
    cut_days=20,
    floor=0.8,
)
FIXED_EXEC = ExecutionRule("freeze_floor", 0.8)


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


def build_breadth_dd20_score(close, amount):
    market = build_market_features(close, amount).reindex(close.index)
    breadth = market["ma_diffusion"].astype(float)
    breadth_dd_20d = (breadth.rolling(20).max() - breadth).clip(lower=0.0)
    score = rolling_percentile(breadth_dd_20d, window=252).shift(1).clip(0.0, 1.0)
    channels = pd.DataFrame(
        {
            "ma_diffusion": breadth,
            "breadth_dd_20d_raw": breadth_dd_20d,
            "breadth_dd_20d": score,
            "risk_appetite": market["risk_appetite"],
            "liquidity": market["liquidity"],
            "volatility": market["volatility"],
        },
        index=close.index,
    )
    return score, channels


def replay_with_cost(cost):
    cfg = StrategyConfig(start="2010-01-01", cost=cost)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    score, channels = build_breadth_dd20_score(close, base["amount"])
    triggers = make_trigger_mask(score, FIXED_TRIGGER)
    overlay_ret, detail, _ = backtest_execution_overlay(
        close,
        base["scheduled_weights"],
        base["timing"].astype(float).reindex(close.index).fillna(0.0),
        triggers,
        FIXED_TRIGGER,
        FIXED_EXEC,
        cfg,
    )
    return base, overlay_ret, detail, triggers, channels


def yearly_table(base_ret, overlay_ret):
    base_year = yearly_returns(base_ret)
    over_year = yearly_returns(overlay_ret)
    years = sorted(set(base_year.index).intersection(over_year.index))
    return pd.DataFrame(
        [
            {
                "year": int(year),
                "baseline_return": float(base_year.loc[year]),
                "overlay_return": float(over_year.loc[year]),
                "delta_return": float(over_year.loc[year] - base_year.loc[year]),
            }
            for year in years
        ]
    )


def regime_masks(close, channels):
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    market_ret = ret.mean(axis=1).fillna(0.0)
    market_nav = (1.0 + market_ret).cumprod()
    trend = (market_nav > market_nav.rolling(120).mean()).shift(1).reindex(close.index).fillna(False)
    breadth_bad = (channels["breadth_dd_20d"] >= 0.70).reindex(close.index).fillna(False)
    high_vol = (rolling_percentile(channels["volatility"], window=252).shift(1) >= 0.80).reindex(close.index).fillna(False)
    return {
        "trend_on": trend,
        "trend_off": ~trend,
        "breadth_dd_low": ~breadth_bad,
        "breadth_dd_high": breadth_bad,
        "high_vol": high_vol,
        "normal_vol": ~high_vol,
    }


def regime_table(base_ret, overlay_ret, masks):
    rows = []
    for name, mask in masks.items():
        mask = mask.reindex(base_ret.index).fillna(False)
        b = base_ret[mask]
        o = overlay_ret.reindex(base_ret.index)[mask].dropna()
        if len(b) < 100 or len(o) < 100:
            continue
        bm = metrics(b)
        om = metrics(o)
        rows.append(
            {
                "regime": name,
                "days": int(mask.sum()),
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
            }
        )
    return pd.DataFrame(rows)


def onset_sensitivity(base_ret, triggers):
    rows = []
    for dd_threshold in [-0.05, -0.08, -0.10, -0.12, -0.15]:
        for separation in [10, 20, 40]:
            onsets, _ = stress_onsets(base_ret, drawdown_threshold=dd_threshold, separation_days=separation)
            for max_lead in [10, 20, 30]:
                rows.append(
                    {
                        "drawdown_threshold": dd_threshold,
                        "separation_days": separation,
                        "max_lead_days": max_lead,
                        **evaluate_lead(triggers, onsets, base_ret.index, min_lead=1, max_lead=max_lead),
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


def nav_and_dd(ret):
    nav = (1.0 + ret.fillna(0.0)).cumprod()
    dd = nav / nav.cummax() - 1.0
    return nav, dd


def plot_year(year, base_ret, overlay_ret, triggers, channels):
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year}-12-31")
    b = base_ret.loc[start:end]
    o = overlay_ret.loc[start:end]
    if b.empty or o.empty:
        return None
    b_nav = (1.0 + b.fillna(0.0)).cumprod()
    o_nav = (1.0 + o.fillna(0.0)).cumprod()
    b_dd = b_nav / b_nav.cummax() - 1.0
    o_dd = o_nav / o_nav.cummax() - 1.0
    trig = triggers.loc[start:end]
    score = channels["breadth_dd_20d"].loc[start:end]
    diffusion = channels["ma_diffusion"].loc[start:end]

    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(b_nav.index, b_nav.values, label="baseline", lw=1.4)
    axes[0].plot(o_nav.index, o_nav.values, label="overlay", lw=1.4)
    axes[0].set_title(f"{year} NAV with breadth_dd_20d triggers")
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    axes[1].fill_between(b_dd.index, b_dd.values, 0, color="tab:red", alpha=0.25, label="baseline dd")
    axes[1].plot(o_dd.index, o_dd.values, color="tab:blue", lw=1.1, label="overlay dd")
    axes[1].set_title("Drawdown")
    axes[1].legend()
    axes[1].grid(alpha=0.25)

    axes[2].plot(score.index, score.values, label="breadth_dd_20d score", lw=1.2)
    axes[2].plot(diffusion.index, diffusion.values, label="MA20 diffusion", lw=1.0, alpha=0.8)
    axes[2].axhline(FIXED_TRIGGER.threshold, color="gray", ls="--", lw=0.8, label="trigger")
    axes[2].set_ylim(-0.02, 1.02)
    axes[2].set_title("Transition score and breadth")
    axes[2].legend(ncol=2)
    axes[2].grid(alpha=0.25)

    for ax in axes:
        for dt in trig[trig].index:
            ax.axvline(dt, color="crimson", alpha=0.22, lw=0.8)

    fig.tight_layout()
    path = CHART_DIR / f"breadth_dd20_{year}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def segment_table(base_ret, overlay_ret, triggers):
    segments = [
        ("warmup_2010_2017", "2010-01-01", "2017-12-31"),
        ("validation_2018_2021", "2018-01-01", "2021-12-31"),
        ("validation_2022_2026", "2022-01-01", "2026-12-31"),
        ("stress_2018", "2018-01-01", "2018-12-31"),
        ("drag_2019", "2019-01-01", "2019-12-31"),
        ("recent_2023_2026", "2023-01-01", "2026-12-31"),
    ]
    rows = []
    for label, start, end in segments:
        b = base_ret.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        o = overlay_ret.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        t = triggers.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        if len(b) < 100 or len(o) < 100:
            continue
        bm = metrics(b)
        om = metrics(o)
        onsets, _ = stress_onsets(b, drawdown_threshold=-0.08, separation_days=20)
        lead = evaluate_lead(t, onsets, b.index, min_lead=1, max_lead=20)
        rows.append(
            {
                "segment": label,
                "start": start,
                "end": end,
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


def event_table(base_ret, overlay_ret, triggers):
    _, b_dd = nav_and_dd(base_ret)
    _, o_dd = nav_and_dd(overlay_ret)
    rows = []
    for dt in triggers[triggers].index:
        if dt not in base_ret.index:
            continue
        pos = base_ret.index.get_loc(dt)
        fwd = {}
        for horizon in [5, 10, 20, 30]:
            end_pos = min(len(base_ret) - 1, pos + horizon)
            window = base_ret.index[pos : end_pos + 1]
            if len(window) <= 1:
                continue
            fwd[f"baseline_fwd_{horizon}d"] = float((1.0 + base_ret.loc[window[1:]]).prod() - 1.0)
            fwd[f"overlay_fwd_{horizon}d"] = float((1.0 + overlay_ret.loc[window[1:]]).prod() - 1.0)
            fwd[f"delta_fwd_{horizon}d"] = fwd[f"overlay_fwd_{horizon}d"] - fwd[f"baseline_fwd_{horizon}d"]
        rows.append(
            {
                "date": dt.date().isoformat(),
                "year": int(dt.year),
                "baseline_dd": float(b_dd.loc[dt]),
                "overlay_dd": float(o_dd.loc[dt]),
                **fwd,
            }
        )
    return pd.DataFrame(rows)


def main():
    print("Running breadth_dd_20d mainline validation...", flush=True)
    base, overlay_ret, detail, triggers, channels = replay_with_cost(CostModel())
    base_ret = base["returns"]

    annual = yearly_table(base_ret, overlay_ret)
    regimes = regime_table(base_ret, overlay_ret, regime_masks(base["close"], channels))
    onsets = onset_sensitivity(base_ret, triggers)
    segments = segment_table(base_ret, overlay_ret, triggers)
    events = event_table(base_ret, overlay_ret, triggers)

    chart_paths = []
    for year in [2018, 2019, 2021, 2024, 2025]:
        path = plot_year(year, base_ret, overlay_ret, triggers, channels)
        if path:
            chart_paths.append(str(path))

    paths = {
        "yearly": OUT_DIR / "breadth_dd20_yearly.csv",
        "regimes": OUT_DIR / "breadth_dd20_regimes.csv",
        "onsets": OUT_DIR / "breadth_dd20_onsets.csv",
        "segments": OUT_DIR / "breadth_dd20_segments.csv",
        "events": OUT_DIR / "breadth_dd20_events.csv",
        "summary": OUT_DIR / "breadth_dd20_summary.json",
    }
    annual.to_csv(paths["yearly"], index=False)
    regimes.to_csv(paths["regimes"], index=False)
    onsets.to_csv(paths["onsets"], index=False)
    segments.to_csv(paths["segments"], index=False)
    events.to_csv(paths["events"], index=False)

    b2018 = metrics(base_ret[base_ret.index.year >= 2018])
    o2018 = metrics(overlay_ret[overlay_ret.index.year >= 2018])
    default_onsets, _ = stress_onsets(base_ret, drawdown_threshold=-0.08, separation_days=20)
    lead = evaluate_lead(triggers, default_onsets, base_ret.index, min_lead=1, max_lead=20)
    summary = {
        "fixed_rule": {
            "score": "breadth_dd_20d",
            "trigger": asdict(FIXED_TRIGGER),
            "execution": {"mode": FIXED_EXEC.mode, "floor": FIXED_EXEC.floor},
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
            "winning_years": int((annual["delta_return"] > 0).sum()),
            "losing_years": int((annual["delta_return"] <= 0).sum()),
            "worst_delta_year": annual.loc[annual["delta_return"].idxmin()].to_dict(),
            "best_delta_year": annual.loc[annual["delta_return"].idxmax()].to_dict(),
        },
        "event_summary": {
            "trigger_count": int(len(events)),
            "mean_delta_fwd_20d": float(events["delta_fwd_20d"].mean()) if "delta_fwd_20d" in events else np.nan,
            "median_delta_fwd_20d": float(events["delta_fwd_20d"].median()) if "delta_fwd_20d" in events else np.nan,
            "positive_delta_fwd_20d_rate": float((events["delta_fwd_20d"] > 0).mean()) if "delta_fwd_20d" in events else np.nan,
        },
        "charts": chart_paths,
        "paths": {k: str(v) for k, v in paths.items() if k != "summary"},
        "notes": [
            "Cost sensitivity is intentionally excluded from this fast mainline run because repeated custom replays are slow.",
            "Use state_transition_signal_discovery_v2.csv for the initial fixed-cost comparison.",
        ],
    }
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")

    print("\n=== breadth_dd_20d 2018+ headline ===", flush=True)
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
    print(f"年度胜/负: {summary['yearly_delta']['winning_years']}/{summary['yearly_delta']['losing_years']}", flush=True)
    print(f"Charts: {CHART_DIR}", flush=True)
    for key, path in paths.items():
        print(f"Wrote: {path}", flush=True)


if __name__ == "__main__":
    main()
