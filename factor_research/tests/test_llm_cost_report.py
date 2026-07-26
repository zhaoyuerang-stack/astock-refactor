"""LLM 成本月度汇总(scripts/ops/llm_cost_report):分组/聚合/成本诚实口径/坏行容错。"""
from __future__ import annotations

import json

from scripts.ops.llm_cost_report import load_events, main, render, summarize


def _ev(**kw):
    base = {"ts": "2026-07-22T08:00:00+08:00", "capability": "complete",
            "provider": "openai_compatible", "model": "m1", "caller": None,
            "prompt_tokens": 100, "completion_tokens": 10, "token_source": "api",
            "cost_usd": None, "latency_ms": 1000, "outcome": "ok"}
    base.update(kw)
    return base


def _write(d, name, events):
    (d / name).write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                         encoding="utf-8")


# ── load_events:按月分组 + --month 过滤 + 坏行跳过 ──


def test_load_events_groups_by_month_and_skips_bad_lines(tmp_path):
    _write(tmp_path, "llm_cost_202607.jsonl", [_ev(), _ev(capability="route")])
    f = tmp_path / "llm_cost_202606.jsonl"
    f.write_text(json.dumps(_ev()) + "\n{坏行\n", encoding="utf-8")
    months = load_events(tmp_path)
    assert sorted(months) == ["202606", "202607"]
    assert len(months["202606"]) == 1 and len(months["202607"]) == 2


def test_load_events_month_filter(tmp_path):
    _write(tmp_path, "llm_cost_202607.jsonl", [_ev()])
    _write(tmp_path, "llm_cost_202606.jsonl", [_ev()])
    assert sorted(load_events(tmp_path, "202607")) == ["202607"]


# ── summarize:聚合 + 成本诚实口径 ──


def test_summarize_aggregates_and_cost_honesty():
    events = [
        _ev(cost_usd=0.001, caller="agent_loop"),                    # 配费率
        _ev(capability="route", cost_usd=None, caller="agent_loop"),  # 未配费率
        _ev(outcome="error", token_source="estimated", prompt_tokens=None, completion_tokens=None),
    ]
    s = summarize(events)
    t = s["total"]
    assert t["calls"] == 3 and t["errors"] == 1
    assert t["cost_known_calls"] == 1 and abs(t["cost_usd"] - 0.001) < 1e-9  # 未配费率不混入
    assert t["estimated_calls"] == 1 and t["prompt_tokens"] == 200  # None token 不计
    assert s["callers"] == {"agent_loop": 2}  # caller=None 不计
    assert ("complete", "m1") in s["rows"] and ("route", "m1") in s["rows"]


def test_render_shows_month_and_cost_coverage():
    s = summarize([_ev(cost_usd=0.002), _ev(capability="route")])
    text = render("202607", s)
    assert "== 202607 ==" in text and "调用 2" in text
    assert "$0.0020(1/2 配费率)" in text and "agent_loop" not in text  # 无 caller 不打印分布


def test_render_empty_month():
    assert "无账" in render("202607", summarize([]))


# ── main:空目录与月度输出 ──


def test_main_empty_dir(tmp_path, capsys):
    assert main(["--dir", str(tmp_path)]) == 0
    assert "无账" in capsys.readouterr().out


def test_main_month_report(tmp_path, capsys):
    _write(tmp_path, "llm_cost_202607.jsonl", [_ev(cost_usd=0.001, caller="agent_loop")])
    assert main(["--dir", str(tmp_path), "--month", "202607"]) == 0
    out = capsys.readouterr().out
    assert "== 202607 ==" in out and "caller 分布: agent_loop=1" in out
