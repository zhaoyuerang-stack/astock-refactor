"""价值类因子

Disposition: dormant — 零消费者(未接 catalog/DSL/白名单,无脚本引用);复活须先走 probe-signal-source 体检(R-ARCH-005 精神)。
"""
import numpy as np
import pandas as pd


def pe_rank_in_industry(pe: pd.Series, industry: pd.Series) -> pd.Series:
    """行业内PE分位数排名（越低越便宜）"""
    return pe.groupby(industry).rank(pct=True)


def pb_rank_in_industry(pb: pd.Series, industry: pd.Series) -> pd.Series:
    return pb.groupby(industry).rank(pct=True)


def ep(pe: pd.DataFrame) -> pd.DataFrame:
    """EP = 1/PE，收益率因子"""
    return 1.0 / pe.replace(0, np.nan)


def bp(pb: pd.DataFrame) -> pd.DataFrame:
    """BP = 1/PB"""
    return 1.0 / pb.replace(0, np.nan)


def size(market_cap: pd.DataFrame) -> pd.DataFrame:
    """市值因子（对数市值，取负=小市值偏好）"""
    return -np.log(market_cap.replace(0, np.nan))
