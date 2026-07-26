"""Dry-run-first migration from legacy registry config to executable specs."""
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

from core.strategy_spec import ExecutableStrategySpec


def _known_spec(family: str, version: str, record: dict) -> ExecutableStrategySpec | None:
    if (family, version) == ("illiquidity", "v3.1"):
        return ExecutableStrategySpec(
            family=family,
            version=version,
            universe={"market": "A_SHARE", "exclude_star": False},
            data={"price_units": "shares_yuan", "warmup_start": "2010-01-01"},
            factor={
                "type": "amihud_illiquidity",
                "window": 20,
                "shift": 1,
                "mad_clip": 5.0,
            },
            selection={"top_n": 25, "rebalance_days": 20},
            timing={"type": "pure_trend_band", "ma": 16, "cap": 1.5},
            policy={"veto": "salience_covariance", "veto_q": 0.30},
            execution={
                "fill": "T_PLUS_1_CLOSE",
                "cost_model": "A_SHARE_STANDARD_V1",
            },
        )
    if (family, version) == ("small-cap-size", "v2.0"):
        return ExecutableStrategySpec(
            family=family,
            version=version,
            universe={"market": "A_SHARE", "exclude_star": True},
            data={"price_units": "shares_yuan", "warmup_start": "2010-01-01"},
            factor={"type": "small_cap_amount", "window": 60, "shift": 1},
            selection={"top_n": 25, "rebalance_days": 20},
            timing={"type": "ma_trend", "ma": 16},
            policy={"veto": "none"},
            execution={
                "fill": "T_PLUS_1_CLOSE",
                "cost_model": "A_SHARE_STANDARD_V1",
            },
        )
    return None


def migrate_strategy_specs(*, apply: bool = False) -> dict:
    import strategy_registry

    data = strategy_registry._load()
    mapped, manual = [], []
    changed = 0
    for family in data.get("families", []):
        family_id = family.get("id", "")
        for version in family.get("versions", []):
            version_id = version.get("version", "")
            spec = _known_spec(family_id, version_id, version)
            if spec is None:
                if version.get("status") in {"在册", "REGISTERED", "DEPLOYED"}:
                    manual.append({"family": family_id, "version": version_id})
                continue
            spec.validate()
            current_hash = (version.get("executable_spec") or {}).get("spec_hash")
            mapped.append({
                "family": family_id,
                "version": version_id,
                "spec_hash": spec.spec_hash,
                "changed": current_hash != spec.spec_hash,
            })
            if current_hash == spec.spec_hash:
                continue
            changed += 1
            if apply:
                strategy_registry.attach_executable_spec(
                    family_id,
                    version_id,
                    spec.to_dict(),
                    spec.spec_hash,
                    require_revalidation=True,
                )
    return {
        "mode": "apply" if apply else "dry_run",
        "changed": changed,
        "mapped": mapped,
        "manual_review_required": manual,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/governance/strategy_spec_migration.json",
    )
    args = parser.parse_args()
    report = migrate_strategy_specs(apply=args.apply)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
