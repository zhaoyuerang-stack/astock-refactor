"""
v2.1 实盘信号检查 — 每日盘后运行，输出下一个交易日的买卖清单。

信号逻辑（WF 验证）:
  gate: 小盘指数 > MA16                  (small_cap_timing, ma_window=16)
  in_market = gate                       (v2.1 仅用 MA16, 无 PT2)

v2.1 参数:
  size_window = 30, rebalance_days = 15, top_n = 30
  timing_ma = 16

用法:
  python3 scripts/ops/signal_check.py              # 今日信号 + 持仓列表
  python3 scripts/ops/signal_check.py --history 5  # 最近5天信号对比
  python3 scripts/ops/signal_check.py --no-holdings # 只看信号，不显示持仓
  python3 scripts/ops/signal_check.py --monthly     # 当月复盘
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from factors.small_cap import small_cap_factor, small_cap_timing
from strategies.small_cap import load_price_panels

# ─── v2.1 参数 ───
SIZE_WINDOW = 30       # sw=30, 捕捉近期流动性萎缩
REBAL_DAYS = 15        # reb=15, 匹配3周筹码周期
TOP_N = 30             # top=30, 降低集中度
TIMING_MA = 16         # MA16 择时
CAPITAL = 1_000_000    # 默认资金
START = "2010-01-01"
STRATEGY_VERSION = "v2.1"


def compute_signal(close, amount):
    """计算 v2.1 信号: 仅 MA16 门"""
    last = close.index[-1]
    timing, _, dist = small_cap_timing(close, amount, TIMING_MA)
    ma16_ok = bool(timing.loc[last])
    dist_val = float(dist.loc[last])
    return {
        "last": last,
        "ma16_ok": ma16_ok,
        "ma16_dist": dist_val,
        "in_market": ma16_ok,
        "version": STRATEGY_VERSION,
    }


def get_holdings(close, amount, last):
    """v2.1: sw=30, top=30, 调仓日=T日(每15个交易日)"""
    factor = small_cap_factor(amount, SIZE_WINDOW)
    f = factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    return f.reindex(active).dropna().nlargest(TOP_N).index.tolist()


def print_signal(sig, close, amount, show_holdings=True):
    last = sig["last"]
    in_market = sig["in_market"]
    per_stock = CAPITAL // TOP_N

    print()
    print("=" * 62)
    print(f"  v2.1 信号日期: {last.date()}  (sw=30, reb=15d, top=30)")
    print("=" * 62)
    print(f"  MA16 择时   : {'✅ 持仓' if sig['ma16_ok'] else '❌ 空仓'}  "
          f"小盘指数 vs MA{TIMING_MA} = {sig['ma16_dist']:+.2%}")
    print(f"  决策        : {'✅ 持仓' if in_market else '❌ 空仓'}")
    print()

    if in_market:
        # 检查是否是调仓日
        idx_pos = close.index.get_loc(last)
        prev_rebal = idx_pos - (idx_pos % REBAL_DAYS)
        is_rebal = (idx_pos - prev_rebal) < 3  # 调仓日±2天
        print(f"  📋 明日操作: 买入 {TOP_N} 只 (每只 {per_stock:,} 元)" +
              ("  [调仓窗口]" if is_rebal else "  [维持原持仓]"))
    else:
        print("  💰 明日操作: 清仓 → 资金转场内货基 / 逆回购")

    if in_market and show_holdings:
        holdings = get_holdings(close, amount, last)
        print(f"\n  持仓列表 ({len(holdings)} 只, 等权, 每只 {per_stock:,} 元):")
        print(f"  {'#':>3}  {'代码':<8} {'当日收盘':>9} {'买入股数(100股整)':>16} {'金额':>10}")
        print(f"  {'─'*52}")
        for i, code in enumerate(holdings):
            price = close.loc[last, code] if code in close.columns else float("nan")
            shares = int(per_stock / price / 100) * 100 if price > 0 else 0
            amt = shares * price
            print(f"  {i+1:>3}. {code}  ¥{price:>7.2f}  {shares:>13,} 股  ¥{amt:>8,.0f}")
    print("=" * 62)


def print_history(close, amount, days=5):
    timing, _, _ = small_cap_timing(close, amount, TIMING_MA)
    recent = close.index[-days:]
    print()
    print(f"  {'日期':<12} {'MA16':^8} {'信号':^8} {'变化'}")
    print(f"  {'─' * 40}")
    prev_signal = None
    for dt in recent:
        ma_ok = bool(timing.loc[dt])
        in_mkt = ma_ok
        signal_str = "✅持仓" if in_mkt else "❌空仓"
        changed = "⚠️ 切换!" if (prev_signal is not None and in_mkt != prev_signal) else ""
        print(f"  {str(dt.date()):<12} {'✅' if ma_ok else '❌':^8} {signal_str:^8} {changed}")
        prev_signal = in_mkt
    print()


def print_monthly_review(close, amount):
    timing, _, _ = small_cap_timing(close, amount, TIMING_MA)
    in_mkt = timing.astype(int)
    last = close.index[-1]
    month_mask = (close.index.year == last.year) & (close.index.month == last.month)
    month_data = in_mkt[month_mask]
    switches = int((month_data.diff().abs() == 1).sum())
    hold_days = int(month_data.sum())
    total_days = len(month_data)
    print()
    print(f"  === {last.year}-{last.month:02d} v2.1 当月复盘 ===")
    print(f"  交易日: {total_days}  持仓: {hold_days}d  空仓: {total_days-hold_days}d  切换: {switches}次")
    print()


def main():
    ap = argparse.ArgumentParser(description=f"v2.1 ({STRATEGY_VERSION}) 实盘信号检查")
    ap.add_argument("--history", type=int, default=0, metavar="N", help="显示最近N天信号")
    ap.add_argument("--monthly", action="store_true", help="当月复盘")
    ap.add_argument("--no-holdings", action="store_true", help="不显示持仓列表")
    args = ap.parse_args()

    print(f"加载数据 (v2.1: sw={SIZE_WINDOW}, reb={REBAL_DAYS}d, top={TOP_N})...",
          end=" ", flush=True)
    close, _, amount = load_price_panels(START)
    print("完成")

    if args.history > 0:
        print_history(close, amount, args.history)
    elif args.monthly:
        print_monthly_review(close, amount)
    else:
        sig = compute_signal(close, amount)
        print_signal(sig, close, amount, show_holdings=not args.no_holdings)


if __name__ == "__main__":
    main()
