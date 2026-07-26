"""财务质量类因子（基于季报数据）

Disposition: dormant — 零消费者(未接 catalog/DSL/白名单,无脚本引用);复活须先走 probe-signal-source 体检(R-ARCH-005 精神)。
"""
import pandas as pd


def roe_ttm(fundamental_df: pd.DataFrame) -> float | None:
    """TTM ROE = 近4季净利润之和 / 平均净资产"""
    df = fundamental_df.sort_values("date").tail(4)
    if len(df) < 4:
        return None
    net_profit = df.get("net_profit", df.get("netprofit_yoy", None))
    equity = df.get("total_equity", None)
    if net_profit is None or equity is None:
        return None
    return net_profit.sum() / equity.mean()


def cfo_to_assets(cashflow_df: pd.DataFrame, balance_df: pd.DataFrame) -> float | None:
    """经营现金流/总资产"""
    cf = cashflow_df.sort_values("date").tail(4)
    bl = balance_df.sort_values("date").tail(1)
    if cf.empty or bl.empty:
        return None
    cfo_col = next((c for c in ["n_cashflow_act", "cash_flow_oper"] if c in cf.columns), None)
    asset_col = next((c for c in ["total_assets", "totalassets"] if c in bl.columns), None)
    if cfo_col is None or asset_col is None:
        return None
    return cf[cfo_col].sum() / bl[asset_col].iloc[0]


def gross_margin_growth(income_df: pd.DataFrame, n: int = 4) -> float | None:
    """毛利率同比变化"""
    df = income_df.sort_values("date")
    gm_col = next((c for c in ["grossprofit_margin", "gross_profit_margin"] if c in df.columns), None)
    if gm_col is None or len(df) < n + 1:
        return None
    return df[gm_col].iloc[-1] - df[gm_col].iloc[-1 - n]


def accrual(fundamental_df: pd.DataFrame) -> float | None:
    """应计项目 = (净利润 - 经营现金流) / 总资产（越小越好）"""
    df = fundamental_df.sort_values("date").tail(4)
    if df.empty:
        return None
    profit_col = next((c for c in ["net_profit", "n_income"] if c in df.columns), None)
    cfo_col = next((c for c in ["n_cashflow_act", "cash_flow_oper"] if c in df.columns), None)
    asset_col = next((c for c in ["total_assets", "totalassets"] if c in df.columns), None)
    if any(c is None for c in [profit_col, cfo_col, asset_col]):
        return None
    return (df[profit_col].sum() - df[cfo_col].sum()) / df[asset_col].mean()
