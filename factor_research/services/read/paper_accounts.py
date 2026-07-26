"""多账户 paper 实测只读视图(T4,PLAN_paper_multiaccount_loop.md)。

给桌面端"并排展示实测净值/回撤/回测偏差"提供数据源。只读
portfolio/paper_accounts.py 落盘的账户文件(account.json/nav.csv/meta.json)+
data_lake/version_returns/<family>__<version>.csv(9-Gate --persist 的台账回测
收益序列,与 decay_monitor/scheduled_portfolio_recompose 同源同口径)。

不动既有单账户契约(services/read/paper.py 的 trade_plan/paper_trades/nav_curve
命名与字段不变),多账户视图另立 contracts(PaperAccountView/PaperAccountsListView),
避免 latest_signal 等既有前端命名域被牵连改名。

三态诚实(R-PROD-001「排名由后端确定性产出并持久化」的读侧对应):
  · 有账户 → 正常返回,附回测偏差
  · 名单健康但空(summary.json 存在但 accounts=[])→ 空列表 + healthy=True
  · summary.json 缺失/无法解析/账户名单来源不可读 → healthy=False + error,
    不静默返回空数组冒充"无账户"
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from contracts.views import PaperAccountNavPoint, PaperAccountsListView, PaperAccountView

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _accounts_root() -> Path:
    from portfolio import paper_accounts as pa

    return pa.ACCOUNTS_ROOT


def _summary_fp() -> Path:
    from portfolio import paper_accounts as pa

    return pa.SUMMARY_FP


def _version_returns_fp(family: str, version: str) -> Path:
    from portfolio import paper_accounts as pa

    return pa.ROOT / "data_lake" / "version_returns" / f"{family}__{version}.csv"


def _read_nav_points(nav_fp: Path) -> list[PaperAccountNavPoint]:
    if not nav_fp.exists():
        return []
    points: list[PaperAccountNavPoint] = []
    with nav_fp.open() as f:
        for row in csv.DictReader(f):
            try:
                points.append(PaperAccountNavPoint(
                    date=row["date"], nav=float(row["nav"]),
                    total_return=float(row["total_return"])))
            except (KeyError, ValueError):
                continue  # 容忍坏尾行(与 paper.py 的既有惯例一致,写入瞬间竞态)
    return points


def _max_drawdown(points: list[PaperAccountNavPoint]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for p in points:
        peak = max(peak, p.nav)
        if peak > 0:
            max_dd = min(max_dd, p.nav / peak - 1)
    return round(max_dd, 6)


def _backtest_deviation(points: list[PaperAccountNavPoint], family: str, version: str) -> dict:
    """paper 实测 NAV 累计收益 vs 该版本台账回测收益(同窗对比,窗=账户存续期)。

    窗口 = [账户第一条 NAV 记录的日期, 最后一条 NAV 记录的日期]——只取
    version_returns 序列里落在这个区间的部分,不用整段回测历史,避免"回测更长
    所以看起来更平滑"这种虚假对比。

    返回三态:
      available=True  → cumulative_deviation(paper 累计收益 - 回测同窗累计收益)、
                        tracking_error(逐日收益差的年化标准差)
      available=False → reason(缺 version_returns 文件 / 窗口数据不足 / 无法解析)
    """
    if len(points) < 2:
        return {"available": False, "reason": "paper NAV 序列不足 2 个交易日,无法算窗口偏差"}

    fp = _version_returns_fp(family, version)
    if not fp.exists():
        return {"available": False,
                "reason": f"version_returns/{family}__{version}.csv 不存在(未跑过 9-Gate --persist)"}
    try:
        bt_ret = pd.read_csv(fp, index_col=0)["ret"]
        bt_ret.index = pd.to_datetime(bt_ret.index)
        bt_ret = bt_ret.dropna()
    except (KeyError, ValueError, pd.errors.ParserError) as exc:
        return {"available": False, "reason": f"version_returns 解析失败({exc})"}

    start = pd.Timestamp(points[0].date)
    end = pd.Timestamp(points[-1].date)
    window = bt_ret[(bt_ret.index >= start) & (bt_ret.index <= end)]
    if len(window) < 2:
        return {"available": False,
                "reason": f"回测收益序列在账户存续窗口({points[0].date}~{points[-1].date})内样本不足"}

    bt_cum = float((1.0 + window).prod() - 1.0)
    paper_cum = points[-1].total_return
    paper_daily = pd.Series(
        [0.0] + [(points[i].nav / points[i - 1].nav - 1.0) for i in range(1, len(points))],
        index=pd.to_datetime([p.date for p in points]),
    )
    common = paper_daily.index.intersection(window.index)
    tracking_error = None
    if len(common) >= 5:
        diff = paper_daily.reindex(common) - window.reindex(common)
        tracking_error = round(float(diff.std() * (252 ** 0.5)), 6)

    return {
        "available": True,
        "window_start": points[0].date,
        "window_end": points[-1].date,
        "paper_cumulative_return": round(paper_cum, 6),
        "backtest_cumulative_return": round(bt_cum, 6),
        "cumulative_deviation": round(paper_cum - bt_cum, 6),
        "tracking_error": tracking_error,
        "common_days": int(len(common)),
    }


def _account_view(meta: dict) -> PaperAccountView:
    from portfolio import paper_accounts as pa

    family, version, status = meta["family"], meta["version"], meta["status"]
    paths = pa.AccountPaths.for_version(family, version)
    points = _read_nav_points(paths.nav_fp)

    deviation = _backtest_deviation(points, family, version) if points else \
        {"available": False, "reason": "无 NAV 记录(账户尚未产生任何估值)"}

    return PaperAccountView(
        name=meta.get("name") or f"{family}.{version}",
        family=family, version=version, status=status,
        reason=meta.get("reason", ""),
        opened_at=meta.get("opened_at", ""),
        frozen_at=meta.get("frozen_at", ""),
        last_update_date=meta.get("last_update_date", ""),
        nav_points=points,
        latest_nav=points[-1].nav if points else 0.0,
        total_return=points[-1].total_return if points else 0.0,
        max_drawdown=_max_drawdown(points),
        backtest_deviation=deviation,
    )


def _ranked_order(summary: dict, metas: list) -> list:
    """展示顺序 = recompose paper_candidates 的排名顺序(后端产物顺序,R-PROD-001
    「不得前端/引擎重算排名」),不是账户目录的字典序。

    summary["provision"]["accounts"] 是 provision_from_recompose() 按 candidates
    (排名靠前在前)顺序 append 出来的列表(新上榜 active/blocked + 尾部按
    sorted(dirname) 追加的下榜 frozen 段)——用它的 (family, version) 顺序对
    metas 重排;summary 里没出现的账户(理论上不该发生,防御性兜底)按
    list_account_metas 原有顺序追加在最后,不丢账户也不假装有序。
    """
    rank_index: dict[tuple[str, str], int] = {}
    for i, acc in enumerate(summary.get("provision", {}).get("accounts") or []):
        fam, ver = acc.get("family"), acc.get("version")
        if fam and ver and (fam, ver) not in rank_index:
            rank_index[(fam, ver)] = i

    def _key(meta):
        idx = rank_index.get((meta.family, meta.version))
        return (0, idx) if idx is not None else (1, 0)

    # stable sort:rank_index 命中的按其顺序排在前面,未命中的保留原相对顺序排在后面。
    return sorted(metas, key=_key)


def list_paper_accounts() -> PaperAccountsListView:
    """多账户并排展示的唯一读入口:顺序 = summary.json 里 recompose 排名顺序
    (_ranked_order),前端不得重排名(R-PROD-001)。
    """
    from portfolio import paper_accounts as pa

    summary_fp = _summary_fp()
    if not summary_fp.exists():
        return PaperAccountsListView(
            healthy=False,
            error=f"summary.json 不存在({summary_fp}):尚未跑过 paper_accounts_update 日更",
            generated_at="", accounts=[],
        )
    try:
        summary = json.loads(summary_fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return PaperAccountsListView(
            healthy=False, error=f"summary.json 无法解析({exc})",
            generated_at="", accounts=[],
        )

    provision = summary.get("provision") or {}
    if provision.get("status") == "rejected":
        return PaperAccountsListView(
            healthy=False,
            error=provision.get("reason") or "候选名单来源被 fail-closed 拒绝(stale/缺失)",
            generated_at=summary.get("generated_at", ""), accounts=[],
        )

    metas = pa.list_account_metas(_accounts_root())
    ordered = _ranked_order(summary, metas)
    accounts = [_account_view(m.to_dict()) for m in ordered]
    return PaperAccountsListView(
        healthy=True, error="",
        generated_at=summary.get("generated_at", ""),
        accounts=accounts,
    )
