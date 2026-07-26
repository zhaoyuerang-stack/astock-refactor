"""Scheduled Cross-Asset Defensive-Leg Search — weekly maintenance step.

每周在统一**边际透镜**下重搜跨资产防御腿({ETF × 趋势窗口}),按对在册 ACTIVE
组合的边际 Δsharpe 排序、标 SHADOW 推荐,落 JSON 供人工/Agent 复核。

为什么进周度:跨资产腿(国债/黄金)是已验证的**唯一无条件正边际**分散源;
ETF 已日更,最优腿/窗口会随新数据漂移,需定期重搜。equity 岛屿搜索已被证 ≈0
边际(STATUS「伪多样性审计」),本步是把发现算力对准真正有边际的空间。

口径权威 = portfolio.cross_asset.search_cross_asset_legs()(与 portfolio_cli
--discover-legs / research 脚本共用同一 reusable 契约,杜绝算法漂移)。
数据为本地 data_lake ETF close,**不联网**。
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "reports" / "research"
ARCHIVE_DIR = REPORT_DIR / "cross_asset_leg_search"


def main() -> int:
    print("=" * 72)
    print("  Scheduled Cross-Asset Defensive-Leg Search (边际透镜,非单独 Sharpe)")
    print("=" * 72)

    from portfolio.cross_asset import search_cross_asset_legs

    res = search_cross_asset_legs()
    mb = res["baseline"]
    print(f"\n  在册 ACTIVE 基线: sh={mb['sharpe']:+.2f} cal={mb['calmar']:+.2f} mdd={mb['maxdd']:+.1%}\n")
    print(f"  {'腿':<20s}{'单Sh':>6s}{'相关':>7s}{'2018':>8s}{'Δsh':>7s}{'Δcal':>7s}  SHADOW?")
    print("  " + "-" * 66)
    for l in res["legs"]:
        flag = "★推荐" if l["shadow_recommend"] else ""
        print(f"  {l['leg']:<20s}{l['standalone_sharpe']:+6.2f}{l['corr_to_book']:+7.2f}"
              f"{l['ret_2018']:+8.1%}{l['d_sharpe']:+7.2f}{l['d_calmar']:+7.2f}  {flag}")
    rec = res["recommended"]
    print(f"\n  SHADOW 推荐({len(rec)}): " + (", ".join(l["leg"] for l in rec) if rec else "无"))

    run_date = datetime.now().date().isoformat()
    payload = {"run_date": run_date, "source": "portfolio.cross_asset.search_cross_asset_legs", **res}
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    latest = REPORT_DIR / "cross_asset_leg_search.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (ARCHIVE_DIR / f"{run_date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[report] {latest}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
