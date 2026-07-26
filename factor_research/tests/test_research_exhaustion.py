"""对抗性测试:研究枯竭信号(services.read.research_exhaustion)+ 收件箱第七源。

Run:  cd factor_research && python3 tests/test_research_exhaustion.py

护栏 C 关注点(不只 happy-path):
  枯竭必须真发火(4 次零产出 → exhausted → 收件箱 attention 事项计入待裁决);
  不得假报(样本不足/文件缺 → insufficient_evidence,刚接仪表的第一周不许喊狼来了);
  搜索环自身失败不得错记为「搜过没产出」(search_failed 剔除出判据);
  坏行跳过但计数透出(不静默吞);
  源读取中途爆炸 → 收件箱 source_error 显式入箱(盲区禁称无事)。
全程 fixture 文件,不读真实 reports/。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.views import DataQualityView, PromotionReadinessView, SystemTruthView
from services.read.decision_inbox import get_decision_inbox
from services.read.research_exhaustion import get_research_exhaustion

# ── fixture 工具 ────────────────────────────────────────────────────────────

def _run(status="no_candidates", ho_ok=0, **kw) -> dict:
    return {"ts": "2026-07-02 08:00:00", "status": status,
            "evaluated": 120, "n_promoted_l3": 0, "n_holdout_ok": ho_ok, **kw}


def _write_runs(tmpdir: str, runs: list, name: str = "runs.jsonl") -> str:
    p = Path(tmpdir) / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else str(r)
                           for r in runs), encoding="utf-8")
    return str(p)


def _healthy_inbox_kwargs(**overrides):
    base = dict(
        gate_verdicts=[],
        system_truth=SystemTruthView(declared_present=False),
        review_pending=[],
        decay=None,
        data_quality_view=DataQualityView(total=100, clean=100, clean_ratio=1.0,
                                          verdict="可用", severe_count=0),
        promotion=PromotionReadinessView(),
        exhaustion={"state": "healthy"},
        recompose=None,
    )
    base.update(overrides)
    return base


# ── 读层三态 ────────────────────────────────────────────────────────────────

def test_exhausted_fires_on_four_zero_runs():
    with tempfile.TemporaryDirectory() as td:
        runs = _write_runs(td, [_run() for _ in range(4)])
        r = get_research_exhaustion(window=4, runs_path=runs, backlog_path=str(Path(td) / "nb.json"))
        assert r["state"] == "exhausted", r
        assert r["runs_considered"] == 4


def test_one_productive_run_keeps_healthy():
    with tempfile.TemporaryDirectory() as td:
        runs = _write_runs(td, [_run(), _run(), _run(status="audited", ho_ok=1), _run()])
        r = get_research_exhaustion(window=4, runs_path=runs)
        assert r["state"] == "healthy", "窗口内有产出必须判 healthy(枯竭不许误报)"


def test_insufficient_evidence_not_false_alarm():
    with tempfile.TemporaryDirectory() as td:
        # 样本不足:3 < window=4 → 不得假报枯竭
        runs = _write_runs(td, [_run() for _ in range(3)])
        assert get_research_exhaustion(window=4, runs_path=runs)["state"] == "insufficient_evidence"
        # 文件缺失(系统刚接上仪表)→ 同样 insufficient,不许喊狼来了
        missing = str(Path(td) / "nope.jsonl")
        assert get_research_exhaustion(window=4, runs_path=missing)["state"] == "insufficient_evidence"


def test_search_failures_do_not_count_as_zero_productivity():
    with tempfile.TemporaryDirectory() as td:
        # 4 次全是搜索环自身崩溃:那是"没搜",不是"搜了没产出" → 不得判枯竭
        runs = _write_runs(td, [_run(status="search_failed") for _ in range(4)])
        r = get_research_exhaustion(window=4, runs_path=runs)
        assert r["state"] == "insufficient_evidence", "search_failed 不得计入零产出证据"
        # 失败与零产出混排:非失败满 4 条且全零 → 枯竭照发
        mixed = _write_runs(td, [
            _run(), _run(status="search_failed"), _run(), _run(),
            _run(status="search_failed"), _run(),
        ], name="mixed.jsonl")
        assert get_research_exhaustion(window=4, runs_path=mixed)["state"] == "exhausted"


def test_corrupt_lines_skipped_but_counted():
    with tempfile.TemporaryDirectory() as td:
        runs = _write_runs(td, [_run(), "{{not-json", _run(), _run(), _run()])
        r = get_research_exhaustion(window=4, runs_path=runs)
        assert r["corrupt_lines"] == 1, "坏行必须计数透出,不静默吞"
        assert r["state"] == "exhausted"


def test_backlog_sorted_and_fail_open():
    with tempfile.TemporaryDirectory() as td:
        bp = Path(td) / "backlog.json"
        bp.write_text(json.dumps({"entries": [
            {"id": "b", "priority": 2}, {"id": "a", "priority": 1},
        ]}), encoding="utf-8")
        runs = _write_runs(td, [_run() for _ in range(4)])
        r = get_research_exhaustion(window=4, runs_path=runs, backlog_path=str(bp))
        assert [e["id"] for e in r["data_source_backlog"]] == ["a", "b"]
        # 清单缺失 → 空表(advisory fail-open),信号本身不受影响
        r2 = get_research_exhaustion(window=4, runs_path=runs, backlog_path=str(Path(td) / "no.json"))
        assert r2["data_source_backlog"] == [] and r2["state"] == "exhausted"

    # 随仓真实清单:能解析、退市回补必须置顶(数据债 > 新信息源)
    real = get_research_exhaustion(window=4, runs_path=str(Path(tempfile.gettempdir()) / "_none.jsonl"))
    assert real["data_source_backlog"], "随仓 data_source_backlog.json 必须可解析非空"
    assert real["data_source_backlog"][0]["id"] == "delisted-backfill"


# ── 收件箱第七源 ────────────────────────────────────────────────────────────

def test_inbox_exhausted_is_attention_and_counted():
    exh = {"state": "exhausted", "window": 4, "detail": "d1;d2;d3;d4",
           "criterion": "c", "data_source_backlog": [{"id": "delisted-backfill", "playbook": "lake"}]}
    v = get_decision_inbox(**_healthy_inbox_kwargs(exhaustion=exh))
    item = next(i for i in v.items if i.kind == "research_exhaustion")
    assert item.severity == "attention"
    assert v.pending_count >= 1 and "无需你介入" not in v.headline
    assert any("delisted-backfill" in e for e in item.evidence)
    assert any("probe-signal-source" in a.entrypoint for a in item.actions)
    assert "advisory" in item.authority, "authority 必须自称 advisory,不得冒充裁决"


def test_inbox_healthy_or_insufficient_creates_no_item():
    for state in ("healthy", "insufficient_evidence"):
        v = get_decision_inbox(**_healthy_inbox_kwargs(exhaustion={"state": state}))
        assert not [i for i in v.items if i.kind == "research_exhaustion"], \
            f"{state} 不得制造事项(假紧迫)"
        assert v.pending_count == 0 and "无需你介入" in v.headline


def test_inbox_source_explosion_surfaces_as_source_error():
    class _BoomDict(dict):
        def get(self, *a, **k):  # noqa: D102
            raise RuntimeError("exhaustion source exploded")

    v = get_decision_inbox(**_healthy_inbox_kwargs(exhaustion=_BoomDict()))
    err = [i for i in v.items if i.kind == "source_error" and "research_exhaustion" in i.key]
    assert err, "源读取爆炸必须显式入箱(盲区禁称无事)"
    assert not v.all_sources_readable
    assert "无需你介入" not in v.headline


def _run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
