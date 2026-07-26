# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Walk-forward selection for HMM stress-guard exposure rules.

Rules are selected only on a trailing training window, then applied to the next
out-of-sample window.  This is intended to test whether the fast-optimized HMM
guard survives rule-selection overfitting.

Usage:
  /usr/bin/python3 -m scripts.research.archive.hmm_stress_guard_walk_forward
"""
import os
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics  # noqa: E402
from scripts.research.archive.hmm_stress_guard_fast_optimize import (  # noqa: E402
    candidate_exposures,
    fast_returns,
    load_probabilities,
    precompute_equal_weight_path,
)
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
RESULT_PATH = OUT_DIR / "hmm_stress_guard_walk_forward_results.csv"
DAILY_PATH = OUT_DIR / "hmm_stress_guard_walk_forward_daily.csv"
SELECTION_PATH = OUT_DIR / "hmm_stress_guard_walk_forward_selections.csv"


@dataclass(frozen=True)
class WFConfig:
    train_days: int
    test_days: int
    start: str = "2018-01-01"
    min_train_annual: float = 0.08
    min_train_exposure: float = 0.35
    max_train_exposure: float = 0.95

    def label(self):
        return f"wf_train{self.train_days}_test{self.test_days}"


def batch_slice_metrics(ret, mask):
    x = ret[:, mask]
    annual = x.mean(axis=1) * 252
    vol = x.std(axis=1, ddof=1) * np.sqrt(252)
    sharpe = np.divide(annual, vol, out=np.zeros_like(annual), where=vol > 0)
    cum = np.cumprod(1.0 + x, axis=1)
    peak = np.maximum.accumulate(cum, axis=1)
    maxdd = np.min(cum / peak - 1.0, axis=1)
    calmar = np.divide(annual, np.abs(maxdd), out=np.zeros_like(annual), where=maxdd < 0)
    return annual, maxdd, sharpe, calmar


def objective(ret, exposure, dates, mask, cfg):
    annual, maxdd, sharpe, calmar = batch_slice_metrics(ret, mask)
    avg_exposure = exposure[:, mask].mean(axis=1)
    score = (0.5 * sharpe + 0.5 * calmar) * np.minimum(np.maximum(annual, 0.0) / 0.20, 1.0)
    score = np.where(annual >= cfg.min_train_annual, score, -np.inf)
    score = np.where(avg_exposure >= cfg.min_train_exposure, score, -np.inf)
    score = np.where(avg_exposure <= cfg.max_train_exposure, score, -np.inf)
    return score


def build_candidate_matrix(close, scheduled, base_timing, cfg):
    path = precompute_equal_weight_path(close, scheduled)
    probs = load_probabilities(close.index)
    labels = []
    exposures = []
    for lb, prob in probs.items():
        local_labels, local_exposure = candidate_exposures(prob, base_timing)
        labels.extend([f"lb{lb} {x}" for x in local_labels])
        exposures.append(local_exposure)
    exposure = np.vstack(exposures)
    ret, _ = fast_returns(exposure, path, cfg)
    return labels, exposure[:, 1:], ret, close.index[1:]


def walk_forward(labels, ret, exposure, dates, cfg):
    start_ts = pd.Timestamp(cfg.start)
    start_pos = int(np.searchsorted(dates.values, start_ts.to_datetime64()))
    cursor = max(cfg.train_days, start_pos)
    stitched = np.full(len(dates), np.nan, dtype="float64")
    chosen = np.full(len(dates), "", dtype=object)
    selections = []

    while cursor < len(dates):
        train_start = cursor - cfg.train_days
        train_end = cursor
        test_end = min(cursor + cfg.test_days, len(dates))
        train_mask = np.zeros(len(dates), dtype=bool)
        train_mask[train_start:train_end] = True
        scores = objective(ret, exposure, dates, train_mask, cfg)
        best = int(np.nanargmax(scores))
        if not np.isfinite(scores[best]):
            best = int(np.nanargmax(ret[:, train_mask].mean(axis=1)))

        stitched[cursor:test_end] = ret[best, cursor:test_end]
        chosen[cursor:test_end] = labels[best]

        train_annual, train_dd, train_sharpe, train_calmar = batch_slice_metrics(ret[[best]], train_mask)
        test_mask = np.zeros(len(dates), dtype=bool)
        test_mask[cursor:test_end] = True
        test_annual, test_dd, test_sharpe, test_calmar = batch_slice_metrics(ret[[best]], test_mask)
        selections.append(
            {
                "wf_config": cfg.label(),
                "train_start": str(dates[train_start].date()),
                "train_end": str(dates[train_end - 1].date()),
                "test_start": str(dates[cursor].date()),
                "test_end": str(dates[test_end - 1].date()),
                "candidate": labels[best],
                "train_score": float(scores[best]),
                "train_annual": float(train_annual[0]),
                "train_maxdd": float(train_dd[0]),
                "train_sharpe": float(train_sharpe[0]),
                "train_calmar": float(train_calmar[0]),
                "test_annual": float(test_annual[0]),
                "test_maxdd": float(test_dd[0]),
                "test_sharpe": float(test_sharpe[0]),
                "test_calmar": float(test_calmar[0]),
            }
        )
        cursor = test_end

    return pd.Series(stitched, index=dates).dropna(), pd.Series(chosen, index=dates), pd.DataFrame(selections)


def row(label, ret, baseline):
    out = {"label": label}
    for year in [2018, 2020, 2023, 2010]:
        sliced = ret[ret.index.year >= year]
        m = metrics(sliced)
        suffix = str(year)
        out[f"annual_{suffix}"] = m["annual"]
        out[f"maxdd_{suffix}"] = m["maxdd"]
        out[f"sharpe_{suffix}"] = m["sharpe"]
        out[f"calmar_{suffix}"] = m["calmar"]
    common = ret.index.intersection(baseline.index)
    out["corr_baseline"] = float(ret.loc[common].corr(baseline.loc[common]))
    return out


def main():
    t0 = time.perf_counter()
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    cfg = StrategyConfig(start="2010-01-01")
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    scheduled = base["scheduled_weights"]
    base_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)
    baseline = base["returns"]

    labels, exposure, ret, dates = build_candidate_matrix(close, scheduled, base_timing, cfg)
    configs = [
        WFConfig(train_days=756, test_days=126),
        WFConfig(train_days=1008, test_days=126),
        WFConfig(train_days=1260, test_days=126),
        WFConfig(train_days=1008, test_days=252),
        WFConfig(train_days=1260, test_days=252),
    ]

    rows = [row("v2.0 baseline", baseline, baseline)]
    daily_frames = []
    selection_frames = []
    for wf in configs:
        wf_ret, chosen, selections = walk_forward(labels, ret, exposure, dates, wf)
        rows.append(row(wf.label(), wf_ret, baseline))
        daily_frames.append(
            pd.DataFrame(
                {
                    "date": wf_ret.index,
                    "ret": wf_ret.values,
                    "candidate": chosen.reindex(wf_ret.index).values,
                    "wf_config": wf.label(),
                }
            )
        )
        selection_frames.append(selections)

    result = pd.DataFrame(rows)
    result.to_csv(RESULT_PATH, index=False)
    pd.concat(daily_frames, ignore_index=True).to_csv(DAILY_PATH, index=False)
    pd.concat(selection_frames, ignore_index=True).to_csv(SELECTION_PATH, index=False)

    print(f"Wrote: {RESULT_PATH}")
    print(f"Wrote: {DAILY_PATH}")
    print(f"Wrote: {SELECTION_PATH}")
    print(f"Candidates: {len(labels)} | elapsed: {time.perf_counter() - t0:.2f}s")
    print(result.to_string(index=False))

    selections = pd.concat(selection_frames, ignore_index=True)
    print("\nTop selected candidates:")
    print(selections.groupby(["wf_config", "candidate"]).size().sort_values(ascending=False).head(20).to_string())


if __name__ == "__main__":
    main()
