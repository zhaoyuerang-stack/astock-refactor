"""策略健康检查 + 多渠道告警。

检查项:
  1. decay_monitor — 因子失效/小盘逆风/夏普回落
  2. 最新信号 — 是否空仓、择时状态
  3. 数据新鲜度 — 最后交易日距今

告警渠道:
  - 终端输出 (默认)
  - macOS 桌面通知 (osascript)
  - Obsidian 告警卡片

用法:
  python3 scripts/ops/health_check.py              # 终端输出
  python3 scripts/ops/health_check.py --notify     # + 桌面通知
  python3 scripts/ops/health_check.py --obsidian   # + Obsidian 卡片

launchd 集成:
  配合 scheduled_daily_update.py，每日盘后自动跑。
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import pandas as pd

OBSIDIAN = Path("/Users/kiki/Personal Wiki/30.output/A股模拟盘")
SIGNALS_DIR = ROOT / "signals"
DECAY_FILE = ROOT / "reports" / "decay_status.json"


# ═══════════════════════════════════════════════════
# Health checks
# ═══════════════════════════════════════════════════

def check_decay() -> dict:
    """Read decay_status.json(scripts/ops/decay_monitor.py 写的多版本 schema:
    {"strategies": [{"strategy": "family.version", "decayed", "rolling_3y_sharpe_latest",
    "reasons", "action"}, ...]}),取部署身份对应的那一条(身份不明时退回第一个
    illiquidity.* 条目保底,不空手)。"""
    if not DECAY_FILE.exists():
        return {"status": "⚠️ 无数据", "alerts": [],
                "detail": "decay_status.json 缺失，先跑 decay_monitor"}

    d = json.loads(DECAY_FILE.read_text())
    family, version = d.get("family", "illiquidity"), d.get("version", "")
    name = f"{family}.{version}" if version else None
    strategies = d.get("strategies", [])
    entry = next((s for s in strategies if s.get("strategy") == name), None) if name else None
    if entry is None:
        entry = next((s for s in strategies if str(s.get("strategy", "")).startswith("illiquidity.")), {})
    status = d.get("status", "?")
    alerts = entry.get("reasons", [])
    return {
        "status": status,
        "alerts": alerts,
        "decayed": entry.get("decayed"),
        "rolling_3y_sharpe": entry.get("rolling_3y_sharpe_latest", 0),
        "strategy": entry.get("strategy", family),
        "updated": d.get("generated_at", "?"),
        "level": "critical" if entry.get("decayed") else "ok",
    }


def check_signal() -> dict:
    """Read latest signal, return status dict."""
    sig_files = sorted(SIGNALS_DIR.glob("[0-9]*-[0-9]*-[0-9]*.json"))
    if not sig_files:
        return {"status": "⚠️ 无信号", "level": "warning",
                "detail": "signals/ 为空，先跑 run_daily.py"}

    s = json.loads(sig_files[-1].read_text())
    in_mkt = s.get("in_market", False)
    dist = s.get("small_index_vs_ma16", 0)
    holdings = s.get("holdings", [])
    hmm_enabled = s.get("hmm_stress_enabled", False)
    hmm_prob = s.get("hmm_stress_prob", 0)

    alerts = []
    if not in_mkt:
        alerts.append(f"择时空仓 (小盘指数 {dist:+.2%} vs MA16)")
    if hmm_enabled and hmm_prob > 0.15:
        alerts.append(f"HMM压力 {hmm_prob:.1%}")

    num_holdings = len(holdings) if holdings else 0

    return {
        "status": "🟢 持仓" if in_mkt else "🔴 空仓",
        "in_market": in_mkt,
        "dist": dist,
        "holdings": num_holdings,
        "hmm_prob": hmm_prob,
        "alerts": alerts,
        "date": s.get("date", "?"),
        "level": "warning" if not in_mkt else "ok",
    }


def check_data_freshness() -> dict:
    """Check if price data is up to date."""
    daily_all = ROOT / "data_lake" / "price" / "daily_all.parquet"
    if not daily_all.exists():
        return {"status": "❌ 无数据", "level": "critical",
                "detail": "daily_all.parquet 缺失"}

    df = pd.read_parquet(daily_all, columns=["date"])
    last_date = pd.to_datetime(df["date"].max()).date()
    today = date.today()
    days_behind = (today - last_date).days

    if days_behind <= 1:
        return {"status": f"✅ 最新 ({last_date})", "level": "ok",
                "last_date": str(last_date), "days_behind": days_behind}
    elif days_behind <= 3:
        return {"status": f"⚠️ 滞后 {days_behind}d ({last_date})", "level": "warning",
                "last_date": str(last_date), "days_behind": days_behind}
    else:
        return {"status": f"❌ 严重滞后 {days_behind}d ({last_date})", "level": "critical",
                "last_date": str(last_date), "days_behind": days_behind}


# ═══════════════════════════════════════════════════
# Notification channels
# ═══════════════════════════════════════════════════

def notify_desktop(title: str, body: str, sound: bool = True):
    """macOS desktop notification via osascript."""
    try:
        script = f'display notification "{body}" with title "{title}"'
        if sound:
            script += ' sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        print(f"  🔔 桌面通知已发送: {title}")
    except Exception as e:
        print(f"  ⚠️ 通知发送失败: {e}")


def notify_obsidian(checks: dict, overall_level: str):
    """Write health check card to Obsidian vault."""
    OBSIDIAN.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    decay = checks.get("decay", {})
    signal = checks.get("signal", {})
    data = checks.get("data", {})

    icon = {"ok": "🟢", "warning": "🟡", "critical": "🔴"}.get(overall_level, "⚪")

    lines = [
        f"# {icon} 策略健康检查 · {now}",
        "",
        f"**整体状态: {overall_level.upper()}**",
        "",
        "## 📈 择时",
        f"- 状态: {signal.get('status', '?')}",
        f"- 小盘指数 vs MA16: {signal.get('dist', 0):+.2%}",
        f"- HMM压力: {signal.get('hmm_prob', 0):.1%}",
        f"- 持仓数: {signal.get('holdings', 0)} 只",
        f"- 信号日: {signal.get('date', '?')}",
        "",
        "## 📉 失效监控",
        f"- 状态: {decay.get('status', '?')}",
        f"- 策略: {decay.get('strategy', '?')}",
        f"- 滚动3年夏普: {decay.get('rolling_3y_sharpe', 0):.2f}(<0.5 触发退役复核)",
        f"- 更新: {decay.get('updated', '?')}",
    ]

    if decay.get("alerts"):
        lines += ["", "### ⚠️ 触发告警"]
        for a in decay["alerts"]:
            lines += [f"- {a}"]
    if signal.get("alerts"):
        for a in signal["alerts"]:
            if a not in (lines or []):
                lines += [f"- {a}"]

    lines += [
        "",
        "## 📊 数据",
        f"- 状态: {data.get('status', '?')}",
        f"- 最后交易日: {data.get('last_date', '?')}",
        "",
        f"> 自动生成 {now} | illiquidity v1.0",
    ]

    (OBSIDIAN / "健康检查.md").write_text("\n".join(lines) + "\n")
    print(f"  📝 Obsidian 卡片已更新: {OBSIDIAN / '健康检查.md'}")


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true", help="发送 macOS 桌面通知")
    ap.add_argument("--obsidian", action="store_true", help="写入 Obsidian 告警卡片")
    args = ap.parse_args()

    print("=" * 60)
    print(f"  策略健康检查  {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 60)

    # Run checks
    print("\n[1/3] 失效监控...")
    # Read existing decay data (run decay_monitor separately to refresh)
    decay = check_decay()
    print(f"  {decay['status']}")

    print("\n[2/3] 最新信号...")
    signal = check_signal()
    print(f"  {signal['status']}  |  小盘指数 {signal['dist']:+.2%}  |  HMM {signal['hmm_prob']:.1%}")

    print("\n[3/3] 数据新鲜度...")
    data = check_data_freshness()
    print(f"  {data['status']}")

    # Determine overall level
    levels = [decay.get("level", "ok"), signal.get("level", "ok"), data.get("level", "ok")]
    if "critical" in levels:
        overall = "critical"
    elif "warning" in levels:
        overall = "warning"
    else:
        overall = "ok"

    icon = {"ok": "🟢", "warning": "🟡", "critical": "🔴"}[overall]
    print(f"\n  {icon} 整体: {overall.upper()}")

    # Build notification body
    alerts = decay.get("alerts", []) + signal.get("alerts", [])
    notif_body = "; ".join(alerts) if alerts else (
        "一切正常" if overall == "ok" else "需关注"
    )

    # Notify
    if args.notify and overall != "ok":
        notify_desktop(
            f"A股策略 {icon} {overall.upper()}",
            notif_body[:200],
        )

    checks = {"decay": decay, "signal": signal, "data": data}
    if args.obsidian:
        notify_obsidian(checks, overall)

    # Return exit code for launchd/scripting
    if overall == "critical":
        sys.exit(2)
    elif overall == "warning":
        sys.exit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
