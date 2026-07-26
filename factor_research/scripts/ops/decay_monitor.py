#!/usr/bin/env python3
"""在册策略定时衰减复测(LOOP_ENGINEERING §5.4)。

alpha 默认会失效 → 主动复测全部「在册」版本(strategy_registry,不止当前部署的那一条
腿——`run_active()` 只覆盖 deployments/production.json 的 legs,RESEARCH_STRATEGY_CATALOG
里压根没有 hq-momentum-hedged/large-cap-growth-hedged 等家族的 runner,范围天生不够),
触发 decay_signal 的标记待退役复核(非删除,承铁律9)。建议周度 cron / 接 scheduled 维护。
落报告 + 经 strategy_registry.attach_decay_check() 写回每个版本的 decay_check 字段
(供 Web /factors 展示实测衰减状态);不改 status/admission,是否退役仍走 workflow 人工决策。

收益序列读 data_lake/version_returns/<family>__<version>.csv(run_nine_gates_all.py
--persist 写的)而非现场回测——新鲜度取决于该版本上次审计时间,不是每次现算;
--audit-stale 在同一 cron 序列里紧邻跑,正常不会长期陈旧。

decay_signal(governance/decay.py):滚动3年夏普 <0.5 / Rank IC 连续4季 <0。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from governance.decay import decay_check

REPORT = ROOT / "data_lake" / "governance" / "decay_monitor.jsonl"
LATEST = ROOT / "reports" / "decay_status.json"
VERSION_RETURNS = ROOT / "data_lake" / "version_returns"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def main():
    print("=" * 72)
    print("  在册策略衰减复测(§5.4)")
    print("=" * 72)

    import strategy_registry
    from governance.holdout import current_data_fingerprint
    from runtime.production_readiness import current_deployment_identity

    try:
        identity = current_deployment_identity()
    except Exception as e:
        identity = {"deployment_identity_error": str(e)[:200]}
        print(f"  [identity] current_deployment_identity() 失败(不阻断报告): {e}", file=sys.stderr)

    data = strategy_registry._load()
    rows = []
    decayed = []
    latest_dates = []
    leg_returns = {}  # 拥挤归因用:name -> 日收益(与 decay 同源 version_returns)
    for fam in data.get("families", []):
        family = fam["id"]
        for v in fam.get("versions", []):
            if v.get("status") != "在册":
                continue
            version = v["version"]
            name = f"{family}.{version}"
            fp = VERSION_RETURNS / f"{family}__{version}.csv"
            if not fp.exists():
                print(f"  [skip] {name}: 无收益序列({fp.name}),需先跑一次 9-Gate --persist")
                continue
            ret = pd.read_csv(fp, index_col=0)["ret"]
            ret.index = pd.to_datetime(ret.index)
            ret = ret.dropna()
            if not len(ret):
                continue
            latest_dates.append(ret.index[-1])
            leg_returns[name] = ret
            res = decay_check(ret)
            rows.append({"strategy": name, **res})
            tag = "⚠️衰减" if res["decayed"] else "健康"
            sh = res.get("rolling_3y_sharpe_latest")
            print(f"  {name:28} 滚动3年夏普={sh}  {tag}  {res['action']}")
            if res["decayed"]:
                decayed.append((name, res["reasons"]))
            try:
                strategy_registry.attach_decay_check(family, version, res)
            except Exception as e:
                print(f"  [persist] {name} 写台账失败(继续,不阻断报告): {e}", file=sys.stderr)

    # 0 在册(或在册均缺收益序列)时:不再早退留下陈旧报告,而是写一份**诚实空报告**刷新控制面。
    # 否则在册数降到 0 后,旧报告(可能列着已退役策略)会永久陈旧、误导 PM 交易台的退役监控。
    # 语义解耦:status=green 表示"无在册策略发生衰减"(0 在册时真),"无 alpha 可用"由 model/入册门
    # 负责(trade_readiness.model_version=not_registered),decay 门不以已退役策略误报 red。
    no_registered = not rows
    if no_registered:
        print("无在册策略可复测(0 在册或均缺收益序列)——写诚实空报告刷新陈旧控制面,不早退。")

    # 拥挤归因维度(§7 失效模式/退役归因字段;孤岛回收:capacity.strategy_pool_crowding)。
    # 披露非判定:不改 status 红绿,只给每腿补 corr_to_pool/crowded 供退役复核时归因
    # 「拥挤」;计算失败显式落 reason,不静默吞(check_control_exceptions 同精神)。
    try:
        from capacity.crowding_score import strategy_pool_crowding
        crowding = strategy_pool_crowding(leg_returns)
    except Exception as e:  # noqa: BLE001
        crowding = {"computable": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}
        print(f"  [crowding] 拥挤度计算失败(披露层,不阻断 decay 报告): {e}", file=sys.stderr)
    if crowding.get("computable"):
        per_leg = crowding.get("per_leg", {})
        for r in rows:
            leg = per_leg.get(r["strategy"])
            if leg:
                r["crowding_corr_to_pool"] = leg["corr_to_pool"]
                r["crowding_max_pair"] = f"{leg['max_pair_with']}({leg['max_pair_corr']})"
                r["crowded"] = leg["crowded"]
        crowded_names = [n for n, l in per_leg.items() if l["crowded"]]
        print(f"  [crowding] 池级拥挤={crowding['pool_crowding_latest']}"
              f"(阈值 {crowding['threshold']});拥挤腿:{crowded_names or '无'}")
    else:
        print(f"  [crowding] 未计算:{crowding.get('reason')}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "a") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=float) + "\n")

    print("-" * 72)
    if decayed:
        print(f"⚠️ {len(decayed)} 个在册策略触发衰减信号,建议走 workflow 标退役复核:")
        for name, reasons in decayed:
            print(f"   {name}: {'; '.join(reasons)}")
    elif no_registered:
        print("ℹ️ 当前 0 在册策略,无衰减可监控;空报告(status=green, strategies=[])仅刷新陈旧控制面。")
    else:
        print("✅ 全部在册策略健康,无衰减。")
    generated_at = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
    latest_date = str(max(latest_dates).date()) if latest_dates else ""
    envelope = {
        "report_type": "decay",
        "generated_at": generated_at,
        **identity,
        "data_fingerprint": current_data_fingerprint(),
        "as_of_date": latest_date,
        "status": "red" if decayed else "green",
        "no_registered": no_registered,
        "strategies": rows,
        "pool_crowding": crowding,
    }
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=float))
    print(f"明细落 {REPORT.relative_to(ROOT)}")
    print(f"控制面最新状态落 {LATEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
