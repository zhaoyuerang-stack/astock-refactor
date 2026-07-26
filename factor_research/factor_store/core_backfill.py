"""Backfill the canonical core factor library into Factor Store."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from factor_store.scoring import (
    evaluate_factor_panel,
    factor_panel_correlation,
    save_factor_score,
)
from factor_store.store import DEFAULT_STORE_ROOT, save_factor_panel
from factors.alpha.base import FactorData
from factors.alpha.builtins.illiq import AmihudIlliq
from factors.composite import size_earnings_factor
from factors.small_cap import small_cap_factor
from factors.utils import mad_clip, safe_zscore


@dataclass(frozen=True)
class CoreBackfillResult:
    factor_ids: dict[str, str]
    correlation_path: Path


CORE_FACTOR_METADATA = {
    "illiquidity": {
        "factor_name": "amihud_illiquidity",
        "version": "v1.0",
        "params": {"window": 20, "transform": "mad_clip_5_zscore"},
        "dependencies": ["price/close", "price/amount"],
        "description": "Amihud mean(|ret| / amount), 20-day rolling window.",
    },
    "small_cap_size": {
        "factor_name": "small_cap_size",
        "version": "v2.0",
        "params": {"window": 60},
        "dependencies": ["price/amount"],
        "description": "Negative log 60-day average amount, MAD clipped and z-scored.",
    },
    "size_earnings": {
        "factor_name": "size_earnings",
        "version": "v1.0",
        "params": {"size_window": 60, "blend_weight": 0.5},
        "dependencies": ["price/amount", "fundamental/net_profit_yoy"],
        "description": "50% small-cap size plus 50% PIT net-profit growth.",
    },
}


def build_core_factor_panels(
    *,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    net_profit_yoy: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Compute the three canonical core factor panels."""
    data = FactorData(close=close, volume=volume, amount=amount)
    illiquidity = AmihudIlliq(window=20).compute(data)
    illiquidity = safe_zscore(mad_clip(illiquidity, n=5))
    return {
        "illiquidity": illiquidity,
        "small_cap_size": small_cap_factor(amount, window=60),
        "size_earnings": size_earnings_factor(
            amount,
            net_profit_yoy,
            size_window=60,
            blend_weight=0.5,
        ),
    }


def backfill_core_factors(
    *,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    net_profit_yoy: pd.DataFrame,
    total_mv: pd.DataFrame | None,
    data_vintage: str,
    store_root: str | Path = DEFAULT_STORE_ROOT,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    primary_horizon: int = 20,
) -> CoreBackfillResult:
    """Compute, persist, score, and correlate the canonical core factors."""
    panels = build_core_factor_panels(
        close=close,
        volume=volume,
        amount=amount,
        net_profit_yoy=net_profit_yoy,
    )
    neutralizers = _size_neutralizer(total_mv, close)
    factor_ids = {}
    for key, panel in panels.items():
        metadata = CORE_FACTOR_METADATA[key]
        manifest = save_factor_panel(
            panel,
            factor_name=metadata["factor_name"],
            version=metadata["version"],
            params=metadata["params"],
            data_vintage=data_vintage,
            dependencies=metadata["dependencies"],
            description=metadata["description"],
            store_root=store_root,
        )
        factor_ids[key] = manifest.factor_id
        score = evaluate_factor_panel(
            panel,
            close,
            factor_id=manifest.factor_id,
            horizons=horizons,
            primary_horizon=primary_horizon,
            neutralizers=neutralizers,
        )
        save_factor_score(score, store_root=store_root)

    matrix = factor_panel_correlation(panels)
    correlation_path = _save_correlation(
        matrix,
        factor_ids=factor_ids,
        data_vintage=data_vintage,
        store_root=store_root,
    )
    return CoreBackfillResult(factor_ids=factor_ids, correlation_path=correlation_path)


def _size_neutralizer(
    total_mv: pd.DataFrame | None,
    close: pd.DataFrame,
) -> dict[str, pd.DataFrame] | None:
    if total_mv is None or total_mv.empty:
        return None
    aligned = total_mv.reindex(index=close.index, columns=close.columns)
    log_size = np.log(aligned.where(aligned > 0))
    return {"log_total_mv": log_size}


def _save_correlation(
    matrix: pd.DataFrame,
    *,
    factor_ids: dict[str, str],
    data_vintage: str,
    store_root: str | Path,
) -> Path:
    root = Path(store_root)
    directory = root / "correlations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "core_factors.json"
    payload = {
        "data_vintage": data_vintage,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "factor_ids": factor_ids,
        "matrix": matrix.to_dict(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return path
