#!/usr/bin/env python3
"""Utility script to monitor and display factor generation efficiency statistics.

Parses data_lake/factory/autoresearch/experiment_log.jsonl and outputs L0 execution times,
discard rates, and veto triggers.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data_lake" / "factory" / "autoresearch"
LOG_PATH = DEFAULT_ROOT / "experiment_log.jsonl"


def print_stats():
    if not LOG_PATH.exists():
        print(f"❌ Log file not found at: {LOG_PATH}")
        print("Please run some autoresearch jobs first.")
        return

    total = 0
    decisions = {}
    l0_times = []
    reasons = {}
    budget_vetos = 0

    print(f"📊 Analyzing experiment log: {LOG_PATH.name}")
    print("==================================================")

    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                total += 1
                dec= data.get("decision", "unknown")
                decisions[dec] = decisions.get(dec, 0) + 1

                reason = data.get("reason", "")
                if "computation time budget exceeded" in reason or "time budget" in reason:
                    budget_vetos += 1

                # Extract time spent
                metrics = data.get("metrics", {})
                experiments = metrics.get("experiments", [])
                for exp in experiments:
                    if exp.get("protocol") == "l0_ic_scan":
                        t = exp.get("cost_spent_seconds")
                        if t is not None:
                            l0_times.append(float(t))

                if reason:
                    # Simplify reason for grouping
                    short_reason = reason.split(":")[0].split(" (")[0]
                    reasons[short_reason] = reasons.get(short_reason, 0) + 1
            except Exception:
                continue

    if total == 0:
        print("⚠️ No experiment records found in log file.")
        return

    print(f"🔍 Total evaluated candidates: {total}")
    print("\n📈 Decisions Breakdown:")
    for dec, count in sorted(decisions.items(), key=lambda x: x[1], reverse=True):
        pct = (count / total) * 100
        print(f"  - {dec.upper():<10} : {count:<5} ({pct:.1f}%)")

    print("\n⏱️ L0 IC Scan Computation Speed (Factor Generation Efficiency):")
    if l0_times:
        l0_times = np.array(l0_times)
        factors_per_min = 60.0 / np.mean(l0_times) if np.mean(l0_times) > 0 else 0
        print(f"  - Mean Time  : {np.mean(l0_times):.3f} seconds")
        print(f"  - Min Time   : {np.min(l0_times):.3f} seconds")
        print(f"  - Max Time   : {np.max(l0_times):.3f} seconds")
        print(f"  - Throughput : {factors_per_min:.1f} factors/minute (average)")
    else:
        # Older records may not have cost_spent_seconds
        print("  - No execution time metrics found in older records.")
        print("    (Running new factor searches will populate this field).")

    print("\n🛑 Discard / Veto Reasons:")
    for r, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:6]:
        pct = (count / total) * 100
        print(f"  - {r:<30} : {count:<5} ({pct:.1f}%)")

    if budget_vetos > 0:
        print(f"\n⚡ Budget Veto Trigger Count: {budget_vetos} factors discarded due to exceeding computation budget.")


if __name__ == "__main__":
    print_stats()
