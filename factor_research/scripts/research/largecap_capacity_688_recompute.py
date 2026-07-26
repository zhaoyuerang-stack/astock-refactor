import sys; sys.path.insert(0,"/Users/kiki/astcok/factor_research")
from factors.large_cap import build_large_cap_premium_factor, load_clean_panels_with_growth
from strategies.small_cap import build_rebalance_weights

panels = load_clean_panels_with_growth()
close, amount = panels["close"], panels["amount"]

def holdings_and_cap(amt, label):
    p = dict(panels); p["amount"] = amt
    factor, univ = build_large_cap_premium_factor(p, universe_size=200, w_cpv_max=0.0)
    sched = build_rebalance_weights(factor, close, top_n=25, rebalance_days=20)
    adv = amt.rolling(20).mean().shift(1)
    last_rd = sorted(sched.keys())[-1]
    hold = sched[last_rd]
    star = [c for c in hold.index if str(c).startswith("688")]
    # 容量:binding = min_i(5% × ADV_i / weight_i);weight=1/25
    advs = adv.loc[last_rd].reindex(hold.index)
    cap_pv = (0.05 * advs / hold).min()   # 元
    binding = (0.05 * advs / hold).idxmin()
    print(f"  [{label}] {str(last_rd)[:10]} 持仓25: 含688 {len(star)}只{star[:4]}")
    print(f"     容量(binding ADV): {cap_pv/1e8:.2f}亿 (binding={binding}, ADV={adv.loc[last_rd,binding]/1e8:.3f}亿)")
    return set(hold.index), star, cap_pv

print("=== large-cap 688 修复连带重算 ===")
c_set, c_star, c_cap = holdings_and_cap(amount, "修复后(正确)")
amt_inflated = amount.copy()
star_cols = [c for c in amount.columns if str(c).startswith("688")]
amt_inflated[star_cols] = amt_inflated[star_cols] * 100
i_set, i_star, i_cap = holdings_and_cap(amt_inflated, "修复前(688×100虚高)")
print(f"\n  持仓重叠: {len(c_set & i_set)}/25 | 修复前后被换掉: {len(i_set - c_set)} 只")
print(f"  容量变化: 虚高 {i_cap/1e8:.2f}亿 → 正确 {c_cap/1e8:.2f}亿 ({(c_cap/i_cap-1)*100:+.0f}%)")
