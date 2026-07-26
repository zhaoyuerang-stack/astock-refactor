"""Daily-Brief Read Service —— 今日简报(产品唯一首屏)。

回答三问:**系统自己干了什么 / 世界有什么变化 / 今天需要我裁决几件事。**

定位(承 DECISION_COCKPITS.md):首屏不是 KPI 大数字墙,是「态势头条 + 待裁决入口」。
单人 shop 打开产品的第一眼应该得到:信任裁决(能不能信当前池)→ 裁决数(要不要介入)
→ 系统活动与世界变化(背景),而不是九个页面等人巡视。

诚实护栏:
- trust banner **原样透传** ``get_trust_calibration``(权威聚合,不重算不改写,禁更绿);
- decision_count 原样来自 ``get_decision_inbox``(含其 fail-closed 语义);
- system_activity / world_state 各源不可读时如实标 ``"unknown"`` + error,绝不填默认值;
- 「近 7 天新增候选」由候选 created_at 机械计数,不由 LLM 叙述。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from contracts.views import DailyBriefView

CHINA_TZ = ZoneInfo("Asia/Shanghai")
RECENT_DAYS = 7  # 「系统最近干了什么」的机械窗口


def _system_activity() -> dict:
    """autoresearch 漏斗现状 + 近 7 天新增候选(机械计数,不叙述)。"""
    try:
        from factory.autoresearch import CandidateRepository
        from services.read.autoresearch import autoresearch_funnel

        funnel = autoresearch_funnel()
        cutoff = datetime.now(CHINA_TZ) - timedelta(days=RECENT_DAYS)
        recent = 0
        for c in CandidateRepository().all():
            ts = str(c.created_at or "")
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CHINA_TZ)
            except ValueError:
                continue  # 无法解析的时间戳不计入(不臆测)
            if dt >= cutoff:
                recent += 1
        return {
            "status": "ok",
            "candidates_total": funnel.total,
            "candidates_recent_7d": recent,
            "review_pending": funnel.review_queue,
            "funnel_stages": funnel.stages,
        }
    except Exception as exc:  # noqa: BLE001 —— 如实标 unknown,不填默认值
        return {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def _world_state() -> dict:
    """世界变化:数据质量 / 实时衰减 / paper 实测净值。逐源独立降级。"""
    out: dict = {}
    try:
        from services.read.state import data_quality

        dq = data_quality(with_duckdb=False)
        out["data"] = {"status": "ok", "verdict": dq.verdict,
                       "clean_ratio": dq.clean_ratio, "severe_count": dq.severe_count}
    except Exception as exc:  # noqa: BLE001
        out["data"] = {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}

    try:
        import json

        from services.read.decision_inbox import DECAY_STATUS

        if DECAY_STATUS.exists():
            d = json.loads(DECAY_STATUS.read_text(encoding="utf-8"))
            out["decay"] = {"status": "ok",
                            "decay_status": str(d.get("status", "unknown")).lower(),
                            "as_of_date": d.get("as_of_date", "")}
        else:
            out["decay"] = {"status": "unmonitored",
                            "note": "reports/decay_status.json 不存在(如实标未监控)"}
    except Exception as exc:  # noqa: BLE001
        out["decay"] = {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}

    try:
        from services.read.paper import nav_curve

        nav = nav_curve()
        if nav.points:
            out["paper"] = {"status": "ok", "latest_nav_date": nav.latest_nav_date,
                            "latest_nav": nav.latest_nav, "total_return": nav.total_return,
                            "max_drawdown": nav.max_drawdown}
        else:
            out["paper"] = {"status": "empty", "note": "paper 无净值点(资金在板凳上,如实展示)"}
    except Exception as exc:  # noqa: BLE001
        out["paper"] = {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}
    return out


def get_daily_brief() -> DailyBriefView:
    # 信任裁决:原样透传权威聚合(禁更绿)。不可读时如实 neutral+error,绝不假 ready。
    try:
        from services.read.trust_calibration import get_trust_calibration

        trust = get_trust_calibration()
        trust_status, trust_headline = trust.banner_status, trust.headline
    except Exception as exc:  # noqa: BLE001
        trust_status = "neutral"
        trust_headline = f"信任裁决不可读({type(exc).__name__}: {exc})——不得视为可信。"

    # 待裁决:原样来自收件箱(含其空箱三态语义)。
    try:
        from services.read.decision_inbox import get_decision_inbox

        inbox = get_decision_inbox()
        decision_count = inbox.pending_count
        decision_headline = inbox.headline
        top = [i for i in inbox.items if i.severity in ("blocked", "attention")][:3]
    except Exception as exc:  # noqa: BLE001
        decision_count = -1  # -1 = 收件箱本身不可读(前端须显式呈现,非 0)
        decision_headline = f"决策收件箱不可读({type(exc).__name__}: {exc})——不得视为无事。"
        top = []

    return DailyBriefView(
        as_of=datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        trust_banner_status=trust_status,
        trust_headline=trust_headline,
        decision_count=decision_count,
        decision_headline=decision_headline,
        top_decisions=top,
        system_activity=_system_activity(),
        world_state=_world_state(),
        truth_sources={
            "trust": "services.read.trust_calibration(原样透传,禁更绿)",
            "decisions": "services.read.decision_inbox",
            "activity": "factory.autoresearch(漏斗+created_at 机械计数)",
            "world": "state.data_quality / reports/decay_status.json / paper.nav_curve",
        },
        honesty="本视图是聚合首屏:trust/decisions 原样透传各自权威,不重算不改写;"
                "各 section 源不可读时如实标 unknown/-1,绝不以默认值假绿。",
    )
