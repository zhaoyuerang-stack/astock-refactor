"""生产环境全链路健康检查.

一次性检查: 数据/信号/launchd/Obsidian/时区/环境.

用法:
  cd /Users/kiki/astcok/factor_research
  /opt/homebrew/bin/python3 scripts/ops/prod_health_check.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd

CHINA_TZ = ZoneInfo("Asia/Shanghai")
SIGNALS = ROOT / "signals"
OBSIDIAN = Path("/Users/kiki/Personal Wiki/30.output/A股v2.0模拟盘")
OLD_OBSIDIAN = Path("/Users/kiki/Personal Wiki/30.output/A股模拟盘")
LOGS = ROOT / "logs/daily_update"


def check(ok, label, detail=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {label:<28} {detail}")


def main():
    now_cn = datetime.now(CHINA_TZ)
    now_local = datetime.now()
    print("=" * 70)
    print("  生产环境健康检查")
    print(f"  中国时间: {now_cn:%Y-%m-%d %H:%M} CST")
    print(f"  本地时间: {now_local:%Y-%m-%d %H:%M} {now_local.astimezone().tzinfo}")
    print("=" * 70)

    # ═══ Python 环境 ═══
    print("\n── Python 环境 ──")
    import platform
    check(True, "Python", f"{platform.python_version()} @ {sys.executable}")
    try:
        import numpy
        import pandas
        check(True, "依赖", f"pandas {pandas.__version__}, numpy {numpy.__version__}")
    except ImportError as e:
        check(False, "依赖", str(e))

    # ═══ 数据 ═══
    print("\n── 数据 ──")
    try:
        from strategies.small_cap import load_price_panels
        close, volume, amount = load_price_panels("2010-01-01")
        last_data_date = close.index[-1].date()
        data_age = (now_cn.date() - last_data_date).days
        data_fresh = data_age <= 3  # 周末+1天合理

        # 判断中国交易日
        cn_weekday = now_cn.weekday()  # 0=Mon
        is_weekend = cn_weekday >= 5
        cn_hour = now_cn.hour

        detail = f"最新={last_data_date} ({data_age}天前)"
        if is_weekend:
            detail += ", 周末无新数据正常"
        elif cn_hour < 16:
            detail += f", 今日数据预计{cn_hour+1:02d}:30后更新"
        check(data_fresh, "行情数据", detail)
        check(True, "股票数", f"{close.shape[1]}只 × {close.shape[0]}日")
    except Exception as e:
        check(False, "行情数据", str(e)[:50])

    # ═══ 信号 ═══
    print("\n── 信号 ──")
    sig_files = sorted(SIGNALS.glob("[0-9]*-[0-9]*-[0-9]*.json"))
    if sig_files:
        latest_sig = sig_files[-1]
        sig = json.loads(latest_sig.read_text())
        sig_date = sig["date"]
        sig_age = (now_cn.date() - pd.Timestamp(sig_date).date()).days
        sig_fresh = sig_age <= 3

        check(sig_fresh, "最新信号", f"{sig_date} ({sig_age}天前)")

        # 信号内容
        in_market = sig.get("in_market", False)
        band_exp = sig.get("band_exposure", 0)
        regime = sig.get("regime", "?")
        rotation = sig.get("rotation", {})
        top_n = sig.get("top_n", "?")

        check(True, "趋势择时", f"{'🟢持仓' if in_market else '🔴空仓'} (Band={band_exp:.2f}x)")
        check(True, "Regime", f"{'🟢 BULL' if regime == 'bull' else '🔴 BEAR'}, top_n={top_n}")
        if rotation.get("recommend_bond"):
            check(True, "轮动建议", f"配置 {rotation.get('bond_code', '?')} {rotation.get('bond_name', '?')}")
        else:
            check(True, "轮动建议", "全仓 illiq 股票")

        # 持仓
        holdings = sig.get("holdings", [])
        if holdings:
            check(True, "持仓", f"{len(holdings)}只: {', '.join(holdings[:5])}{'...' if len(holdings)>5 else ''}")
        else:
            check(not in_market, "持仓", "空仓")
    else:
        check(False, "信号文件", "无 signals/*.json, 跑 run_daily.py")

    # ═══ launchd ═══
    print("\n── 定时任务 ──")
    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        has_daily = "com.astcok.daily-update" in result.stdout
        has_weekly = "com.astcok.weekly-maintenance" in result.stdout
        check(has_daily, "数据更新(launchd)", "daily-update")
        check(has_weekly, "周维护(launchd)", "weekly-maintenance")

        # 最近日志
        logs_list = sorted(LOGS.glob("202?-*"), key=os.path.getmtime, reverse=True)
        if logs_list:
            latest_log = logs_list[-1]
            log_time = datetime.fromtimestamp(os.path.getmtime(latest_log))
            log_age_hrs = (now_local - log_time).total_seconds() / 3600
            ok_log = log_age_hrs < 48
            check(ok_log, "最近执行日志", f"{latest_log.name} ({log_age_hrs:.0f}小时前)")
        else:
            check(False, "执行日志", "无日志文件")
    except Exception as e:
        check(False, "launchd", str(e)[:50])

    # ═══ Obsidian 输出 ═══
    print("\n── Obsidian 输出 ──")
    # 主目录
    if OBSIDIAN.exists():
        daily_files = sorted(OBSIDIAN.glob("今日操作_*.md"), key=os.path.getmtime, reverse=True)
        if daily_files:
            latest_md = daily_files[0]
            md_time = datetime.fromtimestamp(os.path.getmtime(latest_md))
            md_age_hrs = (now_local - md_time).total_seconds() / 3600
            ok_md = md_age_hrs < 48
            check(ok_md, "今日操作(主)", f"{latest_md.name} ({md_age_hrs:.0f}小时前)")
        else:
            check(False, "今日操作(主)", "无文件")
    else:
        check(False, "输出目录(主)", f"不存在: {OBSIDIAN}")

    # 旧目录
    if OLD_OBSIDIAN.exists():
        old_files = list(OLD_OBSIDIAN.glob("今日操作*.md"))
        if old_files:
            check(True, "旧输出目录", f"A股模拟盘/ ({len(old_files)}个文件, 已废弃, 可手动删除)")
        else:
            check(True, "旧输出", "空目录")
    else:
        check(True, "旧输出", "目录不存在")

    # ═══ 时间对齐 ═══
    print("\n── 时间对齐 ──")
    cn_hour = now_cn.hour; cn_min = now_cn.minute
    cn_market_closed = (cn_hour > 15) or (cn_hour == 15 and cn_min >= 0)
    next_update = "今天 16:30 CST" if cn_hour < 16 else "明天 16:30 CST"
    check(True, "北京时间", f"{cn_hour:02d}:{cn_min:02d}")
    check(cn_market_closed, "中国收盘", "已收盘" if cn_market_closed else f"未收盘, 预计{next_update}更新数据")

    # ═══ 模拟盘状态 ═══
    print("\n── 模拟盘 ──")
    account_file = ROOT / "paper/account.json"
    if account_file.exists():
        acc = json.loads(account_file.read_text())
        nav = acc.get("cash", 0) + acc.get("position_value", 0)
        ret = nav / acc.get("init_capital", 1) - 1
        check(True, "账户", f"总资产={nav/1e4:.0f}万, 累计={ret:+.2%}")
    else:
        check(False, "账户", "paper/account.json 不存在")

    # ═══ 总结 ═══
    print("\n" + "=" * 70)
    print("  建议操作:")
    needs_update = data_age > 3 and not is_weekend and cn_market_closed
    if needs_update:
        print(f"  1. 数据过期{data_age}天 → 手动跑: python3 scripts/ops/scheduled_daily_update.py")
    elif data_age > 3 and not cn_market_closed:
        print("  数据过期正常(周末/待收盘), 系统将在北京时间16:30自动更新")
    if sig_files and sig_age > 3 and cn_market_closed:
        print(f"  2. 信号过期{sig_age}天 → 手动跑: python3 run_daily.py --no-update")
    if not needs_update and cn_market_closed:
        print("  一切正常 ✅")
    print("=" * 70)


if __name__ == "__main__":
    main()
