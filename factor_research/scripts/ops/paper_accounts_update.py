"""paper 多账户日更入口(WS-D 执行侧,PLAN_paper_multiaccount_loop.md T3)。

流程:① 读 reports/research/portfolio_recompose.json::paper_candidates → provision
(开户/冻结,stale/缺失 fail-closed);② 加载全市场价量面板(与 run_daily.py 同款
strategies.small_cap.load_price_panels + lake.load_raw_close);③ 对所有非 frozen
账户逐一 update_all(T+1 成交/估值,复用 T1 参数化的 paper_engine 原语 + T2 的
canonical build_executable_strategy 目标持仓);④ 状态摘要落
paper/accounts/summary.json。

不第二次实现信号/回测逻辑(R-BT-001):本脚本只负责"取数 + 调用
portfolio.paper_accounts 的 provision_from_recompose/update_all",决策与记账
全部在 paper_accounts.py 里(与部署/生产信号解耦,不读 deployments/production.json)。

调度旁路:挂 scripts/ops/scheduled_daily_update.py 的 run_paper_accounts_update()
(与既有 run_paper_forward_smallcap 同款——失败被吞掉、写进 report 但不影响
scheduled_daily_update 的整体 status,不阻断/不标记日更 failed)。

用法(cwd=factor_research): python3 -m scripts.ops.paper_accounts_update
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def _load_prices(warmup_start: str = "2010-01-01"):
    """与 run_daily.py 同款面板构造:canonical close/volume/amount + 不复权 raw_close。
    不新写数据加载逻辑,只复用既有 strategies.small_cap.load_price_panels /
    lake.load_lake.load_raw_close(R-BT-001:数据面板与生产同源)。
    """
    from core.engine import PricePanel
    from lake.load_lake import load_raw_close
    from strategies.small_cap import load_price_panels

    close, volume, amount = load_price_panels(warmup_start)
    raw_close = load_raw_close(start=warmup_start).reindex(index=close.index, columns=close.columns)
    return PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)


def run_paper_accounts_update(as_of: str | None = None, *, dry_run: bool = False) -> dict:
    """provision + update_all + 落 summary.json。返回本次运行摘要 dict。

    dry_run=True:只 provision(不加载价量、不 update_all),供人工核对候选名单
    /账户状态机而不产生任何成交——供本地/CI 快速自检,不联网、不读数据湖。
    """
    from portfolio import paper_accounts as pa

    summary: dict = {"generated_at": _now_iso(), "dry_run": dry_run}

    provision_result = pa.provision_from_recompose()
    summary["provision"] = {
        "status": provision_result["status"],
        "reason": provision_result.get("reason", ""),
        "accounts": provision_result.get("accounts", []),
    }

    if provision_result["status"] != "ok":
        summary["update"] = {"ran": False, "reason": "provision_rejected"}
        _write_summary(summary)
        return summary

    if dry_run:
        summary["update"] = {"ran": False, "reason": "dry_run"}
        _write_summary(summary)
        return summary

    if not provision_result.get("accounts"):
        summary["update"] = {"ran": False, "reason": "no_candidates"}
        _write_summary(summary)
        return summary

    prices = _load_prices()
    resolved_as_of = as_of or str(prices.close.index[-1].date())
    results = pa.update_all(prices, resolved_as_of)
    summary["update"] = {"ran": True, "as_of": resolved_as_of, "accounts": results}
    _write_summary(summary)
    return summary


def _write_summary(summary: dict) -> None:
    from portfolio import paper_accounts as pa

    pa.SUMMARY_FP.parent.mkdir(parents=True, exist_ok=True)
    pa.SUMMARY_FP.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只 provision,不加载价量/不更新账本")
    ap.add_argument("--as-of", default=None, help="覆盖估值/成交日期(默认取价格面板最新交易日)")
    args = ap.parse_args()

    summary = run_paper_accounts_update(as_of=args.as_of, dry_run=args.dry_run)
    print(f"=== paper 多账户日更 {summary['generated_at']} ===")
    print(f"  provision: {summary['provision']['status']}"
          + (f"({summary['provision']['reason']})" if summary["provision"]["reason"] else ""))
    for acc in summary["provision"].get("accounts", []):
        print(f"    {acc.get('family')}.{acc.get('version')}: {acc.get('status')}"
              + (f" — {acc.get('reason')}" if acc.get("reason") else ""))
    update = summary.get("update", {})
    if update.get("ran"):
        print(f"  update(as_of={update['as_of']}):")
        for r in update["accounts"]:
            print(f"    {r.get('family')}.{r.get('version')}: {r.get('status')}"
                  + (f" nav={r.get('nav')} trades={r.get('trades')}" if r.get("status") == "active" else "")
                  + (f" — {r.get('reason')}" if r.get("reason") else ""))
    else:
        print(f"  update: 未运行({update.get('reason', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
