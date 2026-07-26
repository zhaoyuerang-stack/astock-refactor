"""独立数据族隔离岛验真(LOOP_ENGINEERING.md #5)。

测试股东行为(holder_count_chg/holdertrade_net)+ 资金流(large_order_net_ratio)
是否构成与价量簇(illiquidity/size)正交的真 alpha,而非又一个同质变体。

复用 strategy_truth_screen 的电池(L0 去 overlay 归因 / 独立 IC+t / 容量)+
factor_pool_screen 的判决规则(真 alpha = L0 裸因子夏普≥0.8 且独立 IC 方向对、|IC|≥0.02)。
全程截 < holdout boundary(§5.2:这是新候选搜索,不是既有策略的confirmatory审计)。
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path("/Users/kiki/astcok/factor_research")
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "research"))

import numpy as np
import pandas as pd
from illiq_largecap_audit import build_weights
from strategy_truth_screen import _run, capacity_est, independent_ic

from core.engine import CostModel, PricePanel
from engine.metrics import metrics
from governance.holdout import assert_search_clean, boundary

START = "2018-01-01"
COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.0)


def factor_registry(close, volume, amount):
    """name -> (factor_df, direction)。含 2 个既有价量因子作正交性参照基线。"""
    from factors.capital_flow import large_order_net_ratio
    from factors.shareholder import holder_count_chg, holdertrade_net

    ret = close.pct_change(fill_method=None)
    adv = amount.rolling(20).mean()
    reg = {}
    # ── 参照基线(价量簇,已知真 alpha)──
    reg["illiq_amihud20"] = ((ret.abs() / (amount + 1.0)).rolling(20).mean(), +1)
    reg["size_neg_adv"] = (-np.log(adv + 1.0), +1)
    # ── 独立数据族新候选 ──
    reg["holder_count_chg"] = (holder_count_chg(close, window=60), +1)
    reg["holdertrade_net"] = (holdertrade_net(close, window=120), +1)
    reg["large_order_net_ratio"] = (large_order_net_ratio(close, window=5), +1)
    return reg


def screen_factor(name, factor, direction, prices, timing):
    weights = build_weights(factor, prices.close, prices.amount, top_n=25, rebal=20, universe=10**9)
    if weights.empty:
        return None, None
    r_L0 = _run(prices, weights, None, 1.0, COST, START).dropna()
    r_L1 = _run(prices, weights, timing, 1.0, COST, START).dropna()
    m0, m1 = metrics(r_L0), metrics(r_L1)
    ic = independent_ic(factor if direction > 0 else -factor, prices.close)
    cap = capacity_est(prices, weights)
    ic_ok = (ic["ic_mean"] > 0) and abs(ic["ic_mean"]) >= 0.02
    real_alpha = (m0["sharpe"] >= 0.8) and ic_ok
    row = {
        "name": name, "L0_annual": round(m0["annual"], 4), "L0_sharpe": round(m0["sharpe"], 3),
        "L0_maxdd": round(m0["maxdd"], 4), "L1_sharpe": round(m1["sharpe"], 3),
        "L1_maxdd": round(m1["maxdd"], 4), "ic_mean": round(ic["ic_mean"], 4),
        "ic_t": round(ic["t_stat"], 2), "cap_亿": cap.get("capacity_aum_亿"),
        "real_alpha": bool(real_alpha),
    }
    return row, r_L0


def main():
    from factors.small_cap import small_cap_timing
    from strategies.small_cap import load_price_panels

    b = boundary()
    close, volume, amount = load_price_panels("2010-01-01")
    # §5.2:新候选搜索,全程截 < boundary,不触碰金库
    close = close[close.index < b]
    volume = volume[volume.index < b]
    amount = amount[amount.index < b]
    assert_search_clean(close.index, label="orthogonal_island_screen")

    timing, _, _ = small_cap_timing(close, amount, 16)
    prices = PricePanel(close=close, volume=volume, amount=amount)

    reg = factor_registry(close, volume, amount)
    rows, returns = [], {}
    for name, (factor, direction) in reg.items():
        print(f"screening {name}...", flush=True)
        row, r_L0 = screen_factor(name, factor, direction, prices, timing)
        if row is None:
            print(f"  ⚠️ {name}: 空权重,跳过")
            continue
        rows.append(row)
        returns[name] = r_L0
        print(f"  L0 夏普{row['L0_sharpe']:.2f} 年化{row['L0_annual']:+.1%} "
              f"独立IC {row['ic_mean']:+.4f}(t={row['ic_t']}) real_alpha={row['real_alpha']}")

    # ── 正交性:新候选 vs 价量簇参照基线的两两相关(L0 收益序列)──
    corr = pd.DataFrame(returns).corr()
    new_names = ["holder_count_chg", "holdertrade_net", "large_order_net_ratio"]
    ref_names = ["illiq_amihud20", "size_neg_adv"]
    print("\n=== 正交性(新候选 L0 收益 vs 价量簇参照基线)===")
    ortho = {}
    for n in new_names:
        if n not in corr.columns:
            continue
        vs_ref = {r: round(float(corr.loc[n, r]), 3) for r in ref_names if r in corr.columns}
        ortho[n] = vs_ref
        print(f"  {n:24} vs illiq={vs_ref.get('illiq_amihud20')} vs size={vs_ref.get('size_neg_adv')}")

    out = {"window": f"{START}~<{b.date()}", "factors": rows,
           "orthogonality_vs_price_volume_cluster": ortho}
    out_path = PROJECT_ROOT / "scratch" / "orthogonal_island_screen.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    print(f"\nWROTE {out_path}")


if __name__ == "__main__":
    main()
