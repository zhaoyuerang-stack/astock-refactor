"""Gross vs Net Performance.

Calculates net performance by deducting trading commissions, tax, slippage,
financing fees, and management/performance charges.
"""
from __future__ import annotations

import pandas as pd


def calculate_gross_to_net(
    gross_returns: pd.Series,
    commission_and_tax: pd.Series,
    slippage: pd.Series,
    financing_costs: pd.Series,
    mgmt_fee_annual: float = 0.02,
    perf_fee_ratio: float = 0.20
) -> pd.DataFrame:
    """Calculate the step-by-step performance decay from gross to net."""
    df = pd.DataFrame(index=gross_returns.index)
    df["gross"] = gross_returns
    df["commission_and_tax"] = commission_and_tax
    df["slippage"] = slippage
    df["financing"] = financing_costs
    
    # Net of transaction costs return
    df["net_trading"] = df["gross"] - df["commission_and_tax"] - df["slippage"] - df["financing"]
    
    # Fee deductions
    daily_mgmt_fee = mgmt_fee_annual / 252
    df["mgmt_fee"] = daily_mgmt_fee
    
    # Simple performance fee (applied only to positive returns for demonstration)
    df["perf_fee"] = df["net_trading"].apply(lambda r: max(0.0, r) * perf_fee_ratio)
    
    df["net_all"] = df["net_trading"] - df["mgmt_fee"] - df["perf_fee"]
    return df
