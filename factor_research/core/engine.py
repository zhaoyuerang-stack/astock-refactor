"""Unified backtest engine — single entry-point for all backtest & factor analysis.

Design goals
------------
1. One ``BacktestEngine`` class handles portfolio-weight backtests, IC analysis,
   and stratified return tests.
2. ``Signal`` is the canonical input (weights, factor, or factor_builder).
3. ``BacktestResult`` is the canonical output (returns, costs, metrics).
4. ``PricePanel`` replaces the ad-hoc (close, volume, amount, raw_close) tuples.
5. Existing APIs in ``core/backtest.py`` and ``engine/backtest.py`` are preserved
   as thin compatibility wrappers (see Phase-2 migration).

Usage
-----
>>> from core.engine import BacktestEngine, Signal, BacktestConfig, PricePanel
>>> engine = BacktestEngine(prices=price_panel, config=BacktestConfig())
>>> result = engine.run(Signal(weights=scheduled_weights, timing=timing_signal))
>>> print(result.metrics)
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from engine.metrics import (
    TARGET_ANNUAL,
    TARGET_MAXDD,
    annual_return,
    annual_vol,
    calmar_ratio,
    compute_hit,
    max_drawdown,
    sharpe_ratio,
)
from engine.metrics import metrics as canonical_metrics

# ---------------------------------------------------------------------------
# Config & Data Containers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostModel:
    """Execution cost assumptions (matches ``core/backtest.CostModel``)."""
    buy_cost: float = 0.00225
    sell_cost: float = 0.00275
    financing_rate: float = 0.065


@dataclass(frozen=True)
class BacktestConfig:
    """Global backtest parameters.

    ``start`` is the **statistics window** start: simulation runs over the full
    price panel (preserving factor warmup and holding continuity), then the
    returns/turnover/cost series are truncated to ``>= start`` before metrics.
    Historically this field was dead (full-panel stats regardless of start),
    which silently diluted OOS metrics with idle pre-start days.

    ``enforce_fill_tradable`` (ADR-040, default True): on each fill date, block
    weight increases when the name is not buyable (volume≤0 or close at up-limit)
    and block weight decreases when not sellable (volume≤0 or close at down-limit).
    Aligns formal backtest with paper/live non-fill reality under T+1 close.
    """
    start: str = "2018-01-01"
    cost: CostModel = field(default_factory=CostModel)
    leverage: float = 1.25
    # Evaluation thresholds — single-strategy entry bar (年化>15% / 回撤<20%)
    # Original 35%/15% was anchored to data_full survivorship bias, retired.
    target_annual: float = 0.15
    target_maxdd: float = 0.20
    enforce_fill_tradable: bool = True


@dataclass(frozen=True)
class PricePanel:
    """Unified price / volume panel.

    Replaces the ad-hoc ``(close, volume, amount, raw_close)`` tuples that
    used to be passed around between modules.
    """
    close: pd.DataFrame          # adjusted close (for return calculation)
    volume: pd.DataFrame         # shares (canonical data-lake unit; not 手)
    amount: pd.DataFrame         # turnover in CNY  (volume × raw_close; see lake.units.implied_amount)
    raw_close: pd.DataFrame | None = None  # unadjusted close (for valuation / order sizing)
    # Official limit prices (unadjusted), optional; used when enforce_fill_tradable.
    up_limit: pd.DataFrame | None = None
    down_limit: pd.DataFrame | None = None
    # Feature-tradable mask (limit+suspend); not the same as fill open-limit mask.
    feature_tradable: pd.DataFrame | None = None
    # 注:曾有 raw_open 字段(暗示开盘成交)但引擎从不读取 → 已删,避免误导。执行契约 = T+1 收盘
    # (见下方 Signal.execution_timing);若要 T+1 开盘/VWAP 成交须新增 T_PLUS_1_OPEN 分支 + 立 ADR。


# ---------------------------------------------------------------------------
# Signal — canonical strategy input
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """Standardised strategy signal.

    Three mutually-preferred modes (checked in order):
    1. **weights** – pre-computed target weights (date × code DataFrame).
    2. **factor**  – raw factor values; engine converts to top-n weights internally.
    3. **factor_builder** – callable ``(prices, config) -> factor DataFrame``.
    """
    # Mode A: decision-date target weights. The engine applies execution_timing.
    decision_weights: pd.DataFrame | None = None

    # Legacy alias. New code must use decision_weights.
    weights: pd.DataFrame | None = None

    # Mode B: factor → top-n weights
    factor: pd.DataFrame | None = None
    top_n: int = 25
    direction: int = 1                      # 1 = long top, -1 = long bottom
    rebalance_freq: str = "20D"             # pandas offset alias

    # Mode C: lazy factor builder (used by factory)
    factor_builder: Callable[[PricePanel, dict | None], pd.DataFrame] | None = None
    factor_config: dict | None = None

    # Timing exposure (daily multiplier). Default cap 1.0 (binary).
    # 2026-06-07: Boost band 需要 > 1.0；调 Signal.exposure_cap 解除。
    timing: pd.Series | None = None
    exposure_cap: float = 1.0   # boost timing 需要传 1.5 等

    # Metadata
    family: str = ""
    version: str = ""
    # 官方执行契约(R-BT-001 唯一权威;同步 SPEC.md §统一回测内核):成交口径 = **T+1 收盘**。
    # 决策日 T 的权重在下一交易日 T+1 收盘建仓、从其再次日起赚(无当日成交、无未来函数);
    # `_map_decisions_to_fill_dates` 对任何非 "T_PLUS_1_CLOSE" 值硬报错。
    # 注:此口径比"清单理想 T+1 开盘/VWAP"晚一档——实盘冲击/滑点建模须按收盘口径。
    execution_timing: str = "T_PLUS_1_CLOSE"

    def _resolve_weights(self, prices: PricePanel) -> pd.DataFrame:
        """Convert factor / factor_builder to target weights."""
        if self.decision_weights is not None:
            return self.decision_weights
        if self.weights is not None:
            return self.weights
        factor = self.factor
        if factor is None and self.factor_builder is not None:
            factor = self.factor_builder(prices, self.factor_config)
        if factor is None:
            raise ValueError("Signal must provide weights, factor, or factor_builder")
        return _factor_to_weights(
            factor,
            top_n=self.top_n,
            direction=self.direction,
            rebalance_freq=self.rebalance_freq,
            close=prices.close,
        )


def _map_decisions_to_fill_dates(
    decisions: pd.DataFrame,
    trading_dates: pd.DatetimeIndex,
    execution_timing: str,
) -> pd.DataFrame:
    if execution_timing != "T_PLUS_1_CLOSE":
        raise ValueError(f"unsupported execution_timing={execution_timing!r}")
    rows = []
    for decision_date, row in decisions.sort_index().iterrows():
        pos = trading_dates.searchsorted(pd.Timestamp(decision_date), side="right")
        if pos >= len(trading_dates):
            continue
        rows.append((trading_dates[pos], row))
    if not rows:
        return pd.DataFrame(index=pd.DatetimeIndex([], dtype="datetime64[ns]"))
    out = pd.DataFrame(
        [row for _, row in rows],
        index=pd.DatetimeIndex([date for date, _ in rows]),
    )
    return out.groupby(level=0).last()


def _map_timing_to_fill_dates(
    timing: pd.Series,
    trading_dates: pd.DatetimeIndex,
    execution_timing: str,
) -> pd.Series:
    frame = _map_decisions_to_fill_dates(
        pd.Series(timing).to_frame("exposure"),
        trading_dates,
        execution_timing,
    )
    return frame["exposure"] if "exposure" in frame else pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# BacktestResult — canonical output
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Unified backtest result.

    Replaces the scattered dicts returned by ``core/backtest.py``,
    ``engine/portfolio.py``, and ``factory/objectives.py``.
    """
    # Core series
    returns: pd.Series
    turnover: pd.Series
    cost: pd.Series

    # Derived
    detail: pd.DataFrame = field(init=False)
    weights_history: pd.DataFrame | None = None

    # IC / stratify (populated by optional analysis calls)
    ic_series: pd.Series | None = None
    ic_summary: dict | None = None
    stratify_ret: pd.DataFrame | None = None

    # Metadata
    family: str = ""
    version: str = ""
    config: BacktestConfig | None = None

    def __post_init__(self):
        self.detail = pd.DataFrame(
            {"ret": self.returns, "turnover": self.turnover, "cost": self.cost},
            index=self.returns.index,
        )

    # ---- metrics(全部委托 engine.metrics 的 canonical 标量公式,禁止在此内联重写) ----

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def annual(self) -> float:
        return annual_return(self.returns)

    @property
    def vol(self) -> float:
        return annual_vol(self.returns)

    @property
    def sharpe(self) -> float:
        return sharpe_ratio(self.returns)

    @property
    def maxdd(self) -> float:
        return max_drawdown(self.returns)

    @property
    def calmar(self) -> float:
        return calmar_ratio(self.returns)

    @property
    def anomalies(self) -> list:
        """结果哨兵:统计上近乎不可能的结果形态,优先怀疑数据而非行情。

        阈值取"分散组合物理上限之外":|组合日收益|>35%(20cm 全跌停 × 1.5 杠杆
        = 30% 才到边界;2026-06 假崩盘日为 -60% 量级)或回撤 >90%。
        只报警不改数——哨兵触发时先查数据(末几日截面分布),再信结论。
        """
        flags = []
        if len(self.returns):
            worst = self.returns.abs().max()
            if worst > 0.35:
                d = self.returns.abs().idxmax()
                flags.append(f"组合单日|r|={worst:.1%} @ {pd.Timestamp(d).date()} 超物理边界,疑数据问题")
        if self.maxdd < -0.90:
            flags.append(f"回撤 {self.maxdd:.1%} 近乎清零,疑数据问题")
        return flags

    @property
    def hit(self) -> bool:
        # 唯一权威判定走 engine.metrics.compute_hit（严格不等号），阈值可被 config 覆盖。
        # NOTE: @property cannot accept extra arguments — thresholds come from self.config.
        t_annual = self.config.target_annual if self.config else TARGET_ANNUAL
        t_maxdd = self.config.target_maxdd if self.config else TARGET_MAXDD
        return compute_hit(self.annual, self.maxdd, t_annual, t_maxdd)

    @property
    def metrics(self) -> dict:
        """委托 ``engine.metrics.metrics()``(单一实现,含 n<100 哨兵与机构级指标)。

        唯一差异:``hit`` 在 n≥100 时用 ``self.hit`` 覆盖——阈值可被本次回测
        config 覆盖,而 canonical ``metrics()`` 只认默认门槛;n<100 哨兵 hit=False 不动。
        """
        m = canonical_metrics(self.returns)
        if self.n >= 100:
            m["hit"] = self.hit
        return m

    @property
    def yearly_returns(self) -> pd.Series:
        return self.returns.groupby(self.returns.index.year).apply(
            lambda x: (1 + x).prod() - 1
        )

    # ---- convenience ----

    def summary(self) -> dict:
        """Human-friendly summary dict (for logging / JSON)."""
        m = self.metrics
        return {
            "annual": f"{m['annual']:+.2%}",
            "maxdd": f"{m['maxdd']:.2%}",
            "sharpe": f"{m['sharpe']:.2f}",
            "calmar": f"{m['calmar']:.2f}",
            "n": self.n,
        }


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """Single entry-point for portfolio backtests and factor analysis."""

    def __init__(self, prices: PricePanel, config: BacktestConfig = None):
        self.prices = prices
        self.config = config or BacktestConfig()

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def run(self, signal: Signal) -> BacktestResult:
        """Run a portfolio-weight backtest from a ``Signal``."""
        weights = signal._resolve_weights(self.prices)
        return self._run_weight_backtest(weights, signal.timing, signal)

    # ------------------------------------------------------------------
    # Factor analysis (ported from engine/backtest.py)
    # ------------------------------------------------------------------

    def run_ic_analysis(
        self,
        factor: pd.DataFrame,
        forward_days: int = 1,
        method: str = "rank",
    ) -> dict:
        """Compute IC time-series and summary (identical to ``engine/backtest``)."""
        close = self.prices.close
        forward_ret = close.pct_change(forward_days).shift(-forward_days)
        ic = _calc_ic(factor, forward_ret, method=method)
        return {
            "ic_series": ic,
            "ic_summary": _ic_summary(ic),
        }

    def run_stratify(
        self,
        factor: pd.DataFrame,
        forward_days: int = 1,
        n_quantile: int = 5,
    ) -> pd.DataFrame:
        """Stratified return test (identical to ``engine/backtest``)."""
        close = self.prices.close
        forward_ret = close.pct_change(forward_days).shift(-forward_days)
        return _stratify_return(factor, forward_ret, n_quantile=n_quantile)

    # ------------------------------------------------------------------
    # Internal: portfolio-weight backtest (ported from core/backtest.py)
    # ------------------------------------------------------------------

    def _run_weight_backtest(
        self,
        scheduled_weights: dict | pd.DataFrame,
        timing_signal: pd.Series | None = None,
        signal_meta: Signal | None = None,
    ) -> BacktestResult:
        """Daily vector backtest with turnover, timing, leverage and financing.

        Logic is a direct port of ``core/backtest.backtest_weights()`` so that
        numerical results are bit-for-bit identical.
        """
        close = self.prices.close
        config = self.config

        # Normalise dict-of-Series to DataFrame if necessary
        if isinstance(scheduled_weights, dict):
            scheduled_weights = _dict_weights_to_df(scheduled_weights, close.index)
        execution_timing = getattr(signal_meta, "execution_timing", "T_PLUS_1_CLOSE")
        scheduled_weights = _map_decisions_to_fill_dates(
            scheduled_weights,
            close.index,
            execution_timing,
        )
        fill_timing = (
            _map_timing_to_fill_dates(timing_signal, close.index, execution_timing)
            if timing_signal is not None
            else None
        )
        fill_timing_daily = (
            fill_timing.reindex(close.index).ffill().fillna(0.0)
            if fill_timing is not None
            else None
        )

        daily_ret = (
            close.pct_change(fill_method=None)
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        dates = list(close.index)
        cols = list(close.columns)
        col_idx = {c: i for i, c in enumerate(cols)}

        current_selected = pd.Series(dtype=float)
        current_weight = np.zeros(len(cols))
        out = np.full(len(dates), np.nan)
        turnover = np.zeros(len(dates))
        cost_paid = np.zeros(len(dates))

        for i, dt in enumerate(dates):
            day_ret = np.array(daily_ret.iloc[i].values, dtype="float64", copy=True)
            day_ret[~np.isfinite(day_ret)] = 0.0
            day_ret = np.clip(day_ret, -1.0, 10.0)
            held = current_weight != 0
            gross_ret = (
                float((day_ret[held] * current_weight[held]).sum()) * config.leverage
                if i > 0
                else 0.0
            )
            financing = 0.0
            if current_weight.sum() > 0 and config.leverage > 1:
                financing = (config.leverage - 1.0) * config.cost.financing_rate / 252.0

            if dt in scheduled_weights.index:
                selected = scheduled_weights.loc[dt]
                if isinstance(selected, pd.DataFrame):
                    selected = selected.iloc[-1]
                current_selected = selected.dropna()

            # Target exposure executes at today's close and earns from next close.
            exposure = 1.0
            if fill_timing_daily is not None:
                exposure = float(fill_timing_daily.iloc[i])
                if not np.isfinite(exposure):
                    exposure = 0.0
                exp_cap = signal_meta.exposure_cap if signal_meta is not None else 1.0
                exposure = min(max(exposure, 0.0), exp_cap)

            target_weight = np.zeros(len(cols))
            if exposure > 0 and len(current_selected):
                for code, weight in current_selected.items():
                    j = col_idx.get(code)
                    if j is not None:
                        target_weight[j] = weight * exposure

            # ADR-040: do not pretend limit-up buys / limit-down sells / suspended
            # names fill at T+1 close. Failed legs keep prior weight (cash gap OK).
            if config.enforce_fill_tradable:
                buyable, sellable = _fill_tradable_masks(self.prices, dt, cols)
                for j in range(len(cols)):
                    if target_weight[j] > current_weight[j] and not buyable[j]:
                        target_weight[j] = current_weight[j]
                    elif target_weight[j] < current_weight[j] and not sellable[j]:
                        target_weight[j] = current_weight[j]

            delta = target_weight - current_weight
            buy_turnover = float(delta[delta > 0].sum())
            sell_turnover = float((-delta[delta < 0]).sum())
            trade_cost = (
                buy_turnover * config.cost.buy_cost
                + sell_turnover * config.cost.sell_cost
            ) * config.leverage

            out[i] = np.nan if i == 0 else gross_ret - trade_cost - financing
            turnover[i] = buy_turnover + sell_turnover
            cost_paid[i] = trade_cost + financing
            current_weight = target_weight

        ret = pd.Series(out, index=dates).dropna()
        to = pd.Series(turnover[1:], index=ret.index)
        co = pd.Series(cost_paid[1:], index=ret.index)

        # start = 统计窗口起点:全面板连续模拟(保留预热/持仓连续性)后切片统计,
        # 否则把 start 前的空仓/预热期算进年化会稀释指标(LESSONS 2026-06-12)
        if config.start:
            stats_from = pd.Timestamp(config.start)
            ret = ret.loc[stats_from:]
            to = to.loc[stats_from:]
            co = co.loc[stats_from:]

        result = BacktestResult(
            returns=ret,
            turnover=to,
            cost=co,
            family=getattr(signal_meta, "family", ""),
            version=getattr(signal_meta, "version", ""),
            config=config,
        )
        for msg in result.anomalies:
            logger.warning(f"🚨 [结果哨兵] {msg} —— 采信前先检查数据湖末几日截面分布")
        return result


# ---------------------------------------------------------------------------
# Internal helpers (ported from existing modules)
# ---------------------------------------------------------------------------

_FILL_PRICE_TOL = 0.01  # fen; same as factors.tradable_mask.DEFAULT_PRICE_TOL


def _fill_tradable_masks(
    prices: PricePanel,
    dt: pd.Timestamp,
    cols: list,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-name buyable/sellable on fill date under T+1 close (ADR-040).

    buyable  = volume>0 and not at up-limit (when limit panels present)
    sellable = volume>0 and not at down-limit
    Missing limit panels → volume-only floor (still blocks 停牌).
    """
    n = len(cols)
    buyable = np.ones(n, dtype=bool)
    sellable = np.ones(n, dtype=bool)

    vol = prices.volume
    if dt in vol.index:
        v = vol.loc[dt].reindex(cols)
        vol_ok = v.notna().to_numpy() & (v.to_numpy(dtype=float) > 0)
    else:
        vol_ok = np.zeros(n, dtype=bool)
    buyable &= vol_ok
    sellable &= vol_ok

    raw = prices.raw_close if prices.raw_close is not None else prices.close
    up = prices.up_limit
    dn = prices.down_limit
    if up is None or dn is None or dt not in raw.index:
        return buyable, sellable
    if dt not in up.index or dt not in dn.index:
        return buyable, sellable

    rc = raw.loc[dt].reindex(cols).to_numpy(dtype=float)
    u = up.loc[dt].reindex(cols).to_numpy(dtype=float)
    d = dn.loc[dt].reindex(cols).to_numpy(dtype=float)
    have_rc = np.isfinite(rc) & (rc > 0)
    have_u = np.isfinite(u) & (u > 0)
    have_d = np.isfinite(d) & (d > 0)
    at_up = have_rc & have_u & (np.abs(rc - u) <= _FILL_PRICE_TOL)
    at_dn = have_rc & have_d & (np.abs(rc - d) <= _FILL_PRICE_TOL)
    buyable &= ~at_up
    sellable &= ~at_dn
    return buyable, sellable


def _factor_to_weights(
    factor: pd.DataFrame,
    top_n: int = 25,
    direction: int = 1,
    rebalance_freq: str = "20D",
    close: pd.DataFrame = None,
) -> pd.DataFrame:
    """Convert factor panel to scheduled target weights.

    Logic matches ``strategies.small_cap.build_rebalance_weights``:
    - rebalance every *rebalance_days* (parsed from ``rebalance_freq``)
    - index is the decision date; engine applies T+1 execution
    - top_n equal weights

    Parameters
    ----------
    close : pd.DataFrame, optional
        Price panel used to find the next-trading-day effective date.
        If omitted, effective date = the rebalance date itself.
    """
    if direction == -1:
        factor = -factor

    # Parse rebalance frequency (e.g. "20D" -> 20)
    try:
        rebalance_days = int(rebalance_freq.replace("D", ""))
    except ValueError:
        rebalance_days = 20  # fallback

    fdates = factor.dropna(how="all").index
    if close is not None:
        fdates = fdates.intersection(close.index)
    if len(fdates) < 100:
        return pd.DataFrame(index=pd.DatetimeIndex([], dtype="datetime64[ns]"))

    rows = []
    for rd in list(fdates[::rebalance_days]):
        if close is not None:
            active = close.loc[rd].dropna().index
        else:
            active = factor.columns

        f = factor.loc[rd].dropna()
        f = f.reindex(active).dropna()
        if len(f) < top_n:
            if len(f) == 0:
                w = pd.Series(0.0, index=[active[0]], name=rd)
                rows.append(w)
            continue
        if f.std() <= 1e-8:
            w = pd.Series(0.0, index=[active[0]], name=rd)
            rows.append(w)
            continue
        top = f.nlargest(top_n).index
        w = pd.Series(1.0 / top_n, index=top, name=rd)
        rows.append(w)

    if not rows:
        return pd.DataFrame(index=pd.DatetimeIndex([], dtype="datetime64[ns]"))

    weight_df = pd.DataFrame(rows).fillna(0)
    weight_df.index = pd.DatetimeIndex(weight_df.index)
    return weight_df


def _dict_weights_to_df(
    weights_dict: dict,
    all_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Convert ``{date: Series}`` (legacy core/backtest format) to DataFrame."""
    if not weights_dict:
        return pd.DataFrame(index=all_dates)
    rows = []
    for dt, s in sorted(weights_dict.items()):
        s = s.copy()
        s.name = dt
        rows.append(s)
    df = pd.DataFrame(rows).fillna(0)
    df.index = pd.DatetimeIndex(df.index)
    return df


# ---- IC analysis helpers (ported from engine/backtest.py) ----

def _calc_ic(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    method: str = "rank",
) -> pd.Series:
    dates = factor.index.intersection(forward_ret.index)
    ics = {}
    for dt in dates:
        f = factor.loc[dt].dropna()
        r = forward_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            continue
        fv, rv = f[common].values, r[common].values
        if method == "rank":
            ic, _ = spearmanr(fv, rv)
        else:
            ic = np.corrcoef(fv, rv)[0, 1]
        ics[dt] = ic
    return pd.Series(ics).sort_index()


def _ic_summary(ic: pd.Series) -> dict:
    return {
        "IC_mean": ic.mean(),
        "IC_std": ic.std(),
        "ICIR": ic.mean() / ic.std() if ic.std() > 0 else np.nan,
        "IC>0_ratio": (ic > 0).mean(),
        "|IC|>0.02_ratio": (ic.abs() > 0.02).mean(),
        "count": len(ic),
    }


# ---- Stratify helpers (ported from engine/backtest.py) ----

def _stratify_return(
    factor: pd.DataFrame,
    forward_ret: pd.DataFrame,
    n_quantile: int = 5,
) -> pd.DataFrame:
    dates = factor.index.intersection(forward_ret.index)
    records = []
    for dt in dates:
        f = factor.loc[dt].dropna()
        r = forward_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < n_quantile * 5:
            continue
        labels = pd.qcut(f[common], n_quantile, labels=False, duplicates="drop")
        group_ret = r[common].groupby(labels).mean()
        row = {"date": dt}
        for g, v in group_ret.items():
            row[f"Q{int(g)+1}"] = v
        records.append(row)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).set_index("date").sort_index()
    return df
