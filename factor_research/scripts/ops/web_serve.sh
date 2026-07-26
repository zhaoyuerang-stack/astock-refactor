#!/bin/zsh
# launchd com.astcok.web 的启动包装 —— 端口 3000 优雅退让,根治 crash-loop。
#
# 行为:
#   - 3000 已被占(通常是手动 `npm run dev`,或已有生产实例)→ 静默 exit 0 退让,
#     绝不抢端口、绝不 EADDRINUSE crash;配合 plist KeepAlive=true + ThrottleInterval=60,
#     launchd 每 60s 温和探测一次,你停掉 dev 后最多 60s 内自动接管。
#   - 3000 空闲 → exec 生产 next start 接管(exec 替换进程,launchd 直接监控 next 本体,崩了能拉起)。
#
# 退出码:0 = 已退让(端口有人服务) | 非 0 = next start 自身异常。
cd /Users/kiki/astcok/web || exit 0

if /usr/sbin/lsof -iTCP:3000 -sTCP:LISTEN -n -P >/dev/null 2>&1; then
  exit 0   # 3000 已有服务(dev 或已在跑),静默退让
fi

exec npm run start -- -p 3000
