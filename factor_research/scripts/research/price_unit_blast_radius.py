"""Compare contaminated and canonical price-unit strategy paths.

Default mode is read-only. Registry evidence is changed only with
``--apply-invalidation`` and always through ``strategy_registry``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from engine.metrics import metrics
from factors.small_cap import small_cap_timing
from factors.veto import salience_covariance_veto
from strategies.small_cap import build_rebalance_weights

CURRENT_PRICE = ROOT / "data_lake/price/daily_all.parquet"
MARKER = ROOT / "data_lake/governance/price_unit_rebuild.json"
DEFAULT_JSON = ROOT / "reports/research/price_unit_blast_radius.json"
DEFAULT_MD = ROOT / "reports/research/price_unit_blast_radius.md"

INCIDENT_CODE = "INVALIDATED_BY_DATA_UNIT_INCIDENT"
INCIDENT_ID = "price-units-20260620"


def _path_metrics(returns: pd.Series) -> dict:
    result = metrics(pd.Series(returns).dropna())
    return {
        key: float(result.get(key, 0.0))
        for key in ("annual", "sharpe", "maxdd")
    }


def compare_strategy_paths(
    old_factor: pd.DataFrame,
    new_factor: pd.DataFrame,
    old_returns: pd.Series,
    new_returns: pd.Series,
    *,
    top_n: int = 25,
) -> dict:
    factor_dates = old_factor.index.intersection(new_factor.index)
    overlaps, jaccards, spearmans = [], [], []
    for date in factor_dates:
        left = old_factor.loc[date].dropna()
        right = new_factor.loc[date].dropna()
        common = left.index.intersection(right.index)
        if len(common) < 2:
            continue
        left_top = set(left.loc[common].nlargest(top_n).index)
        right_top = set(right.loc[common].nlargest(top_n).index)
        if left_top or right_top:
            intersection = len(left_top & right_top)
            overlaps.append(intersection / max(1, min(top_n, len(left_top), len(right_top))))
            jaccards.append(intersection / max(1, len(left_top | right_top)))
        corr = left.loc[common].corr(right.loc[common], method="spearman")
        if pd.notna(corr):
            spearmans.append(float(corr))

    common_returns = old_returns.index.intersection(new_returns.index)
    old_r = old_returns.reindex(common_returns).fillna(0.0)
    new_r = new_returns.reindex(common_returns).fillna(0.0)
    old_m, new_m = _path_metrics(old_r), _path_metrics(new_r)
    old_cum = float((1.0 + old_r).prod() - 1.0)
    new_cum = float((1.0 + new_r).prod() - 1.0)
    return {
        "top_n_overlap": float(np.mean(overlaps)) if overlaps else 0.0,
        "top_n_jaccard": float(np.mean(jaccards)) if jaccards else 0.0,
        "factor_spearman": float(np.mean(spearmans)) if spearmans else 0.0,
        "daily_return_abs_mean": float((new_r - old_r).abs().mean()),
        "cumulative_return_delta": new_cum - old_cum,
        "old_metrics": old_m,
        "new_metrics": new_m,
        "annual_delta": new_m["annual"] - old_m["annual"],
        "sharpe_delta": new_m["sharpe"] - old_m["sharpe"],
        "maxdd_delta": new_m["maxdd"] - old_m["maxdd"],
    }


def invalidation_reasons(comparison: dict) -> list[str]:
    reasons = []
    if comparison.get("top_n_overlap", 0.0) < 0.80:
        reasons.append("top25_overlap_below_0.80")
    if comparison.get("factor_spearman", 0.0) < 0.95:
        reasons.append("factor_spearman_below_0.95")
    if abs(comparison.get("annual_delta", 0.0)) > 0.02:
        reasons.append("annual_delta_above_2pct")
    if abs(comparison.get("sharpe_delta", 0.0)) > 0.10:
        reasons.append("sharpe_delta_above_0.10")
    if abs(comparison.get("maxdd_delta", 0.0)) > 0.02:
        reasons.append("maxdd_delta_above_2pct")
    return reasons


def _load_panel(path: Path, start: str) -> PricePanel:
    frame = pd.read_parquet(
        path,
        columns=["date", "code", "close", "volume", "amount"],
        filters=[("date", ">=", pd.Timestamp(start))],
    )
    frame["date"] = pd.to_datetime(frame["date"])
    fields = {
        field: frame.pivot(index="date", columns="code", values=field).sort_index()
        for field in ("close", "volume", "amount")
    }
    return PricePanel(**fields)


def _run_factor(
    prices: PricePanel,
    factor: pd.DataFrame,
    *,
    timing: pd.Series,
    veto: pd.DataFrame | None = None,
    leverage: float = 1.0,
    universe: int | None = None,
) -> pd.Series:
    if universe is None:
        weights = build_rebalance_weights(
            factor,
            prices.close,
            top_n=25,
            rebalance_days=20,
            veto_factor=veto,
            veto_q=0.30,
        )
    else:
        adv = prices.amount.rolling(20).mean()
        weights = {}
        dates = factor.dropna(how="all").index.intersection(prices.close.index)
        for date in dates[::20]:
            f = factor.loc[date].dropna()
            pool = adv.loc[date].dropna().nlargest(universe).index
            selected = f.reindex(pool).dropna().nlargest(25)
            if len(selected) == 25:
                weights[date] = pd.Series(1.0 / 25, index=selected.index)
    config = BacktestConfig(
        start=str(prices.close.index[0].date()),
        leverage=leverage,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
    )
    return BacktestEngine(prices, config).run(
        Signal(
            decision_weights=weights,
            timing=timing,
            execution_timing="T_PLUS_1_CLOSE",
        )
    ).returns


def _strategy_paths(prices: PricePanel) -> dict:
    close, amount = prices.close, prices.amount
    amihud = (close.pct_change(fill_method=None).abs() / amount.replace(0, np.nan)).rolling(20).mean()
    size = -np.log(amount.rolling(60).mean().replace(0, np.nan))
    binary, _, dist = small_cap_timing(close, amount, 16)
    band = (1.0 + dist.clip(-0.5, 0.5) * 8.0).clip(0.0, 1.5)
    band = band.where(dist > 0, 0.0).shift(1).fillna(0.0)
    veto = salience_covariance_veto(close).shift(1)
    market = (1.0 + close.pct_change(fill_method=None).fillna(0.0).mean(axis=1)).cumprod()
    market_timing = (market > market.rolling(16).mean()).astype(float).shift(1).fillna(0.0)
    return {
        "illiquidity/v3.1": (
            amihud,
            _run_factor(prices, amihud, timing=band, veto=veto, leverage=1.0),
        ),
        "small-cap-size/v2.0": (
            size,
            _run_factor(prices, size, timing=binary.astype(float), leverage=1.25),
        ),
        "illiquidity-large-cap/v1.0": (
            amihud,
            _run_factor(
                prices,
                amihud,
                timing=market_timing,
                leverage=1.25,
                universe=800,
            ),
        ),
    }


def _autoresearch_amount_candidates() -> list[str]:
    path = ROOT / "data_lake/factory/autoresearch/candidates.jsonl"
    if not path.exists():
        return []
    affected = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        ast_text = json.dumps(record.get("ast") or {}, ensure_ascii=False).lower()
        if "amount" in ast_text or "illiquidity" in ast_text or "volume_ratio" in ast_text:
            affected.append(record.get("fingerprint", ""))
    return sorted({item for item in affected if item})


def run_blast_radius(
    backup_price: Path,
    current_price: Path = CURRENT_PRICE,
    *,
    start: str = "2018-01-01",
) -> dict:
    old_paths = _strategy_paths(_load_panel(backup_price, start))
    new_paths = _strategy_paths(_load_panel(current_price, start))
    strategies = {}
    for name in old_paths:
        old_factor, old_returns = old_paths[name]
        new_factor, new_returns = new_paths[name]
        comparison = compare_strategy_paths(
            old_factor,
            new_factor,
            old_returns,
            new_returns,
        )
        reasons = invalidation_reasons(comparison)
        strategies[name] = {
            **comparison,
            "invalidation_reasons": reasons,
            "evidence_status": INCIDENT_CODE if reasons else "UNCHANGED",
        }
    latest = max(path[0].index.max() for path in new_paths.values())
    strategies["current-production-signal/top25"] = {
        "as_of_date": str(pd.Timestamp(latest).date()),
        "covered_by": "illiquidity/v3.1",
        "top_n_overlap": strategies["illiquidity/v3.1"]["top_n_overlap"],
    }
    return {
        "incident_id": INCIDENT_ID,
        "start": start,
        "backup_price": str(backup_price),
        "current_price": str(current_price),
        "strategies": strategies,
        "amount_dependent_autoresearch_candidates": _autoresearch_amount_candidates(),
    }


def _write_markdown(report: dict, path: Path) -> None:
    lines = ["# Price Unit Incident Blast Radius", ""]
    for name, result in report["strategies"].items():
        lines += [
            f"## {name}",
            "",
            f"- evidence: {result.get('evidence_status', 'comparison')}",
            f"- Top-N overlap: {result.get('top_n_overlap')}",
            f"- Spearman: {result.get('factor_spearman')}",
            f"- annual Δ: {result.get('annual_delta')}",
            f"- Sharpe Δ: {result.get('sharpe_delta')}",
            f"- max drawdown Δ: {result.get('maxdd_delta')}",
            f"- reasons: {result.get('invalidation_reasons', [])}",
            "",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def _apply_invalidations(report: dict) -> None:
    import strategy_registry

    for identity, result in report["strategies"].items():
        if not result.get("invalidation_reasons") or "/" not in identity:
            continue
        family, version = identity.split("/", 1)
        strategy_registry.attach_data_incident(
            family,
            version,
            {
                "code": INCIDENT_CODE,
                "incident_id": INCIDENT_ID,
                "resolved": False,
                "reasons": result["invalidation_reasons"],
            },
        )


def main() -> int:
    marker = json.loads(MARKER.read_text())
    default_backup = Path(marker["backup_path"]) / "daily_all.parquet"
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-price", type=Path, default=default_backup)
    parser.add_argument("--current-price", type=Path, default=CURRENT_PRICE)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--apply-invalidation", action="store_true")
    args = parser.parse_args()
    report = run_blast_radius(args.backup_price, args.current_price, start=args.start)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float))
    _write_markdown(report, args.output_md)
    if args.apply_invalidation:
        _apply_invalidations(report)
    print(json.dumps({
        "output_json": str(args.output_json),
        "output_md": str(args.output_md),
        "invalidated": [
            name for name, result in report["strategies"].items()
            if result.get("invalidation_reasons")
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
