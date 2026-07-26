#!/usr/bin/env python3
"""Research-only audit for the MA regime -> 511010 defensive overlay.

This script is deliberately not a registry or deployment path. It builds a
structured evidence packet for human review and keeps all production write
flags false.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.analysis.walk_forward import deflated_sharpe
from factors.small_cap import small_cap_timing
from lake.cross_asset import load_etf_daily
from strategies.small_cap import load_price_panels


def performance_metrics(returns: pd.Series) -> dict:
    """Daily-return metrics for research comparison, not admission."""
    ret = pd.Series(returns, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if ret.empty:
        return {
            "n_days": 0,
            "cumulative_return": 0.0,
            "annual_return": 0.0,
            "annual_volatility": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
        }

    annual_return = float(ret.mean() * 252)
    annual_volatility = float(ret.std(ddof=0) * np.sqrt(252))
    sharpe = annual_return / annual_volatility if annual_volatility > 0 else 0.0
    nav = (1.0 + ret).cumprod()
    max_drawdown = float((nav / nav.cummax() - 1.0).min())

    return {
        "n_days": int(len(ret)),
        "cumulative_return": float(nav.iloc[-1] - 1.0),
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": float(sharpe),
        "max_drawdown": max_drawdown,
    }


def _bond_return_series(bond_close: pd.Series, index: pd.Index) -> pd.Series:
    close = pd.Series(bond_close, dtype="float64")
    close.index = pd.to_datetime(close.index)
    return close.sort_index().pct_change(fill_method=None).reindex(index).fillna(0.0)


def _rotation_returns(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    bond_close: pd.Series,
    ma_window: int,
) -> dict:
    timing, small_nav, dist = small_cap_timing(close, amount, ma_window=ma_window)
    index = small_nav.index.intersection(pd.to_datetime(bond_close.index))
    small_ret = small_nav.pct_change(fill_method=None).reindex(index).fillna(0.0)
    bond_ret = _bond_return_series(bond_close, index)
    bull = timing.reindex(index, fill_value=False).astype(bool)

    cash_strategy = small_ret.where(bull, 0.0)
    bond_strategy = small_ret.where(bull, bond_ret)
    bond_when_bear = bond_ret.where(~bull, 0.0)

    return {
        "bull": bull,
        "dist": dist.reindex(index),
        "small_cap_ma_cash": cash_strategy,
        "small_cap_ma_bond": bond_strategy,
        "bond_when_bear": bond_when_bear,
        "incremental_bond_vs_cash": bond_strategy - cash_strategy,
    }


def audit_defensive_overlay(
    close: pd.DataFrame,
    amount: pd.DataFrame,
    bond_close: pd.Series,
    *,
    ma_window: int = 16,
    trial_windows: list[int] | None = None,
    start: str | None = None,
) -> dict:
    """Build a non-deploying evidence packet for MA + 511010 rotation."""
    if trial_windows is None:
        trial_windows = list(range(5, 61))
    if ma_window not in trial_windows:
        trial_windows = [*trial_windows, ma_window]
    trial_windows = sorted({int(w) for w in trial_windows})

    eval_start = pd.Timestamp(start) if start is not None else None
    close = close.copy()
    amount = amount.reindex(index=close.index, columns=close.columns)
    bond_close = pd.Series(bond_close).copy()

    selected = _rotation_returns(close, amount, bond_close, ma_window)
    if eval_start is not None:
        selected = {key: value.loc[value.index >= eval_start] for key, value in selected.items()}
    bull = selected["bull"]
    bear_days = int((~bull).sum())
    bull_days = int(bull.sum())

    metrics = {
        "small_cap_ma_cash": performance_metrics(selected["small_cap_ma_cash"]),
        "small_cap_ma_bond": performance_metrics(selected["small_cap_ma_bond"]),
        "bond_when_bear": performance_metrics(selected["bond_when_bear"]),
        "incremental_bond_vs_cash": performance_metrics(selected["incremental_bond_vs_cash"]),
    }

    trials = []
    for window in trial_windows:
        trial = _rotation_returns(close, amount, bond_close, window)
        if eval_start is not None:
            trial = {key: value.loc[value.index >= eval_start] for key, value in trial.items()}
        m = performance_metrics(trial["small_cap_ma_bond"])
        trials.append(
            {
                "window": int(window),
                "annual_return": m["annual_return"],
                "annual_volatility": m["annual_volatility"],
                "sharpe": m["sharpe"],
                "max_drawdown": m["max_drawdown"],
            }
        )
    best = max(trials, key=lambda row: row["sharpe"]) if trials else None

    selected_ret = selected["small_cap_ma_bond"].dropna()
    if len(selected_ret) > 2:
        dsr = deflated_sharpe(
            observed_sr=metrics["small_cap_ma_bond"]["sharpe"],
            n_trials=max(1, len(trial_windows)),
            n_periods=len(selected_ret),
            skew=float(selected_ret.skew()),
            kurt=float(selected_ret.kurtosis() + 3.0),
            annualized=True,
        )
    else:
        dsr = {
            "dsr": 0.0,
            "p_value": 1.0,
            "e_max_sr": 0.0,
            "significant_05": False,
            "observed_tstat": 0.0,
        }

    verdict = "needs_human_review" if bear_days > 0 and len(selected_ret) > 30 else "blocked"

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate": {
            "family": "defensive-ma-bond",
            "version": f"ma{ma_window}-511010-draft",
            "role": "defensive",
            "overlay": "small_cap_ma_regime_to_511010",
            "status": "draft_research_only",
        },
        "deployment_verdict": verdict,
        "recommendation": "do_not_deploy_without_independent_9_gate",
        "decision_boundary": {
            "registry_write": False,
            "deployment_write": False,
            "production_allowed": False,
        },
        "evidence": {
            "ma_window": int(ma_window),
            "selected_window": int(ma_window),
            "n_trials": int(len(trial_windows)),
            "trial_windows": trial_windows,
            "best_window": int(best["window"]) if best else None,
            "best_sharpe": float(best["sharpe"]) if best else 0.0,
            "selected_sharpe": metrics["small_cap_ma_bond"]["sharpe"],
            "dsr_p_value": float(dsr["p_value"]),
            "dsr_significant_05": bool(dsr["significant_05"]),
            "bull_days": bull_days,
            "bear_days": bear_days,
        },
        "metrics": metrics,
        "parameter_trials": trials,
        "notes": [
            "Research-only packet; it is not a registry admission result.",
            "DSR uses the supplied MA window grid only; full 9-Gate must account for all search freedom.",
            "Cash baseline means bear-regime daily return is zero; bond overlay earns 511010 returns in bear regimes.",
        ],
    }


def load_bond_close(code: str = "511010") -> pd.Series:
    df = load_etf_daily(code)
    if df is None:
        raise FileNotFoundError(f"ETF data not found: data_lake/cross_asset/etf/{code}.parquet")
    close_col = "raw_close" if "raw_close" in df.columns else "close"
    return df.sort_values("date").set_index("date")[close_col].astype("float64")


def write_report(report: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "defensive_overlay_audit.json"
    md_path = output_dir / "defensive_overlay_audit.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    ev = report["evidence"]
    m = report["metrics"]
    lines = [
        "# Defensive Overlay Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Candidate: {report['candidate']['version']} ({report['candidate']['status']})",
        f"Verdict: {report['deployment_verdict']}",
        f"Recommendation: {report['recommendation']}",
        "",
        "## Decision Boundary",
        "",
        f"- Registry write: {report['decision_boundary']['registry_write']}",
        f"- Deployment write: {report['decision_boundary']['deployment_write']}",
        f"- Production allowed: {report['decision_boundary']['production_allowed']}",
        "",
        "## Evidence",
        "",
        f"- Selected MA window: {ev['selected_window']}",
        f"- Trial windows: {ev['n_trials']}",
        f"- Best window by Sharpe: {ev['best_window']} ({ev['best_sharpe']:.3f})",
        f"- Selected Sharpe: {ev['selected_sharpe']:.3f}",
        f"- DSR p-value: {ev['dsr_p_value']:.4f}",
        f"- Bull days / bear days: {ev['bull_days']} / {ev['bear_days']}",
        "",
        "## Metrics",
        "",
        "| Sleeve | Days | Annual | Vol | Sharpe | Max DD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, block in m.items():
        lines.append(
            f"| {name} | {block['n_days']} | {block['annual_return']:.2%} | "
            f"{block['annual_volatility']:.2%} | {block['sharpe']:.2f} | "
            f"{block['max_drawdown']:.2%} |"
        )
    lines.extend(["", "## Notes", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def run_audit(
    *,
    start: str = "2018-01-01",
    history_start: str = "2010-01-01",
    ma_window: int = 16,
    bond_code: str = "511010",
    output_dir: Path | None = None,
) -> dict:
    close, _volume, amount = load_price_panels(history_start)
    bond_close = load_bond_close(bond_code)
    report = audit_defensive_overlay(
        close=close,
        amount=amount,
        bond_close=bond_close,
        ma_window=ma_window,
        start=start,
    )
    if output_dir is not None:
        json_path, md_path = write_report(report, output_dir)
        report["outputs"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Research-only MA defensive overlay audit.")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--history-start", default="2010-01-01")
    parser.add_argument("--ma-window", type=int, default=16)
    parser.add_argument("--bond-code", default="511010")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "research",
        help="Write JSON/Markdown evidence packet here.",
    )
    args = parser.parse_args()

    report = run_audit(
        start=args.start,
        history_start=args.history_start,
        ma_window=args.ma_window,
        bond_code=args.bond_code,
        output_dir=args.output_dir,
    )
    ev = report["evidence"]
    print("Defensive overlay audit complete")
    print(f"  candidate: {report['candidate']['version']}")
    print(f"  verdict: {report['deployment_verdict']}")
    print(f"  production_allowed: {report['decision_boundary']['production_allowed']}")
    print(f"  selected_sharpe: {ev['selected_sharpe']:.3f}")
    print(f"  dsr_p_value: {ev['dsr_p_value']:.4f}")
    if "outputs" in report:
        print(f"  json: {report['outputs']['json']}")
        print(f"  markdown: {report['outputs']['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
