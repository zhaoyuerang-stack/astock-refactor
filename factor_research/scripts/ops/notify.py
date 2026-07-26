"""
统一运维告警通道(生产)。

设计原则:**告警是旁路,绝不抛异常**。任一通道失败只记日志,不影响调用方
主流程(日更的 status / launchd 返回码绝不能被告警拖垮)。

配置分两处(开关与密钥物理分离,因 settings.yaml 进 git):
- 非敏感开关: ``app_config/settings.yaml::notify`` (desktop/alert_on/recovery)
  → ``get_settings().notify``,由调用方读取。
- 远程通道密钥: ``data_lake/agent/notify_config.json`` (gitignored,不进 git):
    {
      "bark":  {"url": "https://api.day.app/<你的key>"},
      "email": {"host":"smtp.qq.com","port":465,"user":"x@qq.com",
                "password":"<授权码>","to":"x@qq.com"}
    }
  缺该文件 → 只发桌面通知(零依赖开箱即用)。

仅用标准库(urllib/smtplib/osascript),无第三方依赖。
"""
import json
import os
import smtplib
import ssl
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTIFY_CONFIG_PATH = ROOT / "data_lake/agent/notify_config.json"


def _escape_applescript(text: str) -> str:
    """转义 AppleScript 字符串里的反斜杠和双引号。

    report 的 error 正文里常含双引号,不转义会破坏 osascript 脚本(health_check
    旧版未转义,是潜在 bug)。先转反斜杠再转双引号,顺序不能反。
    """
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify_desktop(title: str, body: str, sound: bool = True) -> bool:
    """macOS 桌面通知(osascript)。非 macOS 或失败返回 False,绝不抛。"""
    try:
        t, b = _escape_applescript(title), _escape_applescript(body)
        script = f'display notification "{b}" with title "{t}"'
        if sound:
            script += ' sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        return True
    except Exception as exc:  # pragma: no cover - 平台/超时兜底
        print(f"  ⚠️ 桌面通知失败: {exc}")
        return False


def _send_obsidian(title: str, body: str) -> bool:
    """追加一条告警卡片到 Obsidian vault,失败返回 False,绝不抛。

    vault 路径同 health_check 约定: 环境变量 ``OBSIDIAN_VAULT``,默认 ``~/Personal Wiki``。
    按月滚动单文件 append(``30.output/2.[A]inbox/ai_data/运维告警_YYYY-MM.md``);多来源
    公用(日更 / 健康检查等),每条 title 区分来源;日更侧去重已在上游 ``maybe_alert`` 完成。
    """
    try:
        vault = os.environ.get("OBSIDIAN_VAULT", str(Path.home() / "Personal Wiki"))
        out_dir = Path(vault) / "30.output" / "2.[A]inbox" / "ai_data"
        out_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        path = out_dir / f"运维告警_{now:%Y-%m}.md"
        if not path.exists():
            path.write_text(
                f"# A股运维告警日志 {now:%Y-%m}\n\n"
                "> 由 `scripts/ops/notify.py` 自动追加(日更/健康检查等),仅研究运维提醒。\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n## {now:%Y-%m-%d %H:%M:%S} · {title}\n\n{body}\n")
        print(f"  📝 Obsidian 告警已写入: {path}")
        return True
    except Exception as exc:
        print(f"  ⚠️ Obsidian 写入失败: {exc}")
        return False


def _load_remote_config() -> dict:
    """读 gitignored 远程通道密钥;缺文件或解析失败返回空(只走桌面)。"""
    if not NOTIFY_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(NOTIFY_CONFIG_PATH.read_text()) or {}
    except Exception as exc:
        print(f"  ⚠️ notify_config.json 解析失败,跳过远程通道: {exc}")
        return {}


def _send_bark(cfg: dict, title: str, body: str) -> bool:
    """Bark(iOS 推送)。URL 形如 https://api.day.app/<key>/<title>/<body>。"""
    url = cfg.get("url")
    if not url:
        return False
    try:
        full = f"{url.rstrip('/')}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}"
        with urllib.request.urlopen(full, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        print(f"  ⚠️ Bark 推送失败: {exc}")
        return False


def _send_email(cfg: dict, title: str, body: str) -> bool:
    """SMTP over SSL(QQ/163/Gmail 等)。需 host/user/password,to 默认发给自己。"""
    try:
        host = cfg["host"]
        port = int(cfg.get("port", 465))
        user = cfg["user"]
        password = cfg["password"]
        to = cfg.get("to", user)
        recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = user
        msg["To"] = to
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
        return True
    except Exception as exc:
        print(f"  ⚠️ 邮件推送失败: {exc}")
        return False


def send_alert(title: str, body: str, *, desktop: bool = True, obsidian: bool = True) -> dict:
    """统一告警入口:桌面 + Obsidian + 所有已配置远程通道 fan-out。

    每通道独立 try/except,返回 {通道: 是否成功},**绝不抛**。
    """
    results: dict = {}
    if desktop:
        results["desktop"] = notify_desktop(title, body)
    if obsidian:
        results["obsidian"] = _send_obsidian(title, body)
    remote = _load_remote_config()
    if "bark" in remote:
        results["bark"] = _send_bark(remote["bark"], title, body)
    if "email" in remote:
        results["email"] = _send_email(remote["email"], title, body)
    sent = [k for k, ok in results.items() if ok]
    print(f"  🔔 告警通道结果: {results} (成功: {sent or '无'})")
    return results


if __name__ == "__main__":
    # 自测: python3 scripts/ops/notify.py
    # 刻意 obsidian=False —— 自测绝不写真实 Obsidian vault(避免污染);
    # Obsidian 写入逻辑由 tests/test_notify.py 用临时 vault 覆盖。
    out = send_alert("A股日更告警(自测)", '测试 "引号" 与通道可用性。', obsidian=False)
    print(json.dumps(out, ensure_ascii=False))
