"""跨资产防御腿搜索——边际适应度的跨资产对应物。

架构说明:AutoResearch DSL 是**截面选股因子**的进化(L0 IC scan + top-N);
跨资产趋势腿是**单资产择时策略**,是另一种 artifact,不属于那个 DSL 白名单。
统一层是**目标函数(对在册组合的边际贡献)**,不是 DSL。本搜索把跨资产腿
纳入与股票搜索同一套边际/去相关/防御透镜。

修正 cross_asset_audit.py 的缺陷:它按**单独 Sharpe≥0.95** 筛 ETF 候选——
正是用户要摆脱的单策略最大化思维。防御腿单独 Sharpe 低但边际价值高
(去相关+逆风为正)。本搜索按**边际贡献**排序,不按单独 Sharpe。

Run:
    cd factor_research && python3 scripts/research/cross_asset_leg_search.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

START = "2018-01-01"


def main():
    # 核心搜索逻辑下沉到 portfolio 层(常规发现流程共用),本脚本只做研究展示
    from portfolio.cross_asset import search_cross_asset_legs

    res = search_cross_asset_legs(start=START)
    mb, rows = res["baseline"], res["legs"]
    print(f"在册 ACTIVE 基线 risk_parity: sh={mb['sharpe']:+.2f} cal={mb['calmar']:+.2f} "
          f"mdd={mb['maxdd']:+.1%} ann={mb['annual']:+.1%}\n")
    for r in rows:
        r["sharpe"] = r["standalone_sharpe"]  # 别名,兼容下方展示
    print(f"{'腿':<20s}{'单Sh':>6s}{'对在册相关':>9s}{'2018':>8s}{'崩盘日':>8s}{'Δsh':>7s}{'Δcal':>7s}")
    print("-" * 72)
    for r in rows:
        gate = "✗<0.95" if r["sharpe"] < 0.95 else "✓"     # 旧审计的单独 Sharpe 闸门
        print(f"{r['leg']:<20s}{r['sharpe']:+6.2f}{r['corr_to_book']:+9.2f}{r['ret_2018']:+8.1%}"
              f"{r['down_capture']*1e4:+7.0f}‱{r['d_sharpe']:+7.2f}{r['d_calmar']:+7.2f}  单闸门{gate}")

    best = rows[0]
    print(f"\n== 最佳边际防御腿 ==\n  {best['leg']}: Δsharpe {best['d_sharpe']:+.2f} / Δcalmar {best['d_calmar']:+.2f}")
    print(f"  对在册相关 {best['corr_to_book']:+.2f} | 2018 {best['ret_2018']:+.1%} | 单独 Sharpe {best['sharpe']:+.2f}")
    if best["sharpe"] < 0.95:
        print(f"  ⚠ 它单独 Sharpe {best['sharpe']:+.2f} < 0.95——旧审计的单策略闸门会**误杀**它,"
              f"但边际贡献 Δsh {best['d_sharpe']:+.2f} 为正:防御腿的价值在组合,不在单吊。")

    out = ROOT / "reports" / "research" / "cross_asset_leg_search.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"baseline": mb, "legs": rows, "best": best}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
