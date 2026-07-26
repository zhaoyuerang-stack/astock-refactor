"""Agent session persistence tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from services.agent.sessions import append_message, create_session, get_session


def test_agent_session_persists_messages(tmp_path):
    session = create_session(page_context="overview", title="测试会话", user_id="u-local", store_dir=tmp_path)
    session_id = session["session_id"]

    append_message(session_id, "user", "这个系统怎么用", store_dir=tmp_path)
    append_message(
        session_id,
        "assistant",
        "先看总览。",
        metadata={"tool": "strategies", "risk": "readonly"},
        store_dir=tmp_path,
    )

    loaded = get_session(session_id, store_dir=tmp_path)
    assert loaded["session_id"] == session_id
    assert loaded["user_id"] == "u-local"
    assert loaded["page_context"] == "overview"
    assert [m["role"] for m in loaded["messages"]] == ["user", "assistant"]
    assert loaded["messages"][1]["metadata"]["tool"] == "strategies"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_agent_session_persists_messages(Path(d))
    print("Agent session tests passed.")
