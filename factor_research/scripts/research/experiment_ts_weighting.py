"""实验: 截面选股 + 时序仓位缩放 vs 等权.

截面: illiquidity (amount w=60) zscore → top-25
时序: 每只股票自身的 illiquidity zscore (相对 252 天历史)
      时序信号 > 0 → 加权; ≤ 0 → 等权基准

对照:
  A: 等权 top-25 (当前)
  B: top-25 按时序号缩放仓位
  C: top-50 → 时序筛选 top-25 (纯时序, 截面只做粗筛)

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/research/experiment_ts_weighting.py
"""
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.chdir(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, str(Path.cwd()))

import numpy as np
import pandas as pd

from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import load_price_panels

STATS_START = "2018-01-01"
INITIAL_CAPITAL = 1_000_000
TOP_N = 25
TS_LOOKBACK = 252  # 时序 zscore 窗口


def build_ts_zscore(amount, window=TS_LOOKBACK):
    """对每只股票独立算时序 zscore: (当前值 - 自身历史均值) / 自身历史 std.

    返回 date×code DataFrame, > 0 = 当前比自身历史平均更便宜(更高 illiquidity).
    """
    # illiquidity raw = -log(avg amount + 1)
    illiq_raw = -np.log(amount.rolling(60).mean() + 1)
    # 时序 zscore: 每列独立
    roll_mean = illiq_raw.rolling(window, min_periods=60).mean()
    roll_std = illiq_raw.rolling(window, min_periods=60).std().replace(0, np.nan)
    ts_z = (illiq_raw - roll_mean) / roll_std
    return ts_z.clip(-5, 5)


def build_ts_filtered_weights(cs_factor, ts_zscore, close, top_n_cs=50, top_n_final=25, reverse=False):
    """两阶段: 截面 top-N → 按时序号选最终持仓, 等权.

    reverse=False: nlargest (ts_zscore 大的 = illiquidity 增加的)
    reverse=True:  nsmallest (ts_zscore 小的 = illiquidity 下降/流动性恢复)
    """
    dates = cs_factor.dropna(how="all").index.intersection(close.index)
    weights = {}

    for dt in dates:
        cs = cs_factor.loc[dt].dropna()
        active = close.loc[dt].dropna().index
        cs = cs.reindex(active).dropna()
        if len(cs) < top_n_cs:
            continue

        cs_pool = cs.nlargest(top_n_cs).index.tolist()

        if dt in ts_zscore.index:
            ts = ts_zscore.loc[dt].reindex(cs_pool).dropna()
            if len(ts) >= top_n_final:
                if reverse:
                    selected = ts.nsmallest(top_n_final).index.tolist()
                else:
                    selected = ts.nlargest(top_n_final).index.tolist()
                w = 1.0 / top_n_final
                weights[dt] = pd.Series(w, index=selected)
    return weights


def build_ts_scaled_weights(cs_factor, ts_zscore, close, top_n=25, reverse=False):
    """截面 top-N, 时序信号缩放仓位: weight = base * scale(ts_zscore).

    reverse=False: ts_zscore > 0 (更贵/更illiquid) → boost (原假设)
    reverse=True:  ts_zscore < 0 (更便宜/更liquid) → boost (反向假设)
    scale clamped [0.5, 2.0], renormalize 使 sum=1.
    """
    dates = cs_factor.dropna(how="all").index.intersection(close.index)
    weights = {}

    for dt in dates:
        cs = cs_factor.loc[dt].dropna()
        active = close.loc[dt].dropna().index
        cs = cs.reindex(active).dropna()
        if len(cs) < top_n:
            continue

        selected = cs.nlargest(top_n)
        base_w = 1.0 / top_n

        if dt in ts_zscore.index:
            ts = ts_zscore.loc[dt].reindex(selected.index).fillna(0)
            # scale: reverse=False → ts>0 boost; reverse=True → ts<0 boost
            if reverse:
                signal = -ts  # ts<0 → -ts>0 → boost
            else:
                signal = ts
            scale = np.where(signal > 0, 1.0 + signal.clip(0, 3) * 0.5, 1.0)
            scale = np.clip(scale, 0.5, 2.0)
            w = base_w * scale
            w = w / w.sum()  # renormalize
            weights[dt] = pd.Series(w, index=selected.index)
        else:
            weights[dt] = pd.Series(base_w, index=selected.index)
    return weights


def build_equal_weights(cs_factor, close, top_n=25):
    """截面 top-N 等权."""
    dates = cs_factor.dropna(how="all").index.intersection(close.index)
    weights = {}
    for dt in dates:
        cs = cs_factor.loc[dt].dropna()
        active = close.loc[dt].dropna().index
        cs = cs.reindex(active).dropna()
        if len(cs) < top_n:
            continue
        selected = cs.nlargest(top_n).index.tolist()
        weights[dt] = pd.Series(1.0 / top_n, index=selected)
    return weights


def run_bt(weights_dict, close, amount, label, leverage=1.25):
    """Run backtest with given weights dict."""
    pt_timing, _, _ = small_cap_timing(close, amount, ma_window=16)
    pt_timing = pt_timing.astype(float)
    # align timing index
    common = pt_timing.dropna().index
    pt_timing = pt_timing.reindex(common).fillna(0.0)

    prices = PricePanel(close=close, volume=None, amount=amount)
    cfg = BacktestConfig(
        start=STATS_START,
        cost=CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065),
        leverage=leverage,
    )
    engine = BacktestEngine(prices=prices, config=cfg)
    signal = Signal(
        weights=weights_dict, timing=pt_timing, exposure_cap=1.0,
        family="experiment", version="ts_test",
    )
    result = engine.run(signal)
    r = result.returns.loc[STATS_START:].dropna()
    if len(r) < 100:
        return None

    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = (annual - 0.025) / vol if vol > 0 else 0.0
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    calmar = annual / abs(maxdd) if maxdd < 0 else 0.0
    nav_final = cum.iloc[-1] * INITIAL_CAPITAL
    turnover = result.detail["turnover"].loc[STATS_START:].mean() * 252
    cost_drag = result.detail["cost"].loc[STATS_START:].mean() * 252
    monthly = r.resample("ME").apply(lambda g: (1 + g).prod() - 1)
    monthly_win = float((monthly > 0).mean())
    n_holdings = []
    for dt, w in weights_dict.items():
        if dt >= pd.Timestamp(STATS_START):
            n_holdings.append(len(w))
    avg_holdings = float(np.mean(n_holdings)) if n_holdings else 0

    return {
        "label": label, "annual": annual, "vol": vol, "sharpe": sharpe,
        "maxdd": maxdd, "calmar": calmar, "nav_final": nav_final,
        "turnover": turnover, "cost_drag": cost_drag,
        "monthly_win": monthly_win, "avg_holdings": avg_holdings,
        "n_days": len(r),
    }


def main():
    print("=" * 70)
    print("  实验: 截面选股 + 时序仓位 vs 等权")
    print("=" * 70)

    # ── 1. 数据 ──
    print("\n[1/4] 加载数据...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    print(f"  {close.shape[1]}只 x {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    # ── 2. 因子 ──
    print("[2/4] 计算截面因子 + 时序 zscore...", flush=True)
    cs_factor = small_cap_factor(amount, window=60)
    ts_zscore = build_ts_zscore(amount, window=TS_LOOKBACK)
    ts_pos_pct = ((ts_zscore.loc[STATS_START:] > 0).mean().mean())
    print(f"  时序 zscore > 0 占比: {ts_pos_pct:.0%} (截面平均)")

    # ── 3. 构建权重 ──
    print("[3/4] 构建三套权重...", flush=True)

    w_eq = build_equal_weights(cs_factor, close, top_n=25)
    w_ts_scale = build_ts_scaled_weights(cs_factor, ts_zscore, close, top_n=25, reverse=False)
    w_ts_scale_r = build_ts_scaled_weights(cs_factor, ts_zscore, close, top_n=25, reverse=True)
    w_ts_filter = build_ts_filtered_weights(cs_factor, ts_zscore, close,
                                             top_n_cs=50, top_n_final=25, reverse=False)
    w_ts_filter_r = build_ts_filtered_weights(cs_factor, ts_zscore, close,
                                               top_n_cs=50, top_n_final=25, reverse=True)

    print(f"  A 等权:          {len(w_eq)} 个调仓日")
    print(f"  B 时序缩放(正向):  {len(w_ts_scale)} 个调仓日")
    print(f"  B2 时序缩放(反向): {len(w_ts_scale_r)} 个调仓日")
    print(f"  C 时序筛选(正向):  {len(w_ts_filter)} 个调仓日")
    print(f"  C2 时序筛选(反向): {len(w_ts_filter_r)} 个调仓日")

    # ── 4. 回测 ──
    print("\n[4/4] 运行回测...", flush=True)
    scenarios = [
        ("A  等权 (当前)", w_eq, 1.25),
        ("B  时序缩放 (原方向)", w_ts_scale, 1.25),
        ("B2 时序缩放 (反向)", w_ts_scale_r, 1.25),
        ("C  时序筛选 (原方向)", w_ts_filter, 1.25),
        ("C2 时序筛选 (反向)", w_ts_filter_r, 1.25),
        ("A1x 等权 (1.0x)", w_eq, 1.0),
    ]

    results = []
    for label, w, lev in scenarios:
        bt = run_bt(w, close, amount, label, leverage=lev)
        if bt:
            results.append(bt)
            print(f"  {label:<22} 年化={bt['annual']:+.2%} 回撤={bt['maxdd']:.2%} "
                  f"夏普={bt['sharpe']:.2f} 卡玛={bt['calmar']:.2f} "
                  f"终值={bt['nav_final']/1e4:.0f}万 换手={bt['turnover']:.1f}x "
                  f"持仓{bt['avg_holdings']:.0f}只")

    # ── 输出 ──
    print(f"\n{'='*90}")
    print("  详细对比")
    print(f"{'='*90}")
    header = (f"  {'场景':<22} {'年化':>8} {'波动':>6} {'回撤':>8} {'夏普':>6} "
              f"{'卡玛':>6} {'终值(万)':>9} {'月胜率':>6} {'换手':>6} {'持仓':>5}")
    print(header)
    print("  " + "─" * 85)
    for bt in results:
        print(f"  {bt['label']:<22} {bt['annual']:>+7.1%} {bt['vol']:>5.0%} "
              f"{bt['maxdd']:>7.1%} {bt['sharpe']:>5.2f} {bt['calmar']:>5.2f} "
              f"{bt['nav_final']/1e4:>8.0f}万 {bt['monthly_win']:>5.0%} "
              f"{bt['turnover']:>5.1f}x {bt['avg_holdings']:>4.0f}")

    # 关键差异
    if len(results) >= 5:
        a = results[0]; b = results[1]; b2 = results[2]
        c = results[3]; c2 = results[4]
        print("\n  正向 (选 illiquidity 增加):")
        print(f"    缩放: Δ年化={b['annual']-a['annual']:+.2%} | "
              f"筛选: Δ年化={c['annual']-a['annual']:+.2%}")
        print("\n  反向 (选 illiquidity 下降/流动性恢复):")
        print(f"    缩放: Δ年化={b2['annual']-a['annual']:+.2%} | "
              f"筛选: Δ年化={c2['annual']-a['annual']:+.2%}")

    print()


if __name__ == "__main__":
    main()
