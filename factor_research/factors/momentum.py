"""
动量类因子
"""
import numpy as np
import pandas as pd

from factors.registry import register_factor


@register_factor(
    "momentum",
    definition=(
        "N 日价格动量 = close/close.shift(n)-1(可选 skip 近端规避短反转);"
        "正=近窗涨幅大"
    ),
    params={"window": (3, 252)},
    data=("price/close",),
    input="close",
    arg_map={"window": "n"},
    searchable=True,
    evidence=(
        "knowledge/direction_registry:momentum-fullmarket-standalone-null"
    ),
)
def mom_n(close: pd.DataFrame, n: int, skip: int = 0) -> pd.DataFrame:
    """N日动量，可跳过最近skip日（规避短反转）"""
    if skip > 0:
        return close.shift(skip) / close.shift(n + skip) - 1
    return close / close.shift(n) - 1


def turnover_mean(amount: pd.DataFrame, capital: pd.DataFrame, n: int) -> pd.DataFrame:
    """N日平均换手率 = 成交额/流通市值"""
    turn = amount / capital
    return turn.rolling(n).mean()


def vol_ratio(volume: pd.DataFrame, short: int = 5, long: int = 20) -> pd.DataFrame:
    """量比 = 近short日均量 / 近long日均量"""
    return volume.rolling(short).mean() / volume.rolling(long).mean()


def price_to_ma(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """价格偏离均线程度 = close/MA(n) - 1"""
    return close / close.rolling(n).mean() - 1


def volatility(
    close: pd.DataFrame,
    n: int = 20,
    tradable: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """N日收益率波动率。

    Default = dirty path (limit closes enter the window). Pass ``tradable``
    (feature mask) or pre-cleanse ``close`` via ``feature_mask_context`` / DSL
    for Phase-2 clean path — vol is the highest |IC| contamination family in
    the 2019–2024 A/B diagnostic.
    """
    if tradable is not None:
        from factors.tradable_mask import factor_volatility_clean

        return factor_volatility_clean(close, tradable, n)
    ret = close.pct_change(fill_method=None)
    return ret.rolling(n).std() * np.sqrt(252)


@register_factor(
    "illiquidity",
    definition=(
        "Amihud 非流动性 = rolling mean(|ret|/(volume×close+1)),amount 代理;"
        "正=单位成交额推动价格幅度大(低流动性)。DSL 执行面喂 (close,volume)"
    ),
    params={"window": (5, 120)},
    data=("price/close", "price/volume", "price/amount"),
    input="close",
    arg_map={"window": "n"},
    searchable=True,
    evidence=(
        "reports/research/illiquidity_strategy_report.md;"
        "reports/research/amihud_rotation_strategy_report.md;"
        "knowledge/direction_registry:size-illiquidity-same-information"
    ),
)
def illiquidity(close: pd.DataFrame, volume: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Amihud 非流动性 = mean(|ret| / amount)，与 `factors.alpha.builtins.illiq.AmihudIlliq` 对齐。

    正式 Amihud(2002) 分母是成交额 amount，不是成交量 volume。
    AutoResearch DSL 表面只有 close/volume 时，用 amount ≈ volume × close 代理
    （在 amount = volume×price 时与 OO 版同构；截面排序下常数单位差可消）。

    历史错误口径曾写 mean(|ret|/volume)，等价于 Amihud×价格水平，会把价格因子
    混进「illiquidity」搜索语义——已纠正，勿回退。
    """
    ret = close.pct_change(fill_method=None).abs()
    amount = volume.astype(float) * close.astype(float)
    daily = ret / (amount.replace(0, np.nan) + 1.0)
    return daily.rolling(n).mean()
