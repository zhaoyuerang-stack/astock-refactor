"""Factor expression system — lazy computation graph nodes.

Factor = recipe, not data.  Call .compute(data) to execute.
Transforms return new Factor nodes (chainable).  Expression operators
(+, -, *, /) produce FactorBlend nodes (deferred import).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

# ---------------------------------------------------------------------------
# FactorData — input container passed to compute()
# ---------------------------------------------------------------------------

@dataclass
class FactorData:
    """Unified input for factor computation.

    All DataFrames are (date × code) wide-format with DatetimeIndex.
    """
    close: pd.DataFrame
    volume: pd.DataFrame
    amount: pd.DataFrame          # volume × raw_close, share×CNY (canonical; 禁 ×100 手数换算)
    raw_close: pd.DataFrame | None = None
    industry: pd.DataFrame | None = None      # industry code (string/int)
    market_cap: pd.DataFrame | None = None     # total market cap (CNY)
    # Feature-tradable mask (Phase-2 opt-in). Not execution/open-limit mask.
    tradable: pd.DataFrame | None = None

    @property
    def trade_dates(self) -> pd.DatetimeIndex:
        return self.close.index

    @property
    def codes(self) -> pd.Index:
        return self.close.columns


# ---------------------------------------------------------------------------
# Filter — boolean mask
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Filter:
    """A lazy boolean mask produced by factor comparisons."""
    factor: Factor
    op: str          # '>', '<', '>=', '<=', '==', '!='
    threshold: float

    def compute(self, data: FactorData) -> pd.DataFrame:
        values = self.factor.compute(data)
        if self.op == '>':
            return values > self.threshold
        elif self.op == '<':
            return values < self.threshold
        elif self.op == '>=':
            return values >= self.threshold
        elif self.op == '<=':
            return values <= self.threshold
        elif self.op == '==':
            return values == self.threshold
        elif self.op == '!=':
            return values != self.threshold
        raise ValueError(f"Unknown op: {self.op}")


# ---------------------------------------------------------------------------
# Factor — lazy computation node
# ---------------------------------------------------------------------------

class Factor(ABC):
    """A lazy recipe for computing (date × code) factor values.

    Subclasses override ``compute(data)``.  Transforms (.zscore(),
    .mad_clip(), etc.) return TransformedFactor nodes that wrap the
    chain.  Expression operators (+, -, *, /) return FactorBlend nodes.

    Factors are hashable (by identity) so they can be used as dict keys
    in FactorBlend.
    """

    def __hash__(self):
        return id(self)

    # 注:__eq__ 在本类下方定义为 DSL 表达式构造(factor == 阈值 → Filter),
    # 不是身份比较;身份语义由 __hash__ = id(self) 承载,dict/set 按身份工作。

    # -- subclasses override this --
    @abstractmethod
    def compute(self, data: FactorData) -> pd.DataFrame:
        """Return factor values as (date × code) DataFrame."""

    # -- transforms (return TransformedFactor) --
    def zscore(self) -> Factor:
        return TransformedFactor(self, [("zscore", (), {})])

    def mad_clip(self, n: float = 5.0) -> Factor:
        return TransformedFactor(self, [("mad_clip", (n,), {})])

    def rank(self, ascending: bool = True) -> Factor:
        return TransformedFactor(self, [("rank", (ascending,), {})])

    def shift(self, periods: int = 1) -> Factor:
        return TransformedFactor(self, [("shift", (periods,), {})])

    def neutralize(self, groups: pd.DataFrame) -> Factor:
        return TransformedFactor(self, [("neutralize", (groups,), {})])

    def rolling_mean(self, window: int) -> Factor:
        return TransformedFactor(self, [("rolling_mean", (window,), {})])

    def rolling_std(self, window: int) -> Factor:
        return TransformedFactor(self, [("rolling_std", (window,), {})])

    def log1p(self) -> Factor:
        return TransformedFactor(self, [("log1p", (), {})])

    def neg(self) -> Factor:
        return TransformedFactor(self, [("neg", (), {})])

    # -- expression algebra (deferred import to avoid circular deps) --
    def __add__(self, other: Factor) -> Factor:
        from factors.blend import FactorBlend
        return FactorBlend({self: 1.0, other: 1.0})

    def __sub__(self, other: Factor) -> Factor:
        from factors.blend import FactorBlend
        return FactorBlend({self: 1.0, other: -1.0})

    def __mul__(self, weight: float) -> Factor:
        if not isinstance(weight, (int, float)):
            return NotImplemented
        from factors.blend import FactorBlend
        return FactorBlend({self: float(weight)})

    def __rmul__(self, weight: float) -> Factor:
        return self.__mul__(weight)

    def __truediv__(self, other: Factor) -> Factor:
        from factors.blend import FactorBlend
        return FactorBlend({self: 1.0, other: -1.0})  # ratio proxy: a / b ≈ a - b in z-score space

    def __neg__(self) -> Factor:
        return self.neg()

    # -- comparisons (return Filter) --
    def __gt__(self, threshold: float) -> Filter:
        return Filter(self, '>', float(threshold))

    def __lt__(self, threshold: float) -> Filter:
        return Filter(self, '<', float(threshold))

    def __ge__(self, threshold: float) -> Filter:
        return Filter(self, '>=', float(threshold))

    def __le__(self, threshold: float) -> Filter:
        return Filter(self, '<=', float(threshold))

    def __eq__(self, other) -> Filter:
        return Filter(self, '==', float(other))

    def __ne__(self, other) -> Filter:
        return Filter(self, '!=', float(other))


# ---------------------------------------------------------------------------
# TransformedFactor — chain of lazy transforms
# ---------------------------------------------------------------------------

# Registry of transform functions (populated by factors.transforms)
_TRANSFORM_FUNCTIONS: dict[str, callable] = {}


def register_transform(name: str, fn=None):
    """Register a transform function so TransformedFactor can look it up.

    Can be used as ``@register_transform(\"name\")`` decorator or
    ``register_transform(\"name\", fn)`` directly.
    """
    if fn is not None:
        _TRANSFORM_FUNCTIONS[name] = fn
        return fn
    def _decorator(fn):
        _TRANSFORM_FUNCTIONS[name] = fn
        return fn
    return _decorator


@dataclass(eq=False)
class TransformedFactor(Factor):
    """A Factor with a chain of transforms applied lazily."""

    _parent: Factor
    _ops: list  # list of (name, args, kwargs)

    # Use identity-based hashing like Factor (list field breaks default hash)
    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def compute(self, data: FactorData) -> pd.DataFrame:
        result = self._parent.compute(data)
        for name, args, kwargs in self._ops:
            fn = _TRANSFORM_FUNCTIONS.get(name)
            if fn is None:
                raise ValueError(f"Unknown transform: {name!r}")
            result = fn(result, *args, **kwargs)
        return result

    def zscore(self) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("zscore", (), {})])

    def mad_clip(self, n: float = 5.0) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("mad_clip", (n,), {})])

    def rank(self, ascending: bool = True) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("rank", (ascending,), {})])

    def shift(self, periods: int = 1) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("shift", (periods,), {})])

    def neutralize(self, groups: pd.DataFrame) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("neutralize", (groups,), {})])

    def rolling_mean(self, window: int) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("rolling_mean", (window,), {})])

    def rolling_std(self, window: int) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("rolling_std", (window,), {})])

    def log1p(self) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("log1p", (), {})])

    def neg(self) -> Factor:
        return TransformedFactor(self._parent, self._ops + [("neg", (), {})])
