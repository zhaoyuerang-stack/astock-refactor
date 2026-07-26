"""独立数据族隔离岛 — 大中盘宇宙重测(LOOP_ENGINEERING.md #5 续)。

上一轮全市场 top25 测试中,3 个新候选(holder_count_chg/holdertrade_net/
large_order_net_ratio)与既有 illiquidity/size 簇相关性高达 0.66-0.83、且都不是
真 alpha——怀疑是"全市场 top-N-by-rank 选股"本身的漏斗效应(任何因子排序都被
导向同一个小盘/不流动角落),不是这些数据缺乏信息。本脚本把候选股池限制到
大中盘(universe=800/300,对齐既有 illiquidity-large-cap 审计的口径),看同样的
因子在不被小盘漏斗支配的空间里是否显出独立信号。

全程截 < holdout boundary(§5.2,新候选搜索)。
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


from illiq_largecap_audit import build_weights, ic_stats

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from engine.metrics import metrics
from governance.holdout import assert_search_clean, boundary

COST = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.0)
UNIVERSES = [800, 300]


def run_bt(prices, weights, timing, start):
    cfg = BacktestConfig(start=start, cost=COST, leverage=1.0)
    return BacktestEngine(prices=prices, config=cfg).run(Signal(weights=weights, timing=timing)).returns


def screen(name, factor, close, amount, prices, timing, start, universe):
    w = build_weights(factor, close, amount, top_n=25, rebal=20, universe=universe)
    if w.empty:
        return None
    r0 = run_bt(prices, w, None, start).dropna()
    m0 = metrics(r0)
    ic = ic_stats(factor, close, amount, universe=universe)
    real_alpha = (m0["sharpe"] >= 0.8) and (ic["ic_mean"] > 0) and abs(ic["ic_mean"]) >= 0.02
    return {
        "universe": universe, "name": name,
        "L0_annual": round(m0["annual"], 4), "L0_sharpe": round(m0["sharpe"], 3),
        "L0_maxdd": round(m0["maxdd"], 4),
        "ic_mean": round(ic["ic_mean"], 4), "ic_ir": round(ic["ic_ir"], 3), "n_ic": ic["n_ic"],
        "real_alpha": bool(real_alpha),
    }


def main():
    from factors.capital_flow import large_order_net_ratio
    from factors.shareholder import holder_count_chg, holdertrade_net
    from strategies.small_cap import load_price_panels

    b = boundary()
    close, volume, amount = load_price_panels("2010-01-01")
    close = close[close.index < b]
    volume = volume[volume.index < b]
    amount = amount[amount.index < b]
    assert_search_clean(close.index, label="orthogonal_island_largecap_screen")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    start = "2018-01-01"

    mkt = (1 + close.pct_change(fill_method=None).fillna(0.0).mean(axis=1)).cumprod()
    timing = (mkt > mkt.rolling(16).mean()).astype(float)

    candidates = {
        "holder_count_chg": holder_count_chg(close, window=60),
        "holdertrade_net": holdertrade_net(close, window=120),
        "large_order_net_ratio": large_order_net_ratio(close, window=5),
    }

    rows = []
    for universe in UNIVERSES:
        print(f"\n=== universe=top{universe} ADV ===")
        for name, factor in candidates.items():
            row = screen(name, factor, close, amount, prices, timing, start, universe)
            if row is None:
                print(f"  ⚠️ {name}: 空权重,跳过")
                continue
            rows.append(row)
            print(f"  {name:24} L0夏普{row['L0_sharpe']:+.2f} 年化{row['L0_annual']:+.1%} "
                  f"独立IC {row['ic_mean']:+.4f}(IR={row['ic_ir']:.2f},n={row['n_ic']}) "
                  f"real_alpha={row['real_alpha']}")

    out = {"window": f"{start}~<{b.date()}", "universes": UNIVERSES, "results": rows}
    out_path = PROJECT_ROOT / "scratch" / "orthogonal_island_largecap_screen.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    print(f"\nWROTE {out_path}")


if __name__ == "__main__":
    main()
