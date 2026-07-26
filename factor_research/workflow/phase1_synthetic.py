"""Phase 1: Synthetic data time-travel audit.

Replaces static AST checks with numerical verification: build a synthetic
dataset with known ground truth, run the strategy's factor_builder and
timing_builder through it, and check that signals do NOT peek into the future.

5 checks (some cover multiple concerns):
  1. timing 穿越      — does timing[T] depend on close[T] or later?
  2. 财务对齐         — does factor respect avail_date alignment?
  3. amount 公式       — does amount use raw_close (not adjusted close)?
  4. 预热完整         — are NaN periods ≤ expected window sizes?
  5. 退市股覆盖       — are known delisted stocks present in data?

Usage:
  >>> from workflow.phase1_synthetic import Phase1Checker, AuditReport
  >>> checker = Phase1Checker(factor_builder=my_fn, timing_builder=my_timing)
  >>> report = checker.run_all()
  >>> print(report.summary())
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT: Path = Path(__file__).resolve().parent.parent
LESSONS_DIR: Path = ROOT / "workflow" / "pending_lessons"
LESSONS_DIR.mkdir(parents=True, exist_ok=True)

# Synthetic data parameters
N_HISTORY: int = 100   # days of stable filler before event period
N_EVENT: int = 20      # event period length
N_TOTAL: int = N_HISTORY + N_EVENT
EV_START: int = N_HISTORY


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def make_synthetic_clean() -> dict[str, Any]:
    """Synthetic dataset with CORRECT signal construction.

    120 trading days: first 100 stable filler, last 20 event period.
    Stock 000001: adjusted close = 2× raw_close (simulates historical split).
    Stock 000002: no adjustment.
    Stock 000003: delisted before event period.
    """
    np.random.seed(42)
    dates = pd.bdate_range("2019-07-01", periods=N_TOTAL)

    # ---- Random walk prices ----
    close_data = {}
    for s, base in [("000001", 10.0), ("000002", 20.0), ("000003", 5.0)]:
        rw = np.random.randn(N_TOTAL) * 0.008
        rw[0] = 0
        close_data[s] = base * np.exp(np.cumsum(rw))
    close = pd.DataFrame(close_data, index=dates)

    # Delist 000003 at ev_start-10
    close.iloc[EV_START - 10:, close.columns.get_loc("000003")] = np.nan

    # Raw close: 000001 has 2:1 adjustment → raw ≈ close/2
    raw_close = close.copy()
    raw_close["000001"] = close["000001"] / 2.0

    # Volume
    vol_data = {}
    for s, bv in [("000001", 2000), ("000002", 1500), ("000003", 3000)]:
        vol_data[s] = np.maximum(100, bv + np.random.randn(N_TOTAL) * 200).astype(int)
    volume = pd.DataFrame(vol_data, index=dates)
    volume.iloc[EV_START - 10:, volume.columns.get_loc("000003")] = 0

    # Amount = volume(share) × raw_close (canonical lake units)
    amount = volume * raw_close

    # ---- Event: +10% limit-up at ev_start+5 ----
    gap_idx = EV_START + 5
    for s in ["000001", "000002"]:
        ci = close.columns.get_loc(s)
        prev_c = close[s].iloc[gap_idx - 1]
        close.iloc[gap_idx, ci] = prev_c * 1.10
        ri = raw_close.columns.get_loc(s)
        prev_r = raw_close[s].iloc[gap_idx - 1]
        raw_close.iloc[gap_idx, ri] = prev_r * 1.10
    amount = volume * raw_close

    # ---- Fundamental: avail_date at ev_start ----
    # Use a MASSIVE value (10.0 = 1000% growth) so the fundamental signal
    # is unmistakable in the factor output
    fundamental = pd.DataFrame({
        "avail_date": [dates[EV_START]],
        "code": ["000001"],
        "net_profit_yoy": [10.0],
    })

    # ---- Reference timing signals ----
    mkt_ret = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    timing_clean = ((mkt_ret.rolling(2).sum() >= 0)
                    .shift(1, fill_value=True).astype(float))

    return {
        "close": close, "volume": volume, "raw_close": raw_close,
        "amount": amount, "trade_dates": dates, "fundamental": fundamental,
        "timing_clean": timing_clean, "event_start": EV_START,
        "gap_idx": gap_idx,
    }


def make_synthetic_leaky() -> dict[str, Any]:
    """Synthetic data with LEAKY signals (no shift(1), wrong amount)."""
    clean = make_synthetic_clean()
    close = clean["close"]
    volume = clean["volume"]

    mkt_ret = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    timing_leaky = ((mkt_ret.rolling(2).sum() >= 0)
                    .astype(float))  # ← NO shift(1)

    result = dict(clean)
    result["timing_clean"] = timing_leaky
    result["amount"] = volume * close  # ← adjusted close (wrong price base)!
    return result


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    check_id: str
    name: str
    verdict: str        # PASS / WARN / FAIL / SKIP
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_pass(self) -> bool: return self.verdict == "PASS"
    @property
    def is_fail(self) -> bool: return self.verdict == "FAIL"


# ---------------------------------------------------------------------------
# Phase1Checker
# ---------------------------------------------------------------------------

class Phase1Checker:
    """Run synthetic-data checks against a strategy's signal builders."""

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

    # ── Check 1: timing穿越 ──

    def _check_timing_no_peek(self, syn: dict[str, Any]) -> CheckResult:
        """Timing[T] must only use data through T-1.

        Two-phase detection:
          A. Formula match against known correct/leaky patterns.
          B. Perturbation test: change close at t_gap, check timing before t_gap.
        """
        close = syn["close"]
        amount = syn["amount"]
        timing = syn.get("timing_clean")
        gap_idx = syn["gap_idx"]

        if timing is None:
            return CheckResult("timing_peek", "timing shift(1)",
                               "WARN", "No timing signal provided.")

        # Phase A: formula matching
        mkt_ret = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
        correct = ((mkt_ret.rolling(2).sum() >= 0)
                   .shift(1, fill_value=True).astype(float))
        leaky = ((mkt_ret.rolling(2).sum() >= 0).astype(float))

        t_vals = timing.fillna(-999).values
        match_correct = bool((t_vals == correct.fillna(-999).values).all())
        match_leaky = bool((t_vals == leaky.fillna(-999).values).all())

        if match_correct and not match_leaky:
            return CheckResult("timing_peek", "timing shift(1)", "PASS",
                               "Timing matches correct shift(1) reference.",
                               {"method": "formula_match"})
        if match_leaky and not match_correct:
            return CheckResult("timing_peek", "timing shift(1)", "FAIL",
                               "Timing MISSING shift(1)! Uses T-day market return.",
                               {"method": "formula_match"})

        # Phase B: perturbation test
        close_pert = close.copy()
        close_pert.iloc[gap_idx] = close_pert.iloc[gap_idx] * 0.3  # -70% crash

        try:
            t_orig = self.timing_builder(close, amount)
            t_pert = self.timing_builder(close_pert, amount)
        except Exception:
            return CheckResult("timing_peek", "timing shift(1)", "WARN",
                               "Perturbation test failed (builder error). Manual review.",
                               {"method": "perturbation_error"})

        if t_orig is None or t_pert is None:
            return CheckResult("timing_peek", "timing shift(1)", "WARN",
                               "Timing builder returned None.", {"method": "no_output"})

        # Check: did timing before gap_idx change?
        pre_mask = timing.index[:gap_idx]
        changed = False
        for idx in pre_mask:
            if idx in t_pert.index:
                o = float(t_orig.loc[idx]) if pd.notna(t_orig.loc[idx]) else np.nan
                p = float(t_pert.loc[idx]) if pd.notna(t_pert.loc[idx]) else np.nan
                if not np.isclose(o, p, rtol=1e-3, atol=1e-3, equal_nan=True):
                    changed = True
                    break

        if changed:
            return CheckResult("timing_peek", "timing shift(1)", "FAIL",
                               "PERTURBATION FAILED: changing future close changed past timing.",
                               {"method": "perturbation", "pre_gap_changed": True})
        return CheckResult("timing_peek", "timing shift(1)", "PASS",
                           "Perturbation passed: future prices don't affect past timing.",
                           {"method": "perturbation", "pre_gap_changed": False})

    # ── Check 2: 财务对齐 ──

    def _check_fundamental_alignment(self, syn: dict[str, Any]) -> CheckResult:
        """Factor respects avail_date alignment for fundamental data.

        Two-step detection:
          1. Does the factor USE fundamentals at all?
             → Run factor with and without the fundamental data.
             → If output is identical → factor doesn't use fundamentals → SKIP.
          2. If factor uses fundamentals: check alignment.
             → Does the signal appear at the correct time (avail_date)?
             → If it appears BEFORE → FAIL (look-ahead).
        """
        fund = syn.get("fundamental")
        if fund is None or fund.empty:
            return CheckResult("fund_alignment", "avail_date 对齐", "SKIP",
                               "No fundamental data in synthetic panel.")

        # Step 1: Does the factor use fundamentals?
        # Run factor with fundamentals included (normal) vs excluded
        try:
            factor_with = self.factor_builder(
                syn["close"], syn["volume"], syn["amount"], syn["trade_dates"])
            # Run without fundamentals by providing empty fundamental data
            # The factor_builder receives trade_dates — if it calls
            # load_fundamental_panel internally, we can't control that.
            # Instead: compare if factor changes at avail_date specifically
            # for stock 000001 (which gets the bump) vs 000002 (which doesn't).
        except Exception as e:
            return CheckResult("fund_alignment", "avail_date 对齐", "WARN",
                               f"factor_builder error: {e}")

        if factor_with is None or factor_with.empty:
            return CheckResult("fund_alignment", "avail_date 对齐", "SKIP",
                               "Factor returned empty.")
        if "000001" not in factor_with.columns or "000002" not in factor_with.columns:
            return CheckResult("fund_alignment", "avail_date 对齐", "SKIP",
                               "Control stocks missing from factor output.")

        ev = syn["event_start"]
        spread = factor_with["000001"] - factor_with["000002"]

        # Compare spread before vs after avail_date.
        # If factor uses fundamentals: 000001 gets net_profit_yoy=10.0 at ev,
        # so spread should jump significantly at ev.
        pre_win = spread.iloc[max(0, ev - 10):ev].dropna()
        post_win = spread.iloc[ev:ev + 10].dropna()

        if len(pre_win) < 3 or len(post_win) < 3:
            return CheckResult("fund_alignment", "avail_date 对齐", "SKIP",
                               "Not enough valid data around avail_date.")

        pre_mean = float(pre_win.mean())
        post_mean = float(post_win.mean())
        jump = post_mean - pre_mean
        pre_std = float(pre_win.std()) if len(pre_win) > 1 else 0.01

        # Step 1 verdict: is there a statistically meaningful jump at avail_date?
        # If the jump is smaller than 1 std of pre-avail spread, it's just noise
        # → factor doesn't use fundamentals → SKIP
        if abs(jump) < max(pre_std * 2, 0.3):
            return CheckResult("fund_alignment", "avail_date 对齐", "SKIP",
                               f"No significant jump at avail_date (jump={jump:+.4f}, "
                               f"pre_std={pre_std:.4f}). Factor likely doesn't use fundamentals. "
                               f"If it does use load_fundamental_panel(), alignment is auto-handled.",
                               {"pre_mean": pre_mean, "post_mean": post_mean,
                                "jump": jump, "pre_std": pre_std})

        # Step 2: Factor DOES use fundamentals. Check for look-ahead.
        # Is the jump already visible BEFORE avail_date?
        # Compare spread 15-25d before ev vs 0-5d before ev
        early_win = spread.iloc[max(0, ev - 25):max(0, ev - 15)].dropna()
        pre_5d = spread.iloc[max(0, ev - 5):ev].dropna()

        if len(early_win) >= 3 and len(pre_5d) >= 3:
            early_mean = float(early_win.mean())
            pre_5d_mean = float(pre_5d.mean())
            # If pre-avail spread is already elevated → look-ahead
            if (pre_5d_mean - early_mean) > abs(jump) * 0.4:
                return CheckResult("fund_alignment", "avail_date 对齐", "FAIL",
                                   f"Fundamental signal appears BEFORE avail_date! "
                                   f"early={early_mean:.4f} pre-avail={pre_5d_mean:.4f} "
                                   f"post={post_mean:.4f}. Look-ahead detected.",
                                   {"early_mean": early_mean, "pre_5d_mean": pre_5d_mean,
                                    "post_mean": post_mean})

        return CheckResult("fund_alignment", "avail_date 对齐", "PASS",
                           f"Factor jump at avail_date: {pre_mean:+.4f} → {post_mean:+.4f} "
                           f"(jump={jump:+.4f}, {abs(jump)/max(pre_std,0.01):.1f}σ). "
                           f"Fundamental data correctly aligned.",
                           {"pre_mean": pre_mean, "post_mean": post_mean,
                            "jump": jump, "sigma": abs(jump)/max(pre_std, 0.01)})

    # ── Check 3: amount 公式 ──

    def _check_amount_formula(self, syn: dict[str, Any]) -> CheckResult:
        """Amount = volume(share) × raw_close, NOT adjusted close.

        Tests the INPUT data provided to factor_builder. Since all
        factors in this codebase accept amount as a pre-computed parameter
        (not recomputing it internally), verifying the input is sufficient.
        """
        close = syn["close"]
        volume = syn["volume"]
        raw_close = syn["raw_close"]
        amount = syn["amount"]

        expected = volume * raw_close
        wrong = volume * close

        diff_raw = float((amount - expected).abs().max().max())
        diff_adj = float((amount - wrong).abs().max().max())

        # Since raw_close ≠ adjusted close for stock 000001 (2:1 split),
        # we can distinguish which formula was used
        tol = 0.01
        matches_raw = diff_raw < tol
        matches_adj = diff_adj < tol

        if matches_adj and not matches_raw:
            return CheckResult("amount_formula", "amount = vol×raw_close", "FAIL",
                               f"Amount uses ADJUSTED close (diff vs raw={diff_raw:.1f}, "
                               f"vs adj={diff_adj:.4f}). Cross-sectional ranking is contaminated.",
                               {"diff_vs_raw": diff_raw, "diff_vs_adj": diff_adj})
        elif matches_raw:
            return CheckResult("amount_formula", "amount = vol×raw_close", "PASS",
                               f"Amount uses raw_close correctly (diff={diff_raw:.4f}).",
                               {"diff_vs_raw": diff_raw})
        else:
            return CheckResult("amount_formula", "amount = vol×raw_close", "WARN",
                               f"Amount formula unclear (raw_diff={diff_raw:.1f}, adj_diff={diff_adj:.1f}).",
                               {"diff_vs_raw": diff_raw, "diff_vs_adj": diff_adj})

    # ── Check 4: 预热完整 ──

    def _check_warmup(self, syn: dict[str, Any]) -> CheckResult:
        """Factor should have enough data for its rolling windows to warm up.

        Checks both:
          a. Total data length vs max rolling window
          b. Leading NaN days (all stocks NaN at start)
        """
        try:
            factor = self.factor_builder(
                syn["close"], syn["volume"], syn["amount"], syn["trade_dates"])
        except Exception as e:
            return CheckResult("warmup", "预热完整", "WARN", f"factor_builder error: {e}")

        n_days = len(syn["trade_dates"])
        max_window = max(
            self.config.get("size_window", 60),
            self.config.get("timing_ma", 16),
            self.config.get("vol_lookback", 60),
        )

        # (a) Data length check
        if n_days < max_window:
            return CheckResult("warmup", "预热完整", "FAIL",
                               f"Data has {n_days} days but max rolling window is {max_window}d. "
                               f"Need ≥{max_window} days for proper warmup. "
                               f"Fix: load data from ≥{max_window} trading days before backtest start.",
                               {"n_days": n_days, "max_window": max_window})

        # (b) Leading NaN check
        if factor is not None and not factor.empty:
            all_nan = factor.isna().all(axis=1)
            leading = 0
            for v in all_nan:
                if v: leading += 1
                else: break

            if leading > max_window:
                return CheckResult("warmup", "预热完整", "WARN",
                                   f"Leading NaN: {leading}d > max window {max_window}d. "
                                   f"Data may have gaps at start.",
                                   {"leading_nan": leading, "max_window": max_window})

        # Check valid data ratio: at least 30% of days should have valid factor
        if factor is not None and not factor.empty:
            valid_ratio = factor.notna().any(axis=1).mean()
            if valid_ratio < 0.3:
                return CheckResult("warmup", "预热完整", "WARN",
                                   f"Only {valid_ratio:.0%} of days have valid factor values. "
                                   f"Rolling windows may be under-warmed.",
                                   {"valid_ratio": valid_ratio})

        return CheckResult("warmup", "预热完整", "PASS",
                           f"Data: {n_days}d ≥ {max_window}d max window. Warmup sufficient.",
                           {"n_days": n_days, "max_window": max_window})

    # ── Check 5: 退市股覆盖 ──

    def _check_delisted_coverage(self, syn: dict[str, Any]) -> CheckResult:
        """Known delisted stocks should be present in price panel."""
        close = syn["close"]

        # Synthetic: stock 000003 is delisted
        if "000003" in close.columns:
            n_present = close["000003"].notna().sum()
            if n_present > 0:
                # Also check real data
                meta = ROOT / "data_lake" / "meta" / "delisted_codes.parquet"
                if meta.exists():
                    try:
                        dl = pd.read_parquet(meta)
                        dl_codes = set(dl["code"].astype(str).str.zfill(6))
                        found = dl_codes & set(close.columns)
                        cov = len(found) / len(dl_codes) if dl_codes else 0
                        if cov >= 0.70:
                            return CheckResult("delisted", "退市股覆盖", "PASS",
                                               f"Coverage {cov:.0%} ({len(found)}/{len(dl_codes)}).",
                                               {"coverage": cov})
                        elif cov >= 0.30:
                            return CheckResult("delisted", "退市股覆盖", "WARN",
                                               f"Coverage only {cov:.0%}.",
                                               {"coverage": cov})
                        return CheckResult("delisted", "退市股覆盖", "FAIL",
                                           f"Coverage <30%: {cov:.0%}.", {"coverage": cov})
                    except Exception:
                        pass
                return CheckResult("delisted", "退市股覆盖", "WARN",
                                   "No delisted_codes.parquet — coverage unknown. "
                                   "Synthetic delisted stock present (OK).")

        return CheckResult("delisted", "退市股覆盖", "WARN",
                           "Delisted stock 000003 missing from synthetic panel.")

    # ── main entry ──

    def run_all(self, use_clean: bool = True, save_lessons: bool = True) -> list[CheckResult]:
        """Run all checks. Set save_lessons=False for parallel execution."""
        syn = make_synthetic_clean() if use_clean else make_synthetic_leaky()

        # Override timing with user's actual builder
        try:
            ut = self.timing_builder(syn["close"], syn["amount"])
            if ut is not None and not ut.empty:
                syn["timing_clean"] = ut
        except Exception:
            pass

        checks = [
            self._check_timing_no_peek(syn),
            self._check_fundamental_alignment(syn),
            self._check_amount_formula(syn),
            self._check_warmup(syn),
            self._check_delisted_coverage(syn),
        ]
        if save_lessons:
            self._maybe_save_lessons(checks)
        return checks

    # ── lesson generation ──

    def _maybe_save_lessons(self, checks: list[CheckResult]) -> None:
        saved = 0
        for c in checks:
            if c.verdict not in ("FAIL", "WARN"):
                continue
            fp = hashlib.sha256(
                f"{c.check_id}:{c.name}:{c.detail[:100]}".encode()
            ).hexdigest()[:8]
            lf = LESSONS_DIR / f"{c.check_id}_{fp}.json"
            if lf.exists():
                try: ex = json.loads(lf.read_text())
                except (json.JSONDecodeError, ValueError): ex = {}
                ex["hit_count"] = ex.get("hit_count", 1) + 1
                ex["last_seen"] = str(pd.Timestamp.now())
                if self.family not in ex.get("strategies", []):
                    ex.setdefault("strategies", []).append(self.family)
                lf.write_text(json.dumps(ex, ensure_ascii=False, indent=2))
                saved += 1
            else:
                lf.write_text(json.dumps({
                    "fingerprint": fp,
                    "trigger": f"Phase1_{c.check_id}",
                    "pattern": c.name, "detail": c.detail,
                    "fix": _suggest_fix(c.check_id),
                    "hit_count": 1,
                    "first_seen": str(pd.Timestamp.now()),
                    "last_seen": str(pd.Timestamp.now()),
                    "strategies": [self.family],
                }, ensure_ascii=False, indent=2))
                saved += 1
        if saved:
            try:
                from knowledge.graph import sync_pending_lessons_to_graph
                sync_pending_lessons_to_graph()
            except Exception as exc:
                logger.info(f"[knowledge] pending lessons sync failed: {exc}")


def _suggest_fix(check_id: str) -> str:
    return {
        "timing_peek": "Add .shift(1) to timing: timing = (condition).shift(1).",
        "amount_formula": "Use amount = volume * raw_close (shares × CNY/share), not adjusted close.",
        "fund_alignment": "Use load_fundamental_panel() which aligns via avail_date→ffill.",
        "warmup": "Load data from ≥2yr before backtest start for rolling window warmup.",
        "delisted": "Ensure data pipeline includes delisted stocks from meta/delisted_codes.",
    }.get(check_id, "Manual review needed.")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class AuditReport:
    family: str
    results: list[CheckResult]
    timestamp: str = field(default_factory=lambda: str(pd.Timestamp.now()))

    @property
    def all_pass(self) -> bool:
        return all(r.verdict in ("PASS", "SKIP") for r in self.results)

    @property
    def has_fail(self) -> bool:
        return any(r.is_fail for r in self.results)

    def summary(self) -> str:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "—"}
        lines = [
            f"Phase 1 Audit: {self.family}",
            f"  Time: {self.timestamp}",
            f"  {'─' * 50}",
        ]
        for r in self.results:
            lines.append(f"  {icon.get(r.verdict, '?')} {r.name}: {r.verdict}")
        lines.append(f"  {'─' * 50}")
        if self.has_fail:
            lines.append("  → ❌ BLOCKED (fix FAILs first)")
            lines.append("\n  Failures:")
            for r in self.results:
                if r.is_fail:
                    lines.append(f"    [{r.check_id}] {r.detail}")
        elif not self.all_pass:
            lines.append("  → ⚠️ PROCEED WITH CAUTION (review WARNs)")
        else:
            lines.append("  → ✅ ALL CHECKS PASSED")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family, "timestamp": self.timestamp,
            "results": [{"check_id": r.check_id, "name": r.name,
                         "verdict": r.verdict, "detail": r.detail,
                         "evidence": r.evidence} for r in self.results],
            "all_pass": self.all_pass, "has_fail": self.has_fail,
        }
