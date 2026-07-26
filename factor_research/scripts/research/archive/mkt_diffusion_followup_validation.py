# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Follow-up validation for the fixed market-diffusion transition rule.

Outputs:
* trigger/NAV/drawdown charts for selected years
* walk-forward style segment checks with fixed parameters

Research-only: writes reports/research artifacts only.

Usage:
  /usr/bin/python3 -m scripts.research.archive.mkt_diffusion_followup_validation
"""
import json
import os
import sys
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
from engine.metrics import metrics
from scripts.research.archive.mkt_diffusion_robustness import (  # noqa: E402
    FIXED_CONFIRM,
    FIXED_EXEC,
    FIXED_TRIGGER,
    build_fixed_triggers,
)
from scripts.research.archive.state_transition_execution_explore import (
    backtest_execution_overlay,  # noqa: E402
)
from scripts.research.archive.state_transition_lead_experiment import (  # noqa: E402
    evaluate_lead,
    stress_onsets,
)
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
CHART_DIR = OUT_DIR / "mkt_diffusion_followup_charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)


def replay_fixed():
    cfg = StrategyConfig(start="2010-01-01", cost=CostModel())
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    triggers, channels = build_fixed_triggers(close, base["amount"])
    overlay_ret, overlay_detail, _ = backtest_execution_overlay(
        close,
        base["scheduled_weights"],
        base["timing"].astype(float).reindex(close.index).fillna(0.0),
        triggers,
        FIXED_TRIGGER,
        FIXED_EXEC,
        cfg,
    )
    return base, overlay_ret, overlay_detail, triggers, channels


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
    score = channels["mkt_low_ma_diffusion"].loc[start:end]
    confirm = channels["mkt_low_risk_appetite"].loc[start:end]

    fig, axes = plt.subplots(3, 1, figsize=(13, 8), sharex=True)
    axes[0].plot(b_nav.index, b_nav.values, label="baseline", lw=1.4)
    axes[0].plot(o_nav.index, o_nav.values, label="overlay", lw=1.4)
    for dt in trig[trig].index:
        axes[0].axvline(dt, color="crimson", alpha=0.22, lw=0.8)
    axes[0].set_title(f"{year} NAV with fixed transition triggers")
    axes[0].legend()
    axes[0].grid(alpha=0.25)

    axes[1].fill_between(b_dd.index, b_dd.values, 0, color="tab:red", alpha=0.25, label="baseline dd")
    axes[1].plot(o_dd.index, o_dd.values, color="tab:blue", lw=1.1, label="overlay dd")
    for dt in trig[trig].index:
        axes[1].axvline(dt, color="crimson", alpha=0.22, lw=0.8)
    axes[1].set_title("Drawdown")
    axes[1].legend()
    axes[1].grid(alpha=0.25)

    axes[2].plot(score.index, score.values, label="mkt_low_ma_diffusion", lw=1.2)
    axes[2].plot(confirm.index, confirm.values, label="mkt_low_risk_appetite", lw=1.0, alpha=0.8)
    axes[2].axhline(0.60, color="gray", ls="--", lw=0.8, label="diffusion trigger")
    axes[2].axhline(0.80, color="purple", ls="--", lw=0.8, label="confirm")
    for dt in trig[trig].index:
        axes[2].axvline(dt, color="crimson", alpha=0.25, lw=0.8)
    axes[2].set_ylim(-0.02, 1.02)
    axes[2].set_title("Transition scores")
    axes[2].legend(ncol=2)
    axes[2].grid(alpha=0.25)

    fig.tight_layout()
    path = CHART_DIR / f"mkt_diffusion_followup_{year}.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


def segment_metrics(base_ret, overlay_ret, triggers, segments):
    rows = []
    for label, start, end in segments:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        b = base_ret.loc[start_ts:end_ts]
        o = overlay_ret.loc[start_ts:end_ts]
        t = triggers.loc[start_ts:end_ts]
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
    b_nav, b_dd = nav_and_dd(base_ret)
    o_nav, o_dd = nav_and_dd(overlay_ret)
    rows = []
    for dt in triggers[triggers].index:
        if dt not in base_ret.index:
            continue
        pos = base_ret.index.get_loc(dt)
        fwd = {}
        for horizon in [5, 10, 20, 30]:
            end_pos = min(len(base_ret) - 1, pos + horizon)
            window = base_ret.index[pos:end_pos + 1]
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
    print("Replaying fixed transition rule...", flush=True)
    base, overlay_ret, _, triggers, channels = replay_fixed()
    base_ret = base["returns"]

    years = [2018, 2019, 2021, 2024, 2025]
    chart_paths = []
    for year in years:
        path = plot_year(year, base_ret, overlay_ret, triggers, channels)
        if path is not None:
            chart_paths.append(str(path))

    segments = [
        ("warmup_2010_2017", "2010-01-01", "2017-12-31"),
        ("validation_2018_2021", "2018-01-01", "2021-12-31"),
        ("validation_2022_2026", "2022-01-01", "2026-12-31"),
        ("stress_2018", "2018-01-01", "2018-12-31"),
        ("drag_2019", "2019-01-01", "2019-12-31"),
        ("recent_2023_2026", "2023-01-01", "2026-12-31"),
    ]
    segment_df = segment_metrics(base_ret, overlay_ret, triggers, segments)
    event_df = event_table(base_ret, overlay_ret, triggers)

    segment_path = OUT_DIR / "mkt_diffusion_followup_segments.csv"
    event_path = OUT_DIR / "mkt_diffusion_followup_events.csv"
    summary_path = OUT_DIR / "mkt_diffusion_followup_summary.json"
    segment_df.to_csv(segment_path, index=False)
    event_df.to_csv(event_path, index=False)

    summary = {
        "fixed_rule": {
            "trigger": {
                "kind": FIXED_TRIGGER.kind,
                "threshold": FIXED_TRIGGER.threshold,
                "min_delta": FIXED_TRIGGER.min_delta,
                "cooldown_days": FIXED_TRIGGER.cut_days,
            },
            "confirm": {
                "name": FIXED_CONFIRM.name,
                "threshold": FIXED_CONFIRM.threshold,
                "lookback": FIXED_CONFIRM.lookback,
            },
            "execution": FIXED_EXEC.mode,
        },
        "charts": chart_paths,
        "segments_path": str(segment_path),
        "events_path": str(event_path),
        "segment_headline": segment_df.to_dict(orient="records"),
        "event_summary": {
            "trigger_count": int(len(event_df)),
            "mean_delta_fwd_20d": float(event_df["delta_fwd_20d"].mean()) if "delta_fwd_20d" in event_df else np.nan,
            "median_delta_fwd_20d": float(event_df["delta_fwd_20d"].median()) if "delta_fwd_20d" in event_df else np.nan,
            "positive_delta_fwd_20d_rate": float((event_df["delta_fwd_20d"] > 0).mean()) if "delta_fwd_20d" in event_df else np.nan,
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Follow-up validation segments ===", flush=True)
    cols = ["segment", "baseline_annual", "overlay_annual", "baseline_maxdd", "overlay_maxdd", "overlay_calmar", "trigger_count"]
    for _, row in segment_df[cols].iterrows():
        print(
            f"{row['segment']:<22} "
            f"年化 {row['baseline_annual']:+.1%}->{row['overlay_annual']:+.1%} "
            f"回撤 {row['baseline_maxdd']:+.1%}->{row['overlay_maxdd']:+.1%} "
            f"卡玛 {row['overlay_calmar']:.2f} 触发{int(row['trigger_count'])}",
            flush=True,
        )
    print(f"\nCharts: {CHART_DIR}", flush=True)
    print(f"Wrote: {segment_path}", flush=True)
    print(f"Wrote: {event_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
