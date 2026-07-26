"""股权质押风险状态信号。

`pledge_stat` 是稀疏状态源,不是全市场连续数值因子。这里不构造
``low pledge = reward`` 的反向全市场排序,只输出高质押、恶化、改善和覆盖状态。
"""
from __future__ import annotations

import pandas as pd


def _valid_mask(panels: dict[str, pd.DataFrame], max_stale_days: int) -> pd.DataFrame:
    ratio = panels["pledge_ratio"]
    state = panels.get("pledge_coverage_state")
    stale_days = panels.get("pledge_stale_days")
    if state is None:
        state = pd.DataFrame("current", index=ratio.index, columns=ratio.columns)
    if stale_days is None:
        stale_days = pd.DataFrame(0.0, index=ratio.index, columns=ratio.columns)
    return ratio.notna() & state.eq("current") & stale_days.le(max_stale_days)


def _flag(mask: pd.DataFrame, valid: pd.DataFrame) -> pd.DataFrame:
    return mask.astype(float).where(valid)


def build_pledge_risk_signals(
    panels: dict[str, pd.DataFrame],
    *,
    high_ratio_threshold: float = 30.0,
    weeks: tuple[int, ...] = (4, 12, 26),
    trading_days_per_week: int = 5,
    improvement_drop_pp: float = 5.0,
    max_stale_days: int = 30,
) -> dict[str, pd.DataFrame]:
    """从 pledge loader 输出生成风险/状态型信号。

    Returns:
      pledge_high_risk: 当前质押率超过阈值。
      pledge_worsening_{n}w: 近 n 周质押率上升。
      pledge_improvement_{n}w: 曾处高质押且近 n 周下降至少 improvement_drop_pp。
      pledge_coverage_flag: current/stale/never_seen/unknown 原样透出。
    """
    ratio = panels["pledge_ratio"].apply(pd.to_numeric, errors="coerce")
    valid = _valid_mask(panels, max_stale_days).reindex_like(ratio)
    out: dict[str, pd.DataFrame] = {
        "pledge_high_risk": _flag(ratio.gt(high_ratio_threshold), valid),
        "pledge_coverage_flag": panels.get(
            "pledge_coverage_state",
            pd.DataFrame("unknown", index=ratio.index, columns=ratio.columns, dtype=object),
        ).copy(),
    }
    for w in weeks:
        periods = int(w * trading_days_per_week)
        prev = ratio.shift(periods)
        delta = ratio - prev
        out[f"pledge_worsening_{w}w"] = _flag(delta.gt(0), valid & prev.notna())
        improve = prev.ge(high_ratio_threshold) & delta.le(-abs(improvement_drop_pp))
        out[f"pledge_improvement_{w}w"] = _flag(improve, valid & prev.notna())
    return out


def load_pledge_risk_signals(trade_dates, codes=None, **kwargs) -> dict[str, pd.DataFrame]:
    """加载 canonical pledge_stat 面板并生成风险状态信号。"""
    from lake.load_lake import load_pledge_stat_panel

    panels = load_pledge_stat_panel(trade_dates, codes=codes, max_stale_days=kwargs.get("max_stale_days", 30))
    return build_pledge_risk_signals(panels, **kwargs)
