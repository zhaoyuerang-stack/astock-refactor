"""contribution_dashboard — 当前 LIVE 组合贡献分解 + Pareto 空白识别。

跑全部 LIVE 母策略 → 算 contribution_decompose → 输出可读报告。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from portfolio.analysis import contribution_decompose, correlation_matrix
from portfolio.composer import compose
from portfolio.composer import metrics as portfolio_metrics
from portfolio.strategy_runners import LIVE_STRATEGIES


def _print_strategy_summary(returns: dict):
    print("\n[1] Live strategies (start..end, ann/sharpe/maxdd):")
    for name, r in returns.items():
        if len(r) < 50:
            print(f"  {name:30s}  insufficient data ({len(r)} days)")
            continue
        ann = float(r.mean() * 252)
        vol = float(r.std() * np.sqrt(252))
        sh = ann / vol if vol > 0 else 0
        cum = (1 + r).cumprod()
        dd = float((cum / cum.cummax() - 1).min())
        print(f"  {name:30s}  {r.index[0].date()}~{r.index[-1].date()}  "
              f"ann={ann:+.1%} sharpe={sh:.2f} maxdd={dd:.1%}")


def _print_correlation(returns: dict):
    corr = correlation_matrix(returns)
    print("\n[2] Correlation matrix:")
    # short column names
    short = {c: c.split(".")[0][:14] for c in corr.columns}
    corr_s = corr.rename(columns=short, index=short)
    print(corr_s.round(2).to_string())

    # Highest pair
    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
    pairs = upper.stack().sort_values(ascending=False)
    if len(pairs):
        a, b = pairs.index[0]
        print(f"\n  highest pair corr={pairs.iloc[0]:.2f}: {a} ↔ {b}")
        a, b = pairs.index[-1]
        print(f"  lowest pair  corr={pairs.iloc[-1]:.2f}: {a} ↔ {b}")


def _print_contribution(returns: dict, method: str = "equal_weight"):
    print(f"\n[3] Contribution decomposition ({method}):")
    decomp = contribution_decompose(returns)
    # 短列名展示
    print(decomp[[
        "weight", "annual", "ann_contrib", "ann_contrib_pct",
        "risk_contrib_pct", "marginal_sharpe", "avg_corr_to_others"
    ]].round(3).to_string())

    print()
    print("  marginal_sharpe > 0 → 多元化贡献正；< 0 → 拖累，应考虑降权")
    print("  avg_corr_to_others 高 → 与其他策略冗余；低 → 增益多元化")


def _print_portfolio_metrics(returns: dict):
    print("\n[4] Portfolio (equal weight) metrics:")
    port_ret, _ = compose(returns, method="equal_weight")
    m = portfolio_metrics(port_ret)
    print(f"  annual = {m['annual']:+.1%}")
    print(f"  vol    = {m['vol']:+.1%}")
    print(f"  sharpe = {m['sharpe']:.2f}")
    print(f"  maxdd  = {m['maxdd']:.1%}")
    print(f"  calmar = {m['calmar']:.2f}")
    print(f"  n_days = {m['n_days']}")


def _print_pareto_gaps(returns: dict):
    print("\n[5] Pareto 空白分析:")
    # 简单分析：每个策略的 annual / vol 二维位置
    pts = []
    for name, r in returns.items():
        if len(r) < 50:
            continue
        ann = float(r.mean() * 252)
        vol = float(r.std() * np.sqrt(252))
        sh = ann / vol if vol > 0 else 0
        pts.append((name, ann, vol, sh))

    if not pts:
        return

    anns = [p[1] for p in pts]
    vols = [p[2] for p in pts]
    print(f"  当前 annual 范围: {min(anns):+.1%} ~ {max(anns):+.1%}")
    print(f"  当前 vol    范围: {min(vols):+.1%} ~ {max(vols):+.1%}")

    # 启发式：是否缺低波低回撤资产？
    low_vol_threshold = 0.12
    n_lowvol = sum(1 for _, _, v, _ in pts if v < low_vol_threshold)
    if n_lowvol == 0:
        print(f"  ⚠ 组合缺低波资产 (vol < {low_vol_threshold:.0%})")
        print("    → Discovery 应优先找 volatility / low_vol / defensive 类因子")
        print("    → L1 survivors 中 volatility__n10/n60 是候选 (vol=8-10%)")
    else:
        print(f"  ✓ 含 {n_lowvol} 个低波资产")

    # 启发式：相关性诊断
    corr_mat = correlation_matrix(returns)
    upper = corr_mat.where(np.triu(np.ones(corr_mat.shape), k=1).astype(bool))
    pairs = upper.stack()
    avg_corr = float(pairs.mean()) if len(pairs) else 0
    print(f"  组合平均两两 corr = {avg_corr:.2f}")
    if avg_corr > 0.6:
        print("  ⚠ 多元化偏弱 (corr > 0.6) → 需找不同性质的 alpha 源")
        print("    → 候选: 动量类 (mom_n)、价值类 (BP/EP)、基本面类 (ROE/quality)")
    elif avg_corr > 0.4:
        print("  ◐ 多元化中等 → 可继续加低相关候选")
    else:
        print("  ✓ 多元化良好")


def main():
    parser = argparse.ArgumentParser(prog="contribution_dashboard")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--strategies", nargs="+",
                        help=f"subset of {list(LIVE_STRATEGIES.keys())}")
    parser.add_argument("--method", default="equal_weight",
                        choices=["equal_weight", "risk_parity"])
    args = parser.parse_args()

    selected = args.strategies or list(LIVE_STRATEGIES.keys())
    bad = [s for s in selected if s not in LIVE_STRATEGIES]
    if bad:
        print(f"unknown strategies: {bad}", file=sys.stderr)
        return 1

    print(f"Running {len(selected)} LIVE strategies (start={args.start})...")
    t0 = time.time()
    returns = {name: LIVE_STRATEGIES[name]["fn"](args.start) for name in selected}
    print(f"  {time.time()-t0:.1f}s")

    _print_strategy_summary(returns)
    _print_correlation(returns)
    _print_contribution(returns, args.method)
    _print_portfolio_metrics(returns)
    _print_pareto_gaps(returns)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
