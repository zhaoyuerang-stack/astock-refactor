"""模拟盘只读视图:今日操作卡 / 交易流水 / 净值曲线(web 跟单闭环)。

数据源(只读,不触发执行):
  paper/account.json + trades.csv + nav.csv(唯一写者 = scripts/ops/paper_trade,launchd 日更)
  + signals/最新信号(轮动指令)。模拟盘全自动按真实盘 T+1 口径执行,无人工环节。
操作卡的「明日计划」与 Obsidian 卡片同口径:参考价 = 信号日收盘,实际按 T+1 成交价模式。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import (
    BondInstructionView,
    CandidateStockRow,
    NavCurveView,
    NavPoint,
    PaperBlockedRow,
    PaperPlanItem,
    PaperPositionRow,
    PaperTradeRow,
    PaperTradesView,
    TradePlanView,
)
from portfolio.paper_engine import (
    NAV_FP,
    SIGNALS,
    TRADES_FP,
    bond_authorization_block,
    defensive_bond_authorized,
    estimate_basket,
    estimate_bond_order,
    get_etf_close,
    load_account,
    load_names,
    valuation,
)

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")

DISCLAIMER = ("本操作卡为策略信号的机械呈现,模拟盘全自动按真实盘 T+1 口径执行;"
              "仅供研究参考,不构成投资建议;回测与模拟盘业绩不代表未来收益。")


def _latest_signal() -> dict:
    files = sorted(SIGNALS.glob("[0-9]*-[0-9]*-[0-9]*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else {}


def _latest_trading_day() -> str:
    """本地交易日历 ≤ 今天的最大交易日(判断信号是否过期)。"""
    try:
        import pandas as pd
        cal = pd.read_parquet(ROOT / "data_lake/meta/trade_calendar.parquet")["date"]
        cal = pd.to_datetime(cal)
        today = pd.Timestamp(datetime.now(CHINA_TZ).date())
        eligible = cal[cal <= today]
        return str(eligible.max().date()) if len(eligible) else ""
    except Exception:  # noqa: BLE001
        return ""


def _read_trades(limit: int | None = None) -> list[PaperTradeRow]:
    if not TRADES_FP.exists():
        return []
    with TRADES_FP.open() as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[-limit:]
    out = []
    for r in rows:
        try:
            out.append(PaperTradeRow(
                date=r["date"], code=r["code"], name=r["name"], side=r["side"],
                shares=int(float(r["shares"])), price=float(r["price"]),
                notional=float(r["notional"]), cost=float(r["cost"]),
                cash_after=float(r["cash_after"])))
        except (KeyError, ValueError):
            continue  # 容忍坏尾行(与 paper_trade 写入瞬间的竞态)
    return out


def trade_plan() -> TradePlanView:
    sig = _latest_signal()
    acc = load_account()
    names = load_names()
    date = str(sig.get("date", "")) or str(acc.get("last_date", ""))
    nav, pos_value, detail = valuation(acc, date) if date else (acc.get("cash", 0.0), 0.0, [])

    # 今日成交 + 受阻(paper_trade 执行时落盘)
    executed = [t for t in _read_trades() if t.date == acc.get("last_date")]
    blocked = []
    last_exec = acc.get("last_exec") or {}
    account_date = str(acc.get("last_date", ""))
    last_exec_signal_date = str(last_exec.get("from_signal") or "")
    if last_exec.get("exec_date") == acc.get("last_date"):
        blocked = [PaperBlockedRow(side=b[0], code=b[1], name=b[2], reason=b[3])
                   for b in last_exec.get("blocked", []) if len(b) >= 4]

    # 明日计划:pending(target + bond)→ 卖出掉队 / 买入新进(参考价=信号日收盘)
    pend = acc.get("pending") or {}
    target = pend.get("target") or []
    plan: list[PaperPlanItem] = []
    sells_est = 0.0
    for code, pos in (acc.get("positions") or {}).items():
        if code in target:
            continue
        from portfolio.paper_engine import get_close
        ref = get_close(code, date) or pos.get("avg_cost", 0.0)
        est = pos["shares"] * ref
        sells_est += est
        plan.append(PaperPlanItem(action="SELL", code=code, name=names.get(code, code),
                                  ref_price=round(ref, 3), est_shares=pos["shares"],
                                  est_notional=round(est, 2)))
    buys_est = 0.0
    new_codes = [c for c in target if c not in (acc.get("positions") or {})]
    if new_codes:
        lev = float(pend.get("leverage") or 0.0) or 1.0
        for code, name, shares, ref, est in estimate_basket(
                date, new_codes, nav * lev, int(sig.get("top_n", 25)), names):
            buys_est += est
            plan.append(PaperPlanItem(action="BUY", code=code, name=name,
                                      ref_price=round(ref, 3), est_shares=shares,
                                      est_notional=round(est, 2)))

    # 债券轮动指令卡(P5):BEAR 闲钱买债 / BULL 卖光债
    bond_view = None
    pend_bond = pend.get("bond") or {}
    held = acc.get("bond") or {}
    bond_code = pend_bond.get("code") or held.get("code") or "511010"
    bond_name = pend_bond.get("name") or held.get("name") or "国债ETF"
    bond_authorized = defensive_bond_authorized(pend_bond)
    if pend_bond.get("enabled") and bond_authorized:
        cash_avail = max(0.0, acc.get("cash", 0.0) + sells_est - buys_est)
        est = estimate_bond_order(date, cash_avail, bond_code)
        side = "HOLD" if held.get("shares") and not est else "BUY"
        ref = (est[1] if est else (get_etf_close(bond_code, date) or 0.0))
        bond_view = BondInstructionView(
            active=True, side=side, code=bond_code, name=bond_name,
            ref_price=round(ref, 3),
            est_shares=est[0] if est else 0,
            est_notional=round(est[2], 2) if est else 0.0,
            shares_held=int(held.get("shares") or 0),
            note="BEAR:次日将全部闲置资金买入国债ETF;切回 BULL 时卖出换回股票")
    elif pend_bond.get("requested") and not bond_authorized:
        reason = pend_bond.get("blocked_reason") or bond_authorization_block(pend_bond)[3]
        ref = get_etf_close(bond_code, date) or held.get("avg_cost", 0.0)
        bond_view = BondInstructionView(
            active=True, side="BLOCKED", authorized=False, blocked_reason=reason,
            code=bond_code, name=bond_name,
            ref_price=round(ref, 3), est_shares=0, est_notional=0.0,
            shares_held=int(held.get("shares") or 0),
            note=f"{reason};该债券轮动非现行可执行")
    elif held.get("shares") and bond_authorized:
        ref = get_etf_close(bond_code, date) or held.get("avg_cost", 0.0)
        bond_view = BondInstructionView(
            active=True, side="SELL", code=bond_code, name=bond_name,
            ref_price=round(ref, 3), est_shares=int(held["shares"]),
            est_notional=round(held["shares"] * ref, 2),
            shares_held=int(held["shares"]),
            note="BULL:次日开盘卖出全部国债ETF,资金买回股票")
    elif held.get("shares"):
        reason = bond_authorization_block(pend_bond or held)[3]
        ref = get_etf_close(bond_code, date) or held.get("avg_cost", 0.0)
        bond_view = BondInstructionView(
            active=True, side="BLOCKED", authorized=False, blocked_reason=reason,
            code=bond_code, name=bond_name,
            ref_price=round(ref, 3), est_shares=0, est_notional=0.0,
            shares_held=int(held["shares"]),
            note=f"遗留债券持仓无独立 defensive 授权;不生成买卖指令。{reason}")

    latest_td = _latest_trading_day()
    stale = bool(latest_td and date and date < latest_td)

    # 提取选股候选股票池
    sig_candidates = sig.get("candidates") or []
    if not sig_candidates and sig.get("holdings"):
        sig_candidates = sig.get("holdings")
    candidates = [CandidateStockRow(code=c, name=names.get(c, c)) for c in sig_candidates]

    return TradePlanView(
        signal_date=date,
        account_date=account_date,
        last_exec_signal_date=last_exec_signal_date,
        generated_at=datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        stale=stale,
        stale_reason=f"信号日 {date} 早于最近交易日 {latest_td},等待数据更新" if stale else "",
        regime=str(sig.get("regime", "")),
        regime_dist=float(sig.get("regime_dist") or 0.0),
        in_market=bool(sig.get("in_market")),
        band_exposure=float(sig.get("band_exposure") or 0.0),
        action=str(sig.get("action", "")),
        small_index_vs_ma16=float(sig.get("small_index_vs_ma16") or 0.0),
        binary_in_market_shadow=bool(sig.get("binary_in_market_shadow")),
        base_in_market=bool(sig.get("base_in_market")),
        executed=executed,
        blocked=blocked,
        plan=plan,
        bond=bond_view,
        positions=[PaperPositionRow(
            code=d["code"], name=d.get("name") or names.get(d["code"], d["code"]),
            shares=d["shares"], cost=d["cost"], price=d["price"],
            mv=d["mv"], pnl=d["pnl"], asset=d.get("asset", "stock")) for d in detail],
        candidates=candidates,
        nav=round(nav, 2),
        cash=round(acc.get("cash", 0.0), 2),
        position_value=round(pos_value, 2),
        total_return=round(nav / acc.get("init_capital", 1.0) - 1, 6) if acc.get("init_capital") else 0.0,
        disclaimer=DISCLAIMER,
    )


def paper_trades(limit: int = 200) -> PaperTradesView:
    rows = _read_trades()
    return PaperTradesView(trades=list(reversed(rows[-limit:])), total=len(rows))


def nav_curve() -> NavCurveView:
    acc = load_account()
    points: list[NavPoint] = []
    if NAV_FP.exists():
        with NAV_FP.open() as f:
            for r in csv.DictReader(f):
                try:
                    points.append(NavPoint(date=r["date"], nav=float(r["nav"]),
                                           cash=float(r["cash"]),
                                           position_value=float(r["position_value"]),
                                           total_return=float(r["total_return"])))
                except (KeyError, ValueError):
                    continue
    max_dd = 0.0
    peak = float("-inf")
    for p in points:
        peak = max(peak, p.nav)
        if peak > 0:
            max_dd = min(max_dd, p.nav / peak - 1)
    return NavCurveView(
        points=points,
        inception=str(acc.get("inception") or ""),
        init_capital=float(acc.get("init_capital") or 0.0),
        latest_nav_date=points[-1].date if points else str(acc.get("last_date") or ""),
        latest_nav=points[-1].nav if points else float(acc.get("cash") or 0.0),
        total_return=points[-1].total_return if points else 0.0,
        max_drawdown=round(max_dd, 6),
    )
