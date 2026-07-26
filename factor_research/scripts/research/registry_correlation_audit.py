"""在册母策略相关性审计:验证"伪多样性"假设。

问题:~10 个母策略是真跨 regime,还是骨子里同一个宏观赌注(小盘风险溢价)、
逆风年(2018/2023)一起跌?真正稀缺的是逆风年为正的防御腿。

只读分析,不改任何状态。输出:全期相关矩阵 + 逐年收益表 + 尾部相关 + 判定。

Run:
    cd factor_research && python3 scripts/research/registry_correlation_audit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

START = "2018-01-01"

FUND_MOM_AST = {
    "type": "linear_combo", "direction": "negative",
    "terms": [
        {"factor": "momentum", "params": {"window": 60}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
        {"factor": "revenue_yoy", "params": {}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
    ],
}


def _collect_returns() -> dict[str, pd.Series]:
    from factors.autoresearch_dsl import compute_dsl_factor
    from portfolio.strategy_runners import _run_with_factor, run_all_live

    series = run_all_live(start=START)  # 5 LIVE 母策略(含 SHADOW + 国债 ETF)

    def fm_builder(close, volume, amount):
        return compute_dsl_factor(close, volume, ast=FUND_MOM_AST)

    series["fundamental-momentum.v0.1"] = _run_with_factor(
        fm_builder, start=START, family="fundamental-momentum", version="v0.1")
    return series


def main():
    series = _collect_returns()
    df = pd.DataFrame(series).dropna(how="all")
    # 对齐到共同交易日(ETF 与股票日历略有差异时取交集)
    df = df.dropna()
    short = {n: n.split(".")[0].replace("gov_bond_etf_511010", "gov_bond").replace("-", "_") for n in df.columns}
    df = df.rename(columns=short)

    print(f"==== 样本 {df.index[0].date()} ~ {df.index[-1].date()}, {len(df)} 天, {df.shape[1]} 策略 ====\n")

    # 1) 全期相关矩阵
    corr = df.corr()
    print("== 全期 Pearson 相关矩阵 ==")
    print(corr.round(2).to_string())

    # 2) 逐年总收益(暴露 regime 依赖:2018/2023 逆风 vs 2021/2025 疯牛)
    yearly = df.groupby(df.index.year).apply(lambda x: (1 + x).prod() - 1)
    print("\n== 逐年总收益 ==")
    print((yearly * 100).round(1).to_string())

    # 3) 逆风年诊断:2018/2023 各策略正负
    print("\n== 逆风年诊断(2018/2023 谁为正)==")
    for yr in (2018, 2023):
        if yr in yearly.index:
            row = yearly.loc[yr]
            pos = [c for c in row.index if row[c] > 0]
            neg = [c for c in row.index if row[c] <= 0]
            print(f"  {yr}: 正={pos or '无'} | 负={neg}")

    # 4) 股票腿平均相关 vs 防御腿(国债)对股票腿相关
    equity = [c for c in df.columns if "gov_bond" not in c]
    bond = [c for c in df.columns if "gov_bond" in c]
    eq_corr = corr.loc[equity, equity]
    avg_eq = (eq_corr.values[np.triu_indices(len(equity), k=1)]).mean()
    print("\n== 多样性结构 ==")
    print(f"  股票腿两两平均相关: {avg_eq:.2f}  (n={len(equity)})")
    if bond:
        b = bond[0]
        print(f"  防御腿 {b} 对股票腿平均相关: {corr.loc[b, equity].mean():.2f}")

    # 5) 尾部相关:市场(股票腿等权)最差 20% 日上的两两相关
    mkt = df[equity].mean(axis=1)
    worst = mkt <= mkt.quantile(0.20)
    tail_corr = df[equity].loc[worst].corr()
    avg_tail = (tail_corr.values[np.triu_indices(len(equity), k=1)]).mean()
    print(f"  股票腿尾部(市场最差20%日)平均相关: {avg_tail:.2f}  (全期 {avg_eq:.2f})")

    verdict = "伪多样性确认:股票腿尾部高相关+逆风年同跌" if avg_tail >= 0.5 else "股票腿尾部相关中等"
    print(f"\n== 判定 ==\n  {verdict}")

    out = ROOT / "reports" / "research" / "registry_correlation_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "window": f"{df.index[0].date()}~{df.index[-1].date()}",
        "n_days": len(df),
        "corr_full": corr.round(3).to_dict(),
        "yearly_returns": (yearly).round(4).to_dict(),
        "avg_equity_corr": round(float(avg_eq), 3),
        "avg_equity_tail_corr": round(float(avg_tail), 3),
        "bond_corr_to_equity": round(float(corr.loc[bond[0], equity].mean()), 3) if bond else None,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
