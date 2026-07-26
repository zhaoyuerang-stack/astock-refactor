"""app_config.log 统一 logger 工厂契约锁。

Run:
    cd factor_research && python3 -m pytest tests/test_app_config_log.py -q
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_config.log import get_logger  # noqa: E402


def test_logger_writes_to_stdout_not_stderr(capsys):
    log = get_logger("_logtest.sub.module")
    log.info("hello-stdout")
    captured = capsys.readouterr()
    assert "hello-stdout" in captured.out, "必须走 stdout(保持既有重定向行为)"
    assert captured.err == ""
    assert "INFO" in captured.out and "_logtest.sub.module" in captured.out


def test_idempotent_no_duplicate_handlers(capsys):
    for _ in range(5):
        get_logger("_logtest2.a")
        get_logger("_logtest2.b")
    top = logging.getLogger("_logtest2")
    assert len(top.handlers) == 1, "重复调用不得叠 handler(否则日志逐行翻倍)"
    get_logger("_logtest2.a").info("once")
    out = capsys.readouterr().out
    assert out.count("once") == 1


def test_level_default_info_suppresses_debug(capsys):
    log = get_logger("_logtest3.x")
    log.debug("hidden-debug")
    log.warning("visible-warning")
    out = capsys.readouterr().out
    assert "hidden-debug" not in out
    assert "visible-warning" in out


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
