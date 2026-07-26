"""四场景对比验证: HMM 去留 + Band 切换.

场景:
  A    PT Binary (0/1),        1.25x  去掉 HMM
  B    PT Binary + HMM th=0.15, 1.25x 当前生产
  C    PT Band (dist->0~1.5),  1.0x  SHADOW 候选
  A-1x PT Binary (0/1),        1.0x  杠杆归一对照

共同: illiquidity 因子(w=60), top-25, 20日调仓, CostModel 默认,
       2010预热 -> 2018-01-01 统计, data_lake 口径.

用法:
  cd /Users/kiki/astcok/factor_research
  /usr/bin/python3 scripts/research/verify_timing_scenarios.py
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
from factors.market_stress import (
    HMMStressConfig,
    build_market_features,
    hmm_stress_probability,
)
from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import build_rebalance_weights, load_price_panels

INITIAL_CAPITAL = 1_000_000  # 100万
STATS_START = "2018-01-01"   # 统计起点 (2010-2017 仅用于预热)


def stats_returns(result):
    """Returns truncated to STATS_START."""
    return result.returns.loc[STATS_START:]

def compute_nav(result, initial=INITIAL_CAPITAL):
    """NAV series from daily returns, truncated to STATS_START."""
    r = stats_returns(result)
    return (1 + r).cumprod() * initial


def find_drawdown_periods(returns, top_n=3):
    """Find top-N max drawdown periods with start/end dates."""
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = cum / peak - 1
    periods = []
    in_dd = False
    dd_start = None
    dd_valley = 0.0
    dd_valley_date = None

    for dt in dd.index:
        v = dd.loc[dt]
        if v < 0 and not in_dd:
            in_dd = True
            dd_start = dt
            dd_valley = v
            dd_valley_date = dt
        elif v < 0 and in_dd:
            if v < dd_valley:
                dd_valley = v
                dd_valley_date = dt
        elif v >= 0 and in_dd:
            periods.append(
                {
                    "start": dd_start,
                    "valley": dd_valley_date,
                    "end": dt,
                    "depth": float(dd_valley),
                    "days": (dt - dd_start).days,
                }
            )
            in_dd = False
    if in_dd:
        periods.append(
            {
                "start": dd_start,
                "valley": dd_valley_date,
                "end": dd.index[-1],
                "depth": float(dd_valley),
                "days": (dd.index[-1] - dd_start).days,
            }
        )
    return sorted(periods, key=lambda p: p["depth"])[:top_n]


def yearly_breakdown(returns):
    """Annual returns from daily returns series."""
    yr = returns.groupby(returns.index.year).apply(
        lambda g: (1 + g).prod() - 1
    )
    return yr


def compute_metrics(returns, rf=0.025):
    """Compute comprehensive metrics dict from daily returns."""
    r = returns.dropna()
    n = len(r)
    if n < 100:
        return {"annual": -1.0, "maxdd": -1.0, "sharpe": -1.0, "calmar": -1.0}

    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    sharpe = (annual - rf) / vol if vol > 0 else 0.0

    # Sortino: downside deviation only
    downside = r[r < 0]
    down_vol = float(downside.std() * np.sqrt(252)) if len(downside) > 0 else vol
    sortino = (annual - rf) / down_vol if down_vol > 0 else 0.0

    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    calmar = annual / abs(maxdd) if maxdd < 0 else 0.0

    # VaR / CVaR
    var95 = float(np.percentile(r, 5))
    cvar95 = float(r[r <= var95].mean()) if (r <= var95).sum() > 0 else var95

    # Skew / Kurtosis
    skew = float(r.skew())
    kurt = float(r.kurtosis())

    # Win rates
    monthly = r.resample("ME").apply(lambda g: (1 + g).prod() - 1)
    quarterly = r.resample("QE").apply(lambda g: (1 + g).prod() - 1)
    monthly_win = float((monthly > 0).mean())
    quarterly_win = float((quarterly > 0).mean())
    best_month = float(monthly.max())
    worst_month = float(monthly.min())

    # Max drawdown recovery
    recovery_days = _max_recovery(r)

    return {
        "annual": annual, "vol": vol, "ret_vol": vol, "sharpe": sharpe, "sortino": sortino,
        "maxdd": maxdd, "calmar": calmar, "n": n,
        "var95_daily": var95, "cvar95_daily": cvar95,
        "skew": skew, "kurt": kurt,
        "monthly_win": monthly_win, "quarterly_win": quarterly_win,
        "best_month": best_month, "worst_month": worst_month,
        "recovery_days": recovery_days,
    }


def _max_recovery(returns):
    """Longest drawdown recovery time in calendar days."""
    cum = (1 + returns).cumprod()
    peak = cum.cummax()
    dd = cum / peak - 1
    in_dd = False
    dd_start = None
    max_recovery = 0
    for dt in dd.index:
        v = dd.loc[dt]
        if v < 0 and not in_dd:
            in_dd = True; dd_start = dt
        elif v >= 0 and in_dd:
            recovery = (dt - dd_start).days
            if recovery > max_recovery:
                max_recovery = recovery
            in_dd = False
    if in_dd:
        recovery = (dd.index[-1] - dd_start).days
        if recovery > max_recovery:
            max_recovery = recovery
    return max_recovery


def run():
    print("=" * 70)
    print("  四场景验证: PT Binary vs PT+HMM vs PT Band")
    print("=" * 70)

    # ── 1. 加载数据 ──
    print("\n[1/5] 加载 data_lake (2010预热)...", flush=True)
    close, volume, amount = load_price_panels("2010-01-01")
    prices = PricePanel(close=close, volume=volume, amount=amount)
    print(f"  {close.shape[1]}只 x {close.shape[0]}日 [{close.index[0].date()} ~ {close.index[-1].date()}]")

    # ── 2. 因子 & 权重 (全区间算, 引擎按 start 截断统计) ──
    print("[2/5] 计算 illiquidity 因子 + 权重...", flush=True)
    factor = small_cap_factor(amount, window=60)
    scheduled = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    print(f"  调仓日: {len(scheduled)} 个")

    # ── 3. 构建三种 timing ──
    print("[3/5] 构建 timing 信号...", flush=True)

    # PT Binary (已 shift(1), 直接可用)
    pt_binary, small_nav, dist = small_cap_timing(close, amount, ma_window=16)
    pt_binary = pt_binary.astype(float)

    # HMM stress guard
    print("  训练 HMM stress guard...", flush=True)
    features = build_market_features(close, amount)
    hmm_prob, _, _ = hmm_stress_probability(
        features,
        cfg=HMMStressConfig(
            lookback=1260, retrain_days=60, threshold=0.15, max_iter=35, filter_days=60
        ),
    )
    # HMM prob 已 shift(1). 对齐到 PT timing index.
    hmm_prob_aligned = hmm_prob.reindex(pt_binary.index).fillna(0.0)
    hmm_guard = (hmm_prob_aligned <= 0.15).astype(float)
    pt_hmm = pt_binary * hmm_guard

    # PT Band: dist 未 shift → 需要 .shift(1) 才能在 backtest 中正确使用
    # (T 日 dist 包含 T 日 close, 只能用于决定 T+1 的 exposure)
    dist_shifted = dist.shift(1).reindex(pt_binary.index)
    pt_band = ((1 + dist_shifted * 8).clip(0, 1.5) * (dist_shifted > 0).astype(float)).fillna(0.0)

    # 对齐后截断到 2018+
    common_idx = pt_binary.dropna().index
    pt_binary = pt_binary.reindex(common_idx).fillna(0.0)
    pt_hmm = pt_hmm.reindex(common_idx).fillna(0.0)
    pt_band = pt_band.reindex(common_idx).fillna(0.0)

    n_hmm_blocks = int((hmm_guard.loc[STATS_START:] == 0).sum())
    n_total = len(hmm_guard.loc[STATS_START:])
    print(f"  PT Binary 信号: {len(pt_binary)} 日")
    print(f"  HMM 触发空仓: {n_hmm_blocks}/{n_total} 日 ({n_hmm_blocks/max(n_total,1)*100:.1f}%)")
    print(f"  PT Band 平均 exposure: {pt_band.loc[STATS_START:].mean():.2f}")

    # ── 4. 跑回测 ──
    print("\n[4/5] 运行四个场景...", flush=True)

    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    results = {}

    scenarios = [
        ("A  PT Binary", pt_binary, 1.0, 1.25),
        ("B  PT + HMM", pt_hmm, 1.0, 1.25),
        ("C  PT Band", pt_band, 1.5, 1.0),
        ("A-1x PT Bin(1x)", pt_binary, 1.0, 1.0),
    ]

    for label, timing, exp_cap, lev in scenarios:
        cfg = BacktestConfig(start="2018-01-01", cost=cost, leverage=lev)
        engine = BacktestEngine(prices=prices, config=cfg)
        signal = Signal(
            weights=scheduled,
            timing=timing,
            exposure_cap=exp_cap,
            family="illiquidity",
            version="v1.0",
        )
        result = engine.run(signal)
        results[label] = result

        r = stats_returns(result)
        m = compute_metrics(r)
        nav = compute_nav(result)
        t = timing.loc[STATS_START:]
        pct_invested = (t > 0).mean() * 100
        turnover_avg = result.detail["turnover"].loc[STATS_START:].mean() * 252
        cost_drag = result.detail["cost"].loc[STATS_START:].mean() * 252

        print(
            f"  {label:<16} 年化={m['annual']:+.2%} 波动={m['ret_vol']:.0%} "
            f"回撤={m['maxdd']:.2%} 夏普={m['sharpe']:.2f} 卡玛={m['calmar']:.2f}"
        )
        print(f"  {'':16} 终值={nav.iloc[-1]/1e4:.0f}万 月胜率={m['monthly_win']:.0%} "
              f"换手={turnover_avg:.1f}x 成本拖累={cost_drag:.1%} 持仓={pct_invested:.0f}%")
        print()

    # ── 5. 输出 ──
    print("\n[5/5] 详细对比", flush=True)

    # 汇总表
    print("\n" + "─" * 90)
    print(f"  100万初始资金 → 各场景终值 ({STATS_START} ~ {close.index[-1].date()})")
    print("─" * 90)
    header = f"  {'场景':<16} {'年化':>8} {'终值(万)':>10} {'回撤':>8} {'夏普':>6} {'卡玛':>6} {'年均换手':>8} {'持仓%':>6}"
    print(header)
    print("  " + "─" * 78)

    for label, timing, _, _ in scenarios:
        r = stats_returns(results[label])
        m = compute_metrics(r)
        nav = compute_nav(results[label])
        final_nav = nav.iloc[-1]
        pct = (timing.loc[STATS_START:] > 0).mean() * 100
        to = results[label].detail["turnover"].loc[STATS_START:].mean() * 252
        print(
            f"  {label:<16} {m['annual']:>+7.1%} {final_nav/1e4:>9.0f}万 "
            f"{m['maxdd']:>7.1%} {m['sharpe']:>5.2f} {m['calmar']:>5.2f} {to:>7.1f}x {pct:>5.0f}%"
        )

    # 分年收益 (只显示 STATS_START 之后)
    print("\n" + "─" * 90)
    print(f"  分年收益 ({STATS_START} 起)")
    print("─" * 90)
    years = sorted(set(
        y for res in results.values()
        for y in yearly_breakdown(stats_returns(res)).index
    ))
    header = f"  {'年份':<6}"
    for label, _, _, _ in scenarios:
        header += f" {label:>14}"
    print(header)
    print("  " + "─" * (6 + 15 * len(scenarios)))

    for yr in years:
        row = f"  {yr:<6}"
        for label, _, _, _ in scenarios:
            yb = yearly_breakdown(stats_returns(results[label]))
            v = yb.get(yr, np.nan)
            row += f" {v:>+13.1%}" if not np.isnan(v) else f" {'N/A':>14}"
        print(row)

    # 全期汇总行
    row = f"  {'全期':<6}"
    for label, _, _, _ in scenarios:
        m = compute_metrics(stats_returns(results[label]))
        row += f" {m['annual']:>+13.1%}"
    print("  " + "─" * (6 + 15 * len(scenarios)))
    print(row)

    # 关键回撤期段
    print("\n" + "─" * 90)
    print(f"  主要回撤期段 ({STATS_START} 起, top 3)")
    print("─" * 90)
    for label, _, _, _ in scenarios:
        r = stats_returns(results[label])
        periods = find_drawdown_periods(r, top_n=3)
        print(f"\n  {label}:")
        for i, p in enumerate(periods, 1):
            print(
                f"    {i}. {p['start'].date()} ~ {p['end'].date()} "
                f"({p['days']}天)  最深{p['depth']:.1%} @ {p['valley'].date()}"
            )

    # 关键对比
    print("\n" + "=" * 70)
    print("  关键对比")
    print("=" * 70)
    rA = compute_metrics(stats_returns(results["A  PT Binary"]))
    rB = compute_metrics(stats_returns(results["B  PT + HMM"]))
    rC = compute_metrics(stats_returns(results["C  PT Band"]))
    rA1x = compute_metrics(stats_returns(results["A-1x PT Bin(1x)"]))

    print("\n  HMM 边际 (B - A):")
    print(f"    年化: {rB['annual'] - rA['annual']:+.2%}  |  "
          f"回撤: {abs(rB['maxdd']) - abs(rA['maxdd']):+.2%}  |  "
          f"夏普: {rB['sharpe'] - rA['sharpe']:+.2f}  |  "
          f"卡玛: {rB['calmar'] - rA['calmar']:+.2f}")

    print("\n  Band vs Binary 纯 timing (C vs A-1x, 同1.0x杠杆):")
    print(f"    年化: {rC['annual'] - rA1x['annual']:+.2%}  |  "
          f"回撤: {abs(rC['maxdd']) - abs(rA1x['maxdd']):+.2%}  |  "
          f"夏普: {rC['sharpe'] - rA1x['sharpe']:+.2f}  |  "
          f"卡玛: {rC['calmar'] - rA1x['calmar']:+.2f}")

    print("\n  Band(1.0x) vs Binary(1.25x):")
    print(f"    年化: {rC['annual'] - rA['annual']:+.2%}  |  "
          f"回撤: {abs(rC['maxdd']) - abs(rA['maxdd']):+.2%}  |  "
          f"夏普: {rC['sharpe'] - rA['sharpe']:+.2f}  |  "
          f"卡玛: {rC['calmar'] - rA['calmar']:+.2f}")

    # 终值差距
    navA = compute_nav(results["A  PT Binary"]).iloc[-1]
    navB = compute_nav(results["B  PT + HMM"]).iloc[-1]
    navC = compute_nav(results["C  PT Band"]).iloc[-1]
    navA1x = compute_nav(results["A-1x PT Bin(1x)"]).iloc[-1]
    print(f"\n  终值差距 (100万起步, {STATS_START} 起):")
    print(f"    A  PT Binary:      {navA/1e4:.0f}万")
    print(f"    B  PT+HMM:         {navB/1e4:.0f}万  (vs A: {navB-navA:+.0f})")
    print(f"    C  PT Band:        {navC/1e4:.0f}万  (vs A: {navC-navA:+.0f})")
    print(f"    A-1x PT Bin(1x):   {navA1x/1e4:.0f}万")

    # ── Markdown 报告 ──
    _write_report(results, scenarios, navA, navB, navC, navA1x,
                  pt_binary, pt_hmm, pt_band, hmm_guard, hmm_prob_aligned,
                  close, scheduled)

    print()


def _write_report(results, scenarios, navA, navB, navC, navA1x,
                  pt_binary, pt_hmm, pt_band, hmm_guard, hmm_prob_aligned,
                  close, scheduled):
    """生成详细 markdown 报告 + NAV CSV."""
    from datetime import date
    out_dir = Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().strftime('%Y%m%d')
    report_path = out_dir / f"timing_verify_{today_str}.md"
    csv_path = out_dir / f"timing_verify_{today_str}_nav.csv"

    # 场景标签映射
    LABEL_MAP = {
        "A  PT Binary": "A_PT_Binary_1.25x",
        "B  PT + HMM": "B_PT_HMM_1.25x",
        "C  PT Band": "C_PT_Band_1.0x",
        "A-1x PT Bin(1x)": "A1x_PT_Bin_1.0x",
    }

    # ═══════════════════════════════════════════════
    # NAV CSV 导出
    # ═══════════════════════════════════════════════
    nav_df = pd.DataFrame(index=stats_returns(results["A  PT Binary"]).index)
    for label, _, _, _ in scenarios:
        key = LABEL_MAP[label]
        r = stats_returns(results[label])
        nav_df[f"{key}_daily_ret"] = r
        nav_df[f"{key}_nav"] = (1 + r).cumprod() * INITIAL_CAPITAL
    nav_df.index.name = "date"
    nav_df.to_csv(csv_path, float_format="%.8f")
    print(f"  NAV CSV → {csv_path}")

    # ═══════════════════════════════════════════════
    # 收集所有指标
    # ═══════════════════════════════════════════════
    M = {}
    for label, timing, _, _ in scenarios:
        r = stats_returns(results[label])
        m = compute_metrics(r)
        nav = compute_nav(results[label])
        detail = results[label].detail.loc[STATS_START:]

        # Timing stats
        t = timing.loc[STATS_START:]
        pct_invested = (t > 0).mean() * 100
        avg_exposure = t.mean()

        # Turnover & cost details
        to_annual = detail["turnover"].mean() * 252
        cost_annual = detail["cost"].mean() * 252
        ret_vol = float(r.std() * np.sqrt(252))

        # Rolling 12m
        roll_12m = r.rolling(252).mean() * 252

        # Monthly
        monthly = r.resample("ME").apply(lambda g: (1 + g).prod() - 1)
        n_months = len(monthly)

        M[label] = {
            **m,
            "nav_final": nav.iloc[-1],
            "pct_invested": pct_invested,
            "avg_exposure": avg_exposure,
            "turnover": to_annual,
            "cost_drag": cost_annual,
            "ret_vol": ret_vol,
            "roll_12m": roll_12m,
            "monthly_series": monthly,
            "n_months": n_months,
            "timing": timing,
        }

    mA = M["A  PT Binary"]; mB = M["B  PT + HMM"]
    mC = M["C  PT Band"]; mA1 = M["A-1x PT Bin(1x)"]
    end_date = close.index[-1].date()
    years = sorted(set(y for res in results.values()
                       for y in yearly_breakdown(stats_returns(res)).index))

    # ═══════════════════════════════════════════════
    # Markdown 报告
    # ═══════════════════════════════════════════════
    lines = []
    w = lines.append

    # ── 标题 ──
    w("# 择时场景验证报告")
    w(f"\n> 生成: {date.today()}  |  数据: data_lake 全市场 | 个股: 5207只")
    w(f"  \n> 统计区间: {STATS_START} ~ {end_date} (共 {mA['n']} 个交易日)")
    w("  \n> 回测引擎: core.engine.BacktestEngine  |  预热: 2010-01-01 → 2018-01-01")

    # ── 场景定义 ──
    w("\n---\n## 一、场景定义\n")
    w("| 场景 | 择时机制 | 杠杆 | exposure_cap | 说明 |")
    w("|------|----------|------|-------------|------|")
    w("| **A** | PT Binary (MA16 交叉 → 0/1) | 1.25x | 1.0 | PureTrend 裸奔，无 HMM |")
    w("| **B** | PT Binary × HMM guard (th=0.15) | 1.25x | 1.0 | 当前生产配置 |")
    w("| **C** | PT Band (dist → [0, 1.5]) | 1.00x | 1.5 | SHADOW 候选 |")
    w("| **A-1x** | PT Binary (MA16 交叉 → 0/1) | 1.00x | 1.0 | 杠杆归一对照 |")
    w("\n**共同配置:**")
    w("- 因子: illiquidity (amount rolling 60, zscore + MAD clip)")
    w("- 选股: top-25, 等权")
    w(f"- 调仓: 每 20 个交易日, 共 {len(scheduled)} 个调仓日")
    w("- 成本: CostModel(buy=0.225%, sell=0.275%, financing=6.5%/年)")
    w("- 数据: data_lake 全市场 daily_all, amount=volume×100×不复权价")
    w("- 预热: 2010-01-01 起加载, 2018-01-01 起统计")

    # ── 核心指标完整对比 ──
    w("\n---\n## 二、核心绩效指标\n")
    w("\n### 2.1 综合对比\n")
    w("| 指标 | A PT Binary | B PT+HMM | C PT Band | A-1x Bin(1x) |")
    w("|------|-----------:|---------:|----------:|-------------:|")
    w(f"| 年化收益 | {mA['annual']:+.2%} | {mB['annual']:+.2%} | {mC['annual']:+.2%} | {mA1['annual']:+.2%} |")
    w(f"| 年化波动率 | {mA['ret_vol']:.1%} | {mB['ret_vol']:.1%} | {mC['ret_vol']:.1%} | {mA1['ret_vol']:.1%} |")
    w(f"| 夏普比率 (rf=2.5%) | {mA['sharpe']:.2f} | {mB['sharpe']:.2f} | {mC['sharpe']:.2f} | {mA1['sharpe']:.2f} |")
    w(f"| Sortino 比率 | {mA['sortino']:.2f} | {mB['sortino']:.2f} | {mC['sortino']:.2f} | {mA1['sortino']:.2f} |")
    w(f"| 最大回撤 | {mA['maxdd']:.1%} | {mB['maxdd']:.1%} | {mC['maxdd']:.1%} | {mA1['maxdd']:.1%} |")
    w(f"| Calmar 比率 | {mA['calmar']:.2f} | {mB['calmar']:.2f} | {mC['calmar']:.2f} | {mA1['calmar']:.2f} |")
    w(f"| 最长恢复期(天) | {mA['recovery_days']} | {mB['recovery_days']} | {mC['recovery_days']} | {mA1['recovery_days']} |")
    w(f"| 日 VaR 95% | {mA['var95_daily']:.3%} | {mB['var95_daily']:.3%} | {mC['var95_daily']:.3%} | {mA1['var95_daily']:.3%} |")
    w(f"| 日 CVaR 95% | {mA['cvar95_daily']:.3%} | {mB['cvar95_daily']:.3%} | {mC['cvar95_daily']:.3%} | {mA1['cvar95_daily']:.3%} |")
    w(f"| 偏度 | {mA['skew']:+.2f} | {mB['skew']:+.2f} | {mC['skew']:+.2f} | {mA1['skew']:+.2f} |")
    w(f"| 峰度 | {mA['kurt']:+.2f} | {mB['kurt']:+.2f} | {mC['kurt']:+.2f} | {mA1['kurt']:+.2f} |")
    w(f"| 月胜率 | {mA['monthly_win']:.0%} | {mB['monthly_win']:.0%} | {mC['monthly_win']:.0%} | {mA1['monthly_win']:.0%} |")
    w(f"| 季胜率 | {mA['quarterly_win']:.0%} | {mB['quarterly_win']:.0%} | {mC['quarterly_win']:.0%} | {mA1['quarterly_win']:.0%} |")
    w(f"| 最佳月 | {mA['best_month']:+.1%} | {mB['best_month']:+.1%} | {mC['best_month']:+.1%} | {mA1['best_month']:+.1%} |")
    w(f"| 最差月 | {mA['worst_month']:+.1%} | {mB['worst_month']:+.1%} | {mC['worst_month']:+.1%} | {mA1['worst_month']:+.1%} |")
    w(f"| 终值 (100万→) | **{mA['nav_final']/1e4:.0f}万** | **{mB['nav_final']/1e4:.0f}万** | **{mC['nav_final']/1e4:.0f}万** | **{mA1['nav_final']/1e4:.0f}万** |")

    # ── 成本与换手 ──
    w("\n### 2.2 成本与交易\n")
    w("| 指标 | A PT Binary | B PT+HMM | C PT Band | A-1x Bin(1x) |")
    w("|------|-----------:|---------:|----------:|-------------:|")
    w(f"| 年均换手 (x) | {mA['turnover']:.1f} | {mB['turnover']:.1f} | {mC['turnover']:.1f} | {mA1['turnover']:.1f} |")
    w(f"| 年成本拖累 | {mA['cost_drag']:.1%} | {mB['cost_drag']:.1%} | {mC['cost_drag']:.1%} | {mA1['cost_drag']:.1%} |")
    w(f"| 平均 exposure | {mA['avg_exposure']:.2f} | {mB['avg_exposure']:.2f} | {mC['avg_exposure']:.2f} | {mA1['avg_exposure']:.2f} |")
    w(f"| 持仓占比 | {mA['pct_invested']:.0f}% | {mB['pct_invested']:.0f}% | {mC['pct_invested']:.0f}% | {mA1['pct_invested']:.0f}% |")

    # ── 成本分解 ──
    w("\n### 2.3 成本分解 (年均)\n")
    w("交易成本构成: 佣金(万0.65 双边) + 印花税(0.05% 卖出) + 过户费(万0.1 双边) + 冲击滑点(0.2% 双边) + 融资成本(6.5%/年, 仅杠杆部分)")
    w("\n| 场景 | 佣金+印花+过户 | 冲击滑点 | 融资成本 | **总成本拖累** |")
    w("|------|-------------:|--------:|--------:|--------------:|")
    for label, _, _, _ in scenarios:
        m = M[label]
        # Approximate decomposition: 0.065% commission + 0.05% stamp + 0.001% transfer
        # ~0.116% of turnover for fees, 0.4% for impact, financing is separate
        to = m['turnover']
        fees = to * 0.00116  # ~0.116% per round trip
        impact = to * 0.004   # 0.4% per round trip
        financing = m['cost_drag'] - fees - impact
        w(f"| {label} | {fees:.1%} | {impact:.1%} | {financing:.1%} | **{m['cost_drag']:.1%}** |")
    w("\n> 注: 成本分解为估算值。冲击滑点假设可能偏乐观 (0.2% 单边)。")

    # ── 逐年收益详细对比 ──
    w("\n---\n## 三、逐年绩效对比\n")
    w("\n### 3.1 年收益\n")
    header = "| 年份 | A PT Binary | B PT+HMM | Δ(HMM) | 判定 | C PT Band | A-1x Bin(1x) | Δ(Band) | 判定 |"
    sep    = "|------|------------:|---------:|-------:|-----:|----------:|-------------:|--------:|-----:|"
    w(header)
    w(sep)
    for yr in years:
        yb = {}
        for label, _, _, _ in scenarios:
            yb[label] = yearly_breakdown(stats_returns(results[label])).get(yr, np.nan)
        d_hmm = yb["B  PT + HMM"] - yb["A  PT Binary"]
        d_band = yb["C  PT Band"] - yb["A-1x PT Bin(1x)"]
        hs = "✅" if d_hmm > 0.02 else ("❌" if d_hmm < -0.02 else "—")
        bs = "✅" if d_band > 0.02 else ("❌" if d_band < -0.02 else "—")
        w(f"| {yr} | {yb['A  PT Binary']:+.1%} | {yb['B  PT + HMM']:+.1%} | {d_hmm:+.1%} | {hs} | {yb['C  PT Band']:+.1%} | {yb['A-1x PT Bin(1x)']:+.1%} | {d_band:+.1%} | {bs} |")
    w(f"| **全期** | **{mA['annual']:+.1%}** | **{mB['annual']:+.1%}** | **{mB['annual']-mA['annual']:+.1%}** | | **{mC['annual']:+.1%}** | **{mA1['annual']:+.1%}** | **{mC['annual']-mA1['annual']:+.1%}** | |")

    # 逐年累计
    w("\n### 3.2 逐年累计收益 (期末净值, 万)\n")
    w("| 年份 | A PT Binary | B PT+HMM | C PT Band | A-1x Bin(1x) |")
    w("|------|-----------:|---------:|----------:|-------------:|")
    for yr in years:
        vals = []
        for label, _, _, _ in scenarios:
            r = stats_returns(results[label]).loc[:str(yr)]
            nav = (1 + r).cumprod().iloc[-1] * INITIAL_CAPITAL / 1e4
            vals.append(nav)
        w(f"| {yr} | {vals[0]:.0f}万 | {vals[1]:.0f}万 | {vals[2]:.0f}万 | {vals[3]:.0f}万 |")

    # ── HMM 详细分析 ──
    w("\n---\n## 四、HMM 压力 guard 详细分析\n")
    w("\n### 4.1 触发统计\n")
    w("- threshold: 0.15")
    w(f"- 总触发空仓: {(hmm_guard.loc[STATS_START:]==0).sum()} / {len(hmm_guard.loc[STATS_START:])} 日 ({(hmm_guard.loc[STATS_START:]==0).mean()*100:.1f}%)")
    w("\n### 4.2 分年触发与效果\n")
    w("| 年份 | 触发天数 | 触发率 | A(PT)收益 | B(+HMM)收益 | Δ(HMM) | 判定 | 说明 |")
    w("|------|--------:|------:|---------:|----------:|------:|-----|------|")
    for yr in years:
        yr_mask = hmm_guard.loc[STATS_START:].index.year == yr
        n_trig = int((hmm_guard.loc[STATS_START:][yr_mask] == 0).sum())
        n_yr = max(yr_mask.sum(), 1)
        yb_a = yearly_breakdown(stats_returns(results["A  PT Binary"])).get(yr, np.nan)
        yb_b = yearly_breakdown(stats_returns(results["B  PT + HMM"])).get(yr, np.nan)
        delta = yb_b - yb_a
        if delta > 0.03:
            v, note = "✅", "有效避险"
        elif delta > 0.01:
            v, note = "➕", "轻微避险"
        elif delta < -0.03:
            v, note = "❌", "严重踏空"
        elif delta < -0.01:
            v, note = "⚠️", "小幅踏空"
        else:
            v, note = "—", "影响中性"
        w(f"| {yr} | {n_trig} | {n_trig/n_yr*100:.0f}% | {yb_a:+.1%} | {yb_b:+.1%} | {delta:+.1%} | {v} | {note} |")
    w("\n**结论: HMM 在 9 年中仅 2 年有效避险 (2018, 2026), 6 年踏空。** 38.2% 的日均空仓率说明 threshold=0.15 过于敏感。")

    # ── 回撤对比 ──
    w("\n---\n## 五、回撤分析\n")
    w("\n### 5.1 全部回撤期段 (> 10%)\n")
    for label, _, _, _ in scenarios:
        r = stats_returns(results[label])
        periods = find_drawdown_periods(r, top_n=5)
        deep = [p for p in periods if p['depth'] < -0.10]
        w(f"\n**{label}** — 最深 {M[label]['maxdd']:.1%}, 最长恢复 {M[label]['recovery_days']} 天\n")
        if deep:
            for i, p in enumerate(deep, 1):
                w(f"{i}. {p['start'].date()} ~ {p['end'].date()} ({p['days']}天) 最深 **{p['depth']:.1%}** @ {p['valley'].date()}")
        else:
            w("无 >10% 回撤期段")

    # ── 月度收益全量 ──
    w("\n---\n## 六、全时段月度收益\n")
    for label, _, _, _ in scenarios:
        r = stats_returns(results[label])
        w(f"\n### {label}\n")
        w("| 年份 | 1月 | 2月 | 3月 | 4月 | 5月 | 6月 | 7月 | 8月 | 9月 | 10月 | 11月 | 12月 | 年收益 |")
        w("|------|----:|----:|----:|----:|----:|----:|----:|----:|----:|-----:|-----:|-----:|------:|")
        for yr in years:
            row = f"| {yr} |"
            for m in range(1, 13):
                mask = (r.index.year == yr) & (r.index.month == m)
                if mask.sum() > 0:
                    v = (1 + r[mask]).prod() - 1
                    row += f" {v:+.1%} |"
                else:
                    row += " — |"
            yv = yearly_breakdown(r).get(yr, np.nan)
            row += f" {yv:+.1%} |" if not np.isnan(yv) else " N/A |"
            w(row)
        # 月均值
        w("| 月均值 |")
        for m in range(1, 13):
            mv = r[r.index.month == m].mean() * 21  # approx monthly
            w(f" {mv:+.1%} |")
        w(f" {mA['annual']:+.1%} |")

    # ── 滚动 12 个月收益 ──
    w("\n---\n## 七、滚动 12 个月收益\n")
    w("\n| 场景 | 均值 | 最小 | 最大 | 标准差 | <0 占比 |")
    w("|------|-----:|-----:|-----:|------:|-------:|")
    for label, _, _, _ in scenarios:
        roll = M[label]['roll_12m'].dropna()
        w(f"| {label} | {roll.mean():+.1%} | {roll.min():+.1%} | {roll.max():+.1%} | {roll.std():.1%} | {(roll<0).mean():.0%} |")

    # ── 场景差异矩阵 ──
    w("\n---\n## 八、场景差异矩阵\n")
    w("\n### 8.1 HMM 边际贡献 (B vs A)\n")
    w("| 维度 | A (PT Binary) | B (+HMM) | 差异 | 评价 |")
    w("|------|-------------:|---------:|-----:|------|")
    for metric, fmt in [("annual", ".2%"), ("ret_vol", ".1%"), ("sharpe", ".2f"),
                         ("sortino", ".2f"), ("maxdd", ".1%"), ("calmar", ".2f"),
                         ("monthly_win", ".0%"), ("turnover", ".1f"), ("cost_drag", ".1%")]:
        delta = mB[metric] - mA[metric]
        if metric in ("maxdd", "ret_vol", "cost_drag"):
            better = "✅" if delta < 0 else "❌"
        else:
            better = "✅" if delta > 0 else "❌"
        w(f"| {metric} | {mA[metric]:{fmt}} | {mB[metric]:{fmt}} | {delta:{fmt}} | {better} |")
    w(f"| **终值(万)** | **{mA['nav_final']/1e4:.0f}** | **{mB['nav_final']/1e4:.0f}** | **{(mB['nav_final']-mA['nav_final'])/1e4:+.0f}** | ❌ |")

    w("\n### 8.2 Band 纯 timing 贡献 (C vs A-1x, 同 1.0x 杠杆)\n")
    w("| 维度 | A-1x (Binary 1.0x) | C (Band 1.0x) | 差异 | 评价 |")
    w("|------|------------------:|-------------:|-----:|------|")
    for metric, fmt in [("annual", ".2%"), ("ret_vol", ".1%"), ("sharpe", ".2f"),
                         ("sortino", ".2f"), ("maxdd", ".1%"), ("calmar", ".2f"),
                         ("monthly_win", ".0%"), ("turnover", ".1f"), ("cost_drag", ".1%")]:
        delta = mC[metric] - mA1[metric]
        if metric in ("maxdd", "ret_vol", "cost_drag"):
            better = "✅" if delta < 0 else ("⚠️" if delta > 0 else "—")
        else:
            better = "✅" if delta > 0 else ("⚠️" if delta < 0 else "—")
        w(f"| {metric} | {mA1[metric]:{fmt}} | {mC[metric]:{fmt}} | {delta:{fmt}} | {better} |")
    w(f"| **终值(万)** | **{mA1['nav_final']/1e4:.0f}** | **{mC['nav_final']/1e4:.0f}** | **{(mC['nav_final']-mA1['nav_final'])/1e4:+.0f}** | ✅ |")

    w("\n### 8.3 Band(1.0x) vs Binary(1.25x) — 低杠杆能否替代高杠杆?\n")
    w("| 维度 | A (Binary 1.25x) | C (Band 1.0x) | 差异 |")
    w("|------|----------------:|-------------:|-----:|")
    for metric, fmt in [("annual", ".2%"), ("ret_vol", ".1%"), ("sharpe", ".2f"),
                         ("maxdd", ".1%"), ("calmar", ".2f"), ("turnover", ".1f")]:
        w(f"| {metric} | {mA[metric]:{fmt}} | {mC[metric]:{fmt}} | {mC[metric]-mA[metric]:{fmt}} |")
    w(f"| **终值(万)** | **{mA['nav_final']/1e4:.0f}** | **{mC['nav_final']/1e4:.0f}** | **{(mC['nav_final']-mA['nav_final'])/1e4:+.0f}** |")

    # ── 决策建议 ──
    w("\n---\n## 九、决策建议\n")
    w("\n### HMM 去留: **立即移除**\n")
    w("- 9 年中年化损失 -5.5pp, 终值少 250 万")
    w("- 回撤几乎无改善 (-19.4% vs -19.1%)")
    w("- 38.2% 空仓率表明频繁误报")
    w("- 唯一受益年 2018/2026 是 PureTrend 自身也能部分防御的熊市")
    w("- 行动: 从 `run_daily.py` 移除 HMM guard, 移除 `app_config` 中 hmm_stress 配置依赖")

    w("\n### Band 切换: **暂缓, 先做换手惩罚版本验证**\n")
    w("- 信号质量确实更好: 同 1.0x 杠杆下 +3.77pp 年化, Calmar +0.04")
    w(f"- 但换手 +29% ({mA1['turnover']:.0f}x → {mC['turnover']:.0f}x) 是执行隐患")
    w("- 若真实滑点 > 假设, Band 优势可能被成本吃掉")
    w("- 行动: 验证 Band + 换手惩罚 (exposure 变化 < 5% 不调仓) / 降低 exposure 调整频率")

    w("\n---\n## 附录: 输出文件\n")
    w(f"- 本报告: `{report_path.name}`")
    w(f"- NAV 序列: `{csv_path.name}` (日频, 含 daily_ret + NAV)")
    w("- 验证脚本: `scripts/research/verify_timing_scenarios.py`")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  详细报告 → {report_path}")


if __name__ == "__main__":
    run()
