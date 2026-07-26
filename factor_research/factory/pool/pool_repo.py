"""Hypothesis 池持久化 — JSONL append-only。

每行一个 Hypothesis 快照。同 id 多次写入时，读取以**最后一条**为准
（status 变化等用 append 实现历史可追溯）。
"""
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from factory.ontology import EconomicThesis, Hypothesis, HypothesisStatus

DEFAULT_POOL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data_lake" / "factory" / "hypothesis_pool.jsonl"
)


class HypothesisPool:
    """Hypothesis JSONL 池。

    简洁实现：内存缓存 + 文件 append。重启时全量重载。
    """

    def __init__(self, path: Path = DEFAULT_POOL_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}
        self._load()

    # ────────────────────────── 公共 API ──────────────────────────

    def add(self, hyp: Hypothesis) -> bool:
        """Insert if new. Returns True if added, False if dup."""
        if hyp.id in self._cache:
            return False
        rec = self._serialize(hyp)
        self._append(rec)
        return True

    def add_many(self, hyps: Iterator[Hypothesis]) -> tuple[int, int]:
        """Bulk add. Returns (added, dup)."""
        added = dup = 0
        for h in hyps:
            if self.add(h):
                added += 1
            else:
                dup += 1
        return added, dup

    def get(self, hyp_id: str) -> Hypothesis | None:
        rec = self._cache.get(hyp_id)
        return self._deserialize(rec) if rec else None

    def update_status(self, hyp_id: str, status: HypothesisStatus) -> Hypothesis:
        if hyp_id not in self._cache:
            raise KeyError(f"Hypothesis {hyp_id} not in pool")
        rec = {**self._cache[hyp_id], "status": status.value}
        self._append(rec)
        return self._deserialize(rec)

    def list_by_status(self, status: HypothesisStatus) -> list[Hypothesis]:
        return [
            self._deserialize(r)
            for r in self._cache.values()
            if r["status"] == status.value
        ]

    def count_by_status(self) -> dict[str, int]:
        return dict(Counter(r["status"] for r in self._cache.values()))

    def all(self) -> list[Hypothesis]:
        return [self._deserialize(r) for r in self._cache.values()]

    def __len__(self):
        return len(self._cache)

    # ────────────────────────── 内部 ──────────────────────────

    def _load(self):
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                self._cache[rec["id"]] = rec   # later wins

    def _append(self, rec: dict):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._cache[rec["id"]] = rec

    @staticmethod
    def _serialize(hyp: Hypothesis) -> dict:
        d = asdict(hyp)
        d["id"] = hyp.id
        d["status"] = hyp.status.value
        if hyp.thesis:
            d["thesis"] = asdict(hyp.thesis)
        return d

    @staticmethod
    def _deserialize(rec: dict) -> Hypothesis:
        thesis_data = rec.get("thesis")
        thesis = EconomicThesis(**thesis_data) if thesis_data else None
        return Hypothesis(
            name=rec["name"],
            description=rec.get("description", ""),
            factor_fn_name=rec["factor_fn_name"],
            factor_params=rec.get("factor_params", {}),
            timing_fn_name=rec.get("timing_fn_name"),
            timing_params=rec.get("timing_params", {}),
            data_dependencies=tuple(rec.get("data_dependencies", ())),
            thesis=thesis,
            source=rec.get("source", "manual"),
            source_ref=rec.get("source_ref"),
            parent_hypothesis_id=rec.get("parent_hypothesis_id"),
            novelty_score=rec.get("novelty_score", 0.0),
            estimated_cost_seconds=rec.get("estimated_cost_seconds", 0.0),
            status=HypothesisStatus(rec.get("status", "drafted")),
            created_at=rec.get("created_at", ""),
        )
