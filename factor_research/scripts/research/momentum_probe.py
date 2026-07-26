"""时序动量/趋势原型:动量/趋势选股 + 全市场趋势择时,验证是否与 v2.0 正交且危机反向。
关键看:① 绩效能否过入册线 ② 与 v2.0 相关性 ③ 2018(v2.0 命门年)是否避险/反向。
预热从 2010 加载、切 2018 统计。用法: /usr/bin/python3 -m scripts.research.momentum_probe
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from engine.metrics import metrics, yearly_returns
from factors.utils import mad_clip, safe_zscore
from strategies.small_cap import (
    StrategyConfig,
    backtest_weights,
    build_rebalance_weights,
    load_price_panels,
    run_small_cap_strategy,
)

close, volume, amount = load_price_panels("2010-01-01")
dret = close.pct_change(fill_method=None)
cfg = StrategyConfig(start="2010-01-01")

# 全市场趋势择时(危机反向的关键:全市场跌破 200 日趋势 → 空仓)
mkt_nav = (1 + dret.mean(axis=1).fillna(0)).cumprod()
trend_on = (mkt_nav > mkt_nav.rolling(200).mean()).shift(1, fill_value=False).astype(float)

v2 = run_small_cap_strategy(cfg)["returns"]
v2_is = v2[v2.index.year >= 2018]


def run_mom(factor, timing, label):
    w = build_rebalance_weights(factor, close, 25, 20)
    r, _ = backtest_weights(close, w, timing, cfg)
    r = r[r.index.year >= 2018]
    m = metrics(r)
    corr = r.corr(v2_is.reindex(r.index))
    print(f"  {label:<26} 年化{m['annual']:+6.1%} 回撤{m['maxdd']:+6.1%} 夏普{m['sharpe']:5.2f} | corr(v2)={corr:+.2f}")
    return r


m_is = metrics(v2_is)
print("=== 时序动量/趋势原型 (2018-2026, 与 v2.0 对照) ===")
print(f"  {'[v2.0 baseline]':<26} 年化{m_is['annual']:+6.1%} 回撤{m_is['maxdd']:+6.1%} 夏普{m_is['sharpe']:5.2f} | corr(v2)=+1.00")

mom120 = safe_zscore(mad_clip(close.pct_change(120, fill_method=None).shift(20)))
trend = safe_zscore(mad_clip(close / close.rolling(120).mean() - 1))

print("\n[无择时]")
run_mom(mom120, None, "120日动量")
run_mom(trend, None, "趋势(价/120MA)")
print("\n[配全市场趋势择时 = 危机反向]")
rm = run_mom(mom120, trend_on, "120日动量+趋势择时")
rt = run_mom(trend, trend_on, "趋势+趋势择时")

print("\n=== 危机年对照(v2.0 命门:2018 亏、2015 疯牛) ===")
for r, lab in [(v2, "v2.0"), (rm, "动量+择时"), (rt, "趋势+择时")]:
    yr = yearly_returns(r)
    print(f"  {lab:<12} 2018:{yr.get(2018, float('nan')):+6.1%}  2015:{yr.get(2015, float('nan')):+7.1%}  2022:{yr.get(2022, float('nan')):+6.1%}")
