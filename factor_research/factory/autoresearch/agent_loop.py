"""Minimal 5-Component Agent Control Loop Engine.

Provides programmatic and command-line execution interfaces.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Add project root to sys.path to resolve imports cleanly
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app_config.log import get_logger
from providers.llm_adapter import LLMAdapter, get_adapter

logger = get_logger(__name__)

ALLOWED_VERIFY_COMMANDS = {"python", "python3", "pytest", "ruff", "mypy"}
SHELL_CONTROL_CHARS = re.compile(r"[;&|<>`$]")


@dataclass
class Intent:
    description: str
    target_files: list[str]
    verify_command: str


@dataclass
class SensorOutput:
    passed: bool
    output: str
    exit_code: int


class Sensor:
    """The Judgement component. Runs verification commands to check if intent is satisfied."""

    def _verify_argv(self, command: str) -> list[str]:
        if not command or not command.strip():
            raise ValueError("verify command is empty")
        if SHELL_CONTROL_CHARS.search(command):
            raise ValueError("verify command contains shell control characters")
        argv = shlex.split(command)
        if not argv:
            raise ValueError("verify command is empty")
        executable = Path(argv[0]).name
        if executable not in ALLOWED_VERIFY_COMMANDS:
            allowed = ", ".join(sorted(ALLOWED_VERIFY_COMMANDS))
            raise ValueError(f"verify command executable is not allowed: {executable}; allowed: {allowed}")
        return argv

    def sense(self, intent: Intent, cwd: Path) -> SensorOutput:
        try:
            argv = self._verify_argv(intent.verify_command)
            res = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=120  # Prevent infinite hangs
            )
            # Combine stdout and stderr
            output = (res.stdout or "") + (res.stderr or "")
            passed = (res.returncode == 0)
            return SensorOutput(passed=passed, output=output, exit_code=res.returncode)
        except subprocess.TimeoutExpired as e:
            output = f"Command timed out after 120s.\n{(e.stdout or '')}\n{(e.stderr or '')}"
            return SensorOutput(passed=False, output=output, exit_code=-1)
        except Exception as e:
            return SensorOutput(passed=False, output=f"Sensor execution failed: {str(e)}", exit_code=-2)


def validate_target_file(rel_path: str, cwd: Path) -> Path:
    """Return a workspace-local path for an allowed target file."""
    if not rel_path or not rel_path.strip():
        raise ValueError("target file path is empty")
    path = Path(rel_path)
    if path.is_absolute():
        raise ValueError(f"absolute target paths are not allowed: {rel_path}")
    if ".." in path.parts:
        raise ValueError(f"parent traversal is not allowed in target path: {rel_path}")

    cwd_resolved = cwd.resolve()
    full_path = (cwd_resolved / path).resolve()
    try:
        full_path.relative_to(cwd_resolved)
    except ValueError as exc:
        raise ValueError(f"target path escapes workspace: {rel_path}") from exc
    return full_path


class Actuator:
    """The Execution component. Queries the AI model to perform file modifications."""

    def __init__(self, adapter: LLMAdapter):
        self.adapter = adapter

    def act(self, intent: Intent, state: dict, feedback: str, cwd: Path) -> dict[str, str]:
        # Gather current file contents
        files_context = {}
        allowed_paths = set()
        for rel_path in intent.target_files:
            full_path = validate_target_file(rel_path, cwd)
            allowed_paths.add(rel_path)
            if full_path.exists():
                files_context[rel_path] = full_path.read_text(encoding="utf-8")
            else:
                files_context[rel_path] = ""

        # System prompt instructions
        system_prompt = (
            "You are the execution Actuator in a software agent control loop.\n"
            "Your task is to modify the allowed files to satisfy the user's intent and resolve any verification errors.\n"
            "You MUST only modify the files listed in the target files list.\n"
            "Return a single JSON markdown code block (fenced with ```json and ```) containing your modifications.\n"
            "Do NOT include any explanation, conversational text, or commentary outside the JSON code block.\n"
            "Your JSON output MUST follow this exact schema:\n"
            "{\n"
            "  \"explanation\": \"Brief description of changes\",\n"
            "  \"files\": [\n"
            "    {\n"
            "      \"path\": \"relative/path/to/file\",\n"
            "      \"content\": \"The absolute full new content of the file\"\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        user_content = {
            "intent": intent.description,
            "allowed_target_files": intent.target_files,
            "sensor_feedback": feedback,
            "current_files_contents": files_context,
            "attempt_history": state.get("history", [])[-3:]  # Pass the last few rounds
        }

        user_prompt = json.dumps(user_content, indent=2, ensure_ascii=False)

        # Execute LLM completion
        response = self.adapter.complete(system_prompt, user_prompt, max_tokens=4000)
        if not response:
            raise RuntimeError("LLM adapter failed to return a response. Check API configuration or environment keys.")

        # Parse JSON block
        parsed = self._parse_json(response)
        if not parsed or "files" not in parsed:
            raise RuntimeError(f"LLM did not return a valid JSON matching the schema.\nResponse received:\n{response}")

        modifications = {}
        for item in parsed["files"]:
            path = item.get("path")
            content = item.get("content")
            if not isinstance(path, str) or not isinstance(content, str):
                logger.warning(f"[Actuator Warning] LLM returned invalid file item: {item}")
                continue
            try:
                validate_target_file(path, cwd)
            except ValueError as exc:
                logger.warning(f"[Actuator Warning] LLM returned unsafe file path: {exc}")
                continue
            if path in allowed_paths:
                modifications[path] = content
            else:
                logger.warning(f"[Actuator Warning] LLM tried to modify unauthorized file: {path}")

        return modifications

    def _parse_json(self, text: str) -> dict | None:
        # Match ```json ... ```
        m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # Fallback: scan for first '{' and last '}'
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1].strip())
            except Exception:
                pass
        return None


class Memory:
    """The State component. Manages persistence of the loop status on disk."""

    def __init__(self, state_path: Path):
        self.state_path = state_path

    def load(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "intent": None,
            "round": 0,
            "history": [],
            "last_error": "",
            "active": False
        }

    def save(self, state: dict):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


class Controller:
    """The Control component. Directs the orchestrator loop and applies safeguards."""

    def __init__(self, intent: Intent, actuator: Actuator, sensor: Sensor, memory: Memory, cwd: Path, max_rounds: int = 5):
        self.intent = intent
        self.actuator = actuator
        self.sensor = sensor
        self.memory = memory
        self.cwd = cwd
        self.max_rounds = max_rounds

    def run(self) -> bool:
        logger.info(f"\n[Controller] Starting execution loop for Intent: '{self.intent.description}'")
        logger.info(f"[Controller] Target files: {self.intent.target_files}")
        logger.info(f"[Controller] Verification command: '{self.intent.verify_command}'")

        # Load current state and initialize goal parameters
        state = self.memory.load()
        state["intent"] = {
            "description": self.intent.description,
            "target_files": self.intent.target_files,
            "verify_command": self.intent.verify_command
        }
        state["active"] = True
        self.memory.save(state)

        try:
            while True:
                # 1. Run Sensor
                logger.info("\n=== [Sensor] Executing verification checks ===")
                sensor_res = self.sensor.sense(self.intent, self.cwd)

                # 2. Check if goal is accomplished
                if sensor_res.passed:
                    logger.info("\n🎉 [Sensor] SUCCESS! All verification checks passed.")
                    state["active"] = False
                    state["last_error"] = ""
                    state["history"].append({
                        "round": state["round"],
                        "status": "passed",
                        "output": "All checks passed successfully."
                    })
                    self.memory.save(state)
                    return True

                # Sensor failed. Increment round
                state["round"] += 1
                curr_round = state["round"]
                logger.warning(f"\n❌ [Sensor] FAILED. Round {curr_round}/{self.max_rounds}")

                # Output snippet (max 300 chars)
                snippet = sensor_res.output.strip()
                if len(snippet) > 400:
                    snippet = snippet[:400] + "\n... (truncated)"
                logger.info(f"[Sensor Output]:\n{snippet}")

                # 3. Guard: Max rounds limit
                if curr_round > self.max_rounds:
                    logger.warning(f"\n🛑 [Controller] Maximum iteration limit ({self.max_rounds}) reached. Aborting.")
                    state["active"] = False
                    state["history"].append({
                        "round": curr_round,
                        "status": "failed_max_rounds",
                        "output": sensor_res.output
                    })
                    self.memory.save(state)
                    return False

                # 4. Guard: Repeating error detection (anti-guessing)
                curr_error = sensor_res.output.strip()
                prev_error = state.get("last_error", "").strip()
                if curr_error == prev_error and curr_round > 1:
                    logger.warning("\n🛑 [Controller] Identical error output received twice in a row. Aborting to prevent wild guessing.")
                    state["active"] = False
                    state["history"].append({
                        "round": curr_round,
                        "status": "failed_repeating_error",
                        "output": sensor_res.output
                    })
                    self.memory.save(state)
                    return False

                state["last_error"] = curr_error

                # 5. Call Actuator
                logger.info("\n🤖 [Actuator] Querying LLM adapter for edits...")
                try:
                    edits = self.actuator.act(self.intent, state, sensor_res.output, self.cwd)
                    if not edits:
                        logger.warning("[Controller Warning] Actuator returned no changes.")
                    else:
                        logger.info(f"[Actuator] Suggested edits for files: {list(edits.keys())}")
                        # Apply changes to disk
                        for rel_path, content in edits.items():
                            if rel_path not in self.intent.target_files:
                                raise RuntimeError(f"Actuator returned unauthorized file: {rel_path}")
                            full_path = validate_target_file(rel_path, self.cwd)
                            full_path.parent.mkdir(parents=True, exist_ok=True)
                            full_path.write_text(content, encoding="utf-8")
                            logger.info(f"[Actuator] Wrote updated content to {rel_path}")
                except Exception as e:
                    logger.warning(f"💥 [Controller Error] Actuator execution failed: {e}")
                    state["active"] = False
                    state["history"].append({
                        "round": curr_round,
                        "status": f"actuator_error: {str(e)}",
                        "output": sensor_res.output
                    })
                    self.memory.save(state)
                    return False

                # Update memory for iteration log
                state["history"].append({
                    "round": curr_round,
                    "status": "failed_retry",
                    "edits_applied": list(edits.keys()) if edits else []
                })
                self.memory.save(state)

        except KeyboardInterrupt:
            logger.warning("\n🛑 [Controller] Interrupted by user. Saving state...")
            state["active"] = False
            self.memory.save(state)
            return False


def main():
    parser = argparse.ArgumentParser(description="5-Component Autonomous Agent Run-Loop Tool")
    parser.add_argument("--intent", required=True, help="Description of the goal/intent")
    parser.add_argument("--files", nargs="+", required=True, help="Target files the loop is allowed to modify")
    parser.add_argument("--verify", required=True, help="Command to run to verify success (exits 0 on success)")
    parser.add_argument("--max-rounds", type=int, default=5, help="Maximum iteration rounds (default: 5)")
    parser.add_argument("--state-file", default="data_lake/governance/agent_loop_state.json", help="Path to state file")
    args = parser.parse_args()

    # Resolve paths
    cwd = Path.cwd()
    state_path = cwd / args.state_file

    # Build components
    intent = Intent(
        description=args.intent,
        target_files=args.files,
        verify_command=args.verify
    )
    sensor = Sensor()
    memory = Memory(state_path)

    # Resolve LLM adapter
    adapter = get_adapter()
    if not adapter.available():
        print("❌ Error: AI Model adapter is not available. Please verify api_key or config in settings.yaml")
        sys.exit(1)

    actuator = Actuator(adapter)

    controller = Controller(
        intent=intent,
        actuator=actuator,
        sensor=sensor,
        memory=memory,
        cwd=cwd,
        max_rounds=args.max_rounds
    )

    success = controller.run()
    if success:
        print("\n✨ Mission accomplished successfully!")
        sys.exit(0)
    else:
        print("\n❌ Loop execution ended in failure. Check state file for trace.")
        sys.exit(2)


if __name__ == "__main__":
    main()
