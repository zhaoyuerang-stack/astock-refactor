"""Create DeploymentManifest only from explicit, fully validated registry identities."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.analysis.nine_gate_policy import decide_nine_gate


def _lookup(identity: str) -> tuple[str, str, dict]:
    import strategy_registry

    family, version = identity.split("/", 1)
    data = strategy_registry._load()
    fam = next((item for item in data.get("families", []) if item.get("id") == family), None)
    record = next(
        (item for item in (fam or {}).get("versions", []) if item.get("version") == version),
        None,
    )
    if record is None:
        raise ValueError(f"registry identity not found: {identity}")
    if record.get("status") not in {"在册", "REGISTERED", "DEPLOYED"}:
        raise ValueError(f"registry identity not deployable: {identity}")
    executable = record.get("executable_spec") or {}
    if not executable.get("spec_hash"):
        raise ValueError(f"registry identity lacks executable spec: {identity}")
    decision = decide_nine_gate(record.get("nine_gate") or {})
    if not decision.approved:
        raise ValueError(
            f"registry identity lacks complete Nine-Gate approval: {identity}: "
            f"{decision.blocking_reasons}"
        )
    return family, version, record


def migrate_deployment(
    *,
    equity: str,
    defensive: str | None,
    manifest_path: Path = ROOT / "deployments/production.json",
    apply: bool = False,
) -> dict:
    if not equity:
        raise ValueError("--equity is required; deployment must never be guessed")
    legs = []
    for identity, role in ((equity, "equity_alpha"), (defensive, "defensive")):
        if not identity:
            continue
        family, version, record = _lookup(identity)
        legs.append({
            "family": family,
            "version": version,
            "spec_hash": record["executable_spec"]["spec_hash"],
            "role": role,
        })
    manifest = {
        "deployment_id": "prod-a-share-v1",
        "environment": "production",
        "status": "active",
        "portfolio_policy": {"type": "regime_rotation", "defensive_cap": 1.0},
        "legs": legs,
    }
    changed = (
        not manifest_path.exists()
        or json.loads(manifest_path.read_text()) != manifest
    )
    if apply and changed:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return {
        "mode": "apply" if apply else "dry_run",
        "ready": True,
        "changed": changed,
        "manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity", required=True)
    parser.add_argument("--defensive")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/governance/deployment_migration.json",
    )
    args = parser.parse_args()
    report = migrate_deployment(
        equity=args.equity,
        defensive=args.defensive,
        apply=args.apply,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
