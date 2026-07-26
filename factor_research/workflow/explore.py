"""Automated strategy exploration — parallel pipeline.

Defines a search space of unexplored factor candidates, runs them
through Phase 1→2→3 with parallel fan-out at each stage.

Usage:
  cd /Users/kiki/astcok/factor_research
  python3 workflow/explore.py
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

import json
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT: Path = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_timing
from factors.utils import mad_clip, safe_zscore

OUT_DIR: Path = ROOT / "reports" / "exploration"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# Picklable factor builders (module-level classes, not closures)
# ═══════════════════════════════════════════════════════════════════

def _pt_timing(close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
    t, _, _ = small_cap_timing(close, amount, ma_window=16)
    return t.astype(float)

def _no_timing(close: pd.DataFrame, amount: pd.DataFrame) -> pd.Series:
    return pd.Series(1.0, index=close.index)


class LowTurnover:
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        t = a / (c * v * 100 + 1)
        return safe_zscore(mad_clip(-t.rolling(self.w).mean()))

class LowVolatility:
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        r = c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip(-r.rolling(self.w).std()))

class Illiquidity:
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        r = c.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip((r / (a + 1)).rolling(self.w).mean()))

class VolPriceDivergence:
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        pc = c.pct_change(self.w, fill_method=None)
        vc = v.rolling(self.w).mean().pct_change(self.w, fill_method=None)
        return safe_zscore(mad_clip((-pc * vc).rolling(5).mean()))

class MomentumResonance:
    def __init__(self, fast: int = 5, slow: int = 20) -> None: self.fast = fast; self.slow = slow
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        mf = c.pct_change(self.fast, fill_method=None)
        ms = c.pct_change(self.slow, fill_method=None)
        return safe_zscore(mad_clip(np.sign(mf) * np.sign(ms) * ms.abs()))

class DualLow:
    def __init__(self, tw: int = 20, vw: int = 20) -> None: self.tw = tw; self.vw = vw
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        lt = - (a / (c * v * 100 + 1)).rolling(self.tw).mean()
        r = c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        lv = -r.rolling(self.vw).std()
        return safe_zscore(mad_clip(lt + lv))

class SizeLowVol:
    def __init__(self, vw: int = 20) -> None: self.vw = vw
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        sz = -np.log(a.rolling(60).mean() + 1)
        r = c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        return safe_zscore(mad_clip(sz - r.rolling(self.vw).std()))

# ── Round 2: orthogonal niches (low correlation to illiquidity) ──

class MidCapReversal:
    """Mid-cap short-term reversal: fade recent returns in mid/large stocks."""
    def __init__(self, w: int = 5) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        # Only consider top 50% by amount (mid-large caps)
        amt_rank = a.rolling(60).mean().rank(axis=1, pct=True)
        mid_mask = amt_rank > 0.50
        ret = c.pct_change(self.w, fill_method=None).replace([np.inf, -np.inf], np.nan)
        # Fade recent returns (reversal), masked to mid-large
        signal = -ret * mid_mask
        return safe_zscore(mad_clip(signal))

class IndustryMomentum:
    """Momentum ranked within industry — removes size effect."""
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        from lake.load_lake import load_fundamental_panel
        ret = c.pct_change(self.w, fill_method=None).replace([np.inf, -np.inf], np.nan)
        # Get industry labels
        fund = load_fundamental_panel(d, fields=['industry'])
        ind = fund.get('industry', pd.DataFrame())
        if ind.empty:
            return safe_zscore(mad_clip(ret))
        ind = ind.reindex(d).ffill()
        # Rank momentum within each industry
        ranked = pd.DataFrame(index=ret.index, columns=ret.columns, dtype=float)
        for dt in ret.index:
            if dt not in ind.index: continue
            row_ret = ret.loc[dt].dropna()
            row_ind = ind.loc[dt].dropna()
            common = row_ret.index.intersection(row_ind.index)
            if len(common) < 50: continue
            for _, group in row_ind.loc[common].groupby(row_ind.loc[common]):
                stocks = group.index.intersection(row_ret.index)
                if len(stocks) < 3: continue
                vals = row_ret.loc[stocks]
                ranked.loc[dt, stocks] = (vals - vals.mean()) / (vals.std() + 1e-8)
        return safe_zscore(mad_clip(ranked))

class LowBeta:
    """Low market sensitivity — defensive stocks."""
    def __init__(self, w: int = 60) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        ret = c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
        mkt = ret.mean(axis=1)
        # Rolling beta: Cov(stock, mkt) / Var(mkt)
        cov = ret.rolling(self.w).cov(mkt)
        var = mkt.rolling(self.w).var() + 1e-8
        beta = cov.div(var, axis=0)
        return safe_zscore(mad_clip(-beta))  # buy low-beta

class IlliqLowVolBlend:
    """illiquidity + low volatility, equal weight."""
    def __init__(self, iw: int = 20, vw: int = 20) -> None: self.iw = iw; self.vw = vw
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        r = c.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
        il = safe_zscore(mad_clip((r/(a+1)).rolling(self.iw).mean()))
        lv = safe_zscore(mad_clip(-c.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).rolling(self.vw).std()))
        return safe_zscore(mad_clip(il + lv))

class IlliqSizeBlend:
    """illiquidity + size factor, equal weight."""
    def __init__(self, iw: int = 20) -> None: self.iw = iw
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        r = c.pct_change(fill_method=None).abs().replace([np.inf, -np.inf], np.nan)
        il = safe_zscore(mad_clip((r/(a+1)).rolling(self.iw).mean()))
        sz = safe_zscore(mad_clip(-np.log(a.rolling(60).mean() + 1)))
        return safe_zscore(mad_clip(il + sz))

class MidCapQuality:
    """Quality in mid-large caps: high ROE, filtered by size."""
    def __init__(self, w: int = 20) -> None: self.w = w
    def __call__(self, c: pd.DataFrame, v: pd.DataFrame, a: pd.DataFrame,
                 d: pd.DatetimeIndex) -> pd.DataFrame:
        from lake.load_lake import load_fundamental_panel
        amt_rank = a.rolling(60).mean().rank(axis=1, pct=True)
        mid_mask = amt_rank > 0.30  # top 70% by size
        fund = load_fundamental_panel(d, fields=['roe'])
        roe = fund.get('roe', pd.DataFrame())
        if roe.empty:
            return safe_zscore(mad_clip(mid_mask.astype(float)))
        roe = roe.reindex(d).ffill()
        quality = safe_zscore(mad_clip(roe)) * mid_mask
        return safe_zscore(mad_clip(quality))


# ═══════════════════════════════════════════════════════════════════
# Candidate pool
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FactorSpec:
    name: str
    factor_builder: Callable[..., pd.DataFrame]
    timing_builder: Callable[..., pd.Series]
    config: dict[str, Any] = field(default_factory=dict)
    niche: str = ""
    hypothesis: str = ""


def make_candidates() -> list[FactorSpec]:
    base = {"top_n": 25, "rebalance_days": 20, "leverage": 1.25,
            "buy_cost": 0.00225, "sell_cost": 0.00275, "financing_rate": 0.065}
    C = []

    for w in [20, 40, 60]:
        C.append(FactorSpec(f"low_turnover_{w}d", LowTurnover(w), _pt_timing,
                            {**base, "window": w}, "low-turnover", "低换手溢价"))
        C.append(FactorSpec(f"low_volatility_{w}d", LowVolatility(w), _pt_timing,
                            {**base, "window": w}, "low-vol", "低波异象"))
        C.append(FactorSpec(f"illiquidity_{w}d", Illiquidity(w), _pt_timing,
                            {**base, "window": w}, "illiquidity", "Amihud非流动性"))
    for w in [20, 40]:
        C.append(FactorSpec(f"vol_price_div_{w}d", VolPriceDivergence(w), _pt_timing,
                            {**base, "window": w}, "vol-price", "量价背离"))
    for f, s in [(5, 20), (10, 40), (20, 60)]:
        C.append(FactorSpec(f"mom_resonance_{f}d_{s}d", MomentumResonance(f, s),
                            _pt_timing, {**base, "fast": f, "slow": s},
                            "momentum", f"动量共振{f}/{s}"))
    for tw, vw in [(20, 20), (40, 40)]:
        C.append(FactorSpec(f"dual_low_{tw}d", DualLow(tw, vw), _pt_timing,
                            {**base, "turn_w": tw, "vol_w": vw}, "dual-low", "低换手+低波"))
    for vw in [20, 40]:
        C.append(FactorSpec(f"size_low_vol_{vw}d", SizeLowVol(vw), _pt_timing,
                            {**base, "vol_w": vw}, "size-low-vol", "小盘低波"))

    # ── Round 2: orthogonal niches (info only, don't block) ──
    for w in [5, 10, 20]:
        C.append(FactorSpec(f"midcap_reversal_{w}d", MidCapReversal(w), _pt_timing,
                            {**base, "window": w}, "mid-reversal", "中盘反转"))
    for w in [20, 40, 60]:
        C.append(FactorSpec(f"industry_mom_{w}d", IndustryMomentum(w), _pt_timing,
                            {**base, "window": w}, "ind-momentum", "行业内动量"))
    for w in [40, 60]:
        C.append(FactorSpec(f"low_beta_{w}d", LowBeta(w), _pt_timing,
                            {**base, "window": w}, "low-beta", "低beta防御"))
    C.append(FactorSpec("midcap_quality", MidCapQuality(20), _pt_timing,
                        {**base}, "mid-quality", "中盘质量"))

    # ── Round 3: winner variants + combos ──
    # Higher leverage illiquidity
    for lev in [1.5, 2.0]:
        C.append(FactorSpec(f"illiquidity_20d_lev{lev}x", Illiquidity(20), _pt_timing,
                            {**base, "leverage": lev}, "illi-lev", f"illiq杠杆{lev}x"))
    # illiquidity + low volatility blend
    C.append(FactorSpec("illiq_low_vol_blend", IlliqLowVolBlend(20, 20), _pt_timing,
                        {**base}, "illiq-lovol", "非流动性+低波等权"))
    # illiquidity + size blend
    C.append(FactorSpec("illiq_size_blend", IlliqSizeBlend(20), _pt_timing,
                        {**base}, "illiq-size", "非流动性+size等权"))
    # Different rebalance: weekly (5d) illiquidity
    C.append(FactorSpec("illiquidity_20d_weekly", Illiquidity(20), _pt_timing,
                        {**base, "rebalance_days": 5}, "illiq-freq", "illiq周频调仓"))
    # Top 50 illiquidity
    C.append(FactorSpec("illiquidity_20d_top50", Illiquidity(20), _pt_timing,
                        {**base, "top_n": 50}, "illiq-wide", "illiq持仓50只"))

    return C


# ═══════════════════════════════════════════════════════════════════
# Phase runners (module-level for pickle compatibility)
# ═══════════════════════════════════════════════════════════════════

def _run_phase1(spec: FactorSpec) -> dict[str, Any]:
    from workflow.phase1_synthetic import Phase1Checker
    checker = Phase1Checker(spec.factor_builder, spec.timing_builder,
                            spec.name, spec.config)
    results = checker.run_all(use_clean=True, save_lessons=False)
    fails = [r for r in results if r.is_fail]
    warns = [r for r in results if r.verdict == "WARN"]
    return {"name": spec.name, "niche": spec.niche,
            "phase1_pass": len(fails) == 0,
            "phase1_fails": [r.check_id for r in fails],
            "phase1_warns": [r.check_id for r in warns]}


def _run_phase2(spec: FactorSpec) -> dict[str, Any]:
    from workflow.phase2_backtest import Phase2Runner
    runner = Phase2Runner(spec.factor_builder, spec.timing_builder,
                          spec.name, spec.config)
    data = runner.run(warmup_start="2010-01-01")
    segs = data.get("segments", {})
    is_s = segs.get("IS  2018-2022", {})
    oos_s = segs.get("OOS 2023-2026", {})
    st_s = segs.get("压力 2010-2017", {})
    blocked, reasons = False, []
    for lbl, s in [("IS", is_s), ("OOS", oos_s), ("压力", st_s)]:
        if s.get("annual", -1) <= 0:
            blocked = True; reasons.append(f"{lbl} annual≤0")
    if data.get("cost_sensitivity", {}).get("verdict") == "FAIL":
        blocked = True; reasons.append("cost FAIL")
    # Correlation is informational only — no longer blocks strategy library expansion
    return {"name": spec.name, "niche": spec.niche,
            "phase2_pass": not blocked, "phase2_reasons": reasons,
            "is_annual": is_s.get("annual", 0), "is_maxdd": is_s.get("maxdd", 0),
            "is_sharpe": is_s.get("sharpe", 0),
            "oos_annual": oos_s.get("annual", 0), "oos_maxdd": oos_s.get("maxdd", 0),
            "stress_annual": st_s.get("annual", 0), "stress_maxdd": st_s.get("maxdd", 0),
            "cost_decay": data.get("cost_sensitivity", {}).get("decay_pct", 1),
            "corr_max": data.get("correlation", {}).get("max_abs_corr", 0)}


def _run_phase3(spec: FactorSpec) -> dict[str, Any]:
    from workflow.phase3_wf import WF3Runner
    runner = WF3Runner(spec.factor_builder, spec.timing_builder,
                       spec.name, spec.config)
    data = runner.run(warmup_start="2010-01-01")
    agg = data.get("aggregate", {})
    wins = data.get("windows", [])
    return {"name": spec.name, "niche": spec.niche,
            "phase3_pass": agg.get("verdict") == "PASS",
            "wf_annual": agg.get("annual", 0), "wf_maxdd": agg.get("maxdd", 0),
            "wf_sharpe": agg.get("sharpe", 0), "wf_calmar": agg.get("calmar", 0),
            "wf_pos": agg.get("positive_windows", 0), "wf_tot": agg.get("total_windows", 0),
            "wf_neg_yrs": [w["test_start_year"] for w in wins if w.get("oos_annual", 1) <= 0]}


# ═══════════════════════════════════════════════════════════════════
# Explorer
# ═══════════════════════════════════════════════════════════════════

class Explorer:
    def __init__(self, candidates: list[FactorSpec], max_workers: int = 8) -> None:
        self.candidates = candidates
        self.max_workers = max_workers
        self.p1: list[dict[str, Any]] = []
        self.p2: list[dict[str, Any]] = []
        self.p3: list[dict[str, Any]] = []

    def run(self) -> list[dict[str, Any]]:
        t0 = time.time()
        logger.info(f"Explorer: {len(self.candidates)} candidates, {self.max_workers} workers\n")

        # ── Phase 1: parallel synthetic audit ──
        logger.info(f"{'='*60}\nPhase 1 (synthetic audit)\n{'='*60}")
        t1 = time.time()
        with ProcessPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(_run_phase1, c): c for c in self.candidates}
            for fut in as_completed(futs):
                r = fut.result(); self.p1.append(r)
                icon = "✅" if r["phase1_pass"] else "❌"
                logger.info(f"  {icon} {r['name']:<28} {r['phase1_fails']}{' '+str(r['phase1_warns']) if r['phase1_warns'] else ''}")
        passed = [r for r in self.p1 if r["phase1_pass"]]
        logger.info(f"  → {len(passed)}/{len(self.candidates)} passed ({time.time()-t1:.0f}s)\n")
        if not passed: return self._report()

        # ── Phase 2: parallel backtest ──
        p2_candidates = [c for c in self.candidates if c.name in {r['name'] for r in passed}]
        logger.info(f"{'='*60}\nPhase 2 (3-segment backtest)\n{'='*60}")
        t2 = time.time()
        with ProcessPoolExecutor(max_workers=min(self.max_workers, 4)) as ex:
            futs = {ex.submit(_run_phase2, c): c for c in p2_candidates}
            for fut in as_completed(futs):
                r = fut.result(); self.p2.append(r)
                icon = "✅" if r["phase2_pass"] else "❌"
                logger.info(f"  {icon} {r['name']:<28} IS={r['is_annual']:+.1%} "
                      f"OOS={r['oos_annual']:+.1%} stress={r['stress_annual']:+.1%} "
                      f"cost={r['cost_decay']:.0%} corr={r['corr_max']:.2f}")
        passed2 = [r for r in self.p2 if r["phase2_pass"]]
        logger.info(f"  → {len(passed2)}/{len(p2_candidates)} passed ({(time.time()-t2)/60:.0f}m)\n")
        if not passed2: return self._report()

        # ── Phase 3: sequential WF ──
        p3_candidates = [c for c in p2_candidates if c.name in {r['name'] for r in passed2}]
        logger.info(f"{'='*60}\nPhase 3 (Walk-Forward, sequential)\n{'='*60}")
        t3 = time.time()
        for i, c in enumerate(p3_candidates):
            logger.info(f"  [{i+1}/{len(p3_candidates)}] {c.name}...", end=" ")
            r = _run_phase3(c); self.p3.append(r)
            icon = "✅" if r["phase3_pass"] else "❌"
            logger.info(f"{icon} WF={r['wf_annual']:+.1%} DD={r['wf_maxdd']:+.1%} "
                  f"S={r['wf_sharpe']:.2f} +{r['wf_pos']}/{r['wf_tot']}")
        passed3 = [r for r in self.p3 if r["phase3_pass"]]
        logger.info(f"  → {len(passed3)}/{len(p3_candidates)} passed ({(time.time()-t3)/60:.0f}m)\n")

        logger.info(f"Total: {(time.time()-t0)/60:.0f}m")
        return self._report()

    def _report(self) -> list[dict[str, Any]]:
        all_r = self.p3 if self.p3 else self.p2 if self.p2 else self.p1
        ranked = sorted(all_r, key=lambda r: r.get("wf_annual", r.get("is_annual", r.get("oos_annual", -999))), reverse=True)

        logger.info(f"\n{'='*90}")
        logger.info("FINAL RANKINGS")
        logger.info(f"{'='*90}")
        hdr = f"{'#':<4} {'Name':<30} {'P1':<5} {'P2':<5} {'P3':<5} {'IS':>7} {'OOS':>7} {'WF':>7} {'WF_DD':>7} {'Shpe':>6}"
        logger.info(hdr)
        logger.info("-" * 90)
        for i, r in enumerate(ranked, 1):
            p1 = "✅" if r.get("phase1_pass", True) else "❌"
            p2 = "✅" if r.get("phase2_pass", True) else "❌"
            p3 = "✅" if r.get("phase3_pass", True) else "❌"
            logger.info(f"{i:<4} {r['name']:<30} {p1:<5} {p2:<5} {p3:<5} "
                  f"{r.get('is_annual',0):>+6.1%} {r.get('oos_annual',0):>+6.1%} "
                  f"{r.get('wf_annual',0):>+6.1%} {r.get('wf_maxdd',0):>+6.1%} "
                  f"{r.get('wf_sharpe',0):>+5.2f}")

        # Save JSON
        rp = OUT_DIR / f"explore_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.json"
        rp.write_text(json.dumps({
            "timestamp": str(pd.Timestamp.now()),
            "n": len(self.candidates),
            "p1_survivors": len([r for r in self.p1 if r.get("phase1_pass")]),
            "p2_survivors": len([r for r in self.p2 if r.get("phase2_pass")]),
            "p3_survivors": len([r for r in self.p3 if r.get("phase3_pass")]),
            "results": _json_safe(ranked),
        }, ensure_ascii=False, indent=2))
        logger.info(f"\nReport → {rp}")
        return ranked


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict): return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.bool_,)): return bool(obj)
    return obj


if __name__ == "__main__":
    candidates = make_candidates()
    niches = set(c.niche for c in candidates)
    logger.info(f"Generated {len(candidates)} candidates across {len(niches)} niches: {sorted(niches)}\n")
    Explorer(candidates, max_workers=8).run()
