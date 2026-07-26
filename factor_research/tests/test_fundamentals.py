"""基本面引擎 + 读层测试。

引擎:确定性数学(合成输入,无数据依赖)。
读层:真实 data_lake,验证 None-safe + 防未来对齐(取最新已披露 ann_date)。
Run: cd factor_research && python3 tests/test_fundamentals.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import pandas as pd

from factory.fundamental import (
    BargainingPowerEstimator,
    FinancialProfile,
    MarketPricingProfile,
    PricingGapEstimator,
    PricingState,
)
from services.read.fundamentals import fundamental_profile


# ── 引擎:议价权 ────────────────────────────────────────────────────────────────
def test_bargaining_full_data():
    bp = BargainingPowerEstimator()
    fp = FinancialProfile(code="X", revenue=400, cost=310, ebit=90,
                          receivables=60, payables=130, inventory=45)
    a = bp.assess(fp)
    assert a["bpi"] is not None and a["bpi"] > 0          # 应付>应收 → 占用上下游
    assert a["ccc_days"] is not None and a["ccc_days"] < 0  # 负现金循环周期 → 强势
    assert 0.0 <= a["pricing_power_score"] <= 1.0


def test_bargaining_missing_balance_items_returns_none_not_garbage():
    bp = BargainingPowerEstimator()
    fp = FinancialProfile(code="X", revenue=100, cost=70, ebit=18)   # 无应收/应付/存货
    a = bp.assess(fp)
    assert a["bpi"] is None and a["ccc_days"] is None       # 缺数据 → None,不编造
    assert a["pricing_power_score"] is not None             # 仍可按毛利估算


# ── 引擎:预期差 ────────────────────────────────────────────────────────────────
def test_pricing_gap_states():
    pg = PricingGapEstimator()
    # 基本面高但已涨多估值高 → 透支
    hot = MarketPricingProfile(code="A", pe_percentile=0.9, pb_percentile=0.9,
                               return_20d=0.4, return_60d=0.7, analyst_revision_ratio=0.9)
    _, s1 = pg.pricing_gap(0.5, hot)
    assert s1 == PricingState.PRICED_IN_RISK
    # 基本面好但股价/估值低位未反应 → 滞后机会
    cold = MarketPricingProfile(code="B", pe_percentile=0.1, pb_percentile=0.1,
                                return_20d=-0.05, return_60d=-0.1, analyst_revision_ratio=0.1)
    _, s2 = pg.pricing_gap(0.8, cold)
    assert s2 == PricingState.LAGGED_OPPORTUNITY
    # 基本面分缺失 → gap/state 为 None,不臆断
    g, s = pg.pricing_gap(None, cold)
    assert g is None and s is None


# ── 读层:真实数据 + None-safe + 防未来对齐 ──────────────────────────────────────
def test_fundamental_profile_real_data():
    p = fundamental_profile("300124")
    assert p["code"] == "300124" and p["name"]
    q = p["quality"]
    assert q["gross_margin"] is not None and 0 < q["gross_margin"] < 1   # 毛利率真值且已转小数
    v = p["valuation"]
    if v.get("pe_pctile") is not None:
        assert 0.0 <= v["pe_pctile"] <= 1.0                              # 分位合法
    # 议价权缺科目时为 None(摄取后点亮),绝不为 0 充数
    b = p["bargaining"]
    assert b["bpi"] is None or isinstance(b["bpi"], float)
    # 若已摄取资负表科目:周转天数须在合理区间(防"存量÷季度流量"未年化的 4 倍虚高回归)
    if b["dso_days"] is not None:
        assert 0 < b["dso_days"] < 730, f"DSO 异常,疑似流量未年化: {b['dso_days']}"
        assert 0 < b["dpo_days"] < 1095


def test_fundamental_profile_uses_latest_disclosed_anndate():
    """防未来:as_of 应等于该股财务表里最大的 ann_date(只取已披露最新)。"""
    p = fundamental_profile("300124")
    df = pd.read_parquet(ROOT / "data_lake" / "financials" / "income_all.parquet")
    sub = df[df["ts_code"].astype(str).str.startswith("300124.")]
    assert p["as_of"] == str(sub["ann_date"].max())


if __name__ == "__main__":
    test_bargaining_full_data()
    test_bargaining_missing_balance_items_returns_none_not_garbage()
    test_pricing_gap_states()
    test_fundamental_profile_real_data()
    test_fundamental_profile_uses_latest_disclosed_anndate()
    print("Fundamentals tests passed.")
