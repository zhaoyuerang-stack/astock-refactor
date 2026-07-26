#!/bin/zsh
set -euo pipefail

ROOT="/Users/kiki/astcok/factor_research"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$AGENTS"
mkdir -p "$ROOT/logs/daily_update" "$ROOT/logs/weekly_maintenance" "$ROOT/logs/api" "$ROOT/logs/web" "$ROOT/logs/healthcheck"
mkdir -p "$ROOT/reports/ops/daily_update" "$ROOT/reports/ops/weekly_maintenance"

for job in com.astcok.daily-update com.astcok.weekly-maintenance com.astcok.api com.astcok.web com.factor.healthcheck; do
  cp "$ROOT/scripts/ops/$job.plist" "$AGENTS/$job.plist"
  launchctl bootout "gui/$UID" "$AGENTS/$job.plist" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$AGENTS/$job.plist"
done

echo "Installed launchd jobs:"
echo "  com.astcok.daily-update        weekdays 00:30 and 01:30 local time; script gates at China 16:30"
echo "  com.astcok.weekly-maintenance  Sunday 02:30 local time"
echo "    steps: aggregate → raw_close → quality → decay_monitor → tradability → live_readiness"
echo "    decay_monitor writes reports/decay_status.json (read by live_readiness + run_daily)"
echo "  com.astcok.api                 常驻 FastAPI :8011(KeepAlive 崩了自动拉起;--reload 代码热更)"
echo "  com.astcok.web                 常驻 Next.js :3000(KeepAlive;改前端代码后 cd web && npm run build 即生效)"
echo "  com.factor.healthcheck         每天本机 05:30(北京 20:30,日更后)健康检查;alert 级推 Obsidian+桌面,warning 仅入日志"
echo
echo "Manual trigger:"
echo "  launchctl kickstart -k gui/$UID/com.astcok.daily-update"
echo "  launchctl kickstart -k gui/$UID/com.astcok.weekly-maintenance"
echo "  launchctl kickstart -k gui/$UID/com.astcok.api"
echo "  launchctl kickstart -k gui/$UID/com.astcok.web"
echo "  launchctl kickstart -k gui/$UID/com.factor.healthcheck"
