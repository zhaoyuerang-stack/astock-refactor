"""Canonical factor composition utilities."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA


def equal_weight_factor(factors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    aligned = list(factors.values())
    return sum(aligned) / len(aligned)


def ic_weight_factor(
    factors: dict[str, pd.DataFrame],
    forward_ret: pd.DataFrame,
    ic_window: int = 12,
) -> pd.DataFrame:
    from engine.factor_analysis import calc_ic

    ic_series = {name: calc_ic(f, forward_ret) for name, f in factors.items()}
    dates = sorted(set.intersection(*[set(f.index) for f in factors.values()]))
    result = {}

    for dt in dates:
        weights = {}
        for name, ic in ic_series.items():
            past_ic = ic[ic.index < dt].tail(ic_window)
            weights[name] = 0.0 if len(past_ic) < 3 else past_ic.mean()
        total_abs = sum(abs(w) for w in weights.values())
        if total_abs < 1e-6:
            continue
        norm_w = {n: w / total_abs for n, w in weights.items()}
        row = sum(factors[n].loc[dt] * w for n, w in norm_w.items() if dt in factors[n].index)
        result[dt] = row

    return pd.DataFrame(result).T


def pca_factor_composite(
    factors: dict[str, pd.DataFrame],
    n_components: int = 1,
) -> pd.DataFrame:
    names = list(factors.keys())
    dates = sorted(set.intersection(*[set(f.index) for f in factors.values()]))
    result = {}

    for dt in dates:
        cols = {name: factors[name].loc[dt] for name in names if dt in factors[name].index}
        df = pd.DataFrame(cols).dropna()
        if len(df) < 50 or df.shape[1] < 2:
            continue
        pca = PCA(n_components=n_components)
        pc = pca.fit_transform(df.values)[:, 0]
        if np.corrcoef(df.iloc[:, 0].values, pc)[0, 1] < 0:
            pc = -pc
        result[dt] = pd.Series(pc, index=df.index)

    return pd.DataFrame(result).T


def factor_corr_matrix(
    factors: dict[str, pd.DataFrame],
    sample_dates: int = 60,
) -> pd.DataFrame:
    names = list(factors.keys())
    dates = sorted(set.intersection(*[set(f.index) for f in factors.values()]))[-sample_dates:]
    corr_accum = pd.DataFrame(0.0, index=names, columns=names)
    count = 0

    for dt in dates:
        cols = {name: factors[name].loc[dt] for name in names if dt in factors[name].index}
        df = pd.DataFrame(cols).dropna()
        if len(df) < 30:
            continue
        corr_accum += df.corr(method="spearman")
        count += 1

    return (corr_accum / max(count, 1)).round(3)
