"""Phase 2: Three-segment backtest with non-overlapping intervals.

  IS   2018-2022  — sample内, parameters can be seen
  OOS  2023-2026  — sample外, strict blind test
  压力 2010-2017  — early stress (2015 crash, 2016 circuit-breaker)

Checks:
  - All segments: annual > 0
  - OOS/IS decay: OOS_annual / IS_annual > 0.3
  - Cost sensitivity: +50% cost → annual decay < 50%
  - Correlation vs registered strategies: |corr| < 0.85

Usage:
  >>> from workflow.phase2_backtest import Phase2Runner, BacktestReport
  >>> runner = Phase2Runner(factor_builder=my_fn, timing_builder=my_timing,
  ...                        family='my-strategy', config={...})
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

# Non-overlapping segments。OOS 终点由 holdout.boundary 动态裁定(见 run() 的 _segments)——
# date >= boundary 是金库,Phase 2 是搜索/验证栈不得触碰,金库仅 validate_on_holdout 唯一一次。
IS_SEG: tuple[str, str, str] = ("IS  2018-2022", "2018-01-01", "2022-12-31")
STRESS_SEG: tuple[str, str, str] = ("压力 2010-2017", "2010-01-01", "2017-12-31")

DEFAULT_TOP_N: int = 25
DEFAULT_REBALANCE: int = 20
DEFAULT_LEVERAGE: float = 1.25


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

def build_rebalance_weights_offset(factor: pd.DataFrame, close: pd.DataFrame, top_n: int = 25,
                                   rebalance_days: int = 20, offset: int = 0) -> dict[pd.Timestamp, pd.Series]:
    fdates = factor.dropna(how="all").index.intersection(close.index)
    if len(fdates) < 50:
        return {}
    weights = {}
    for rd in list(fdates[offset::rebalance_days]):
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


def run_segment(close: pd.DataFrame, volume: pd.DataFrame, amount: pd.DataFrame,
                weights: dict[pd.Timestamp, pd.Series], timing: pd.Series | None,
                leverage: float, cost_model: CostModel) -> dict[str, Any]:
    """Run a single backtest segment and return metrics."""
    prices = PricePanel(close=close, volume=volume, amount=amount)
    engine = BacktestEngine(
        prices=prices,
        config=BacktestConfig(
            start=str(close.index[0].date()),
            cost=cost_model,
            leverage=leverage,
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
        "cost_drag": result.detail["cost"].mean() * 252,
        "n_days": len(result.returns),
        "returns": result.returns,
        "hit": m["hit"],
    }


# ---------------------------------------------------------------------------
# Phase2Runner
# ---------------------------------------------------------------------------

class Phase2Runner:
    """Run three-segment backtest + cost sensitivity + correlation check."""

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
        self.top_n = self.config.get("top_n", DEFAULT_TOP_N)
        self.rebalance = self.config.get("rebalance_days", DEFAULT_REBALANCE)
        self.leverage = self.config.get("leverage", DEFAULT_LEVERAGE)
        _default = CostModel()  # 费率唯一权威(R-COST-001),兜底值不写字面量
        self.base_cost = CostModel(
            buy_cost=self.config.get("buy_cost", _default.buy_cost),
            sell_cost=self.config.get("sell_cost", _default.sell_cost),
            financing_rate=self.config.get("financing_rate", _default.financing_rate),
        )

    # ── main ──

    def run(self, warmup_start: str = "2010-01-01") -> dict[str, Any]:
        """Run all Phase 2 checks. Returns dict with segments + checks."""
        logger.info(f"Phase 2: {self.family}")

        # Load data once with warmup, then 截到 < holdout boundary(§5.2 缝③):整个验证栈
        # (三段/成本/相关性/decay)都从这批被裁过的面板派生 → 金库 date>=boundary 永不进入
        # 因子计算、回测或选择判定。仅 validate_on_holdout 唯一一次校验金库。
        close, volume, amount = load_data(warmup_start)
        b = boundary()
        pre = close.index[-1]
        close = close.loc[close.index < b]
        volume = volume.loc[volume.index < b]
        amount = amount.loc[amount.index < b]
        trade_dates = close.index
        assert_search_clean(trade_dates, label="Phase 2 验证栈")  # 自查门:末日必须 < boundary
        logger.info(f"  Data: {close.shape[1]} stocks × {close.shape[0]} days "
              f"[{trade_dates[0].date()}~{trade_dates[-1].date()}] "
              f"(已截金库 boundary={b.date()};原末日 {pre.date()})")

        # OOS 终点 = 金库前一日(boundary 动态),金库段不属于 OOS,留给 validate_on_holdout。
        oos_end = min(pd.Timestamp("2026-12-31"), b - pd.Timedelta(days=1))
        oos_seg = (f"OOS 2023-{oos_end.year}", "2023-01-01", str(oos_end.date()))
        segment_defs = [IS_SEG, oos_seg, STRESS_SEG]
        # 段角色映射:显示标签随 boundary 变(如 "OOS 2023-2024"),跨模块传递
        # 必须用稳定 role 键,不得让 phase4 精确匹配完整标签字符串(2026-07-11 review)
        seg_roles = {IS_SEG[0]: "is", oos_seg[0]: "oos", STRESS_SEG[0]: "stress"}

        # Build factor + timing on (已截) full range
        factor = self.factor_builder(close, volume, amount, trade_dates)
        timing = self.timing_builder(close, amount)
        weights = build_rebalance_weights(factor, close, self.top_n, self.rebalance)
        logger.info(f"  Factor: {factor.dropna(how='all').shape[0]} valid days, "
              f"weights: {len(weights)} rebalance events")

        # ── Three segments ──
        segments = {}
        segments_by_role = {}
        for label, start, end in segment_defs:
            mask = (trade_dates >= pd.Timestamp(start)) & (trade_dates <= pd.Timestamp(end))
            if mask.sum() < 50:
                logger.warning(f"  {label}: ⚠️ too few days ({mask.sum()})")
                continue

            c = close.loc[mask]; v = volume.loc[mask]; a = amount.loc[mask]
            t = timing.loc[mask] if timing is not None else None

            # Filter weights to segment dates
            w_seg = {dt: ws for dt, ws in weights.items() if dt in c.index}

            res = run_segment(c, v, a, w_seg, t, self.leverage, self.base_cost)
            segments[label] = res
            segments_by_role[seg_roles[label]] = res
            logger.info(f"  {label}: annual={res['annual']:+.1%} maxdd={res['maxdd']:+.1%} "
                  f"sharpe={res['sharpe']:.2f} turn={res['turnover']:.1f}x")

        # ── Cost sensitivity ──
        cost_check = self._check_cost_sensitivity(close, volume, amount, weights, timing)

        # ── Correlation ──
        corr_check = self._check_correlation(close, volume, amount, weights, timing)

        # ── OOS/IS decay ──
        decay_check = self._check_decay(segments)

        # ── Offset sensitivity (多偏移扰动测试) ──
        mask_is = (trade_dates >= pd.Timestamp(IS_SEG[1])) & (trade_dates <= pd.Timestamp(IS_SEG[2]))
        c_is = close.loc[mask_is]
        v_is = volume.loc[mask_is]
        a_is = amount.loc[mask_is]
        t_is = timing.loc[mask_is] if timing is not None else None

        offset_1_weights = build_rebalance_weights_offset(factor, close, self.top_n, self.rebalance, offset=1)
        w_offset_1 = {dt: ws for dt, ws in offset_1_weights.items() if dt in c_is.index}
        res_o1 = run_segment(c_is, v_is, a_is, w_offset_1, t_is, self.leverage, self.base_cost)

        offset_2_weights = build_rebalance_weights_offset(factor, close, self.top_n, self.rebalance, offset=2)
        w_offset_2 = {dt: ws for dt, ws in offset_2_weights.items() if dt in c_is.index}
        res_o2 = run_segment(c_is, v_is, a_is, w_offset_2, t_is, self.leverage, self.base_cost)

        base_annual = segments.get(IS_SEG[0], {}).get("annual", 0.0)
        offset_fail = False
        if base_annual > 0:
            if res_o1["annual"] < -0.05 or res_o2["annual"] < -0.05:
                offset_fail = True
            if res_o1["annual"] < 0.2 * base_annual or res_o2["annual"] < 0.2 * base_annual:
                offset_fail = True
        offset_check = {
            "base_annual": float(base_annual),
            "offset_1_annual": float(res_o1["annual"]),
            "offset_2_annual": float(res_o2["annual"]),
            "verdict": "FAIL" if offset_fail else "PASS"
        }

        report = {
            "family": self.family,
            "config": self.config,
            "segments": segments,
            "segments_by_role": segments_by_role,
            "cost_sensitivity": cost_check,
            "correlation": corr_check,
            "oos_is_decay": decay_check,
            "offset_sensitivity": offset_check,
            "timestamp": str(pd.Timestamp.now()),
        }

        # Save
        safe_name = self.family.replace("/", "_")
        out_path = OUT_DIR / f"{safe_name}_phase2.json"
        # Convert non-serializable items
        serializable = _make_serializable(report)
        out_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2))
        logger.info(f"  Report → {out_path}")

        return report

    # ── checks ──

    def _check_cost_sensitivity(self, close: pd.DataFrame, volume: pd.DataFrame,
                                amount: pd.DataFrame, weights: dict[pd.Timestamp, pd.Series],
                                timing: pd.Series | None) -> dict[str, Any]:
        """Run backtest with +50% costs, compare annual return."""
        # Full period 2018-2026 for cost sensitivity
        mask = (close.index >= "2018-01-01") & (close.index <= "2026-12-31")
        c = close.loc[mask]; v = volume.loc[mask]; a = amount.loc[mask]
        t = timing.loc[mask] if timing is not None else None
        w_seg = {dt: ws for dt, ws in weights.items() if dt in c.index}

        # Base cost
        base = run_segment(c, v, a, w_seg, t, self.leverage, self.base_cost)

        # +50% cost
        stressed_cost = CostModel(
            buy_cost=self.base_cost.buy_cost * 1.5,
            sell_cost=self.base_cost.sell_cost * 1.5,
            financing_rate=self.base_cost.financing_rate * 1.5,
        )
        stressed = run_segment(c, v, a, w_seg, t, self.leverage, stressed_cost)

        annual_decay = base["annual"] - stressed["annual"]
        decay_pct = annual_decay / base["annual"] if base["annual"] > 0 else float("inf")

        verdict = "PASS" if decay_pct < 0.5 else "FAIL"
        logger.info(f"  Cost +50%: annual {base['annual']:+.1%}→{stressed['annual']:+.1%} "
              f"(decay={decay_pct:.0%}) → {verdict}")

        return {
            "verdict": verdict,
            "base_annual": base["annual"],
            "stressed_annual": stressed["annual"],
            "decay_pct": decay_pct,
            "threshold": 0.5,
        }

    def _check_correlation(self, close: pd.DataFrame, volume: pd.DataFrame,
                           amount: pd.DataFrame, weights: dict[pd.Timestamp, pd.Series],
                           timing: pd.Series | None) -> dict[str, Any]:
        """Check daily return correlation vs registered strategies."""
        # Load registered strategies from strategy_versions.json
        reg_path = ROOT / "strategy_versions.json"
        if not reg_path.exists():
            return {"verdict": "SKIP", "detail": "No strategy registry found."}

        registry = json.loads(reg_path.read_text())
        my_family = self.family.split("/")[0]

        # Collect OTHER-family active strategies for comparison
        active_strategies = []
        for fam in registry.get("families", []):
            if fam["id"] == my_family:
                continue  # skip same family (would be self-correlation)
            for v in fam.get("versions", []):
                if v.get("status") == "在册":
                    active_strategies.append(f"{fam['id']}/{v['version']}")

        if not active_strategies:
            return {"verdict": "SKIP",
                    "detail": f"No other active strategies outside {my_family} family."}

        # Run our strategy on 2018-2026
        mask = (close.index >= "2018-01-01") & (close.index <= "2026-12-31")
        c = close.loc[mask]; v = volume.loc[mask]; a = amount.loc[mask]
        t = timing.loc[mask] if timing is not None else None
        w_seg = {dt: ws for dt, ws in weights.items() if dt in c.index}
        our_res = run_segment(c, v, a, w_seg, t, self.leverage, self.base_cost)
        our_ret = our_res["returns"].dropna()

        # For small-cap family itself, skip correlation check (it IS the baseline)
        if my_family == "small-cap-size":
            return {"verdict": "SKIP",
                    "detail": "Strategy is small-cap-size baseline — no external comparison needed."}

        # For each active OTHER-family strategy, compute return correlation
        # by running the small-cap baseline as reference
        correlations = {}
        for s_id in active_strategies:
            try:
                from factors.small_cap import small_cap_factor, small_cap_timing
                sc = small_cap_factor(a, window=60)
                bl_timing, _, _ = small_cap_timing(c, a, ma_window=16)
                bl_weights = build_rebalance_weights(sc, c, self.top_n, self.rebalance)
                bl_res = run_segment(c, v, a, bl_weights, bl_timing.astype(float),
                                     self.leverage, self.base_cost)
                bl_ret = bl_res["returns"].dropna()
                common = our_ret.index.intersection(bl_ret.index)
                if len(common) > 100:
                    corr = our_ret.loc[common].corr(bl_ret.loc[common])
                    correlations[s_id] = corr
            except Exception:
                continue

        if not correlations:
            return {"verdict": "SKIP", "detail": "Could not compute correlations against other strategies."}

        max_corr = max(abs(c) for c in correlations.values())
        if max_corr > 0.85:
            verdict = "FAIL"
        elif max_corr > 0.70:
            verdict = "WARN"
        else:
            verdict = "PASS"

        logger.info(f"  Correlation vs active: max|corr|={max_corr:.3f} → {verdict}")

        return {
            "verdict": verdict,
            "max_abs_corr": max_corr,
            "correlations": {k: round(v, 4) for k, v in correlations.items()},
            "threshold_warn": 0.70,
            "threshold_fail": 0.85,
        }

    def _check_decay(self, segments: dict[str, Any]) -> dict[str, Any]:
        """Check OOS/IS annual return decay。按前缀取段(OOS 标签的终点年随 boundary 变)。"""
        is_seg: dict[str, Any] = next((v for k, v in segments.items() if k.startswith("IS")), {})
        oos_seg: dict[str, Any] = next((v for k, v in segments.items() if k.startswith("OOS")), {})

        if not is_seg or not oos_seg:
            return {"verdict": "SKIP", "detail": "Missing IS or OOS segment."}

        is_ann = is_seg.get("annual", 0)
        oos_ann = oos_seg.get("annual", 0)

        if is_ann <= 0:
            return {"verdict": "FAIL", "detail": f"IS annual ≤ 0 ({is_ann:+.1%})."}

        ratio = oos_ann / is_ann
        verdict = "PASS" if ratio > 0.3 else "FAIL"

        logger.info(f"  OOS/IS decay: {oos_ann:+.1%} / {is_ann:+.1%} = {ratio:.2f} → {verdict}")

        return {
            "verdict": verdict,
            "is_annual": is_ann,
            "oos_annual": oos_ann,
            "ratio": ratio,
            "threshold": 0.3,
        }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class BacktestReport:
    family: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=lambda: str(pd.Timestamp.now()))

    @property
    def has_fail(self) -> bool:
        for check in ["cost_sensitivity", "correlation", "oos_is_decay"]:
            if self.data.get(check, {}).get("verdict") == "FAIL":
                return True
        for seg in self.data.get("segments", {}).values():
            if seg.get("annual", -1) <= 0:
                return True
        return False

    def summary(self) -> str:
        d = self.data
        segs = d.get("segments", {})
        lines = [
            f"Phase 2 Backtest: {self.family}",
            f"  Time: {self.timestamp}",
            f"  {'─' * 50}",
            "  Segments:",
        ]
        # 按前缀稳定排序展示(OOS 标签的终点年随 holdout boundary 变)。
        _order = {"IS": 0, "OOS": 1, "压力": 2}
        for label in sorted(segs, key=lambda k: next((_order[p] for p in _order if k.startswith(p)), 9)):
            s = segs.get(label, {})
            if s:
                icon = "✅" if s.get("annual", -1) > 0 else "❌"
                lines.append(
                    f"    {icon} {label}: annual={s['annual']:+.1%} "
                    f"maxdd={s['maxdd']:+.1%} sharpe={s['sharpe']:.2f} "
                    f"turn={s.get('turnover',0):.1f}x"
                )
        lines.append(f"  {'─' * 50}")

        # Cost sensitivity
        cs = d.get("cost_sensitivity", {})
        lines.append(f"  {'✅' if cs.get('verdict')=='PASS' else '❌'} Cost +50%: "
                     f"decay={cs.get('decay_pct',1):.0%} "
                     f"(limit 50%) → {cs.get('verdict','?')}")

        # OOS/IS decay
        dc = d.get("oos_is_decay", {})
        lines.append(f"  {'✅' if dc.get('verdict')=='PASS' else '❌'} OOS/IS decay: "
                     f"ratio={dc.get('ratio',0):.2f} "
                     f"(limit >0.3) → {dc.get('verdict','?')}")

        # Correlation
        cc = d.get("correlation", {})
        lines.append(f"  {'✅' if cc.get('verdict') in ('PASS','SKIP') else '⚠️' if cc.get('verdict')=='WARN' else '❌'} "
                     f"Correlation: max|corr|={cc.get('max_abs_corr',0):.3f} "
                     f"(warn>0.70, fail>0.85) → {cc.get('verdict','?')}")

        lines.append(f"  {'─' * 50}")
        lines.append(f"  → {'❌ BLOCKED' if self.has_fail else '✅ PASSED'}")
        return "\n".join(lines)


def _make_serializable(obj: Any) -> Any:
    """Convert numpy types to Python native types for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return str(obj)
    if isinstance(obj, (pd.Series, pd.DataFrame)):
        return None  # don't serialize large objects
    return obj
