"""Alpha Audit 真实数据 demo:审 fund_mom 的 revenue_yoy 成分对量价 base 的增量。

机制现已抽成 host/market-agnostic 模块 `research_toolkit.alpha_audit`(NW 重叠校正
+ RidgeCV 联合增量 + 置换 + 四判决);本脚本只做**本仓库量价口径**的数据接入与展示,
是把通用审计模块接到 A股全市场/2018-2026 的实证 demo(借机制不照搬结论:本地重算)。

Run:
    cd factor_research && python3 scripts/research/alpha_audit_fund_mom.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HORIZON = 20  # fund_mom 调仓 20d
START = "2018-01-01"


def main():
    from factors.fundamental import revenue_yoy
    from factors.momentum import illiquidity, mom_n, vol_ratio, volatility
    from factors.small_cap import small_cap_factor
    from research_toolkit import audit_factor, corrected_icir
    from services.actions.autoresearch import _load_validation_data

    close, volume, amount, _ = _load_validation_data(START)
    fwd = close.pct_change(HORIZON, fill_method=None).shift(-HORIZON)

    # 量价 base 池(= 在册量价因子族)
    base = {
        "small_cap": small_cap_factor(amount, 60),
        "illiquidity": illiquidity(close, volume, 20),
        "momentum60": mom_n(close, 60),
        "volatility20": volatility(close, 20),
        "volume_ratio": vol_ratio(volume, 5, 20),
    }
    rev = revenue_yoy(close)

    print("==== Alpha Audit demo: revenue_yoy vs 量价 base(全市场/2018-2026)====\n")
    print("【NW 重叠校正】horizon=20,看 raw ICIR 是否虚高")
    print(f"  {'因子':<16}{'raw':>8}{'nonoverlap':>12}{'nw':>8}  虚高(raw/nw)")
    for name, p in [("revenue_yoy", rev), ("momentum60", base["momentum60"])]:
        r = corrected_icir(p, fwd, horizon=HORIZON)
        infl = r["raw_icir"] / r["nw_icir"] if r["nw_icir"] > 1e-6 else float("inf")
        print(f"  {name:<16}{r['raw_icir']:>8.3f}{r['nonoverlap_icir']:>12.3f}{r['nw_icir']:>8.3f}  {infl:>6.1f}x")

    rep = audit_factor(rev, fwd, base, candidate_id="revenue_yoy", horizon=HORIZON)
    print("\n【RidgeCV 联合增量 + 置换 + 四判决】")
    print(f"  表面增量 {rep.surface_increment:+.4f} | 置换增量 {rep.permuted_increment:+.4f} | "
          f"真增量 {rep.true_increment:+.4f}")
    print(f"  NW ICIR {rep.nw_icir:+.3f}(raw {rep.raw_icir:+.3f})")
    print(f"\n==== 判决: {rep.verdict.value.upper()} ====")
    if rep.notes:
        print("  " + " / ".join(rep.notes))
    print(f"  含义: revenue_yoy 对量价 base "
          f"{'冗余/price-in(对 book 无增量;但作为 fund_mom 独立成分另测)' if rep.verdict.value != 'real' else '有真增量'}")


if __name__ == "__main__":
    main()
