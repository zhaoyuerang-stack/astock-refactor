"""Compare state-driven overlays for the v2.0 small-cap strategy.

This experiment reuses existing HMM research artifacts and replays a small set
of representative exposure rules through the core backtester. It is intentionally
research-only: no production signal, registry, or scheduler files are changed.

Usage:
  /usr/bin/python3 -m scripts.research.microstructure_overlay_experiment
"""
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics
from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
EXIT_DAILY = OUT_DIR / "hmm_exit_smallcap_daily.csv"
STRESS_DAILY = OUT_DIR / "hmm_stress_guard_smallcap_daily.csv"


def load_series_from_daily(path, label, value_col):
    usecols = ["date", value_col, "label"]
    pieces = []
    for chunk in pd.read_csv(path, usecols=usecols, parse_dates=["date"], chunksize=250_000):
        part = chunk.loc[chunk["label"] == label, ["date", value_col]]
        if not part.empty:
            pieces.append(part)
    if not pieces:
        raise ValueError(f"label not found in {path.name}: {label}")
    out = pd.concat(pieces).drop_duplicates("date", keep="last").set_index("date")[value_col]
    return out.astype("float64").sort_index()


def linear_cap(risk, floor, lo, hi):
    risk = risk.clip(0.0, 1.0)
    span = max(hi - lo, 1e-9)
    scaled = ((risk - lo) / span).clip(0.0, 1.0)
    return 1.0 - (1.0 - floor) * scaled


def soft_floor(risk, floor, power):
    risk = risk.clip(0.0, 1.0)
    return floor + (1.0 - floor) * ((1.0 - risk) ** power)


def slice_metrics(ret, start_year):
    return metrics(ret[ret.index.year >= start_year])


def summarize(label, ret, detail, timing, exposure, baseline_ret):
    row = {"label": label}
    for year in [2018, 2023, 2010]:
        m = slice_metrics(ret, year)
        suffix = str(year)
        row[f"annual_{suffix}"] = m["annual"]
        row[f"maxdd_{suffix}"] = m["maxdd"]
        row[f"sharpe_{suffix}"] = m["sharpe"]
        row[f"calmar_{suffix}"] = m["calmar"]

    base_2018 = baseline_ret[baseline_ret.index.year >= 2018]
    ret_2018 = ret[ret.index.year >= 2018]
    detail_2018 = detail[detail.index.year >= 2018]
    timing_2018 = timing[timing.index.year >= 2018]
    exposure_2018 = exposure[exposure.index.year >= 2018]

    row["corr_v2_2018"] = ret_2018.corr(base_2018)
    row["timing_on_rate_2018"] = float(timing_2018.mean())
    row["overlay_exposure_2018"] = float(exposure_2018.mean())
    row["turnover_ann_2018"] = float(detail_2018["turnover"].mean() * 252)
    row["cost_ann_2018"] = float(detail_2018["cost"].mean() * 252)
    row["trade_days_2018"] = int((detail_2018["turnover"] > 1e-12).sum())
    return row


def replay(label, exposure, base):
    cfg = StrategyConfig(start="2010-01-01")
    close = base["close"]
    scheduled = base["scheduled_weights"]
    smallcap_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    exposure = exposure.reindex(close.index).fillna(0.0).clip(0.0, 1.0)
    timing = smallcap_timing * exposure
    ret, detail = backtest_weights(close, scheduled, timing, cfg)
    return summarize(label, ret, detail, timing, exposure, base["returns"])


def main():
    cfg = StrategyConfig(start="2010-01-01")
    print("Loading baseline small-cap strategy...", flush=True)
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    baseline_exposure = pd.Series(1.0, index=close.index)
    baseline_row = replay("v2.0 baseline", baseline_exposure, base)

    raw_risk_label = "hmm_risk_raw small_market lb1260 rt60"
    print("Loading cached HMM exit risk...", flush=True)
    risk = load_series_from_daily(EXIT_DAILY, raw_risk_label, "risk_prob").reindex(close.index)

    stress_label = "hmm_stress floor lb756 rt60 th0.40 floor0.7"
    print("Loading cached HMM stress guard exposure...", flush=True)
    stress_exposure = load_series_from_daily(STRESS_DAILY, stress_label, "guard_exposure").reindex(close.index)

    candidates = [
        ("exit_soft_keep_return floor0.99 power1.5", soft_floor(risk.fillna(1.0), floor=0.99, power=1.5)),
        ("exit_linear_balanced floor0.80 lo0.50 hi0.60", linear_cap(risk.fillna(1.0), floor=0.80, lo=0.50, hi=0.60)),
        ("exit_linear_defensive floor0.40 lo0.50 hi0.75", linear_cap(risk.fillna(1.0), floor=0.40, lo=0.50, hi=0.75)),
        ("stress_guard_floor0.70_th0.40", stress_exposure.fillna(0.0)),
    ]

    rows = [baseline_row]
    for label, exposure in candidates:
        print(f"Replaying {label}...", flush=True)
        rows.append(replay(label, exposure, base))

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

    result = result.sort_values(["calmar_2018", "sharpe_2018"], ascending=[False, False])
    result_path = OUT_DIR / "microstructure_overlay_experiment.csv"
    summary_path = OUT_DIR / "microstructure_overlay_experiment_summary.json"
    result.to_csv(result_path, index=False)

    variants = result[result["label"] != "v2.0 baseline"].copy()
    summary = {
        "baseline": baseline_row,
        "best_by_calmar_2018": variants.sort_values(["calmar_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_by_sharpe_2018": variants.sort_values(["sharpe_2018", "annual_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "best_return_preservation": variants.sort_values(["annual_2018", "sharpe_2018"], ascending=[False, False]).iloc[0].to_dict(),
        "notes": [
            "Uses cached HMM artifacts from reports/research; no HMM retraining.",
            "Daily A-share data is used as a proxy for latent market state; this is not true LOB microstructure data.",
            "Exposure is shifted in the upstream HMM artifacts, so replay avoids same-day look-ahead.",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    cols = [
        "label",
        "annual_2018",
        "maxdd_2018",
        "sharpe_2018",
        "calmar_2018",
        "turnover_ann_2018",
        "cost_ann_2018",
        "trade_days_2018",
    ]
    print("\n=== Microstructure overlay experiment ===", flush=True)
    for _, row in result[cols].iterrows():
        print(
            f"{row['label']:<46} "
            f"年化{row['annual_2018']:+.1%} 回撤{row['maxdd_2018']:+.1%} "
            f"夏普{row['sharpe_2018']:.2f} 卡玛{row['calmar_2018']:.2f} "
            f"换手/年{row['turnover_ann_2018']:.1f} 成本/年{row['cost_ann_2018']:.1%} "
            f"交易日{int(row['trade_days_2018'])}",
            flush=True,
        )
    print(f"\nWrote: {result_path}", flush=True)
    print(f"Wrote: {summary_path}", flush=True)


if __name__ == "__main__":
    main()
