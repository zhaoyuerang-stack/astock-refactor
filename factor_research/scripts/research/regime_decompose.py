"""v2.0 干净口径(修复 amount 后)总评:年度 / 剔极端年三段(含波动) / 双轨目标评估 / 净值波动图。
走 run_small_cap_strategy(确定用改过的 load_price_panels)。
用法(cwd=factor_research): /usr/bin/python3 -m scripts.research.regime_decompose
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402
import numpy as np  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from engine.metrics import metrics, yearly_returns
from strategies.small_cap import StrategyConfig, run_small_cap_strategy

ret = run_small_cap_strategy(StrategyConfig(start="2010-01-01"))["returns"]

print("=== v2.0 干净口径 年度收益 ===")
extreme = []
for y, r in yearly_returns(ret).items():
    if abs(r) > 0.5:
        extreme.append(int(y))
    print(f"  {int(y)}: {r:+7.1%}" + ("   <- 极端" if abs(r) > 0.5 else ""))


def seg(r, label):
    m = metrics(r)
    vol = float(r.std() * np.sqrt(252))
    print(f"  {label:<20} 年化{m['annual']:+6.1%} 波动{vol:5.1%} 回撤{m['maxdd']:+6.1%} "
          f"夏普{m['sharpe']:5.2f} 卡玛{m.get('calmar', 0):5.2f}")


print(f"\n=== 三段 × 剔除极端年 {extreme}(剔后回撤/波动仅供参考)===")
for label, y0 in [("样本内2018-2026", 2018), ("压力2010-2026", 2010)]:
    r = ret[ret.index.year >= y0]
    print(f"\n[{label}]")
    seg(r, "全样本")
    seg(r[r.index.year != 2025], "剔除2025")
    seg(r[~r.index.year.isin(extreme)], "剔除全部极端年")

# ── 双轨目标评估 ──
print("\n=== 双轨目标评估(满意线 年化≥20%&夏普≥1.0 / 卓越线 年化≥28%或卡玛≥1.6)===")
for label, y0 in [("样本内2018-2026", 2018), ("压力2010-2026", 2010)]:
    m = metrics(ret[ret.index.year >= y0])
    sat = m["annual"] >= 0.20 and m["sharpe"] >= 1.0
    exc = m["annual"] >= 0.28 or m.get("calmar", 0) >= 1.6
    print(f"  [{label}] 年化{m['annual']:.1%}/夏普{m['sharpe']:.2f}/卡玛{m.get('calmar',0):.2f}"
          f" → 满意线{'✅' if sat else '❌'}  卓越线{'✅' if exc else '❌'}")

# ── 图 ──
nav = (1 + ret).cumprod()
dd = nav / nav.cummax() - 1
rv = ret.rolling(60).std() * np.sqrt(252)
fig, ax = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
ax[0].plot(nav.index, nav.values, lw=1)
ax[0].set_yscale("log")
ax[0].set_title("v2.0 (clean amount) NAV log 2010-2026")
ax[0].grid(alpha=.3)
ax[1].fill_between(dd.index, dd.values, 0, color="crimson", alpha=.4)
ax[1].set_title("Drawdown")
ax[1].grid(alpha=.3)
ax[2].plot(rv.index, rv.values, color="purple", lw=1)
ax[2].axhline(float(rv.mean()), color="gray", ls="--", lw=.8, label=f"mean {float(rv.mean()):.0%}")
ax[2].set_title("Rolling 60d vol")
ax[2].legend()
ax[2].grid(alpha=.3)
plt.tight_layout()
Path("reports").mkdir(exist_ok=True)
plt.savefig("reports/v2_clean_diagnostics.png", dpi=90)
print("\n图: reports/v2_clean_diagnostics.png")
