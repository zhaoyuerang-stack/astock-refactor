"""illiquidity 模拟盘(真实盘成交逻辑):T+1 close 成交 + pending order + 停牌/涨跌停约束。

真实盘口径(你要求"所有模拟按真实盘"):
  · T 日盘后出信号 → 记 pending → **T+1 close 按不复权收盘价成交**
  · 停牌(当天无开盘价)不可买卖;一字涨停(开盘=涨停价)买不进、一字跌停卖不出
  · 等权 1/top_n,A股 100 股整数倍;成交/估值全用不复权价(daily_raw 的 raw_open/raw_close)
  · 本金 100 万,1.0x;成本 买 0.225% / 卖 0.275%
  · 回测口径(core 的 run_small_cap_strategy 收盘撮合)不动——回测归回测,成交归真实买卖
执行引擎: portfolio/paper_engine.py(与 web 操作卡 services/read/paper.py 共享,本文件只管 CLI + Obsidian)
状态: paper/account.json(含 pending) + trades.csv + nav.csv
输出: <OBSIDIAN>/今日操作.md(覆盖) + 历史/YYYY-MM-DD.md(归档)
用法(cwd=factor_research): /usr/bin/python3 -m scripts.ops.paper_trade [--preview]
"""
import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from portfolio.paper_engine import (  # noqa: E402
    BUY_COST,
    ETF_BUY_COST,
    INIT_CAPITAL,
    LEVERAGE,
    SELL_COST,
    SIGNALS,
    append_trades,
    defensive_bond_authorized,
    estimate_basket,
    estimate_bond_order,
    execute_to_target,
    load_account,
    load_names,
    save_account,
    upsert_nav,
    valuation,
)

OBSIDIAN = Path("/Users/kiki/Personal Wiki/30.output/A股v2.0模拟盘")


def fmt(x):
    return f"{x:,.0f}"


def read_decay():
    dp = ROOT / "reports/decay_status.json"
    return json.loads(dp.read_text()) if dp.exists() else None


def build_pending_bond(signal):
    """从信号构造 pending bond 指令;无独立 defensive 授权时 fail-closed。"""
    rot = signal.get("rotation", {}) or {}
    requested = bool(rot.get("recommend_bond"))
    pend_bond = {
        "requested": requested,
        "enabled": requested,
        "code": rot.get("bond_code", "511010"),
        "name": rot.get("bond_name", "国债ETF"),
    }
    auth = rot.get("defensive_authorization")
    if isinstance(auth, dict) and auth:
        pend_bond["authorization"] = auth
    if not defensive_bond_authorized(pend_bond):
        pend_bond["enabled"] = False
        if requested:
            pend_bond["blocked_reason"] = "defensive overlay 未授权,拒绝债券轮动"
    return pend_bond


def render_card(date, signal, decay, acc, nav, pos_value, detail, trades, blocked, names, exec_from):
    ret = nav / acc["init_capital"] - 1
    buys = [t for t in trades if t[3] == "BUY"]
    sells = [t for t in trades if t[3] == "SELL"]
    today_str = datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
    is_fresh = (date == today_str)
    strategy_ver = signal.get("strategy_version", "v1.0")
    title = f"# A股 模拟盘 · {date}" + ("" if is_fresh else f" (生成于 {today_str})")
    stale_note = "" if is_fresh else f"\n> ⚠️ 信号日期 {date} 非今日({today_str})——最新可用数据为 {date} 收盘, 等待下次数据更新.\n"
    lines = [
        title,
        "",
        f"> 自动生成 {datetime.now(CHINA_TZ):%Y-%m-%d %H:%M} CST | illiquidity {strategy_ver} | 本金 {fmt(acc['init_capital'])} | "
        f"杠杆 {LEVERAGE}x | 真实盘 T+1 开盘成交",
        stale_note,
        "## 📋 今日开盘成交" + (f"(执行 {exec_from} 信号)" if exec_from else ""),
        "",
    ]
    if not exec_from:
        lines += ["今日无待执行订单(模拟盘首日或上一信号为空仓)。", ""]
    elif not trades and not blocked:
        lines += ["目标与持仓一致,今日开盘无买卖。", ""]
    else:
        if sells:
            lines += [f"**卖出 {len(sells)} 只(开盘价):**", "", "| 代码 | 名称 | 股数 | 开盘价 | 金额 |", "|--|--|--|--|--|"]
            lines += [f"| {t[1]} | {t[2]} | {t[4]} | {t[5]} | {fmt(t[6])} |" for t in sells]
            lines += [""]
        if buys:
            tot = sum(t[6] for t in buys)
            lines += [f"**买入 {len(buys)} 只(开盘价,合计 ≈ {fmt(tot)}):**", "",
                      "| 代码 | 名称 | 股数 | 开盘价 | 金额 |", "|--|--|--|--|--|"]
            lines += [f"| {t[1]} | {t[2]} | {t[4]} | {t[5]} | {fmt(t[6])} |" for t in buys]
            lines += [""]
        if blocked:
            lines += [f"**⛔ 未成交 {len(blocked)} 笔(真实盘约束):**", "", "| 方向 | 代码 | 名称 | 原因 |", "|--|--|--|--|"]
            lines += [f"| {b[0]} | {b[1]} | {b[2]} | {b[3]} |" for b in blocked]
            lines += [""]

    # 明日计划(本日信号 → 次日开盘执行)
    pend = acc.get("pending") or {}
    target = pend.get("target") or []
    lines += ["## 📅 明日开盘计划" + f"(本日 {date} 信号 → 次日开盘执行)", ""]
    if signal["in_market"] and target:
        plan = estimate_basket(date, target, nav, signal["top_n"], names)
        lines += [f"目标持仓 {len(target)} 只等权;次日开盘买入(参考价=今收,实际按开盘价):", "",
                  "| 代码 | 名称 | 预计股数 | 参考价 | 预计金额 |", "|--|--|--|--|--|"]
        lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.2f} | {fmt(r[4])} |" for r in plan]
        lines += [""]
    else:
        lines += ["**空仓观望**,明日开盘不建仓(若有持仓则次日开盘清仓)。", ""]
    pend_bond = pend.get("bond") or {}
    if pend_bond.get("enabled"):
        est = estimate_bond_order(date, acc["cash"], pend_bond.get("code", "511010"))
        if est:
            sh, ref, est_amt = est
            lines += [f"**🔄 债券轮动(BEAR)**:次日闲置资金买入 {pend_bond.get('code','511010')} "
                      f"{pend_bond.get('name','国债ETF')} ≈ {sh} 份 × {ref:.3f} = {fmt(est_amt)}"
                      f"(参考价=今收,费率 {ETF_BUY_COST:.2%})", ""]
    elif pend_bond.get("requested") and pend_bond.get("blocked_reason"):
        lines += [f"**🔄 债券轮动(BEAR)**: {pend_bond['blocked_reason']}。该信号非现行可执行。", ""]
    elif acc.get("bond") and (acc["bond"] or {}).get("shares"):
        if defensive_bond_authorized(pend_bond):
            lines += [f"**🔄 债券轮动(BULL)**:次日开盘卖出全部 {acc['bond']['code']} "
                      f"{acc['bond'].get('name','国债ETF')} {acc['bond']['shares']} 份,资金买回股票", ""]
        else:
            lines += [f"**🔄 债券遗留持仓**:{acc['bond']['code']} "
                      f"{acc['bond'].get('name','国债ETF')} {acc['bond']['shares']} 份无独立 defensive 授权,不生成买卖指令。", ""]

    lines += [
        "## 💰 账户", "", "| 指标 | 值 |", "|--|--|",
        f"| 总资产 | {fmt(nav)} |",
        f"| 现金 | {fmt(acc['cash'])} |",
        f"| 持仓市值 | {fmt(pos_value)} |",
        f"| 累计收益 | {ret:+.2%} |",
        f"| 持仓数 | {len(detail)} 只 |",
        f"| 起始日 | {acc.get('inception')} |",
        "",
    ]
    if detail:
        lines += ["## 📦 当前持仓", "", "| 代码 | 名称 | 股数 | 成本 | 现价 | 市值 | 浮盈 |", "|--|--|--|--|--|--|--|"]
        for d in sorted(detail, key=lambda x: -x["mv"]):
            pr = f"{d['price']:.2f}" if d["price"] else "停牌"
            dname = d.get("name") or names.get(d["code"], d["code"])
            lines += [f"| {d['code']} | {dname} | {d['shares']} | "
                      f"{d['cost']:.2f} | {pr} | {fmt(d['mv'])} | {d['pnl']:+,.0f} |"]
        lines += [""]
    else:
        lines += ["## 📦 当前持仓", "", "(空仓,持币观望)", ""]

    dist = signal.get("small_index_vs_ma16", 0)
    lines += ["## 📈 择时 & 失效监控", "",
              f"- 择时: {'🟢 持仓' if signal['in_market'] else '🔴 空仓'}  (小盘指数 vs MA16: {dist:+.2%})",
              "  > 小盘指数=成交额后50%股票的等权平均净值. MA16=其16日均线.",
              "  > 站上MA16=小盘趋势向好→持仓; 跌破=小盘走弱→空仓."]
    # decay_status.json 是 scripts/ops/decay_monitor.py 写的多版本 schema
    # ({"strategies": [{"strategy": "family.version", "decayed", "rolling_3y_sharpe_latest",
    # "reasons", "action"}, ...]}),按本卡实际部署的 illiquidity.{strategy_ver} 取一条。
    decay_entry = None
    if decay:
        decay_entry = next(
            (s for s in decay.get("strategies", []) if s.get("strategy") == f"illiquidity.{strategy_ver}"),
            None,
        )
    if decay_entry is not None:
        warn_tag = "🔴 衰减" if decay_entry.get("decayed") else "🟢 健康"
        lines += [f"- 失效: {warn_tag}  更新 {decay.get('generated_at', '')}",
                  f"  > 滚动3年夏普={decay_entry.get('rolling_3y_sharpe_latest')}: <0.5 触发退役复核.",
                  f"  > {decay_entry.get('action', '')}"]
        if decay_entry.get("reasons"):
            lines += [f"  - 触发: {'; '.join(decay_entry['reasons'])}"]
    else:
        lines += ["- 失效: (decay_status.json 缺失或无 illiquidity 条目,跑 decay_monitor 刷新)"]

    # ── 轮动建议 ──
    rotation = signal.get("rotation", {})
    regime = rotation.get("current_regime", signal.get("regime", ""))
    if regime:
        regime_icon = "🟢 BULL" if regime == "bull" else "🔴 BEAR"
        dist_val = signal.get('regime_dist', 0)
        lines += [
            "", "## 🔄 Regime 轮动", "",
            f"- 当前 Regime: **{regime_icon}** (偏离度: {dist_val:+.2%})",
            "  > 偏离度=小盘指数相对MA16的距离. >0=BULL(趋势向上), ≤0=BEAR(趋势向下).",
            f"  > 数据来源: 最新交易日 {signal['date']} 收盘价, T日只用T-1日数据(shifted,防未来函数).",
        ]
        if regime == "bear" and rotation.get("recommend_bond"):
            lines += [
                f"- 💡 **建议**: 空仓资金配置 **{rotation.get('bond_code', '511010')} {rotation.get('bond_name', '国债ETF')}**",
                "  > 回测验证: BEAR期间债券年化+2.7% vs 现金0%, 10年累计多赚592万(AmihudIlliq).",
                f"- 买入方式: 和买股票一样, 代码 {rotation.get('bond_code', '511010')}, 股票账户直接交易",
                "- 切换回 BULL 时卖出债券, 买回 illiq 股票",
            ]
        elif regime == "bear":
            lines += [
                "- ⛔ **债券轮动非现行可执行**: 未授权独立 defensive overlay,不生成 511010 买入指令。",
                f"  > {rotation.get('note', 'defensive overlay 未授权,拒绝债券轮动')}",
            ]
        else:
            lines += [
                "- 全仓 illiquidity 股票 (当前策略)",
                "  > BULL期间全仓小盘非流动性股票, Band动态杠杆0~1.5x.",
            ]
        lines += [""]

    # ── 因子健康 (来自 factor_health.json) ──
    health_fp = ROOT / "reports/factor_health.json"
    if health_fp.exists():
        health = json.loads(health_fp.read_text())
        lines += ["## 🩺 因子健康", "",
                  f"  > 更新: {health.get('updated', '?')}, 滚动12月Sharpe动量. 📈加速=好, 📉减速=关注."]
        for hname, hdata in health.items():
            if hname == "updated": continue
            sh = hdata["sharpe"]; mom = hdata["momentum_6m"]; trend = hdata["trend"]
            icon = "📈" if trend == "加速" else "📉"
            current_mark = " ⬅ 当前" if "当前" in hname else ""
            lines.append(f"- {icon} {hname}: Sharpe={sh:.2f}, 6月变化={mom:+.0f}%, {trend}{current_mark}")
        lines.append("")

    lines += [
        "## 📖 卖出规则(系统自动执行)", "",
        "- 择时转空(小盘指数跌破 MA16)→ 次日开盘**全部清仓**",
        "- 调仓日(每 20 交易日)→ 次日开盘卖掉**掉出 top25** 的、买入新进的",
        "- 无单只止盈止损(截面策略,靠分散+调仓控制风险)",
        "", "## ⚠️ 口径声明", "",
        "- **真实盘成交**:T 日盘后出信号 → **T+1 开盘价**成交(你收盘后才看到信号,只能次日买)",
        "- **停牌/涨跌停**:停牌不可买卖;一字涨停买不进、一字跌停卖不出(见上「未成交」)",
        f"- 成交/估值用**不复权价**(daily_raw);成本 买 {BUY_COST:.3%} / 卖 {SELL_COST:.3%}",
        f"- 杠杆 {LEVERAGE}x;容量:本金 {fmt(acc['init_capital'])} ≪ 策略容量 ~2700万 (AmihudIlliq v3.1)",
        "- 回测口径(收盘撮合)另算——回测归回测,本卡是真实买卖逻辑",
    ]
    return "\n".join(lines) + "\n"


def build_preview(names):
    """假设择时转多:展示「次日开盘建仓清单」(不碰正式账户)。"""
    from factors.small_cap import small_cap_timing
    from factors.utils import mad_clip, safe_zscore
    from strategies.small_cap import load_price_panels
    print("[preview] 加载数据 + 计算 illiquidity top25 名单(约 1-2 分钟)...")
    close, volume, amount = load_price_panels("2010-01-01")
    last = close.index[-1]
    # illiquidity factor
    ret_abs = close.pct_change(fill_method=None).abs().replace([float('inf'), float('-inf')], float('nan'))
    illiq_raw = (ret_abs / (amount + 1)).rolling(20, min_periods=10).mean()
    factor = safe_zscore(mad_clip(illiq_raw))
    f = factor.loc[last].dropna()
    active = close.loc[last].dropna().index
    holdings = f.reindex(active).dropna().nlargest(25).index.tolist()
    # Timing dist
    timing_raw, _, timing_dist = small_cap_timing(close, amount, 16)
    dist = float(timing_dist.loc[last]) if last in timing_dist.index else 0.0
    sig = {"date": last, "holdings": holdings, "top_n": 25, "timing_dist": dist,
           "in_market": bool(timing_raw.loc[last])}
    date = str(sig["date"].date())
    plan = estimate_basket(date, sig["holdings"], INIT_CAPITAL, 25, names)
    tot = sum(r[4] for r in plan)
    warn = (f"> 🔮 **建仓预览** —— 假如择时转多,系统会在**次日开盘**按此清单建仓。\n"
            f"> ⛔ **当前实际择时 🔴 空仓({sig['timing_dist']:+.2%} vs MA16),切勿照此买入!**\n"
            f"> 参考价=信号日({date})收盘,实际按 T+1 开盘价成交。\n\n")
    lines = [warn, f"# 建仓预览 · {date}", "",
             f"**目标 {len(plan)} 只等权,合计 ≈ {fmt(tot)},剩余 ≈ {fmt(INIT_CAPITAL - tot)}**", "",
             "| 代码 | 名称 | 预计股数 | 参考价 | 预计金额 |", "|--|--|--|--|--|"]
    lines += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]:.2f} | {fmt(r[4])} |" for r in plan]
    OBSIDIAN.mkdir(parents=True, exist_ok=True)
    (OBSIDIAN / "建仓预览.md").write_text("\n".join(lines) + "\n")
    print(f"=== 建仓预览 {date}(假设转多,次日开盘建仓)===")
    print(f"  目标 {len(plan)} 只 | 合计 {fmt(tot)} | 剩余 {fmt(INIT_CAPITAL - tot)}")
    print(f"  → Obsidian: {OBSIDIAN / '建仓预览.md'}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="生成次日开盘建仓预览,不碰正式账户")
    args = ap.parse_args()
    if args.preview:
        return build_preview(load_names())

    sig_files = sorted(SIGNALS.glob("[0-9]*-[0-9]*-[0-9]*.json"))
    if not sig_files:
        print("无 signals/*.json,先跑 run_daily.py")
        return 1
    signal = json.loads(sig_files[-1].read_text())
    date = signal["date"]
    names = load_names()
    decay = read_decay()
    acc = load_account()
    if acc["inception"] is None:
        acc["inception"] = date

    trades, blocked = [], []
    exec_from = None
    if acc["last_date"] != date:
        # 1. 结算上次 pending:用 date(=上个信号的次日)开盘价执行上个信号的目标
        pend = acc.get("pending")
        if pend:
            exec_from = pend["signal_date"]
            # 用 pending 记录的 leverage (band_exposure 动态杠杆) 执行;fallback 1.0
            execute_to_target(acc, date, pend["target"], signal["top_n"], names,
                              trades, blocked, leverage=pend.get("leverage", LEVERAGE),
                              bond=pend.get("bond"))
            if trades:
                append_trades(trades)
        # 记录本次执行摘要(web 操作卡展示受阻明细用;blocked 不进 trades.csv)
        last_exec = {
            "exec_date": date,
            "from_signal": exec_from,
            "blocked": [list(b) for b in blocked],
        }
        if pend:
            last_exec.update({
                key: pend.get(key, "")
                for key in (
                    "deployment_id", "family", "version", "spec_hash",
                    "data_fingerprint",
                )
            })
        acc["last_exec"] = last_exec
        # 2. 记录新 pending:本信号 → 下个交易日开盘执行
        # leverage = band_exposure (动态),空仓时 0;fallback 旧字段或常量
        target = signal["holdings"] if signal["in_market"] else []
        pend_leverage = float(signal.get("band_exposure",
                                          signal.get("leverage", LEVERAGE))) if target else 0.0
        # 债券轮动指令:必须带独立 defensive 授权;旧信号只会记录 requested,不会执行。
        pend_bond = build_pending_bond(signal)
        acc["pending"] = {
            "signal_date": date,
            "target": target,
            "leverage": pend_leverage,
            "bond": pend_bond,
            "deployment_id": signal.get("deployment_id", ""),
            "family": signal.get("family") or signal.get("strategy", ""),
            "version": signal.get("version") or signal.get("strategy_version", ""),
            "spec_hash": signal.get("spec_hash", ""),
            "data_fingerprint": signal.get("data_fingerprint", ""),
        }
        acc["last_date"] = date

    nav, pos_value, detail = valuation(acc, date)
    ret = nav / acc["init_capital"] - 1
    upsert_nav(date, nav, acc["cash"], pos_value, ret)
    save_account(acc)

    card = render_card(date, signal, decay, acc, nav, pos_value, detail, trades, blocked, names, exec_from)
    daily_file = None
    try:
        OBSIDIAN.mkdir(parents=True, exist_ok=True)
        daily_file = OBSIDIAN / f"今日操作_{date}.md"
        daily_file.write_text(card)
        (OBSIDIAN / "历史").mkdir(exist_ok=True)
        (OBSIDIAN / "历史" / f"{date}.md").write_text(card)
        print(f"  → Obsidian: {daily_file}")
    except Exception as e:
        print(f"  ⚠️ Warning: Failed to write to Obsidian path ({OBSIDIAN}): {e}")

    print(f"=== illiquidity v1.0 模拟盘(真实盘 T+1){date} ===")
    print(f"  今日开盘成交: 买{len([t for t in trades if t[3]=='BUY'])} 卖{len([t for t in trades if t[3]=='SELL'])} "
      f"受阻{len(blocked)}" + (f"(执行{exec_from}信号)" if exec_from else "(无待执行)"))
    nxt = (acc.get("pending") or {}).get("target") or []
    print(f"  明日开盘计划: {'建仓/持有 '+str(len(nxt))+' 只' if (signal['in_market'] and nxt) else '空仓观望'}")
    print(f"  总资产 {fmt(nav)} | 现金 {fmt(acc['cash'])} | 持仓 {len(detail)}只 {fmt(pos_value)} | 累计 {ret:+.2%}")
    if daily_file:
        print(f"  → Obsidian: {daily_file}")
    else:
        print("  → Obsidian: (Not written due to permission error)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
