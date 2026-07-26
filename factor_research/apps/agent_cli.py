"""Machine-readable CLI gateway for the system Agent tool registry (ADR-037).

Thin argparse shell over services.agent.capability(2026-07-22 P1-4② 下沉;
分派器核心原居本文件,services.agent.protocol_runner 曾 lazy import 本模块
造成 services→apps 倒灌)。本文件 re-export 公共名,`from apps.agent_cli
import AgentCliError, call_capability, capability_catalog, main` 公共面不变。

Readonly tools run freely. Mid-risk tools require --confirm-token matching
ASTOCK_MID_CONFIRM_TOKEN. High-risk tools that only propose are treated as
callable when risk is high but fn is not None (proposals); rebalance stays blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from services.agent.capability import (  # noqa: E402
    AgentCliError,
    _jsonable,
    call_capability,
    capability_catalog,
)

__all__ = ["AgentCliError", "call_capability", "capability_catalog", "main"]

MAX_ARGUMENTS_BYTES = 16 * 1024


def _parse_arguments(raw: str) -> dict[str, Any]:
    if len(raw.encode("utf-8")) > MAX_ARGUMENTS_BYTES:
        raise AgentCliError("arguments payload is too large")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentCliError(f"invalid arguments JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise AgentCliError("arguments must be a JSON object")
    return value


def _emit(payload: Any, stream: Any = sys.stdout) -> None:
    json.dump(_jsonable(payload), stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_cli", description="Agent capability gateway (ADR-037)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("catalog", help="List executable capabilities")
    call_parser = subparsers.add_parser("call", help="Execute one capability")
    call_parser.add_argument("--tool", required=True, help="Capability name from catalog")
    call_parser.add_argument("--args-json", default="{}", help="JSON object of arguments")
    call_parser.add_argument(
        "--confirm-token",
        default="",
        help="Required for mid-risk tools; must match ASTOCK_MID_CONFIRM_TOKEN",
    )
    call_parser.add_argument(
        "--readonly-only",
        action="store_true",
        help="Reject non-readonly tools (legacy desktop filter)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "catalog":
            _emit({"capabilities": capability_catalog()})
            return 0
        payload = call_capability(
            args.tool,
            _parse_arguments(args.args_json),
            confirm_token=args.confirm_token or None,
            readonly_only=bool(args.readonly_only),
        )
        _emit({"capability": args.tool, "result": payload})
        return 0
    except AgentCliError as exc:
        _emit({"error": str(exc)}, sys.stderr)
        return 2
    except Exception as exc:
        _emit({"error": f"capability execution failed: {exc}"}, sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
