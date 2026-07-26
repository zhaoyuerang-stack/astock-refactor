"""Scheduled Composite Allocation Search — weekly research step (WS2, ADR-034).

每周在**边际透镜**下重搜在册腿的组合配置(配权方法 × 腿子集),按对 risk_parity
基线的 Δsharpe 排序、标 SHADOW 推荐,落 JSON 供人工复核。regime_adaptive 配权的
regime 信号取自 WS6 审计层(RegimeEngine trend,已 lag),小盘 reload 披露参考腿
取在册腿名匹配 small/illiq 者。

口径权威 = portfolio.composite_search.search_composite_allocations()(reusable 契约,
holdout 截断 + trial 记账在契约内强制)。本地 data_lake,不联网。
发现 ≠ 晋级:推荐仅供人工;晋级唯一通道 = workflow/promote_composite.py。
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
ARCHIVE_DIR = REPORT_DIR / "composite_search"


def _regime_signal():
    """WS6 审计层 regime trend(已 lag)→ 0/1 bull 信号;算不出退 None(跳过该方法)。"""
    try:
        from services.read.regime_audit import load_regime_labels

        labels = load_regime_labels()
        return (labels["trend"] == "up").astype(float)
    except Exception as e:
        print(f"  [regime] 信号不可用,跳过 regime_adaptive: {type(e).__name__}: {e}")
        return None


def _smallcap_ref(legs: dict) -> object:
    """小盘参考腿 = 在册腿名匹配 small/illiq 者(等权合成);无匹配退 None(跳过披露)。"""
    import pandas as pd

    hits = {k: v for k, v in legs.items() if any(t in k.lower() for t in ("small", "illiq"))}
    if not hits:
        return None
    return pd.DataFrame(hits).mean(axis=1)


def main() -> int:
    print("=" * 72)
    print("  Scheduled Composite Allocation Search (边际透镜,发现≠晋级)")
    print("=" * 72)

    from portfolio.composite_search import search_composite_allocations
    from portfolio.strategy_runners import run_active

    legs = run_active()
    res = search_composite_allocations(
        legs=legs,
        regime_signal=_regime_signal(),
        smallcap_ref=_smallcap_ref(legs),
    )
    mb = res.get("baseline")
    if not mb:
        print(f"  {res.get('note')}")
        return 0
    print(f"\n  基线 risk_parity({len(mb['legs'])}腿): sh={mb['sharpe']:+.2f} "
          f"cal={mb['calmar']:+.2f} mdd={mb['maxdd']:+.1%}\n")
    print(f"  {'配置':<34s}{'Sh':>6s}{'Δsh':>7s}{'Δmdd':>8s}{'小盘ρ':>7s}  SHADOW?")
    print("  " + "-" * 70)
    for r in res["configs"]:
        if "error" in r:
            continue
        tag = r["method"] + ("(-" + r["dropped"] + ")" if r.get("dropped") else "")
        sc = f"{r['smallcap_corr']:+.2f}" if r.get("smallcap_corr") is not None else "  n/a"
        flag = "★推荐" if r["shadow_recommend"] else ("⚠reload" if r.get("smallcap_reload") else "")
        print(f"  {tag:<34s}{r['sharpe']:+6.2f}{r['d_sharpe']:+7.2f}{r['d_maxdd']:+8.2%}{sc:>7s}  {flag}")
    rec = res["recommended"]
    print(f"\n  SHADOW 推荐({len(rec)}): "
          + (", ".join(r["method"] + ("(-" + r["dropped"] + ")" if r.get("dropped") else "") for r in rec)
             if rec else "无(现有配置已饱和,正确信号非失败)"))

    run_date = datetime.now().date().isoformat()
    payload = {"run_date": run_date,
               "source": "portfolio.composite_search.search_composite_allocations", **res}
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    latest = REPORT_DIR / "composite_search.json"
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
