"""Phase 3: Walk-Forward validation.

Independent of Phase 2's static split. Runs rolling 3yr-train → 1yr-test
windows continuously across the full sample (2010-2026). This is the
strongest overfitting test.

Design:
  - Train: 3 years (756 trading days)
  - Test:  1 year  (252 trading days)
  - Step:  1 year
  - Minimum 5 full windows
  - No parameter optimization (tests the strategy as-is)
  - Passing: OOS aggregate annual > 0 AND positive in ≥2/3 of windows

Usage:
  >>> from workflow.phase3_wf import WF3Runner, WFReport
  >>> runner = WF3Runner(factor_builder=my_fn, timing_builder=my_timing,
  ...                     family='my-strategy', config={...})
  >>> report = runner.run()
  >>> print(report.summary())
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from governance.holdout import assert_search_clean, boundary
from strategies.small_cap import load_price_panels as load_data

ROOT: Path = Path(__file__).resolve().parent.parent
OUT_DIR: Path = ROOT / "reports" / "discovery"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TRAIN_YEARS: int = 3
TEST_YEARS: int = 1
STEP_YEARS: int = 1
MIN_WINDOWS: int = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_rebalance_weights(factor: pd.DataFrame, close: pd.DataFrame, top_n: int = 25,
                            rebalance_days: int = 20) -> dict[pd.Timestamp, pd.Series]:
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 50:
        return {}
    weights = {}
    for rd in list(fdates[::rebalance_days]):
        pos = close.index.get_loc(rd)
        if pos + 1 >= len(close.index):
            continue
        effective = close.index[pos + 1]
        f = factor.loc[rd].dropna()
        active = close.loc[rd].dropna().index
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            continue
        weights[effective] = pd.Series(1.0 / top_n, index=f.nlargest(top_n).index)
    return weights


def _year_bounds(year: int) -> tuple[str, str]:
    """Return (first_trading_day_of_year, last_trading_day_of_year) as strings."""
    return f"{year}-01-01", f"{year}-12-31"


# ---------------------------------------------------------------------------
# WF3Runner
# ---------------------------------------------------------------------------

class WF3Runner:
    """Walk-Forward validation with rolling 3yr-train → 1yr-test windows."""

    def __init__(
        self,
        factor_builder: Callable[
            [pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DatetimeIndex],
            pd.DataFrame,
        ],
        timing_builder: Callable[[pd.DataFrame, pd.DataFrame], pd.Series],
        family: str = "unnamed",
        config: dict[str, Any] | None = None,
    ) -> None:
        self.factor_builder = factor_builder
        self.timing_builder = timing_builder
        self.family = family
        self.config = config or {}
        self.top_n = self.config.get("top_n", 25)
        self.rebalance = self.config.get("rebalance_days", 20)
        self.leverage = self.config.get("leverage", 1.25)
        _default = CostModel()  # 费率唯一权威(R-COST-001),兜底值不写字面量
        self.cost = CostModel(
            buy_cost=self.config.get("buy_cost", _default.buy_cost),
            sell_cost=self.config.get("sell_cost", _default.sell_cost),
            financing_rate=self.config.get("financing_rate", _default.financing_rate),
        )

    def run(self, warmup_start: str = "2010-01-01") -> dict[str, Any]:
        """Run Walk-Forward and return results dict."""
        logger.info(f"Phase 3 WF: {self.family}")

        # Load all data, then 截到 < holdout boundary(§5.2 缝③):walk-forward 的训练/测试窗口
        # 与每窗因子计算都从被裁面板派生 → 金库年(date>=boundary)不进入任何窗口或选择判定。
        # 仅 validate_on_holdout 唯一一次校验金库。
        close, volume, amount = load_data(warmup_start)
        b = boundary()
        pre = close.index[-1]
        close = close.loc[close.index < b]
        volume = volume.loc[volume.index < b]
        amount = amount.loc[amount.index < b]
        trade_dates = close.index
        assert_search_clean(trade_dates, label="Phase 3 walk-forward")  # 自查门
        logger.info(f"  Data: {close.shape[1]} stocks, "
              f"{trade_dates[0].date()}~{trade_dates[-1].date()} "
              f"(已截金库 boundary={b.date()};原末日 {pre.date()})")

        # Build windows
        all_years = sorted(set(d.year for d in trade_dates))
        eligible_test_years = [
            y for y in all_years
            if y - TRAIN_YEARS >= all_years[0] and y + TEST_YEARS - 1 <= all_years[-1]
        ]

        windows = []
        for test_start_year in eligible_test_years[::STEP_YEARS]:
            train_start = pd.Timestamp(f"{test_start_year - TRAIN_YEARS}-01-01")
            train_end = pd.Timestamp(f"{test_start_year - 1}-12-31")
            test_start = pd.Timestamp(f"{test_start_year}-01-01")
            test_end = pd.Timestamp(f"{test_start_year + TEST_YEARS - 1}-12-31")
            # Clamp to available data
            if train_start < trade_dates[0] or test_end > trade_dates[-1]:
                continue
            windows.append({
                "train_start": train_start, "train_end": train_end,
                "test_start": test_start, "test_end": test_end,
                "test_start_year": test_start_year,
            })

        if len(windows) < MIN_WINDOWS:
            logger.warning(f"  ⚠️ Only {len(windows)} WF windows (need ≥{MIN_WINDOWS}). "
                  f"Need more data history.")
            return {"family": self.family, "error": "insufficient_windows",
                    "n_windows": len(windows)}

        logger.info(f"  Windows: {len(windows)}")

        # ── Run WF ──
        oos_returns = []
        window_results = []

        for _, win in enumerate(windows):
            # Train period (build factor, but we don't optimize — just verify OOS)
            t_mask = (trade_dates >= win["train_start"]) & (trade_dates <= win["train_end"])
            c_tr = close.loc[t_mask]; v_tr = volume.loc[t_mask]; a_tr = amount.loc[t_mask]
            td_tr = trade_dates[t_mask]

            # Build factor on train (for diagnostics only)
            f_tr = self.factor_builder(c_tr, v_tr, a_tr, td_tr)
            w_tr = build_rebalance_weights(f_tr, c_tr, self.top_n, self.rebalance)
            t_tr = self.timing_builder(c_tr, a_tr)

            # Actually, for fixed-parameter WF, we build factor on FULL data
            # and evaluate OOS — this tests whether the factor construction
            # itself (not its parameters) generalizes.
            # Test period
            v_mask = (trade_dates >= win["test_start"]) & (trade_dates <= win["test_end"])
            c_te = close.loc[v_mask]; v_te = volume.loc[v_mask]; a_te = amount.loc[v_mask]
            td_te = trade_dates[v_mask]

            # Build factor on test period (same formula, no parameters from train)
            f_te = self.factor_builder(c_te, v_te, a_te, td_te)
            w_te = build_rebalance_weights(f_te, c_te, self.top_n, self.rebalance)
            t_te = self.timing_builder(c_te, a_te)

            res_te = self._backtest(c_te, v_te, a_te, w_te, t_te)
            if res_te is None:
                continue

            # Train diagnostics
            res_tr = self._backtest(c_tr, v_tr, a_tr, w_tr, t_tr)
            train_annual = res_tr["annual"] if res_tr else 0.0

            window_results.append({
                "test_start_year": win["test_start_year"],
                "train_annual": train_annual,
                "oos_annual": res_te["annual"],
                "oos_sharpe": res_te["sharpe"],
                "oos_maxdd": res_te["maxdd"],
                "oos_turnover": res_te["turnover"],
                "oos_days": res_te["n_days"],
            })
            oos_returns.append(res_te["returns"])

            logger.info(f"    {win['test_start_year']}: train_ann={train_annual:+.1%} → "
                  f"oos_ann={res_te['annual']:+.1%} oos_dd={res_te['maxdd']:+.1%} "
                  f"oos_sharpe={res_te['sharpe']:+.2f}")

        # ── Aggregate OOS ──
        if not oos_returns:
            return {"family": self.family, "error": "no_valid_windows"}

        wf_ret = pd.concat(oos_returns).sort_index()
        wf_ret = wf_ret[~wf_ret.index.duplicated(keep="first")]
        wf_annual = float(wf_ret.mean() * 252)
        wf_vol = float(wf_ret.std() * np.sqrt(252))
        wf_sharpe = wf_annual / wf_vol if wf_vol > 0 else 0.0
        wf_cum = (1 + wf_ret).cumprod()
        wf_maxdd = float((wf_cum / wf_cum.cummax() - 1).min())
        wf_calmar = wf_annual / abs(wf_maxdd) if wf_maxdd < 0 else 0.0

        oos_annuals = [w["oos_annual"] for w in window_results]
        pos_windows = sum(1 for a in oos_annuals if a > 0)
        n_windows = len(window_results)

        verdict = "PASS" if wf_annual > 0 and pos_windows >= n_windows * 2 / 3 else "FAIL"

        logger.info(f"\n  WF Aggregate OOS: annual={wf_annual:+.1%} sharpe={wf_sharpe:.2f} "
              f"maxdd={wf_maxdd:+.1%} calmar={wf_calmar:.2f}")
        logger.info(f"  Positive windows: {pos_windows}/{n_windows} → {verdict}")

        report = {
            "family": self.family,
            "config": self.config,
            "windows": window_results,
            "aggregate": {
                "annual": wf_annual,
                "sharpe": wf_sharpe,
                "maxdd": wf_maxdd,
                "calmar": wf_calmar,
                "n_days": len(wf_ret),
                "positive_windows": pos_windows,
                "total_windows": n_windows,
                "min_positive_ratio": 2 / 3,
                "verdict": verdict,
            },
            "timestamp": str(pd.Timestamp.now()),
        }

        safe_name = self.family.replace("/", "_")
        out_path = OUT_DIR / f"{safe_name}_phase3_wf.json"
        out_path.write_text(json.dumps(_make_serializable(report),
                                        ensure_ascii=False, indent=2))
        logger.info(f"  Report → {out_path}")

        return report

    def _backtest(self, close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame,
                  weights: dict[pd.Timestamp, pd.Series], timing: pd.Series | None) -> dict[str, Any] | None:
        """Run backtest on a date range. Returns metrics dict or None."""
        if len(weights) < 2 or close.shape[0] < 50:
            return None
        prices = PricePanel(close=close, volume=volume, amount=amount)
        engine = BacktestEngine(
            prices=prices,
            config=BacktestConfig(
                start=str(close.index[0].date()),
                cost=self.cost,
                leverage=self.leverage,
            ),
        )
        signal = Signal(weights=weights, timing=timing)
        result = engine.run(signal)
        m = result.metrics
        return {
            "annual": m["annual"],
            "maxdd": m["maxdd"],
            "sharpe": m["sharpe"],
            "calmar": m["calmar"],
            "turnover": result.detail["turnover"].mean() * 252,
            "n_days": len(result.returns),
            "returns": result.returns,
        }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class WFReport:
    family: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: str(pd.Timestamp.now()))

    @property
    def has_fail(self) -> bool:
        agg = self.data.get("aggregate", {})
        return agg.get("verdict") == "FAIL"

    def summary(self) -> str:
        d = self.data
        agg = d.get("aggregate", {})
        windows = d.get("windows", [])

        lines = [
            f"Phase 3 Walk-Forward: {self.family}",
            f"  Time: {self.timestamp}",
            f"  {'─' * 55}",
        ]

        if "error" in d:
            lines.append(f"  ⚠️ {d['error']}")
            return "\n".join(lines)

        lines.append(f"  Windows ({len(windows)}):")
        for w in windows:
            icon = "✅" if w["oos_annual"] > 0 else "❌"
            lines.append(
                f"    {icon} {w['test_start_year']}: "
                f"train={w['train_annual']:+.1%} → "
                f"oos_ann={w['oos_annual']:+.1%} "
                f"oos_dd={w['oos_maxdd']:+.1%} "
                f"oos_sharpe={w['oos_sharpe']:+.2f}"
            )

        lines.append(f"  {'─' * 55}")
        lines.append("  WF Aggregate OOS:")
        lines.append(f"    Annual: {agg['annual']:+.1%}")
        lines.append(f"    Sharpe: {agg['sharpe']:.2f}")
        lines.append(f"    MaxDD:  {agg['maxdd']:+.1%}")
        lines.append(f"    Calmar: {agg['calmar']:.2f}")
        lines.append(f"  Positive: {agg['positive_windows']}/{agg['total_windows']} "
                     f"(need ≥{agg['total_windows']*2//3})")
        lines.append(f"  {'─' * 55}")
        lines.append(f"  → {'✅ PASSED' if not self.has_fail else '❌ FAILED'}")
        return "\n".join(lines)


def _make_serializable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.bool_,)): return bool(obj)
    if isinstance(obj, (pd.Timestamp,)): return str(obj)
    if isinstance(obj, (pd.Series, pd.DataFrame)): return None
    return obj
