"""Experiment 归档 — JSONL append-only。

任何 Experiment（含失败/DISCARDED）都写入，永不删除。
"""
import json
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from factory.ontology import (
    Decision,
    Experiment,
    ExperimentProtocol,
    ExperimentResult,
)

DEFAULT_LOG_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data_lake" / "factory" / "experiment_log.jsonl"
)


class ExperimentLog:
    """JSONL 归档。每行一个 Experiment。"""

    def __init__(self, path: Path = DEFAULT_LOG_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, exp: Experiment) -> None:
        rec = asdict(exp)
        rec["protocol"] = exp.protocol.value
        rec["decision"] = exp.decision.value
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def append_many(self, exps: Iterator[Experiment]) -> int:
        n = 0
        for e in exps:
            self.append(e)
            n += 1
        return n

    def iter_all(self) -> Iterator[Experiment]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield self._deserialize(json.loads(line))

    def list_by_hypothesis(self, hyp_id: str) -> list[Experiment]:
        return [e for e in self.iter_all() if e.hypothesis_id == hyp_id]

    def list_by_protocol(self, protocol: ExperimentProtocol) -> list[Experiment]:
        return [e for e in self.iter_all() if e.protocol == protocol]

    def count_by_decision(self) -> dict[str, int]:
        from collections import Counter
        return dict(Counter(e.decision.value for e in self.iter_all()))

    @staticmethod
    def _deserialize(rec: dict) -> Experiment:
        return Experiment(
            experiment_id=rec["experiment_id"],
            hypothesis_id=rec["hypothesis_id"],
            protocol=ExperimentProtocol(rec["protocol"]),
            vintage_id=rec["vintage_id"],
            result=ExperimentResult(**rec["result"]),
            decision=Decision(rec["decision"]),
            cost_spent_seconds=rec.get("cost_spent_seconds", 0.0),
            run_at=rec.get("run_at", ""),
            notes=rec.get("notes", ""),
        )
