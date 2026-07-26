"""资金流类因子

Disposition: dormant — 零消费者(未接 catalog/DSL/白名单,无脚本引用);复活须先走 probe-signal-source 体检(R-ARCH-005 精神)。
"""
import pandas as pd


def main_flow_ratio(flow_df: pd.DataFrame, n: int = 5) -> float | None:
    """N日主力净流入占比 = 主力净流入 / 总成交额"""
    df = flow_df.sort_values("date").tail(n)
    if df.empty:
        return None
    main_col = next((c for c in ["main_net_inflow", "net_mf_amount"] if c in df.columns), None)
    amt_col = next((c for c in ["total_amount", "amount"] if c in df.columns), None)
    if main_col is None:
        return None
    if amt_col is not None and df[amt_col].sum() > 0:
        return df[main_col].sum() / df[amt_col].sum()
    return df[main_col].mean()


def smart_money(flow_df: pd.DataFrame, n: int = 10) -> float | None:
    """超大单净流入占比（聪明钱）"""
    df = flow_df.sort_values("date").tail(n)
    if df.empty:
        return None
    xl_col = next((c for c in ["super_large_net", "xl_net_inflow"] if c in df.columns), None)
    amt_col = next((c for c in ["total_amount", "amount"] if c in df.columns), None)
    if xl_col is None:
        return None
    if amt_col is not None and df[amt_col].sum() > 0:
        return df[xl_col].sum() / df[amt_col].sum()
    return df[xl_col].mean()


def flow_divergence(flow_df: pd.DataFrame, n: int = 10) -> float | None:
    """资金流分歧度 = (超大单+大单净流入) - (中单+小单净流入)"""
    df = flow_df.sort_values("date").tail(n)
    if df.empty:
        return None
    cols = df.columns.tolist()
    big = [c for c in cols if any(k in c for k in ["super_large", "large", "xl", "l_net"])]
    small = [c for c in cols if any(k in c for k in ["small", "medium", "s_net", "m_net"])]
    if not big or not small:
        return None
    return df[big[0]].sum() - df[small[0]].sum()
