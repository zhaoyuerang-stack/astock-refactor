"""Strategy Composer — regime-conditional 组合编排与优化.

输入: 按 regime 分组的策略腿池
输出: 最优编排方案的 JSON 策略定义

用法:
  from factory.strategy_composer import StrategyComposer, LegSpec
  composer = StrategyComposer(close, amount, bond_returns)
  composer.add_legs("bull", [leg1, leg2])
  composer.add_legs("bear", [leg3, bond_leg])
  best = composer.optimize()
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app_config.log import get_logger
from factory.regime import RegimeEngine

logger = get_logger(__name__)


@dataclass
class LegSpec:
    """策略腿规格."""
    name: str
    regime: str
    daily_returns: pd.Series
    description: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class StrategyDefinition:
    """编排完成的策略定义 — 可直接序列化."""
    name: str
    legs: dict[str, LegSpec]
    regime_classifier: str = "puretrend_ma16"
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy": self.name,
            "regime_classifier": self.regime_classifier,
            "legs": {
                regime: {
                    "name": leg.name, "params": leg.params,
                }
                for regime, leg in self.legs.items()
            },
            "metrics": {
                k: round(v, 6) if isinstance(v, float) else v
                for k, v in self.metrics.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class StrategyComposer:
    """策略编排器."""

    def __init__(
        self, close, amount,
        regime_engine=None, start="2016-01-01",
    ):
        self.close = close; self.amount = amount; self.start = start
        self.regime_engine = regime_engine or RegimeEngine(close, amount)
        self._legs: dict[str, list[LegSpec]] = {}
        self._regime_masks: dict[str, pd.Series] = {}
        self._results: list[dict] = []

    def add_legs(self, regime: str, legs: list[LegSpec]):
        if regime not in self._legs:
            self._legs[regime] = []
        self._legs[regime].extend(legs)

    def _get_regime_mask(self, regime: str) -> pd.Series:
        if regime not in self._regime_masks:
            if regime == "bull":
                self._regime_masks[regime] = self.regime_engine.get_regime_mask(
                    start=self.start, trend="up")
            elif regime == "bear":
                self._regime_masks[regime] = self.regime_engine.get_regime_mask(
                    start=self.start, trend="down")
            else:
                self._regime_masks[regime] = pd.Series(
                    True, index=self.close.loc[self.start:].index)
        return self._regime_masks[regime]

    def _compose_returns(self, leg_map: dict[str, LegSpec]) -> pd.Series:
        all_rets = [l.daily_returns for l in leg_map.values()]
        common = all_rets[0].index
        for r in all_rets[1:]:
            common = common.intersection(r.index)

        masks = {regime: self._get_regime_mask(regime).reindex(common).fillna(False)
                 for regime in leg_map}

        combined = []
        for dt in common:
            val = 0.0
            for regime, leg in leg_map.items():
                if masks[regime].loc[dt]:
                    val = leg.daily_returns.loc[dt]
                    break
            combined.append(val)
        return pd.Series(combined, index=common)

    def optimize(self, top_n_per_regime=5, verbose=True) -> StrategyDefinition:
        import importlib
        asymmetry_audit = importlib.import_module("factory.analysis.asymmetry_audit")
        asymmetry_report = asymmetry_audit.asymmetry_report
        regimes = sorted(self._legs.keys())
        if len(regimes) < 2:
            raise ValueError("至少需要两个 regime")

        candidates = {r: self._legs[r][:top_n_per_regime] for r in regimes}
        from itertools import product
        regime_names = list(candidates.keys())
        leg_lists = [candidates[r] for r in regime_names]
        total = 1
        for ll in leg_lists: total *= len(ll)

        if verbose:
            logger.info(f"Composer: {len(regime_names)} regimes x {'x'.join(str(len(l)) for l in leg_lists)} = {total} 组合")

        mkt = self.close.loc[self.start:].pct_change().mean(axis=1).fillna(0)
        combos = []

        for leg_tuple in product(*leg_lists):
            leg_map = dict(zip(regime_names, leg_tuple, strict=True))
            r = self._compose_returns(leg_map)
            if len(r) < 100: continue

            rep = asymmetry_report(r, mkt, "combo")
            ann = float(r.mean() * 252)
            dd = float(((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min())
            nav = (1 + r).cumprod().iloc[-1]
            vol = float(r.std() * np.sqrt(252))
            sh = (ann - 0.025) / vol if vol > 0 else 0
            cal = ann / abs(dd) if dd < 0 else 0

            score = (
                rep.asymmetry_score * 0.40
                + min(max(sh / 3.0, 0), 1) * 0.30
                + min(max(cal / 3.0, 0), 1) * 0.20
                + min(max(ann / 0.50, 0), 1) * 0.10
            )

            combos.append({
                "leg_map": leg_map, "ann": ann, "mdd": dd, "sh": sh,
                "cal": cal, "nav": nav, "gain_pain": rep.gain_pain,
                "asym_score": rep.asymmetry_score, "composite_score": score,
            })

        combos.sort(key=lambda c: c["composite_score"], reverse=True)
        self._results = combos

        if verbose and combos:
            best = combos[0]
            logger.info(f"  最优: {best['ann']:+.1%}/{best['mdd']:.1%}/sh={best['sh']:.2f} "
                        f"score={best['composite_score']:.0%} nav={best['nav']*100:.0f}万")

        best = combos[0]
        return StrategyDefinition(
            name="regime_composer_v1", legs=best["leg_map"],
            regime_classifier="puretrend_ma16",
            metrics={
                "annual": best["ann"], "maxdd": best["mdd"],
                "sharpe": best["sh"], "calmar": best["cal"],
                "gain_pain": best["gain_pain"],
                "asymmetry_score": best["asym_score"],
                "composite_score": best["composite_score"],
                "nav_100w": best["nav"] * 100,
            },
        )

    def report(self, top_n=10):
        if not self._results:
            logger.info("先调用 optimize()"); return
        logger.info(f"\n  {'排名':<4} {'Bull':<25} {'Bear':<25} {'年化':>8} {'回撤':>8} {'夏普':>6} {'评分':>6}")
        logger.info("  " + "-" * 90)
        for i, c in enumerate(self._results[:top_n]):
            b_name = c["leg_map"].get("bull", LegSpec("?", "?", pd.Series())).name
            br_name = c["leg_map"].get("bear", LegSpec("?", "?", pd.Series())).name
            logger.info(f"  {i+1:<4} {b_name:<25} {br_name:<25} {c['ann']:>+7.1%} "
                        f"{c['mdd']:>+7.1%} {c['sh']:>5.2f} {c['composite_score']:>5.0%}")
