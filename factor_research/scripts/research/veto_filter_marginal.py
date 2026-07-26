"""Host-scoped VetoFilter marginal contribution protocol.

The veto filter has no independent NAV. This script compares a host strategy
with and without candidate-pool exclusion and reports deltas only.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from factors.veto import loser_veto_reversal
from research_toolkit import HostSpec, compute_marginal_report
from strategies.small_cap import build_rebalance_weights, load_price_panels

DEFAULT_WINDOWS = {
    "insample_2018_2026": ("2018-01-01", None),
    "oos_2023_2026": ("2023-01-01", None),
    "stress_2010_2026": ("2010-01-01", None),
}


def _annualized_turnover(detail: pd.DataFrame) -> float:
    return float(detail["turnover"].fillna(0.0).mean() * 252)


def _annualized_cost(detail: pd.DataFrame) -> float:
    return float(detail["cost"].fillna(0.0).mean() * 252)


def _metrics(result) -> dict:
    m = dict(result.metrics)
    m["turnover_annual"] = _annualized_turnover(result.detail)
    m["cost_annual"] = _annualized_cost(result.detail)
    return m


def summarize_marginal(window_report: dict) -> dict:
    return dict(window_report["summary"])


def _run_host(close, volume, amount, factor, timing, *, top_n, rebalance_days, veto_factor=None, veto_q=0.10):
    scheduled = build_rebalance_weights(
        factor,
        close,
        top_n,
        rebalance_days,
        veto_factor=veto_factor,
        veto_q=veto_q,
    )
    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(prices=prices, config=BacktestConfig(start=str(close.index[0].date()), cost=CostModel()))
    return engine.run(Signal(weights=scheduled, timing=timing, family="small-cap-size", version="veto-review"))


def run_marginal_veto_protocol(
    *,
    close: pd.DataFrame,
    host_factor: pd.DataFrame,
    veto_factor: pd.DataFrame,
    start: str,
    windows: Mapping[str, tuple[str, str | None]] = DEFAULT_WINDOWS,
    top_n: int = 25,
    rebalance_days: int = 20,
    veto_q: float = 0.10,
    volume: pd.DataFrame | None = None,
    amount: pd.DataFrame | None = None,
    timing: pd.Series | None = None,
) -> dict:
    """Run host twice per window and report marginal deltas only."""
    volume = volume if volume is not None else pd.DataFrame(1.0, index=close.index, columns=close.columns)
    amount = amount if amount is not None else pd.DataFrame(1.0, index=close.index, columns=close.columns)
    timing = timing if timing is not None else pd.Series(1.0, index=close.index, dtype="float64")

    out = {
        "artifact_type": "VetoFilter",
        "host": "small-cap-size",
        "start": start,
        "top_n": top_n,
        "rebalance_days": rebalance_days,
        "veto_q": veto_q,
        "windows": {},
    }
    for name, (w_start, w_end) in windows.items():
        idx = close.loc[w_start:w_end].index
        if len(idx) < 40:
            continue
        close_w = close.loc[idx]
        volume_w = volume.reindex(index=idx, columns=close.columns)
        amount_w = amount.reindex(index=idx, columns=close.columns)
        factor_w = host_factor.reindex(index=idx, columns=close.columns)
        veto_w = veto_factor.reindex(index=idx, columns=close.columns)
        timing_w = timing.reindex(idx).fillna(0.0)

        base_result = _run_host(close_w, volume_w, amount_w, factor_w, timing_w, top_n=top_n, rebalance_days=rebalance_days)
        veto_result = _run_host(
            close_w,
            volume_w,
            amount_w,
            factor_w,
            timing_w,
            top_n=top_n,
            rebalance_days=rebalance_days,
            veto_factor=veto_w,
            veto_q=veto_q,
        )
        marginal = compute_marginal_report(
            base_returns=base_result.returns,
            controlled_returns=veto_result.returns,
            base_detail=base_result.detail,
            controlled_detail=veto_result.detail,
            artifact_id="loser_veto_reversal",
            host=HostSpec(family="small-cap-size", version="veto-review"),
        )
        window_report = {
            "base": marginal.base,
            "veto": marginal.controlled,
            "yearly": marginal.yearly,
            "summary": marginal.summary,
        }
        out["windows"][name] = window_report
    return out


def register_loser_veto_observation(
    *,
    host_family: str,
    host_version: str,
    metrics: dict,
    notes: str = "",
    version: str = "v0.1-observe",
) -> str:
    """Register the veto as a host-scoped observation, not LIVE."""
    from strategy_registry import register, register_family

    register_family(
        "loser_veto_reversal",
        "输家端反转低波否决器",
        hypothesis="A股无做空,输家端负 alpha 无法被套利,变现方式是排除宿主候选池的死亡分位而非独立做多。",
        regime="作为宿主策略候选池 VetoFilter;无独立净值,只看边际贡献。",
        decay_signal="连续2年边际贡献为负;或宿主小盘风格 beta 大涨年份中误杀强势股。",
        status="paused",
    )
    return register(
        "loser_veto_reversal",
        version,
        "反转+低波死亡分位排除器观察版",
        config={
            "artifact_type": "VetoFilter",
            "host": {"family": host_family, "version": host_version},
            "veto_q": 0.10,
            "application": "filter candidate pool before top_n; refill positions; rebalance-day only",
        },
        data_scope={"source": "data_lake", "period": "marginal_protocol", "survivorship_bias": False},
        metrics=metrics,
        status="条件假设/观察",
        notes=notes,
    )


def main():
    close, volume, amount = load_price_panels("2010-01-01")
    host = small_cap_factor(amount, 60)
    timing, _, _ = small_cap_timing(close, amount, 16)
    veto = loser_veto_reversal(close)
    report = run_marginal_veto_protocol(
        close=close,
        volume=volume,
        amount=amount,
        host_factor=host,
        veto_factor=veto,
        timing=timing,
        start="2010-01-01",
    )
    out = ROOT / "reports" / "research" / "veto_filter_marginal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
