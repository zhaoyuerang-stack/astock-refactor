"""Factor Store scoring utilities.

This module turns a stored factor panel into comparable research evidence:
Rank IC, Newey-West ICIR, IC decay, monotonicity, turnover, optional
neutralized ICIR, and factor-to-factor behavior correlation.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .store import DEFAULT_STORE_ROOT


@dataclass(frozen=True)
class FactorScore:
    factor_id: str
    primary_horizon: int
    ic_mean: float
    ic_std: float
    icir: float
    nw_icir: float
    ic_win_rate: float
    ic_count: int
    ic_decay: dict[int, float]
    monotonicity_corr: float | None
    turnover_mean: float | None
    neut_nw_icir: float | None
    icir_retention: float | None
    created_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactorScore:
        return cls(
            factor_id=str(data["factor_id"]),
            primary_horizon=int(data["primary_horizon"]),
            ic_mean=float(data["ic_mean"]),
            ic_std=float(data["ic_std"]),
            icir=float(data["icir"]),
            nw_icir=float(data["nw_icir"]),
            ic_win_rate=float(data["ic_win_rate"]),
            ic_count=int(data["ic_count"]),
            ic_decay={int(k): float(v) for k, v in dict(data.get("ic_decay", {})).items()},
            monotonicity_corr=_optional_float(data.get("monotonicity_corr")),
            turnover_mean=_optional_float(data.get("turnover_mean")),
            neut_nw_icir=_optional_float(data.get("neut_nw_icir")),
            icir_retention=_optional_float(data.get("icir_retention")),
            created_at=str(data["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_factor_panel(
    factor: pd.DataFrame,
    close: pd.DataFrame,
    *,
    factor_id: str,
    horizons: tuple[int, ...] = (1, 5, 10, 20),
    primary_horizon: int = 20,
    neutralizers: dict[str, pd.DataFrame] | None = None,
    n_quantile: int = 5,
    top_quantile: float = 0.2,
    min_obs: int = 30,
) -> FactorScore:
    """Evaluate one factor panel against forward returns from ``close``."""
    factor = _clean_panel(factor)
    close = _clean_panel(close)
    if primary_horizon not in horizons:
        horizons = tuple(horizons) + (primary_horizon,)

    fwd_primary = _forward_returns(close, primary_horizon)
    ic = rank_ic_series(factor, fwd_primary, min_obs=min_obs)
    ic_mean = float(ic.mean()) if not ic.empty else 0.0
    ic_std = float(ic.std()) if len(ic) > 1 else 0.0
    icir = ic_mean / ic_std if ic_std > 0 else 0.0
    nw_icir = newey_west_icir(ic, max_lag=primary_horizon)
    win_rate = float((ic > 0).mean()) if ic_mean >= 0 and len(ic) else float((ic < 0).mean())

    decay = {}
    for horizon in horizons:
        h_ic = rank_ic_series(factor, _forward_returns(close, horizon), min_obs=min_obs)
        decay[int(horizon)] = float(h_ic.mean()) if not h_ic.empty else 0.0

    mono = quantile_monotonicity(factor, fwd_primary, n_quantile=n_quantile, min_obs=min_obs)
    turnover = top_quantile_turnover(factor, quantile=top_quantile, min_obs=min_obs)

    neut_nw_icir = None
    retention = None
    if neutralizers:
        neutral_factor = neutralize_factor_panel(factor, neutralizers, min_obs=min_obs)
        neut_ic = rank_ic_series(neutral_factor, fwd_primary, min_obs=min_obs)
        neut_nw_icir = newey_west_icir(neut_ic, max_lag=primary_horizon)
        retention = abs(neut_nw_icir) / abs(nw_icir) if abs(nw_icir) > 1e-12 else 0.0

    return FactorScore(
        factor_id=factor_id,
        primary_horizon=primary_horizon,
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=float(icir),
        nw_icir=float(nw_icir),
        ic_win_rate=win_rate,
        ic_count=int(len(ic)),
        ic_decay=decay,
        monotonicity_corr=mono,
        turnover_mean=turnover,
        neut_nw_icir=neut_nw_icir,
        icir_retention=retention,
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )


def rank_ic_series(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    *,
    min_obs: int = 30,
) -> pd.Series:
    """Daily cross-sectional Spearman Rank IC."""
    dates = factor.index.intersection(forward_returns.index)
    values = {}
    for dt in dates:
        f = factor.loc[dt]
        r = forward_returns.loc[dt]
        common = f.index.intersection(r.index)
        if len(common) < min_obs:
            continue
        f = f.loc[common]
        r = r.loc[common]
        mask = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
        if int(mask.sum()) < min_obs:
            continue
        if f[mask].std() <= 1e-12 or r[mask].std() <= 1e-12:
            continue
        ic, _ = spearmanr(f[mask], r[mask])
        if np.isfinite(ic):
            values[dt] = float(ic)
    return pd.Series(values, dtype=float).sort_index()


def newey_west_icir(daily_ic: pd.Series | np.ndarray, max_lag: int = 20) -> float:
    """Newey-West corrected ICIR using a Bartlett kernel."""
    ic = np.asarray(daily_ic, dtype=float)
    ic = ic[np.isfinite(ic)]
    n = len(ic)
    if n < 2:
        return 0.0
    max_lag = max(1, min(int(max_lag), n - 1))
    var = float(ic.var())
    if var <= 1e-12:
        return 0.0
    lr_var = var
    for lag in range(1, max_lag + 1):
        corr = np.corrcoef(ic[:-lag], ic[lag:])[0, 1]
        if np.isfinite(corr):
            weight = 1.0 - lag / (max_lag + 1)
            lr_var += 2.0 * weight * corr * var
    return abs(float(ic.mean())) / np.sqrt(max(lr_var, 1e-12))


def quantile_monotonicity(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    *,
    n_quantile: int = 5,
    min_obs: int = 30,
) -> float | None:
    """Spearman correlation between factor quantile rank and mean forward return."""
    rows = []
    for dt in factor.index.intersection(forward_returns.index):
        f = factor.loc[dt].dropna()
        r = forward_returns.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < max(min_obs, n_quantile * 5):
            continue
        try:
            labels = pd.qcut(f.loc[common], n_quantile, labels=False, duplicates="drop")
        except ValueError:
            continue
        group_ret = r.loc[common].groupby(labels).mean()
        if len(group_ret) == n_quantile:
            rows.append(group_ret.to_numpy(dtype=float))
    if not rows:
        return None
    mean_returns = np.nanmean(np.vstack(rows), axis=0)
    corr, _ = spearmanr(np.arange(n_quantile), mean_returns)
    return float(corr) if np.isfinite(corr) else None


def top_quantile_turnover(
    factor: pd.DataFrame,
    *,
    quantile: float = 0.2,
    min_obs: int = 30,
) -> float | None:
    """Average top-quantile membership churn between adjacent dates."""
    previous: set[str] | None = None
    turnovers = []
    for _, row in factor.iterrows():
        values = row.dropna()
        if len(values) < min_obs:
            continue
        n_top = max(1, int(np.ceil(len(values) * quantile)))
        current = set(values.nlargest(n_top).index.astype(str))
        if previous is not None:
            denom = max(len(previous), len(current), 1)
            turnovers.append(1.0 - len(previous & current) / denom)
        previous = current
    if not turnovers:
        return None
    return float(np.mean(turnovers))


def neutralize_factor_panel(
    factor: pd.DataFrame,
    neutralizers: dict[str, pd.DataFrame],
    *,
    min_obs: int = 30,
) -> pd.DataFrame:
    """Cross-sectionally regress a factor on neutralizer panels and return residuals."""
    dates = factor.index
    out = pd.DataFrame(index=factor.index, columns=factor.columns, dtype=float)
    for dt in dates:
        y = factor.loc[dt]
        x_cols = []
        for panel in neutralizers.values():
            if dt in panel.index:
                x_cols.append(panel.loc[dt].rename(f"x{len(x_cols)}"))
        if not x_cols:
            continue
        x = pd.concat(x_cols, axis=1)
        common = y.index.intersection(x.index)
        y = y.loc[common]
        x = x.loc[common]
        mask = y.notna() & np.isfinite(y)
        for col in x.columns:
            mask &= x[col].notna() & np.isfinite(x[col])
        if int(mask.sum()) < min_obs:
            continue
        yv = y.loc[mask].to_numpy(dtype=float)
        xv = x.loc[mask].to_numpy(dtype=float)
        design = np.column_stack([np.ones(len(xv)), xv])
        beta, _, _, _ = np.linalg.lstsq(design, yv, rcond=None)
        out.loc[dt, y.loc[mask].index] = yv - design @ beta
    return out


def factor_panel_correlation(
    panels: dict[str, pd.DataFrame],
    *,
    min_obs: int = 30,
) -> pd.DataFrame:
    """Average daily cross-sectional Spearman correlation matrix."""
    names = list(panels)
    corr = pd.DataFrame(np.eye(len(names)), index=names, columns=names, dtype=float)
    for i, left_name in enumerate(names):
        for j in range(i + 1, len(names)):
            right_name = names[j]
            value = _average_row_spearman(panels[left_name], panels[right_name], min_obs=min_obs)
            corr.loc[left_name, right_name] = value
            corr.loc[right_name, left_name] = value
    return corr


def save_factor_score(
    score: FactorScore,
    *,
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> Path:
    root = Path(store_root)
    scores_dir = root / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    path = scores_dir / f"{score.factor_id}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(score.to_dict(), fh, ensure_ascii=True, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
    return path


def load_factor_score(
    factor_id: str,
    *,
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> FactorScore:
    path = Path(store_root) / "scores" / f"{factor_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"factor score not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return FactorScore.from_dict(json.load(fh))


def _forward_returns(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    return close.pct_change(horizon, fill_method=None).shift(-horizon)


def _clean_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError("panel index must be a DatetimeIndex")
    return panel.sort_index().astype("float64").replace([np.inf, -np.inf], np.nan)


def _average_row_spearman(left: pd.DataFrame, right: pd.DataFrame, *, min_obs: int) -> float:
    values = []
    for dt in left.index.intersection(right.index):
        lrow = left.loc[dt]
        rrow = right.loc[dt]
        common = lrow.index.intersection(rrow.index)
        if len(common) < min_obs:
            continue
        lrow = lrow.loc[common]
        rrow = rrow.loc[common]
        mask = lrow.notna() & rrow.notna() & np.isfinite(lrow) & np.isfinite(rrow)
        if int(mask.sum()) < min_obs:
            continue
        if lrow[mask].std() <= 1e-12 or rrow[mask].std() <= 1e-12:
            continue
        corr, _ = spearmanr(lrow[mask], rrow[mask])
        if np.isfinite(corr):
            values.append(float(corr))
    return float(np.mean(values)) if values else 0.0


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    out = float(value)
    return out if np.isfinite(out) else None
