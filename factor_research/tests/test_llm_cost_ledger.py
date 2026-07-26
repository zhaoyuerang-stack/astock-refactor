"""LLM 成本落账(providers/llm_ledger + llm_adapter 接线):schema/usage 解析/费率/fail-open。

纪律锚点:记尺寸不记内容;api/estimated 两级 token 不混淆;未知模型 cost=null;
落账失败永不阻断 LLM 调用;失败调用先于 except 吞没落账。
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from providers import llm_adapter, llm_ledger


@pytest.fixture()
def cost_dir(tmp_path, monkeypatch):
    d = tmp_path / "llm_cost"
    monkeypatch.setenv("ASTOCK_LLM_COST_DIR", str(d))
    return d


@pytest.fixture()
def pricing(tmp_path, monkeypatch):
    p = tmp_path / "pricing.yaml"
    p.write_text(
        "pricing:\n  test-model: {input_per_m: 1.0, output_per_m: 2.0}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_ledger, "_PRICING", p)
    return p


def _read_events(d: Path) -> list[dict]:
    return [
        json.loads(line)
        for f in sorted(d.glob("llm_cost_*.jsonl"))
        for line in f.read_text(encoding="utf-8").splitlines()
    ]


# ── parse_usage:双形状 + 取不到 ──


def test_parse_usage_openai_shape():
    assert llm_ledger.parse_usage(
        "openai_compatible", {"usage": {"prompt_tokens": 10, "completion_tokens": 4}}
    ) == (10, 4)


def test_parse_usage_anthropic_shape():
    assert llm_ledger.parse_usage(
        "anthropic", {"usage": {"input_tokens": 7, "output_tokens": 3}}
    ) == (7, 3)


def test_parse_usage_missing_or_bad():
    assert llm_ledger.parse_usage("openai_compatible", {}) == (None, None)
    assert llm_ledger.parse_usage("openai_compatible", {"usage": {"prompt_tokens": "x"}}) == (None, None)


# ── estimate_cost:命中/未知/null ──


def test_estimate_cost_known_model(pricing):
    # 1000 in × $1.0/M + 500 out × $2.0/M = $0.002
    assert llm_ledger.estimate_cost("test-model", 1000, 500) == 0.002


def test_estimate_cost_unknown_model_is_null(pricing):
    assert llm_ledger.estimate_cost("mystery", 1000, 500) is None


def test_estimate_cost_missing_tokens_is_null(pricing):
    assert llm_ledger.estimate_cost("test-model", None, 500) is None


# ── record_call:账行 schema / estimated 回退 / error_kind / fail-open ──


def test_record_call_api_tokens_and_cost(cost_dir, pricing):
    with llm_ledger.set_caller("unit_test"):
        llm_ledger.record_call(
            capability="complete", provider="openai_compatible", model="test-model",
            system_chars=100, user_chars=200,
            data={"usage": {"prompt_tokens": 1000, "completion_tokens": 500}},
            latency_ms=123, outcome="ok",
        )
    (ev,) = _read_events(cost_dir)
    assert ev["capability"] == "complete" and ev["caller"] == "unit_test"
    assert ev["prompt_tokens"] == 1000 and ev["completion_tokens"] == 500
    assert ev["token_source"] == "api" and ev["cost_usd"] == 0.002
    assert ev["outcome"] == "ok" and "error_kind" not in ev


def test_record_call_estimated_when_no_usage(cost_dir):
    llm_ledger.record_call(
        capability="route", provider="openai_compatible", model="mystery",
        system_chars=40, user_chars=40, data={}, latency_ms=5, outcome="ok",
    )
    (ev,) = _read_events(cost_dir)
    assert ev["token_source"] == "estimated" and ev["prompt_tokens"] == 20
    assert ev["completion_tokens"] is None and ev["cost_usd"] is None


def test_record_call_error_carries_kind(cost_dir):
    llm_ledger.record_call(
        capability="ping", provider="anthropic", model="m", system_chars=4, user_chars=4,
        data=None, latency_ms=9, outcome="error", error_kind="HTTP 401",
    )
    (ev,) = _read_events(cost_dir)
    assert ev["outcome"] == "error" and ev["error_kind"] == "HTTP 401"


def test_record_call_never_raises_on_unwritable_dir(tmp_path, monkeypatch):
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ASTOCK_LLM_COST_DIR", str(blocked))
    llm_ledger.record_call(
        capability="complete", provider="p", model="m", system_chars=1, user_chars=1,
        data={}, latency_ms=1, outcome="ok",
    )  # 不抛即过


# ── adapter 接线:真实 token 落账 / 失败先于吞没 / NullAdapter 零账 ──


def test_openai_adapter_complete_records_real_tokens(cost_dir, pricing, monkeypatch):
    def _post(url, headers, payload, timeout=30):
        return {"choices": [{"message": {"content": " ok "}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}

    monkeypatch.setattr(llm_adapter, "_http_post", _post)
    a = llm_adapter.OpenAICompatAdapter("test-model", "http://x/v1", "k")
    assert a.complete("s", "u") == "ok"
    (ev,) = _read_events(cost_dir)
    assert ev["capability"] == "complete" and ev["model"] == "test-model"
    assert ev["prompt_tokens"] == 1000 and ev["cost_usd"] == 0.002


def test_openai_adapter_error_recorded_before_swallow(cost_dir, monkeypatch):
    def _boom(url, headers, payload, timeout=30):
        raise urllib.error.HTTPError(url, 500, "err", {}, None)

    monkeypatch.setattr(llm_adapter, "_http_post", _boom)
    a = llm_adapter.OpenAICompatAdapter("m", "http://x/v1", "k")
    assert a.complete("s", "u") is None  # 行为不变:吞异常返 None
    (ev,) = _read_events(cost_dir)
    assert ev["outcome"] == "error" and ev["error_kind"] == "HTTP 500"


def test_anthropic_adapter_records_anthropic_usage(cost_dir, monkeypatch):
    def _post(url, headers, payload, timeout=30):
        return {"content": [{"text": " hi "}], "usage": {"input_tokens": 7, "output_tokens": 3}}

    monkeypatch.setattr(llm_adapter, "_http_post", _post)
    a = llm_adapter.AnthropicAdapter("claude-x", "k")
    assert a.complete("s", "u") == "hi"
    (ev,) = _read_events(cost_dir)
    assert ev["provider"] == "anthropic"
    assert ev["prompt_tokens"] == 7 and ev["completion_tokens"] == 3


def test_null_adapter_records_nothing(cost_dir):
    assert llm_adapter.NullAdapter().complete("s", "u") is None
    assert _read_events(cost_dir) == []
