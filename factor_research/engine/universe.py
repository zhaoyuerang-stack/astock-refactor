"""
股票池过滤模块

每个截面日期生成可交易股票池，过滤掉：
  - ST / *ST 股票（名称含ST）
  - 上市不足180天的次新股
  - 当日停牌（成交量为0）

涨跌停**不在此模块过滤**（历史注释曾写「可选」但从未实现）。
  - 执行侧一字板：见 ``portfolio.paper_engine``（开盘触板）
  - 特征侧上游污染：见 ``factors.tradable_mask``（rolling 前 mask，非事后 universe）
  - ``apply_universe_mask`` 只 scrub 截面，**救不了**滚动窗口内的脏价
"""
import pandas as pd


def build_universe(
    date: pd.Timestamp,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    profile: pd.DataFrame = None,
    st_codes: set = None,
    min_listed_days: int = 180,
) -> pd.Index:
    """
    返回指定日期的可交易股票池（code列表）

    profile: DataFrame，index=code，需含 list_date 列
    st_codes: 当日ST股票代码集合
    """
    if date not in close.index:
        return pd.Index([])

    candidates = close.columns

    # 过滤停牌（成交量为0或NaN）
    if date in volume.index:
        vol = volume.loc[date]
        candidates = candidates[vol.reindex(candidates).fillna(0) > 0]

    # 过滤ST
    if st_codes:
        candidates = candidates[~candidates.isin(st_codes)]

    # 过滤次新股
    if profile is not None and "list_date" in profile.columns:
        list_date = pd.to_datetime(profile["list_date"]).reindex(candidates)
        listed_days = (date - list_date).dt.days
        candidates = candidates[listed_days.fillna(0) >= min_listed_days]

    return candidates


def build_universe_panel(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    profile: pd.DataFrame = None,
    st_codes_by_date: dict = None,
    min_listed_days: int = 180,
    freq: str = "W",
) -> dict[pd.Timestamp, pd.Index]:
    """
    批量生成各调仓日的股票池（按freq采样，默认每周）
    返回 {date: universe_codes} 字典
    """
    rebal_dates = close.resample(freq).last().index
    universes = {}
    for dt in rebal_dates:
        if dt not in close.index:
            # 取最近交易日
            avail = close.index[close.index <= dt]
            if avail.empty:
                continue
            dt = avail[-1]
        st = st_codes_by_date.get(dt, set()) if st_codes_by_date else None
        universes[dt] = build_universe(dt, close, volume, profile, st, min_listed_days)
    return universes


def apply_universe_mask(
    factor: pd.DataFrame,
    universes: dict[pd.Timestamp, pd.Index],
) -> pd.DataFrame:
    """
    将因子矩阵中不在股票池的股票置为 NaN
    universes 可以是截面日期粒度（每日）或调仓日粒度（每周/月），
    自动用 ffill 对齐到因子的所有日期
    """
    # 构建 mask：date×code，True=在池内
    all_codes = factor.columns
    mask = pd.DataFrame(False, index=factor.index, columns=all_codes)

    sorted_u_dates = sorted(universes.keys())
    for _, dt in enumerate(factor.index):
        # 找最近一个不超过 dt 的 universe
        past = [d for d in sorted_u_dates if d <= dt]
        if not past:
            continue
        uni = universes[past[-1]]
        valid = uni.intersection(all_codes)
        mask.loc[dt, valid] = True

    return factor.where(mask)
