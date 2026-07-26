"""Feature-tradable masks for A-share limit-up/limit-down upstream contamination.

Upstream contamination: post-hoc row filters cannot undo non-executable closes
that already entered rolling means / corr / ranks (arXiv:2507.07107, USTC).

This module is **construction hygiene**, not alpha:
  · ``build_feature_tradable_mask`` — True = price may enter rolling features
  · ``cleanse`` — set non-tradable cells to NaN before window ops
  · helpers for masked pct_change / rolling mean
  · ``load_feature_tradable_context`` / ``feature_mask_context`` — Phase-2 opt-in

Feature mask ≠ execution mask (paper_engine open-limit buy/sell). Do not mix.

ADR-040: trade-affecting paths default **on** (DSL / search / production backtest).
Hermetic tests may pass ``feature_mask=\"off\"`` or set env ``ASTOCK_FEATURE_MASK=off``.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd

# A-share quotes are fen-rounded; 1 fen is the natural equality tolerance.
DEFAULT_PRICE_TOL = 0.01

# Bump when limit/suspend rules for *features* change (invalidates clean caches).
FEATURE_MASK_VERSION = "feat_tradable_v1"
# Explicit dirty / no-mask token (cache paths omit suffix when version is none).
FEATURE_MASK_NONE = "none"

# (tradable_panel | None, version_str) — thread/task local for DSL opt-in.
_FEATURE_MASK_CV: contextvars.ContextVar[tuple[pd.DataFrame | None, str]] = contextvars.ContextVar(
    "feature_tradable_mask",
    default=(None, FEATURE_MASK_NONE),
)


def _align_like(
    panel: pd.DataFrame,
    index: pd.Index,
    columns: pd.Index,
) -> pd.DataFrame:
    return panel.reindex(index=index, columns=columns)


def at_limit_flags(
    raw_close: pd.DataFrame,
    up_limit: pd.DataFrame,
    down_limit: pd.DataFrame,
    *,
    price_tol: float = DEFAULT_PRICE_TOL,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Boolean panels: close within ``price_tol`` of official up/down limit.

    Missing limit or missing close → False (not flagged as at-limit).
    Compare on **raw (unadjusted)** prices; limit tables are unadjusted.
    """
    if price_tol < 0:
        raise ValueError(f"price_tol must be >= 0, got {price_tol}")

    idx = raw_close.index
    cols = raw_close.columns
    rc = raw_close.astype(float)
    up = _align_like(up_limit.astype(float), idx, cols)
    dn = _align_like(down_limit.astype(float), idx, cols)

    have_rc = rc.notna() & (rc > 0)
    have_up = up.notna() & (up > 0)
    have_dn = dn.notna() & (dn > 0)

    at_up = have_rc & have_up & ((rc - up).abs() <= price_tol)
    at_dn = have_rc & have_dn & ((rc - dn).abs() <= price_tol)
    return at_up.fillna(False), at_dn.fillna(False)


def build_feature_tradable_mask(
    raw_close: pd.DataFrame,
    up_limit: pd.DataFrame,
    down_limit: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    *,
    price_tol: float = DEFAULT_PRICE_TOL,
) -> pd.DataFrame:
    """Feature-tradable mask (date × code), True = may enter rolling windows.

    Non-tradable when any of:
      · raw_close missing or <= 0
      · within ``price_tol`` of up_limit or down_limit (when limit present)
      · volume missing or <= 0 (if ``volume`` provided)

    Missing limit quotes do **not** mark non-tradable (fail-open on incomplete
    limit coverage); only explicit near-limit or bad volume/price blocks.
    """
    rc = raw_close.astype(float)
    have_px = rc.notna() & (rc > 0)
    at_up, at_dn = at_limit_flags(rc, up_limit, down_limit, price_tol=price_tol)
    tradable = have_px & ~at_up & ~at_dn

    if volume is not None:
        vol = _align_like(volume.astype(float), rc.index, rc.columns)
        tradable = tradable & vol.notna() & (vol > 0)

    return tradable.fillna(False)


def cleanse(panel: pd.DataFrame, tradable: pd.DataFrame) -> pd.DataFrame:
    """Zero out non-tradable cells to NaN (never 0 — avoids fake zero-return days)."""
    mask = _align_like(tradable.astype(bool), panel.index, panel.columns).fillna(False)
    return panel.where(mask)


def masked_pct_change(
    close: pd.DataFrame,
    tradable: pd.DataFrame,
    *,
    periods: int = 1,
) -> pd.DataFrame:
    """pct_change on cleansed close; limit/suspend days do not create returns."""
    return cleanse(close, tradable).pct_change(periods=periods, fill_method=None)


def masked_rolling_mean(
    panel: pd.DataFrame,
    tradable: pd.DataFrame,
    window: int,
    *,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Rolling mean that ignores non-tradable observations (NaN-aware)."""
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    mp = min_periods if min_periods is not None else max(1, window // 2)
    return cleanse(panel, tradable).rolling(window, min_periods=mp).mean()


def mask_summary(tradable: pd.DataFrame) -> dict[str, float | int]:
    """Coverage stats for reports (not alpha evidence)."""
    vals = tradable.to_numpy(dtype=bool, na_value=False)
    n = int(vals.size)
    n_true = int(vals.sum()) if n else 0
    return {
        "n_cells": n,
        "n_tradable": n_true,
        "tradable_rate": float(n_true / n) if n else float("nan"),
        "n_dates": int(len(tradable.index)),
        "n_codes": int(len(tradable.columns)),
    }


def factor_momentum_clean(
    close: pd.DataFrame,
    tradable: pd.DataFrame,
    n: int,
    *,
    skip: int = 0,
) -> pd.DataFrame:
    """Momentum on cleansed prices: clean[t-skip]/clean[t-n-skip]-1."""
    c = cleanse(close, tradable)
    if skip > 0:
        return c.shift(skip) / c.shift(n + skip) - 1.0
    return c / c.shift(n) - 1.0


def factor_volatility_clean(
    close: pd.DataFrame,
    tradable: pd.DataFrame,
    n: int = 20,
    *,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Return vol on cleansed path; annualised like factors.momentum.volatility."""
    ret = masked_pct_change(close, tradable)
    mp = min_periods if min_periods is not None else max(5, n // 2)
    return ret.rolling(n, min_periods=mp).std() * np.sqrt(252.0)


def factor_price_to_ma_clean(
    close: pd.DataFrame,
    tradable: pd.DataFrame,
    n: int,
    *,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """close/MA(n)-1 with MA over cleansed closes only."""
    c = cleanse(close, tradable)
    mp = min_periods if min_periods is not None else max(1, n // 2)
    ma = c.rolling(n, min_periods=mp).mean()
    return c / ma - 1.0


def factor_illiquidity_clean(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    tradable: pd.DataFrame,
    n: int = 20,
    *,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Amihud-style rolling mean on cleansed ret/amount (matches factors.momentum.illiquidity form)."""
    c = cleanse(close, tradable)
    v = cleanse(volume.astype(float), tradable)
    ret = c.pct_change(fill_method=None).abs()
    amount = v * c
    daily = ret / (amount.replace(0, np.nan) + 1.0)
    mp = min_periods if min_periods is not None else max(5, n // 2)
    return daily.rolling(n, min_periods=mp).mean()


# ── Phase-2: load bundle + opt-in context for DSL / factor_store ─────────────


@dataclass(frozen=True)
class FeatureTradableContext:
    """Aligned price + feature-tradable panels for one research window.

    ``close`` / ``volume`` remain **uncleansed** (dirty path parity).
    ``clean_*`` are ready for rolling features. ``mask_version`` must enter
    any factor_store / DSL cache key when clean panels are used.
    """

    close: pd.DataFrame
    volume: pd.DataFrame
    amount: pd.DataFrame | None
    raw_close: pd.DataFrame
    up_limit: pd.DataFrame
    down_limit: pd.DataFrame
    tradable: pd.DataFrame
    clean_close: pd.DataFrame
    clean_volume: pd.DataFrame
    mask_version: str
    price_tol: float
    start: str
    end: str | None


def get_active_feature_mask() -> tuple[pd.DataFrame | None, str]:
    """Return (tradable_or_None, mask_version). Default = (None, none)."""
    return _FEATURE_MASK_CV.get()


def normalize_feature_mask_version(version: str | None) -> str:
    """Map empty/none/off/dirty → FEATURE_MASK_NONE; else strip and keep."""
    if version is None:
        return FEATURE_MASK_NONE
    v = str(version).strip()
    if not v or v.lower() in {FEATURE_MASK_NONE, "off", "dirty", "default"}:
        return FEATURE_MASK_NONE
    return v


@contextlib.contextmanager
def feature_mask_context(
    tradable: pd.DataFrame,
    *,
    version: str = FEATURE_MASK_VERSION,
) -> Iterator[tuple[pd.DataFrame, str]]:
    """Opt-in: AutoResearch DSL and callers see active feature-tradable mask.

    Default search remains dirty when this context is not entered.
    """
    ver = normalize_feature_mask_version(version)
    if ver == FEATURE_MASK_NONE:
        raise ValueError("feature_mask_context requires a non-none mask version")
    if not isinstance(tradable, pd.DataFrame):
        raise TypeError("tradable must be a DataFrame")
    token = _FEATURE_MASK_CV.set((tradable.astype(bool), ver))
    try:
        yield tradable, ver
    finally:
        _FEATURE_MASK_CV.reset(token)


def load_feature_tradable_context(
    start: str,
    *,
    end: str | None = None,
    codes: list[str] | None = None,
    price_tol: float = DEFAULT_PRICE_TOL,
    fields: tuple[str, ...] = ("close", "volume", "amount"),
    mask_version: str = FEATURE_MASK_VERSION,
) -> FeatureTradableContext:
    """One-shot load: prices + stk_limit + feature-tradable mask + clean panels.

    Lives in factors/ (not lake/) so mask logic stays with consumers and the
    dependency arrow remains lake ← factors (R-ARCH-001).

    ``end`` inclusive; if set, panels are truncated after load. Rolling warmup
    still uses history from ``start`` only — pass an earlier start if needed.
    """
    from lake.load_lake import load_prices, load_raw_close, load_tushare_panel

    want = tuple(fields)
    if "close" not in want or "volume" not in want:
        raise ValueError("fields must include close and volume")
    px = load_prices(codes=codes, start=start, fields=want)
    close = px["close"]
    volume = px["volume"]
    amount = px.get("amount")

    if end is not None:
        end_ts = pd.Timestamp(end)
        close = close.loc[:end_ts]
        volume = volume.loc[:end_ts]
        if amount is not None:
            amount = amount.loc[:end_ts]

    raw = load_raw_close(codes=codes, start=start)
    raw = raw.reindex(index=close.index, columns=close.columns)

    lim = load_tushare_panel(
        "stk_limit",
        close.index,
        fields=["up_limit", "down_limit"],
        codes=list(close.columns) if codes is None else codes,
    )
    up = lim["up_limit"].reindex(index=close.index, columns=close.columns)
    dn = lim["down_limit"].reindex(index=close.index, columns=close.columns)

    tradable = build_feature_tradable_mask(raw, up, dn, volume, price_tol=price_tol)
    ver = normalize_feature_mask_version(mask_version)
    if ver == FEATURE_MASK_NONE:
        ver = FEATURE_MASK_VERSION

    return FeatureTradableContext(
        close=close,
        volume=volume,
        amount=amount,
        raw_close=raw,
        up_limit=up,
        down_limit=dn,
        tradable=tradable,
        clean_close=cleanse(close, tradable),
        clean_volume=cleanse(volume, tradable),
        mask_version=ver,
        price_tol=float(price_tol),
        start=str(pd.Timestamp(start).date()),
        end=str(pd.Timestamp(end).date()) if end is not None else None,
    )


@contextlib.contextmanager
def feature_mask_session(
    start: str,
    *,
    end: str | None = None,
    codes: list[str] | None = None,
    price_tol: float = DEFAULT_PRICE_TOL,
    mask_version: str = FEATURE_MASK_VERSION,
) -> Iterator[FeatureTradableContext]:
    """Load lake panels and enter ``feature_mask_context`` for the block."""
    ctx = load_feature_tradable_context(
        start,
        end=end,
        codes=codes,
        price_tol=price_tol,
        mask_version=mask_version,
    )
    with feature_mask_context(ctx.tradable, version=ctx.mask_version):
        yield ctx


# Process-local cache: full-market stk_limit load is heavy; reuse within a run.
_AUTO_TRADABLE_CACHE: dict[tuple, tuple[pd.DataFrame, str]] = {}
VOL_FLOOR_VERSION = "vol_floor_v1"


def volume_floor_tradable(volume: pd.DataFrame) -> pd.DataFrame:
    """Minimum feature mask: volume missing/≤0 → non-tradable (停牌 floor)."""
    return volume.notna() & (volume.astype(float) > 0)


def resolve_feature_tradable_for_panel(
    close: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    *,
    prefer_lake: bool = True,
    price_tol: float = DEFAULT_PRICE_TOL,
) -> tuple[pd.DataFrame | None, str]:
    """Resolve feature-tradable for a price panel (ADR-040 default-on path).

    Order:
      1. active ``feature_mask_context``
      2. lake stk_limit + volume (cached by window/shape)
      3. volume-only floor
      4. (None, none) if nothing available
    """
    ctx_t, ctx_v = get_active_feature_mask()
    if ctx_t is not None and normalize_feature_mask_version(ctx_v) != FEATURE_MASK_NONE:
        aligned = ctx_t.reindex(index=close.index, columns=close.columns).fillna(False)
        return aligned.astype(bool), normalize_feature_mask_version(ctx_v)

    if prefer_lake and len(close.index) > 0:
        start = str(pd.Timestamp(close.index[0]).date())
        end = str(pd.Timestamp(close.index[-1]).date())
        key = (start, end, int(close.shape[0]), int(close.shape[1]), float(price_tol))
        if key in _AUTO_TRADABLE_CACHE:
            cached_t, cached_v = _AUTO_TRADABLE_CACHE[key]
            return (
                cached_t.reindex(index=close.index, columns=close.columns).fillna(False),
                cached_v,
            )
        try:
            ctx = load_feature_tradable_context(
                start,
                end=end,
                price_tol=price_tol,
            )
            tradable = ctx.tradable.reindex(index=close.index, columns=close.columns).fillna(False)
            # Synthetic/foreign code grids reindex to all-False — fall through to volume floor.
            if float(tradable.to_numpy(dtype=bool).mean()) < 1e-6:
                raise ValueError("feature tradable has no overlap with panel codes")
            if volume is not None:
                tradable = tradable & volume_floor_tradable(
                    volume.reindex(index=close.index, columns=close.columns)
                )
            _AUTO_TRADABLE_CACHE[key] = (tradable, ctx.mask_version)
            return tradable, ctx.mask_version
        except Exception:
            pass

    if volume is not None:
        t = volume_floor_tradable(volume.reindex(index=close.index, columns=close.columns))
        return t.astype(bool), VOL_FLOOR_VERSION
    return None, FEATURE_MASK_NONE


def price_panel_kwargs_from_context(ctx: FeatureTradableContext) -> dict:
    """Build kwargs for ``core.engine.PricePanel`` from a feature context."""
    amount = ctx.amount
    if amount is None:
        amount = ctx.volume.astype(float) * ctx.raw_close.astype(float)
    return {
        "close": ctx.close,
        "volume": ctx.volume,
        "amount": amount,
        "raw_close": ctx.raw_close,
        "up_limit": ctx.up_limit,
        "down_limit": ctx.down_limit,
        "feature_tradable": ctx.tradable,
    }
