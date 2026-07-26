"""Classic Alpha101 factor implementations for A-shares.

All functions accept:
  close: pd.DataFrame
  volume: pd.DataFrame | None
and return:
  pd.DataFrame of factor values.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --- Shared Operators ---
def R(x): return x.rank(axis=1, pct=True)
def M(x, d): return x.rolling(d).mean()
def S(x, d): return x.rolling(d).std()
def T(x, d): return x.rolling(d).sum()
def O(x, d): return x.shift(d)
def Delta(x, d): return x - O(x, d)
def C(x, y, d): return x.rolling(d).corr(y)
def Sgn(x): return np.sign(x)
def Abs(x): return abs(x)
def Z(x): return x.sub(x.mean(1), 0).div(x.std(1).replace(0, 1), 0)

# --- Helper to get returns ---
def _returns(close: pd.DataFrame) -> pd.DataFrame:
    return close.pct_change(fill_method=None).fillna(0.0)

# --- 32 Screened Alpha101 Factors ---

def alpha_001(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return R(S(r.where(r < 0, close), 20)) - 0.5

def alpha_002(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_002 requires volume")
    r = _returns(close)
    return -C(R(Delta(np.log(volume + 1), 2)), R(r), 6)

def alpha_003(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_003 requires volume")
    return -C(R(close), R(volume), 10)

def alpha_005(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    # DEGENERATE (transplant residue): close-close≡0 → constant rank multiplier;
    # collapses to price_to_ma-like info. Kept for audit; **not** in ALLOWED_FACTORS/DSL.
    return R(close - M(close, 10)) * (-Abs(R(close - close)))

def alpha_006(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_006 requires volume")
    return -C(close, volume, 10)

def alpha_008(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return -R(Delta(T(close, 5) * T(r, 5), 10))

def alpha_009(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    diff = Delta(close, 1)
    return diff.where(diff > 0, -diff)

def alpha_012(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_012 requires volume")
    return Sgn(Delta(volume, 1)) * (-Delta(close, 1))

def alpha_013(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_013 requires volume")
    return -R(C(R(close), R(volume), 5))

def alpha_014(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_014 requires volume")
    r = _returns(close)
    return -R(Delta(r, 3)) * C(close, volume, 10)

def alpha_015(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_015 requires volume")
    return -T(R(C(R(close), R(volume), 3)), 3)

def alpha_017(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return -R(S(r, 10)) * Delta(close, 1)

def alpha_018(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return -R(S(Abs(r), 20)) * Sgn(r)

def alpha_019(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return -Sgn(Delta(close, 7)) * (1.0 + R(1.0 + T(r, 250)))

def alpha_020(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    diff = R(close - O(close, 1))
    return -diff * diff * diff

def alpha_021(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    return M(close, 5) - M(close, 20)

def alpha_022(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    return (C(close, O(close, 1), 5) - 0.5) * R(Delta(close, 1))

def alpha_023(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    m5 = M(close, 5)
    return m5.where(m5 >= M(close, 20), -1.0)

def alpha_024(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    # Near-duplicate of alpha_009 short-return cluster (|rank-corr|≈1); not searchable.
    return -Delta(close, 1)

def alpha_025(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    return -S(r, 20) * R(close)

def alpha_028(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_028 requires volume")
    max_c = close.rolling(20).max()
    return Z(C(M(volume, 20), close, 5) + (R(close) - close / (max_c + 1e-8)))

def alpha_030(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_030 requires volume")
    r = _returns(close)
    return R(r) + R(volume / (M(volume, 20) + 1.0))

def alpha_032(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    return Z(M(close, 7) - close) + 20.0 * Z(C(close, O(close, 5), 230))

def alpha_033(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    return R(-(1.0 - close / (O(close, 1) + 1e-8)))

def alpha_034(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    ratio = S(r, 2) / (S(r, 5) + 1e-6)
    return R(1.0 - R(ratio)) + R(1.0 - R(Delta(close, 1)))

def alpha_037(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    r = _returns(close)
    ret_c = -r * close
    return R(C(O(ret_c, 1), close, 200)) + R(ret_c)

def alpha_038(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    return -R(close - O(close, 9)) * R(close / (O(close, 1) + 1e-8))

def alpha_040(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_040 requires volume")
    return -R(S(close, 10)) * C(close, volume, 10)

def alpha_044(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_044 requires volume")
    return -C(close, R(volume), 5)

def alpha_049(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    # Exact twin of alpha_024; not searchable (n_trials honesty).
    return -Delta(close, 1)

def alpha_050(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_050 requires volume")
    return -R(C(R(volume), R(close), 5)).rolling(5).max()

def alpha_055(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> pd.DataFrame:
    if volume is None:
        raise ValueError("alpha_055 requires volume")
    low_12 = close.rolling(12).min()
    high_12 = close.rolling(12).max()
    stoch = (close - low_12) / (high_12 - low_12 + 1e-6)
    return -C(R(stoch), R(volume), 6)
