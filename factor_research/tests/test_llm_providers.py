"""验证:Agent LLM 多 provider 选择 + 安全不变量。

Run: cd factor_research && python3 tests/test_llm_providers.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from providers.llm_adapter import (
    AnthropicAdapter,
    NullAdapter,
    OpenAICompatAdapter,
    ai_model_info,
    get_adapter,
)
from services.agent.planner import ask


def _backup_runtime():
    from providers.llm_adapter import _RUNTIME
    return (_RUNTIME, _RUNTIME.read_text(encoding="utf-8") if _RUNTIME.exists() else None)


def _restore_runtime(bk):
    path, content = bk
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(content, encoding="utf-8")   # 还原用户真实配置,绝不丢


def test_default_is_null():
    """无运行时配置 → NullAdapter(非破坏:测后还原用户配置)。"""
    bk = _backup_runtime()
    try:
        bk[0].unlink(missing_ok=True)
        a = get_adapter()
        assert isinstance(a, NullAdapter), f"expected Null, got {type(a).__name__}"
    finally:
        _restore_runtime(bk)
    print("✅ 无运行时配置 → NullAdapter,规则式")


def test_openai_compat_availability():
    assert OpenAICompatAdapter("deepseek-chat", "https://api.deepseek.com/v1", "sk-x").available() is True
    assert OpenAICompatAdapter("deepseek-chat", "https://api.deepseek.com/v1", "").available() is False
    assert OpenAICompatAdapter("", "https://api.deepseek.com/v1", "sk-x").available() is False
    assert OpenAICompatAdapter("m", "", "sk-x").available() is False
    print("✅ OpenAI 兼容适配器 available() 逻辑正确(覆盖 DeepSeek/Qwen/Kimi/GLM/Ollama/OpenAI)")


def test_anthropic_availability():
    assert AnthropicAdapter("claude-opus-4-8", "sk-x").available() is True
    assert AnthropicAdapter("claude-opus-4-8", "").available() is False
    print("✅ Anthropic 原生适配器 available() 正确")


def test_ai_model_info_shape():
    info = ai_model_info()
    for k in ("provider", "model", "base_url", "llm_ready", "mode"):
        assert k in info, k
    print(f"✅ ai_model_info 字段齐:mode={info['mode']}")


def test_config_save_mask_reset():
    """UI 写配置:key 绝不回传明文(非破坏:测后还原用户真实配置)。"""
    import json

    from providers.llm_adapter import llm_config_masked, save_runtime_config
    bk = _backup_runtime()
    try:
        save_runtime_config("openai_compatible", "deepseek-chat", "https://api.deepseek.com/v1", "sk-SECRET999")
        m = llm_config_masked()
        assert "sk-SECRET999" not in json.dumps(m, ensure_ascii=False), "API key 泄漏!"
        assert m["has_key"] is True and m["provider"] == "openai_compatible"
        assert m["key_hint"] and "SECRET" not in m["key_hint"]
    finally:
        _restore_runtime(bk)               # 还原用户配置,不丢
    print("✅ UI 配置:保存/脱敏 OK,key 不回传明文(用户配置已还原)")


def test_lockdown_independent_of_llm():
    """安全不变量:无论接哪个模型,降仓(high)仍仅提案,LLM 绕不过不越权门。"""
    from unittest.mock import MagicMock, patch
    mock_adapter = MagicMock()
    mock_adapter.available.return_value = True
    # Return JSON intent classifying the request as rebalance
    mock_adapter.complete.return_value = '{"skill": "system_status", "tool": "rebalance", "intent": "rebalance"}'
    
    with patch("services.agent.skills.get_adapter", return_value=mock_adapter):
        r = ask("帮我降仓调仓", {"current_page": "portfolio"})
        assert r["tool"] == "rebalance" and r["output"]["requires_human_confirmation"] is True
    print("✅ 不越权不变量:LLM 只能路由/解读,高风险动作仍仅提案不执行")


if __name__ == "__main__":
    print("Running LLM multi-provider tests...\n")
    test_default_is_null()
    test_openai_compat_availability()
    test_anthropic_availability()
    test_ai_model_info_shape()
    test_config_save_mask_reset()
    test_lockdown_independent_of_llm()
    print("\n🎉 LLM provider tests passed!")
