"""
因子中性化模块

标准流程：
  1. 截面去极值（MAD法）
  2. 标准化（zscore）
  3. 行业 + 市值中性化（OLS回归残差）
"""
import numpy as np
import pandas as pd


def mad_winsorize(s: pd.Series, n: float = 5.0) -> pd.Series:
    """MAD去极值：超出 median ± n*MAD 的值截断"""
    s = s.dropna()
    med = s.median()
    mad = (s - med).abs().median()
    upper = med + n * mad
    lower = med - n * mad
    return s.clip(lower, upper)


def zscore_series(s: pd.Series) -> pd.Series:
    """One-dimensional z-score for a single cross-section or generic Series."""
    return (s - s.mean()) / (s.std() + 1e-10)


def zscore(s: pd.Series) -> pd.Series:
    """Backward-compatible wrapper for ``zscore_series``."""
    return zscore_series(s)


def neutralize_cross_section(
    factor: pd.Series,
    industry: pd.Series,
    log_cap: pd.Series = None,
) -> pd.Series:
    """
    单截面因子中性化。
    factor, industry, log_cap 的 index 均为 stock code。
    返回回归残差（已去除行业和市值的线性影响）。
    """
    common = factor.dropna().index
    if industry is not None:
        common = common.intersection(industry.dropna().index)
    if log_cap is not None:
        common = common.intersection(log_cap.dropna().index)
    if len(common) < 30:
        return pd.Series(dtype=float)

    y = factor[common].values

    # 构建设计矩阵：行业哑变量 + 对数市值
    ind = industry[common]
    dummies = pd.get_dummies(ind, drop_first=True).astype(float)
    X = dummies.values

    if log_cap is not None:
        cap_col = log_cap[common].values.reshape(-1, 1)
        X = np.hstack([X, cap_col])

    X = np.hstack([np.ones((len(y), 1)), X])   # 加截距

    try:
        coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        residual = y - X @ coef
    except Exception:
        return pd.Series(dtype=float)

    return pd.Series(residual, index=common)


def process_factor(
    factor: pd.DataFrame,
    industry_map: pd.DataFrame,
    cap: pd.DataFrame = None,
    winsorize_n: float = 5.0,
) -> pd.DataFrame:
    """
    对整个因子矩阵（date × code）逐截面做：
      去极值 → 中性化 → 标准化

    industry_map: DataFrame with columns [code, industry]
    cap: date × code 的流通市值 DataFrame（可选，传入则做市值中性化）
    """
    ind_series = industry_map.set_index("code")["industry"]
    result = {}

    for dt, row in factor.iterrows():
        row = row.dropna()
        if len(row) < 50:
            continue

        # 去极值
        row = mad_winsorize(row, winsorize_n)

        # 行业 + 市值中性化
        ind = ind_series.reindex(row.index)
        lncap = None
        if cap is not None and dt in cap.index:
            cap_row = cap.loc[dt].reindex(row.index)
            lncap = np.log(cap_row.replace(0, np.nan))

        row = neutralize_cross_section(row, ind, lncap)
        if row.empty:
            continue

        # 标准化
        result[dt] = zscore(row)

    return pd.DataFrame(result).T
