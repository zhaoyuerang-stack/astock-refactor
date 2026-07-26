#!/usr/bin/env python3
"""Upstream contamination A/B diagnostic (Phase 1 — measure only, no default-path change).

Compares price-volume factors computed on:
  A  contaminated path  — raw close enters rolling windows (current common practice)
  B  clean path         — limit/suspend days NaN'd *before* rolling (feature-tradable mask)

Optional IC universe:
  *_all  — IC over all codes with valid factor & forward ret
  *_uni  — IC restricted to feature-tradable names on the signal date
            (separates window pollution from cross-section filter effects)

Holdout: end is clipped to < governance.holdout.boundary() (never touches gold vault).

Not alpha evidence (R-LLM-001). Paper numbers (arXiv:2507.07107) are hypotheses only;
only this run's local numbers may be cited in reports.

Usage (cwd=factor_research):
  python -m scripts.research.upstream_contamination_ab
  python -m scripts.research.upstream_contamination_ab --start 2019-01-01 --json \\
      reports/research/upstream_contamination_ab.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from engine.factor_analysis import calc_ic, ic_summary, newey_west_icir  # noqa: E402
from factors.momentum import (  # noqa: E402
    illiquidity,
    mom_n as mom_n_factor,
    price_to_ma,
    volatility,
)
from factors.tradable_mask import (  # noqa: E402
    at_limit_flags,
    factor_illiquidity_clean,
    factor_momentum_clean,
    factor_price_to_ma_clean,
    factor_volatility_clean,
    load_feature_tradable_context,
    mask_summary,
)
from governance.holdout import boundary as holdout_boundary  # noqa: E402

DEFAULT_START = "2019-01-01"
DEFAULT_JSON = ROOT / "reports" / "research" / "upstream_contamination_ab.json"
DEFAULT_MD = ROOT / "reports" / "research" / "upstream_contamination_ab.md"


def _clip_end(end: str | None) -> pd.Timestamp:
    """Research end exclusive of holdout vault."""
    b = holdout_boundary()
    if end is None:
        return b - pd.Timedelta(days=1)
    e = pd.Timestamp(end)
    if e >= b:
        print(f"[holdout] clipping end {e.date()} → {(b - pd.Timedelta(days=1)).date()} (< {b.date()})")
        return b - pd.Timedelta(days=1)
    return e


def _monthly_rebalance_dates(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    d = index[(index >= start) & (index <= end)]
    if len(d) == 0:
        return []
    last = pd.Series(d, index=d).groupby([d.year, d.month]).last()
    return [pd.Timestamp(x) for x in last.tolist()]


def _forward_returns_on_rebalance(close: pd.DataFrame, rb: list[pd.Timestamp]) -> pd.DataFrame:
    """Month-to-month forward return panel indexed by rebalance date (signal date)."""
    rows = {}
    for i in range(len(rb) - 1):
        d0, d1 = rb[i], rb[i + 1]
        if d0 not in close.index or d1 not in close.index:
            continue
        rows[d0] = close.loc[d1] / close.loc[d0] - 1.0
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).T.sort_index()


def _apply_universe(factor: pd.DataFrame, tradable: pd.DataFrame) -> pd.DataFrame:
    m = tradable.reindex(index=factor.index, columns=factor.columns).fillna(False)
    return factor.where(m)


def _ic_pack(factor: pd.DataFrame, fwd: pd.DataFrame, *, max_lag: int = 1) -> dict:
    ic = calc_ic(factor, fwd, method="rank")
    if ic.empty:
        return {
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "icir": float("nan"),
            "nw_icir": float("nan"),
            "ic_pos_ratio": float("nan"),
            "n_ic": 0,
        }
    summary = ic_summary(ic)
    return {
        "ic_mean": float(summary["IC_mean"]),
        "ic_std": float(summary["IC_std"]),
        "icir": float(summary["ICIR"]) if pd.notna(summary["ICIR"]) else float("nan"),
        "nw_icir": float(newey_west_icir(ic, max_lag=max_lag)),
        "ic_pos_ratio": float(summary["IC>0_ratio"]),
        "n_ic": int(summary["count"]),
    }


def _corr_ab(a: pd.DataFrame, b: pd.DataFrame, dates: list[pd.Timestamp], min_obs: int = 30) -> float:
    """Mean cross-sectional Spearman(A,B) on rebalance dates."""
    vals = []
    for dt in dates:
        if dt not in a.index or dt not in b.index:
            continue
        x = a.loc[dt]
        y = b.loc[dt]
        common = x.index.intersection(y.index)
        m = x.loc[common].notna() & y.loc[common].notna()
        if int(m.sum()) < min_obs:
            continue
        from scipy.stats import spearmanr

        rho, _ = spearmanr(x.loc[common][m], y.loc[common][m])
        if np.isfinite(rho):
            vals.append(float(rho))
    return float(np.mean(vals)) if vals else float("nan")


def _factor_pair(
    name: str,
    dirty: pd.DataFrame,
    clean: pd.DataFrame,
    tradable: pd.DataFrame,
    fwd: pd.DataFrame,
    rb: list[pd.Timestamp],
) -> dict:
    dirty_u = _apply_universe(dirty, tradable)
    clean_u = _apply_universe(clean, tradable)
    pack = {
        "factor": name,
        "A_all": _ic_pack(dirty, fwd),
        "B_all": _ic_pack(clean, fwd),
        "A_uni": _ic_pack(dirty_u, fwd),
        "B_uni": _ic_pack(clean_u, fwd),
        "cs_spearman_A_B": _corr_ab(dirty, clean, rb),
        "cs_spearman_A_B_uni": _corr_ab(dirty_u, clean_u, rb),
    }
    # Signed and |IC| gaps. For reversal factors (IC<0), |IC| inflation is the
    # relevant "looks better in research" metric; signed A/B-1 alone is ambiguous.
    for tag in ("all", "uni"):
        a = pack[f"A_{tag}"]["ic_mean"]
        b = pack[f"B_{tag}"]["ic_mean"]
        if np.isfinite(a) and np.isfinite(b) and abs(b) > 1e-12:
            pack[f"ic_mean_rel_A_over_B_{tag}"] = float(a / b - 1.0)
        else:
            pack[f"ic_mean_rel_A_over_B_{tag}"] = float("nan")
        if np.isfinite(a) and np.isfinite(b):
            pack[f"ic_mean_diff_A_minus_B_{tag}"] = float(a - b)
            pack[f"abs_ic_diff_A_minus_B_{tag}"] = float(abs(a) - abs(b))
        else:
            pack[f"ic_mean_diff_A_minus_B_{tag}"] = float("nan")
            pack[f"abs_ic_diff_A_minus_B_{tag}"] = float("nan")
        if np.isfinite(a) and np.isfinite(b) and abs(b) > 1e-12:
            pack[f"abs_ic_rel_A_over_B_{tag}"] = float(abs(a) / abs(b) - 1.0)
        else:
            pack[f"abs_ic_rel_A_over_B_{tag}"] = float("nan")
    return pack


def run(
    *,
    start: str = DEFAULT_START,
    end: str | None = None,
    price_tol: float = 0.01,
    mom_n: int = 20,
    vol_n: int = 20,
    ma_n: int = 20,
    illiq_n: int = 20,
) -> dict:
    t0 = time.time()
    start_ts = pd.Timestamp(start)
    end_ts = _clip_end(end)
    if end_ts < start_ts:
        raise ValueError(f"empty window: start={start_ts.date()} end={end_ts.date()}")

    print(
        f"[load] feature_tradable_context start={start_ts.date()} end={end_ts.date()} "
        f"holdout_boundary={holdout_boundary().date()}"
    )
    ctx = load_feature_tradable_context(
        str(start_ts.date()),
        end=str(end_ts.date()),
        price_tol=price_tol,
    )
    close = ctx.close
    volume = ctx.volume
    raw = ctx.raw_close
    up = ctx.up_limit
    dn = ctx.down_limit
    tradable = ctx.tradable
    at_up, at_dn = at_limit_flags(raw, up, dn, price_tol=price_tol)
    # Restrict summary stats to evaluation window (after warmup)
    eval_slice = (tradable.index >= start_ts) & (tradable.index <= end_ts)
    tradable_eval = tradable.loc[eval_slice]
    msum = mask_summary(tradable_eval)
    at_up_rate = float(at_up.loc[eval_slice].to_numpy(dtype=bool, na_value=False).mean())
    at_dn_rate = float(at_dn.loc[eval_slice].to_numpy(dtype=bool, na_value=False).mean())
    vol0_rate = float((volume.loc[eval_slice].fillna(0) <= 0).to_numpy().mean())

    print(
        f"[mask] tradable_rate={msum['tradable_rate']:.4%} "
        f"at_up={at_up_rate:.4%} at_dn={at_dn_rate:.4%} vol0={vol0_rate:.4%} "
        f"shape={tradable_eval.shape}"
    )

    # Factors (full path then slice IC dates)
    print("[factor] computing A (dirty) / B (clean)…")
    pairs = {
        f"momentum_{mom_n}": (
            mom_n_factor(close, mom_n),
            factor_momentum_clean(close, tradable, mom_n),
        ),
        f"volatility_{vol_n}": (
            volatility(close, vol_n),
            factor_volatility_clean(close, tradable, vol_n),
        ),
        f"price_to_ma_{ma_n}": (
            price_to_ma(close, ma_n),
            factor_price_to_ma_clean(close, tradable, ma_n),
        ),
        f"illiquidity_{illiq_n}": (
            illiquidity(close, volume, illiq_n),
            factor_illiquidity_clean(close, volume, tradable, illiq_n),
        ),
    }

    rb = _monthly_rebalance_dates(close.index, start_ts, end_ts)
    fwd = _forward_returns_on_rebalance(close, rb)
    print(f"[ic] monthly rebalances={len(rb)} fwd_rows={len(fwd)}")

    factor_rows = []
    for name, (dirty, clean) in pairs.items():
        row = _factor_pair(name, dirty, clean, tradable, fwd, rb)
        factor_rows.append(row)
        abs_rel = row.get("abs_ic_rel_A_over_B_all")
        abs_diff = row.get("abs_ic_diff_A_minus_B_all")
        print(
            f"  {name}: IC_A={row['A_all']['ic_mean']:+.4f} IC_B={row['B_all']['ic_mean']:+.4f} "
            f"|IC|Δ(A−B)={abs_diff:+.4f} |IC|rel={abs_rel if abs_rel == abs_rel else float('nan'):+.2%} "
            f"ρ(A,B)={row['cs_spearman_A_B']:.4f}"
        )

    elapsed = time.time() - t0
    report = {
        "meta": {
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "citation_hypothesis_only": "arXiv:2507.07107 (USTC); numbers below are local recompute only",
            "holdout_boundary": str(holdout_boundary().date()),
            "start": str(start_ts.date()),
            "end": str(end_ts.date()),
            "price_tol": price_tol,
            "rebalance": "month_end",
            "forward": "month_to_month close/close-1",
            "ic": "cross-sectional Spearman rank IC on rebalance dates",
            "default_path_changed": False,
            "elapsed_sec": round(elapsed, 1),
            "n_dates": int(len(close.index)),
            "n_codes": int(len(close.columns)),
        },
        "mask": {
            **msum,
            "at_up_rate": at_up_rate,
            "at_down_rate": at_dn_rate,
            "volume_zero_rate": vol0_rate,
            "price_tol": price_tol,
        },
        "factors": factor_rows,
        "reading_guide": {
            "A_all": "contaminated windows; IC on all valid names",
            "B_all": "clean windows; IC on all valid names",
            "A_uni / B_uni": "same factors; IC restricted to feature-tradable on signal date",
            "ic_mean_rel_A_over_B_* > 0": "contaminated path shows higher |signed| IC mean than clean — possible inflation",
            "cs_spearman_A_B": "how much cross-sectional ranks still agree after cleansing",
            "not_evidence_of_alpha": True,
        },
    }
    return report


def _fmt_ic(pack: dict) -> str:
    return (
        f"IC={pack['ic_mean']:+.4f} ICIR={pack['icir']:+.3f} "
        f"NW={pack['nw_icir']:+.3f} n={pack['n_ic']}"
    )


def to_markdown(report: dict) -> str:
    m = report["meta"]
    mask = report["mask"]
    lines = [
        "# Upstream contamination A/B diagnostic (Phase 1)",
        "",
        f"> Generated: `{m['created_at']}` · window `{m['start']}` → `{m['end']}` "
        f"(holdout boundary `{m['holdout_boundary']}`, vault never touched).",
        "",
        "## Scope",
        "",
        "- **Not alpha evidence** (R-LLM-001). Local measurement of construction bias only.",
        "- Paper pointer (hypothesis only): arXiv:2507.07107 — do not cite paper IC/Sharpe as ours.",
        "- **Default factor / engine paths unchanged** (`default_path_changed: false`).",
        "- Feature mask: raw_close within `price_tol` of `stk_limit` up/down, or volume≤0 → non-tradable;",
        "  non-tradable closes become NaN **before** rolling.",
        "",
        "## Mask coverage",
        "",
        f"| metric | value |",
        f"| --- | --- |",
        f"| tradable_rate | {mask['tradable_rate']:.4%} |",
        f"| at_up_rate | {mask['at_up_rate']:.4%} |",
        f"| at_down_rate | {mask['at_down_rate']:.4%} |",
        f"| volume_zero_rate | {mask['volume_zero_rate']:.4%} |",
        f"| price_tol | {mask['price_tol']} |",
        f"| n_dates × n_codes | {mask['n_dates']} × {mask['n_codes']} |",
        "",
        "## Factor A/B (monthly rank IC)",
        "",
        "| factor | IC_A (dirty) | IC_B (clean) | \\|IC\\|Δ(A−B) | \\|IC\\|rel | ρ(A,B) | IC_A_uni | IC_B_uni |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["factors"]:
        lines.append(
            f"| {row['factor']} "
            f"| {row['A_all']['ic_mean']:+.4f} "
            f"| {row['B_all']['ic_mean']:+.4f} "
            f"| {row['abs_ic_diff_A_minus_B_all']:+.4f} "
            f"| {row['abs_ic_rel_A_over_B_all']:+.2%} "
            f"| {row['cs_spearman_A_B']:.4f} "
            f"| {row['A_uni']['ic_mean']:+.4f} "
            f"| {row['B_uni']['ic_mean']:+.4f} |"
        )
    lines += [
        "",
        "### Detail",
        "",
    ]
    for row in report["factors"]:
        lines += [
            f"**{row['factor']}**",
            f"- A_all: {_fmt_ic(row['A_all'])}",
            f"- B_all: {_fmt_ic(row['B_all'])}",
            f"- A_uni: {_fmt_ic(row['A_uni'])}",
            f"- B_uni: {_fmt_ic(row['B_uni'])}",
            f"- cs_spearman(A,B)={row['cs_spearman_A_B']:.4f} (uni={row['cs_spearman_A_B_uni']:.4f})",
            "",
        ]
    lines += [
        "## How to read",
        "",
        "- **|IC|Δ / |IC|rel on `*_all`**: contamination effect on measured **strength** "
        "(|IC_A|−|IC_B|; positive ⇒ dirty path looks stronger — classic inflation).",
        "- Signed IC for A-share price factors is often **negative** (reversal); use |IC| metrics first.",
        "- **`*_uni`**: IC restricted to feature-tradable names on the signal date — "
        "if A_all moves a lot vs A_uni but A_uni ≈ B_uni, inflation was mostly bad IC universe.",
        "- **ρ(A,B) near 1**: cleansing barely reorders names; lower ρ (e.g. vol): ranks move.",
        "- Large |IC| gap or ρ≪1 ⇒ prioritize Phase 2 for that factor family.",
        "- Small gaps ⇒ keep the utility; no urgent full-library migration.",
        "",
        "## Next (not done here)",
        "",
        "1. Phase 2: load-path helper for clean panels + factor_store cache key includes mask version.",
        "2. Align `core.engine` fill constraints with `paper_engine` open-limit rules (execution mask).",
        "3. Optional NOTE in `direction_registry` / LESSONS once Phase 1 numbers are owner-reviewed.",
        "",
        f"_elapsed {m['elapsed_sec']}s · n_dates={m['n_dates']} n_codes={m['n_codes']}_",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=None, help="inclusive end; default = day before holdout boundary")
    p.add_argument("--price-tol", type=float, default=0.01)
    p.add_argument("--mom-n", type=int, default=20)
    p.add_argument("--vol-n", type=int, default=20)
    p.add_argument("--ma-n", type=int, default=20)
    p.add_argument("--illiq-n", type=int, default=20)
    p.add_argument("--json", type=Path, default=DEFAULT_JSON)
    p.add_argument("--md", type=Path, default=DEFAULT_MD)
    p.add_argument("--no-write", action="store_true", help="print only, do not write report files")
    args = p.parse_args(argv)

    report = run(
        start=args.start,
        end=args.end,
        price_tol=args.price_tol,
        mom_n=args.mom_n,
        vol_n=args.vol_n,
        ma_n=args.ma_n,
        illiq_n=args.illiq_n,
    )
    md = to_markdown(report)
    print("\n" + md)

    if not args.no_write:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        args.md.write_text(md if md.endswith("\n") else md + "\n")
        print(f"[write] {args.json}")
        print(f"[write] {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
