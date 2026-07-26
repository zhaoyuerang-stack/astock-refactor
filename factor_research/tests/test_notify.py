"""notify 通道 + 日更告警去重/恢复逻辑回归测试。

风格: 自检脚本(python3 tests/test_notify.py),与 test_all.sh 一致。
全程 mock,不真发通知 / 不联网。
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ops import notify


def test_applescript_escape():
    """error 正文里的双引号/反斜杠被转义,不破坏 osascript。"""
    out = notify._escape_applescript('say "hi" \\ bye')
    assert '\\"' in out
    assert '\\\\' in out
    print("✅ test_applescript_escape passed")


def test_send_alert_desktop_only_when_no_remote():
    """无 notify_config.json → 只走桌面,返回 {'desktop': True}。"""
    with mock.patch.object(notify, "notify_desktop", return_value=True) as m_desk, \
         mock.patch.object(notify, "_load_remote_config", return_value={}):
        results = notify.send_alert("t", "b", obsidian=False)
    assert results == {"desktop": True}
    m_desk.assert_called_once()
    print("✅ test_send_alert_desktop_only_when_no_remote passed")


def test_send_alert_never_raises_on_bad_channel():
    """远程通道连不通 → 该通道 False,send_alert 不抛,桌面仍成功。"""
    # localhost:1 立即 connection refused(不等 10s 超时)
    bad = {"bark": {"url": "http://127.0.0.1:1/unreachable"}}
    with mock.patch.object(notify, "notify_desktop", return_value=True), \
         mock.patch.object(notify, "_load_remote_config", return_value=bad):
        results = notify.send_alert("t", "b", obsidian=False)
    assert results["desktop"] is True
    assert results["bark"] is False
    print("✅ test_send_alert_never_raises_on_bad_channel passed")


def test_send_obsidian_writes_card():
    """Obsidian 通道把告警卡片追加到 vault(用临时 vault,绝不碰真实库)。"""
    with tempfile.TemporaryDirectory() as d:
        old = os.environ.get("OBSIDIAN_VAULT")
        os.environ["OBSIDIAN_VAULT"] = d
        try:
            ok = notify._send_obsidian("🔴 A股日更失败 2026-06-18", "status=failed | error:boom")
        finally:
            if old is None:
                os.environ.pop("OBSIDIAN_VAULT", None)
            else:
                os.environ["OBSIDIAN_VAULT"] = old
        assert ok is True
        files = list((Path(d) / "30.output" / "2.[A]inbox" / "ai_data").glob("运维告警_*.md"))
        assert len(files) == 1, f"应生成 1 个月度告警文件,实际 {files}"
        content = files[0].read_text(encoding="utf-8")
        assert "status=failed" in content
        assert "A股日更失败" in content
    print("✅ test_send_obsidian_writes_card passed")


def _failed_report(status="failed"):
    return {
        "status": status,
        "error": 'boom "with quote"',
        "price_update": {"ok": False, "error": "x"},
        "signal": {"generated": False, "reason": "stale_data"},
    }


def test_price_unit_alert_body_includes_category():
    from scripts.ops import scheduled_daily_update as sdu

    body = sdu._alert_body(
        {
            "status": "failed",
            "price_update": {
                "ok": False,
                "error": "bad units",
                "error_category": "price_unit_contract",
            },
        }
    )

    assert "price_unit_contract" in body


def test_daily_alert_dedup():
    """同一天同一 failed 只推一次(launchd 盘后重试 4 次不刷屏)。"""
    from scripts.ops import scheduled_daily_update as sdu
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "2026-06-18.json"
        with mock.patch("scripts.ops.notify.send_alert") as m_send:
            sdu.maybe_alert(_failed_report(), rp)   # 第一次:推
            sdu.maybe_alert(_failed_report(), rp)   # 第二次:去重,不推
        assert m_send.call_count == 1, f"期望推送 1 次,实际 {m_send.call_count}"
        assert (Path(d) / ".alert_2026-06-18.json").exists()
    print("✅ test_daily_alert_dedup passed")


def test_daily_alert_recovery():
    """failed 后转 ok → 发恢复通知 + 清哨兵。"""
    from scripts.ops import scheduled_daily_update as sdu
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "2026-06-18.json"
        sentinel = Path(d) / ".alert_2026-06-18.json"
        with mock.patch("scripts.ops.notify.send_alert") as m_send:
            sdu.maybe_alert(_failed_report(), rp)   # 推失败 + 写哨兵
            assert sentinel.exists()
            sdu.maybe_alert({"status": "ok"}, rp)   # 恢复
        assert m_send.call_count == 2, f"期望 2 次(失败+恢复),实际 {m_send.call_count}"
        recovery_title = m_send.call_args_list[1].args[0]
        assert "恢复" in recovery_title
        assert not sentinel.exists(), "恢复后哨兵应清除"
    print("✅ test_daily_alert_recovery passed")


def test_daily_alert_ok_silent():
    """正常 ok(无先前失败)→ 不打扰,不发通知。"""
    from scripts.ops import scheduled_daily_update as sdu
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "2026-06-18.json"
        with mock.patch("scripts.ops.notify.send_alert") as m_send:
            sdu.maybe_alert({"status": "ok"}, rp)
        assert m_send.call_count == 0
    print("✅ test_daily_alert_ok_silent passed")


def test_daily_alert_skipped_no_alert():
    """skipped_* 状态不告警(锁竞争/太早/已 ok 都是正常)。"""
    from scripts.ops import scheduled_daily_update as sdu
    with tempfile.TemporaryDirectory() as d:
        rp = Path(d) / "2026-06-18.json"
        with mock.patch("scripts.ops.notify.send_alert") as m_send:
            sdu.maybe_alert({"status": "skipped_already_ok"}, rp)
            sdu.maybe_alert({"status": "skipped_before_china_time"}, rp)
        assert m_send.call_count == 0
    print("✅ test_daily_alert_skipped_no_alert passed")


if __name__ == "__main__":
    test_applescript_escape()
    test_send_alert_desktop_only_when_no_remote()
    test_send_alert_never_raises_on_bad_channel()
    test_send_obsidian_writes_card()
    test_daily_alert_dedup()
    test_daily_alert_recovery()
    test_daily_alert_ok_silent()
    test_daily_alert_skipped_no_alert()
    print("\n🎉 All notify tests passed!")
