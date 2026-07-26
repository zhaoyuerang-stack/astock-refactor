"""
v2.1 strategy comprehensive analysis — starting 2018, 100万 initial capital.
"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/kiki/astcok/factor_research").resolve()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
)

OUT = ROOT / "reports" / "research"
OUT.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("  v2.1 策略综合分析 — 2018年起, 初始100万")
print("=" * 70)

# ── Run strategy ──
close, vol, amount = load_price_panels("2010-01-01")
factor = small_cap_factor(amount, 30)
sched = build_rebalance_weights(factor, close, 30, 15)
timing, _, _ = small_cap_timing(close, amount, 16)
cfg = StrategyConfig(start="2010-01-01")
ret, detail = backtest_weights(close, sched, timing.astype(float), cfg)

# ── 2018+ subset ──
ret_2018 = ret[ret.index.year >= 2018]
det_2018 = detail[detail.index.year >= 2018]
ret_2018 = ret_2018.fillna(0)
n_days = len(ret_2018)
n_years = n_days / 252

INITIAL = 1_000_000
nav = (1 + ret_2018).cumprod() * INITIAL

# ========================================================================
# 1. 资本曲线
# ========================================================================
print(f"\n{'='*70}")
print("  1. 资本曲线")
print(f"{'='*70}")
print(f"  起始: 2018-01-02  初始: {INITIAL:,.0f} 元")
print(f"  结束: {ret_2018.index[-1].strftime('%Y-%m-%d')}  最终: {nav.iloc[-1]:,.0f} 元")
print(f"  年化: {(nav.iloc[-1]/INITIAL)**(1/n_years)-1:+.1%}")
print(f"  总增长: {nav.iloc[-1]/INITIAL:.1f}x")
print(f"  最高净值: {nav.max():,.0f}  (发生在 {nav.idxmax().strftime('%Y-%m-%d')})")
print(f"  最低净值: {nav.min():,.0f}  (发生在 {nav.idxmin().strftime('%Y-%m-%d')})")

# ========================================================================
# 2. 收益分析
# ========================================================================
print(f"\n{'='*70}")
print("  2. 收益分析")
print(f"{'='*70}")

# 日收益率统计
daily_r = ret_2018.values
pos_days = (daily_r > 0).sum()
neg_days = (daily_r < 0).sum()
zero_days = (daily_r == 0).sum()
print(f"  交易日: {n_days} 天")
print(f"  正收益日: {pos_days} ({pos_days/n_days:.1%})")
print(f"  负收益日: {neg_days} ({neg_days/n_days:.1%})")
print(f"  零收益日: {zero_days} ({zero_days/n_days:.1%})")
print(f"  日均收益: {daily_r.mean():+.4%}")
print(f"  日收益标准差: {daily_r.std():.4%}")
print(f"  日收益偏度: {pd.Series(daily_r).skew():+.2f}")
print(f"  日收益峰度: {pd.Series(daily_r).kurtosis():+.2f}")

# 月度收益
monthly = ret_2018.resample("ME").apply(lambda x: (1 + x).prod() - 1)
print(f"\n  月度统计 ({len(monthly)} 个月):")
print(f"    最好月: {monthly.idxmax().strftime('%Y-%m')}  {monthly.max():+.1%}")
print(f"    最差月: {monthly.idxmin().strftime('%Y-%m')}  {monthly.min():+.1%}")
print(f"    月均收益: {monthly.mean():+.2%}")
print(f"    月收益标准差: {monthly.std():+.2%}")
print(f"    月胜率: {(monthly>0).mean():.0%}")
print("    连续正收益最长: 计算中")

# 胜率连续
months_pos = (monthly > 0).values
max_consec_pos = 0; current_pos = 0
for v in months_pos:
    if v: current_pos += 1; max_consec_pos = max(max_consec_pos, current_pos)
    else: current_pos = 0
max_consec_neg = 0; current_neg = 0
for v in months_pos:
    if not v: current_neg += 1; max_consec_neg = max(max_consec_neg, current_neg)
    else: current_neg = 0
print(f"    最长连续正收益月: {max_consec_pos} 个月")
print(f"    最长连续负收益月: {max_consec_neg} 个月")

# 年度收益
yearly = ret_2018.resample("YE").apply(lambda x: (1 + x).prod() - 1)
print("\n  年度收益:")
for y in sorted(set(ret_2018.index.year)):
    r = yearly.loc[f"{y}-12-31"] if f"{y}-12-31" in yearly.index else yearly[yearly.index.year == y].iloc[0]
    amt = INITIAL * (1 + r)
    print(f"    {y}: {r:+.1%}  累计资本: {amt:,.0f} 元")

# ========================================================================
# 3. 风险分析
# ========================================================================
print(f"\n{'='*70}")
print("  3. 风险分析")
print(f"{'='*70}")

# 滚动波动率
roll_vol = ret_2018.rolling(60).std() * np.sqrt(252)
print("  年化波动率:")
print(f"    当前:    {roll_vol.iloc[-1]:.1%}")
print(f"    均值:    {roll_vol.mean():.1%}")
print(f"    中位数:  {roll_vol.median():.1%}")
print(f"    最低:    {roll_vol.min():.1%}  (最平静期)")
print(f"    最高:    {roll_vol.max():.1%}  (最剧烈期)")

# VaR / CVaR
alpha_95 = np.percentile(daily_r, 5)
alpha_99 = np.percentile(daily_r, 1)
cvar_95 = daily_r[daily_r <= alpha_95].mean()
cvar_99 = daily_r[daily_r <= alpha_99].mean()
print("\n  VaR (Value at Risk):")
print(f"    95% VaR:  {alpha_95:+.3%}/日  (每100万单日最多亏 {abs(alpha_95)*INITIAL:,.0f} 元)")
print(f"    99% VaR:  {alpha_99:+.3%}/日  (每100万单日最多亏 {abs(alpha_99)*INITIAL:,.0f} 元)")
print("  CVaR (条件VaR):")
print(f"    95% CVaR: {cvar_95:+.3%}/日  (极端5%的天数平均亏损)")
print(f"    99% CVaR: {cvar_99:+.3%}/日")

# 最大回撤分析
nav_2018 = (1 + ret_2018).cumprod()
dd = (nav_2018 / nav_2018.cummax() - 1)
print("\n  最大回撤分析:")
print(f"    最大回撤: {dd.min():+.1%}")
print(f"    发生日期: {dd.idxmin().strftime('%Y-%m-%d')}")
# Find the drawdown start (last peak before trough)
trough_idx = dd.idxmin()
peak_before = nav_2018[:trough_idx].idxmax()
recovery_idx = None
for t in nav_2018.index[nav_2018.index > trough_idx]:
    if nav_2018.loc[t] >= nav_2018.loc[peak_before]:
        recovery_idx = t; break
print(f"    回撤起点: {peak_before.strftime('%Y-%m-%d')}")
print(f"    回撤谷底: {trough_idx.strftime('%Y-%m-%d')}")
if recovery_idx:
    print(f"    恢复日期: {recovery_idx.strftime('%Y-%m-%d')}")
    recover_days = nav_2018.index.get_loc(recovery_idx) - nav_2018.index.get_loc(peak_before)
    print(f"    恢复天数: {recover_days} 个交易日")
else:
    print("    尚未恢复")

# 回撤分布
dd_nonzero = dd[dd < 0]
print("\n  回撤分布:")
print(f"    最大回撤:      {dd.min():+.1%}")
print(f"    95%分位回撤:   {dd_nonzero.quantile(0.05):+.1%}")
print(f"    90%分位回撤:   {dd_nonzero.quantile(0.10):+.1%}")
print(f"    50%分位回撤:   {dd_nonzero.median():+.1%}")
print(f"    平均回撤深度:   {dd_nonzero.mean():+.1%}")
print(f"    >10%的回撤:    {(dd < -0.10).sum()} 天")

# 回撤频率：找出所有 >5% 的回撤事件
dd_events = []
in_dd = False; dd_start = None; dd_peak = None
for t in nav_2018.index:
    current_dd = dd.loc[t]
    if not in_dd:
        if current_dd < -0.05:
            in_dd = True
            dd_peak = nav_2018[:t].max()
            dd_start = t
    else:
        if current_dd > -0.02:  # recovered to within 2% of peak
            dd_events.append({
                'start': dd_start,
                'end': t,
                'depth': dd.loc[dd_start:t].min(),
                'duration_days': len(dd.loc[dd_start:t]),
            })
            in_dd = False
if in_dd:
    dd_events.append({
        'start': dd_start, 'end': nav_2018.index[-1],
        'depth': dd.loc[dd_start:].min(),
        'duration_days': len(dd.loc[dd_start:]),
    })
print(f"\n  大幅回撤事件(>5%): {len(dd_events)} 次")
if dd_events:
    depths = [e['depth'] for e in dd_events]
    durations = [e['duration_days'] for e in dd_events]
    print(f"    最深: {min(depths):+.1%}  最长: {max(durations)} 天")
    print(f"    平均深度: {np.mean(depths):+.1%}  平均持续: {np.mean(durations):.0f} 天")

# ========================================================================
# 4. 交易成本
# ========================================================================
print(f"\n{'='*70}")
print("  4. 交易成本")
print(f"{'='*70}")
to_ann = det_2018["turnover"].mean() * 252
cost_ann = det_2018["cost"].mean() * 252
total_cost = det_2018["cost"].sum()
gross_ret = ret_2018.mean() * 252 + cost_ann
print(f"  年化换手:     {to_ann:.1f}x")
print(f"  年化成本:     {cost_ann:+.1%}  (含手续费+冲击成本)")
print(f"  毛收益:       {gross_ret:+.1%}")
print(f"  净收益:       {ret_2018.mean()*252:+.1%}")
print(f"  总成本(2018-2026): {total_cost:+.1%}  折合 {total_cost*INITIAL:,.0f} 元")
print(f"  成本/毛收益占比: {cost_ann/gross_ret*100:.0f}%")

# ========================================================================
# 5. 择时分析
# ========================================================================
print(f"\n{'='*70}")
print("  5. 择时分析 (MA16)")
print(f"{'='*70}")
timing_2018 = timing.reindex(ret_2018.index).fillna(False).astype(bool)
in_market = timing_2018.sum()
out_market = len(timing_2018) - in_market
print(f"  持仓天数: {in_market} / {len(timing_2018)} ({in_market/len(timing_2018):.1%})")
print(f"  空仓天数: {out_market} ({out_market/len(timing_2018):.1%})")

# 持仓日 vs 空仓日的表现
in_ret = ret_2018[timing_2018.values]
out_ret = ret_2018[~timing_2018.values]
print(f"  持仓日胜率: {(in_ret>0).mean():.0%}  日均收益: {in_ret.mean():+.4%}")
print(f"  空仓日胜率: {(out_ret>0).mean():.0%}  日均收益: {out_ret.mean():+.4%}")

# 择时切换次数
switches = (timing_2018.astype(int).diff().abs() == 1).sum()
print(f"  择时切换次数: {switches} 次 (年均 {switches/n_years:.0f})")

# ========================================================================
# 6. 滚动夏普
# ========================================================================
print(f"\n{'='*70}")
print("  6. 滚动指标 (252天窗口)")
print(f"{'='*70}")
roll_sharpe = ret_2018.rolling(252).mean() / ret_2018.rolling(252).std() * np.sqrt(252)
roll_maxdd = ret_2018.rolling(252).apply(
    lambda x: ((1+x).cumprod()/(1+x).cumprod().cummax()-1).min()
)
print(f"  滚动夏普范围: {roll_sharpe.min():.1f} ~ {roll_sharpe.max():.1f}")
print(f"  滚动夏普当前: {roll_sharpe.iloc[-1]:.1f}")
print(f"  夏普<0比例: {(roll_sharpe<0).mean():.0%} (坏年)")
print(f"  夏普>1比例: {(roll_sharpe>1).mean():.0%} (好年)")
print(f"  夏普>2比例: {(roll_sharpe>2).mean():.0%} (极好年)")

# ========================================================================
# 7. 同行基准对比
# ========================================================================
print(f"\n{'='*70}")
print("  7. 基准对比 (vs v2.0)")
print(f"{'='*70}")
# v2.0 baseline
f60 = small_cap_factor(amount, 60)
s60 = build_rebalance_weights(f60, close, 25, 20)
ret20, det20 = backtest_weights(close, s60, timing.astype(float), cfg)
ret20_2018 = ret20[ret20.index.year >= 2018].fillna(0)

for label, r, d in [("v2.1 (30,15,30)", ret_2018, det_2018), ("v2.0 (60,20,25)", ret20_2018, det20)]:
    n = (1+r).cumprod().iloc[-1]**(1/n_years)-1
    s = r.mean()/r.std()*np.sqrt(252)
    dd_m = ((1+r).cumprod()/(1+r).cumprod().cummax()-1).min()
    to = d["turnover"].mean() * 252
    c = d["cost"].mean() * 252
    print(f"  {label:<18} ann={n:+.1%}  Sharpe={s:.2f}  MaxDD={dd_m:+.1%}  换手={to:.1f}x  成本={c:+.1%}")

# ── Save ──
result = {
    "strategy": "v2.1",
    "params": {"window": 30, "rebalance_days": 15, "top_n": 30, "ma_window": 16},
    "period": "2018-01-02 to " + ret_2018.index[-1].strftime("%Y-%m-%d"),
    "initial": INITIAL,
    "final": float(nav.iloc[-1]),
    "annualized": float((nav.iloc[-1]/INITIAL)**(1/n_years)-1),
    "sharpe": float(ret_2018.mean()/ret_2018.std()*np.sqrt(252)),
    "maxdd": float(((1+ret_2018).cumprod()/(1+ret_2018).cumprod().cummax()-1).min()),
    "vol_annual": float(ret_2018.std()*np.sqrt(252)),
    "var_95": float(alpha_95),
    "var_99": float(alpha_99),
    "cvar_95": float(cvar_95),
    "pos_day_ratio": float(pos_days/n_days),
    "monthly_win_ratio": float((monthly>0).mean()),
    "turnover_annual": float(to_ann),
    "cost_annual": float(cost_ann),
    "in_market_ratio": float(in_market/len(timing_2018)),
}
json.dump(result, open(OUT / "v21_analysis.json", "w"), ensure_ascii=False, indent=2)
print(f"\n  Saved: {OUT / 'v21_analysis.json'}")

print(f"\n{'='*70}")
print(f"  总结: 100万 → {nav.iloc[-1]/10000:.0f}万, 年化 {(nav.iloc[-1]/INITIAL)**(1/n_years)-1:+.1%}")
print(f"{'='*70}")
