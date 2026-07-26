"""Tests for the 5-Component Agent Control Loop."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.autoresearch.agent_loop import (
    Actuator,
    Controller,
    Intent,
    Memory,
    Sensor,
    SensorOutput,
    validate_target_file,
)
from providers.llm_adapter import NullAdapter


class MockSensor(Sensor):
    def __init__(self, sequence: list[SensorOutput]):
        self.sequence = sequence
        self.index = 0

    def sense(self, intent: Intent, cwd: Path) -> SensorOutput:
        if self.index < len(self.sequence):
            out = self.sequence[self.index]
            self.index += 1
            return out
        return SensorOutput(passed=True, output="", exit_code=0)


class MockActuator(Actuator):
    def __init__(self):
        super().__init__(NullAdapter())
        self.call_count = 0

    def act(self, intent: Intent, state: dict, feedback: str, cwd: Path) -> dict[str, str]:
        self.call_count += 1
        return {"fake_file.py": "print('fixed')"}


class JsonAdapter(NullAdapter):
    def __init__(self, response: str):
        self.response = response

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        return self.response


class EscapingActuator(Actuator):
    def __init__(self):
        super().__init__(NullAdapter())

    def act(self, intent: Intent, state: dict, feedback: str, cwd: Path) -> dict[str, str]:
        return {"../evil.py": "print('escape')"}


def test_controller_success_path(tmp_path):
    intent = Intent("Make it pass", ["fake_file.py"], "mock_cmd")
    sensor = MockSensor([
        SensorOutput(passed=False, output="Test error on line 4", exit_code=1),
        SensorOutput(passed=True, output="All green", exit_code=0),
    ])
    actuator = MockActuator()
    state_file = tmp_path / "state.json"
    memory = Memory(state_file)

    controller = Controller(
        intent=intent,
        actuator=actuator,
        sensor=sensor,
        memory=memory,
        cwd=tmp_path,
        max_rounds=5
    )

    success = controller.run()
    assert success is True
    assert actuator.call_count == 1
    state = memory.load()
    assert state["active"] is False
    assert state["round"] == 1


def test_controller_max_rounds_path(tmp_path):
    intent = Intent("Always fail", ["fake_file.py"], "mock_cmd")
    # Provide 6 failures so it exceeds limit of 3
    sensor = MockSensor([
        SensorOutput(passed=False, output="Error 1", exit_code=1),
        SensorOutput(passed=False, output="Error 2", exit_code=1),
        SensorOutput(passed=False, output="Error 3", exit_code=1),
        SensorOutput(passed=False, output="Error 4", exit_code=1),
    ])
    actuator = MockActuator()
    state_file = tmp_path / "state.json"
    memory = Memory(state_file)

    controller = Controller(
        intent=intent,
        actuator=actuator,
        sensor=sensor,
        memory=memory,
        cwd=tmp_path,
        max_rounds=3
    )

    success = controller.run()
    assert success is False
    assert actuator.call_count == 3
    state = memory.load()
    assert state["round"] == 4  # Exceeded limit of 3
    assert state["active"] is False


def test_controller_anti_guessing_path(tmp_path):
    intent = Intent("Repeating error", ["fake_file.py"], "mock_cmd")
    # Same error output "Fatal Error A" returned twice
    sensor = MockSensor([
        SensorOutput(passed=False, output="Fatal Error A", exit_code=1),
        SensorOutput(passed=False, output="Fatal Error A", exit_code=1),
    ])
    actuator = MockActuator()
    state_file = tmp_path / "state.json"
    memory = Memory(state_file)

    controller = Controller(
        intent=intent,
        actuator=actuator,
        sensor=sensor,
        memory=memory,
        cwd=tmp_path,
        max_rounds=5
    )

    success = controller.run()
    # Should abort immediately after round 2 detects repeating error
    assert success is False
    assert actuator.call_count == 1
    state = memory.load()
    assert state["round"] == 2
    assert "repeating_error" in state["history"][-1]["status"]


def test_sensor_rejects_shell_control_characters(tmp_path):
    intent = Intent("No shell injection", ["fake_file.py"], "pytest -q; rm -rf /tmp/agent-loop")

    result = Sensor().sense(intent, tmp_path)

    assert result.passed is False
    assert result.exit_code == -2
    assert "shell control" in result.output


def test_sensor_rejects_non_allowlisted_executable(tmp_path):
    intent = Intent("No arbitrary command", ["fake_file.py"], "curl https://example.com/script.sh")

    result = Sensor().sense(intent, tmp_path)

    assert result.passed is False
    assert result.exit_code == -2
    assert "not allowed" in result.output


def test_sensor_executes_verify_command_as_argv_without_shell(monkeypatch, tmp_path):
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs

        class Result:
            stdout = "ok"
            stderr = ""
            returncode = 0

        return Result()

    monkeypatch.setattr("factory.autoresearch.agent_loop.subprocess.run", fake_run)
    intent = Intent("Run tests", ["fake_file.py"], "pytest -q tests/test_agent_loop.py")

    result = Sensor().sense(intent, tmp_path)

    assert result.passed is True
    assert calls["argv"] == ["pytest", "-q", "tests/test_agent_loop.py"]
    assert "shell" not in calls["kwargs"]


def test_target_file_validation_rejects_escape_paths(tmp_path):
    assert validate_target_file("safe/file.py", tmp_path) == tmp_path.resolve() / "safe" / "file.py"

    with pytest.raises(ValueError, match="absolute"):
        validate_target_file(str((tmp_path / "outside.py").resolve()), tmp_path)
    with pytest.raises(ValueError, match="Parent|parent"):
        validate_target_file("../outside.py", tmp_path)


def test_actuator_filters_unsafe_or_unauthorized_paths(tmp_path):
    response = """```json
{
  "explanation": "try mixed writes",
  "files": [
    {"path": "allowed.py", "content": "print('ok')"},
    {"path": "../evil.py", "content": "print('escape')"},
    {"path": "/tmp/evil.py", "content": "print('absolute')"},
    {"path": "other.py", "content": "print('unauthorized')"}
  ]
}
```"""
    actuator = Actuator(JsonAdapter(response))
    intent = Intent("Only allowed", ["allowed.py"], "pytest -q")

    edits = actuator.act(intent, {"history": []}, "failed", tmp_path)

    assert edits == {"allowed.py": "print('ok')"}


def test_controller_rejects_actuator_escape_edits(tmp_path):
    intent = Intent("Reject escaped write", ["fake_file.py"], "pytest -q")
    sensor = MockSensor([SensorOutput(passed=False, output="needs edit", exit_code=1)])
    state_file = tmp_path / "state.json"

    controller = Controller(
        intent=intent,
        actuator=EscapingActuator(),
        sensor=sensor,
        memory=Memory(state_file),
        cwd=tmp_path,
        max_rounds=1,
    )

    success = controller.run()

    assert success is False
    assert not (tmp_path.parent / "evil.py").exists()
    assert "actuator_error" in Memory(state_file).load()["history"][-1]["status"]
