# [STATUS: archived] 已退役探索变体族,不再维护;仅供追溯。见 scripts/research/archive/__init__.py
"""Fast rule-layer optimizer for cached HMM stress probabilities.

The slow path re-runs a full stock-level backtest for every exposure rule.
Here the selected equal-weight basket is precomputed once, then each candidate
only supplies a daily scalar exposure.  For the small-cap strategy this makes
turnover/cost calculation depend on daily basket overlap and exposure changes,
not on the full stock universe.

Usage:
  /usr/bin/python3 -m scripts.research.archive.hmm_stress_guard_fast_optimize
"""
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from strategies.small_cap import StrategyConfig, backtest_weights, run_small_cap_strategy

OUT_DIR = ROOT / "reports" / "research"
DAILY_PATH = OUT_DIR / "hmm_stress_guard_smallcap_daily.csv"
OUT_PATH = OUT_DIR / "hmm_stress_guard_fast_optimization.csv"


def _dict_weights_to_frame(weights, index):
    out = pd.DataFrame(index=index)
    for dt, s in weights.items():
        out.loc[dt, s.index] = s.values
    return out


def precompute_equal_weight_path(close, scheduled_weights):
    """Precompute unit basket return and overlap counts for scalar exposure rules."""
    weights = _dict_weights_to_frame(scheduled_weights, close.index)
    scheduled_dates = set(scheduled_weights.keys())
    daily_ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1.0, 10.0)

    dates = close.index
    unit_ret = np.zeros(len(dates), dtype="float64")
    cur_count = np.zeros(len(dates), dtype="float64")
    prev_count = np.zeros(len(dates), dtype="float64")
    overlap = np.zeros(len(dates), dtype="float64")

    current = pd.Series(dtype="float64")
    previous_names = set()
    previous_count = 0
    for i, dt in enumerate(dates):
        if i == 0:
            continue
        if dt in scheduled_dates:
            current = weights.loc[dt].dropna()
        names = set(current.index)
        n = len(names)
        if n:
            values = current.values.astype("float64")
            if not np.allclose(values, 1.0 / n):
                raise ValueError("fast optimizer currently assumes equal-weight baskets")
            unit_ret[i] = float(daily_ret.loc[dt, list(names)].mean())
        cur_count[i] = n
        prev_count[i] = previous_count
        overlap[i] = len(names & previous_names)
        previous_names = names
        previous_count = n

    return {
        "dates": dates,
        "unit_ret": unit_ret,
        "cur_count": cur_count,
        "prev_count": prev_count,
        "overlap": overlap,
    }


def fast_returns(exposures, path, cfg):
    """Vectorized return simulation for candidate exposure matrix C x T."""
    e = np.asarray(exposures, dtype="float64").clip(0.0, 1.0)
    c, t = e.shape
    prev = np.concatenate([np.zeros((c, 1)), e[:, :-1]], axis=1)

    n = np.maximum(path["cur_count"], 1.0)
    prev_n = np.maximum(path["prev_count"], 1.0)
    overlap = path["overlap"]
    new_count = path["cur_count"] - overlap
    removed_count = path["prev_count"] - overlap

    cur_w = e / n[None, :]
    prev_w = prev / prev_n[None, :]
    buy = new_count[None, :] * cur_w + overlap[None, :] * np.maximum(cur_w - prev_w, 0.0)
    sell = removed_count[None, :] * prev_w + overlap[None, :] * np.maximum(prev_w - cur_w, 0.0)
    buy[:, 0] = 0.0
    sell[:, 0] = 0.0

    trade_cost = (buy * cfg.cost.buy_cost + sell * cfg.cost.sell_cost) * cfg.leverage
    has_position = (e > 0.0) & (path["cur_count"][None, :] > 0)
    financing = np.where(has_position, (cfg.leverage - 1.0) * cfg.cost.financing_rate / 252.0, 0.0)
    gross = e * path["unit_ret"][None, :] * cfg.leverage
    ret = gross - trade_cost - financing
    return ret[:, 1:], trade_cost[:, 1:] + financing[:, 1:]


def batch_metrics(ret, dates, start_year):
    mask = dates.year >= start_year
    x = ret[:, mask]
    annual = x.mean(axis=1) * 252
    vol = x.std(axis=1, ddof=1) * np.sqrt(252)
    sharpe = np.divide(annual, vol, out=np.zeros_like(annual), where=vol > 0)
    cum = np.cumprod(1.0 + x, axis=1)
    peak = np.maximum.accumulate(cum, axis=1)
    maxdd = np.min(cum / peak - 1.0, axis=1)
    calmar = np.divide(annual, np.abs(maxdd), out=np.zeros_like(annual), where=maxdd < 0)
    return annual, maxdd, sharpe, calmar


def load_probabilities(close_index):
    daily = pd.read_csv(DAILY_PATH, parse_dates=["date"])
    probs = {}
    for lb in [756, 1008, 1260]:
        sub = daily[daily["label"].str.contains(f"lb{lb} rt60")]
        label = sub["label"].iloc[0]
        probs[lb] = sub[sub["label"] == label].set_index("date")["prob_stress"].reindex(close_index)
    return probs


def candidate_exposures(prob, base_timing):
    labels = []
    rows = []
    p = prob.reindex(base_timing.index).fillna(1.0).clip(0.0, 1.0)

    for th in np.round(np.arange(0.05, 0.96, 0.01), 2):
        for floor in np.round(np.arange(0.1, 1.01, 0.05), 2):
            labels.append(f"floor th{th:.2f} floor{floor:.2f}")
            rows.append(np.where(p.values > th, floor, 1.0) * base_timing.values)

    for floor in np.round(np.arange(0.1, 0.91, 0.05), 2):
        for power in [0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]:
            labels.append(f"soft floor{floor:.2f} pow{power:.2f}")
            rows.append((floor + (1.0 - floor) * ((1.0 - p.values) ** power)) * base_timing.values)

    for span in [3, 5, 10, 20, 40]:
        smooth = p.ewm(span=span, adjust=False).mean()
        for th in np.round(np.arange(0.10, 0.91, 0.05), 2):
            for floor in [0.3, 0.4, 0.5, 0.6, 0.7]:
                labels.append(f"ema span{span} th{th:.2f} floor{floor:.1f}")
                rows.append(np.where(smooth.values > th, floor, 1.0) * base_timing.values)

    return labels, np.vstack(rows)


def rows_from_batch(labels, lb, ret, exposure, dates):
    a18, d18, s18, c18 = batch_metrics(ret, dates, 2018)
    a23, d23, s23, c23 = batch_metrics(ret, dates, 2023)
    a10, d10, s10, c10 = batch_metrics(ret, dates, 2010)
    avg_exp = exposure[:, dates.year >= 2018].mean(axis=1)
    objective = (0.5 * s18 + 0.5 * c18) * np.minimum(a18 / 0.20, 1.0)
    return pd.DataFrame(
        {
            "label": [f"lb{lb} {x}" for x in labels],
            "lb": lb,
            "annual_2018": a18,
            "maxdd_2018": d18,
            "sharpe_2018": s18,
            "calmar_2018": c18,
            "annual_2023": a23,
            "maxdd_2023": d23,
            "sharpe_2023": s23,
            "calmar_2023": c23,
            "annual_2010": a10,
            "maxdd_2010": d10,
            "sharpe_2010": s10,
            "calmar_2010": c10,
            "avg_exposure_2018": avg_exp,
            "objective": objective,
        }
    )


def validate_fast_path(close, scheduled, base_timing, path, cfg):
    checks = [
        ("floor th0.60 floor0.40", 0.60, 0.40),
        ("floor th0.30 floor0.50", 0.30, 0.50),
    ]
    prob = load_probabilities(close.index)[1008]
    for label, th, floor in checks:
        guard = pd.Series(np.where(prob.fillna(1.0) > th, floor, 1.0), index=close.index)
        exposure = base_timing * guard
        full, _ = backtest_weights(close, scheduled, exposure, cfg)
        fast, _ = fast_returns(exposure.values[None, :], path, cfg)
        fast_s = pd.Series(fast[0], index=close.index[1:])
        err = float(np.nanmax(np.abs(full.reindex(fast_s.index).values - fast_s.values)))
        if err > 1e-12:
            raise AssertionError(f"fast path mismatch for {label}: {err}")


def main():
    t0 = time.perf_counter()
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    cfg = StrategyConfig(start="2010-01-01")
    base = run_small_cap_strategy(cfg)
    close = base["close"]
    scheduled = base["scheduled_weights"]
    base_timing = base["timing"].astype(float).reindex(close.index).fillna(0.0)

    path = precompute_equal_weight_path(close, scheduled)
    validate_fast_path(close, scheduled, base_timing, path, cfg)

    frames = []
    probs = load_probabilities(close.index)
    dates = close.index[1:]
    for lb, prob in probs.items():
        labels, exposure = candidate_exposures(prob, base_timing)
        ret, _ = fast_returns(exposure, path, cfg)
        frames.append(rows_from_batch(labels, lb, ret, exposure[:, 1:], dates))

    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["objective", "sharpe_2018", "annual_2018"], ascending=[False, False, False])
    out.to_csv(OUT_PATH, index=False)
    elapsed = time.perf_counter() - t0
    print(f"Wrote: {OUT_PATH}")
    print(f"Candidates: {len(out)} | elapsed: {elapsed:.2f}s")
    print(out.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
