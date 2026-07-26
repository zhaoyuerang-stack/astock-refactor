"""LLM 调用成本落账(P2,2026-07-22)。

每次 LLM API 调用由 providers/llm_adapter 接线 append 一行 JSONL 到
``reports/llm_cost/llm_cost_YYYYMM.jsonl``——运维观测(同 services/agent/audit.py
先例:不走数据湖写入口;``ASTOCK_LLM_COST_DIR`` 可覆盖,测试用)。

纪律:
- **记尺寸,不记内容**:prompt/completion 正文绝不落账,只记 token 数;
- **token 两级**:API 返回 usage → 真实值(token_source="api");否则按
  (system+user 字符数)//4 估算 prompt(token_source="estimated",completion 留 null);
- **成本诚实**:cost_usd 仅费率表(app_config/llm_pricing.yaml,按路径读文件,
  守 providers 叶子"配置经文件注入"规则)命中时填;未知模型/token 缺失 → null,
  不编造费率;
- **fail-open**:落账失败(OSError)只 logger.warning,永不阻断 LLM 调用。
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DIR = ROOT / "reports" / "llm_cost"
_PRICING = ROOT / "app_config" / "llm_pricing.yaml"

_caller: ContextVar[str | None] = ContextVar("llm_ledger_caller", default=None)


@contextmanager
def set_caller(tag: str) -> Iterator[None]:
    """调用方标签(agent_loop/skills.route 等),opt-in;不设置则账行 caller=null。"""
    token = _caller.set(tag)
    try:
        yield
    finally:
        _caller.reset(token)


def _ledger_dir() -> Path:
    return Path(os.environ.get("ASTOCK_LLM_COST_DIR", str(_DEFAULT_DIR)))


def _load_pricing() -> dict[str, dict[str, float]]:
    """读费率表;文件缺失/解析失败/条目非法 → 尽量降级,成本记 null。"""
    try:
        import yaml  # providers 叶子规则允许 stdlib+yaml
    except ImportError:
        return {}
    try:
        raw = yaml.safe_load(_PRICING.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        logger.warning("llm_pricing.yaml 读取失败,cost 记 null: %s", exc)
        return {}
    out: dict[str, dict[str, float]] = {}
    for model, rates in (raw.get("pricing") or {}).items():
        try:
            out[str(model)] = {
                "input_per_m": float(rates["input_per_m"]),
                "output_per_m": float(rates["output_per_m"]),
            }
        except (KeyError, TypeError, ValueError):
            logger.warning("llm_pricing.yaml 条目非法,跳过: %r", model)
    return out


def parse_usage(provider: str, data: dict) -> tuple[int | None, int | None]:
    """从 API 响应取真实 token,返回 (prompt, completion);取不到给 (None, None)。

    openai_compatible 形状: usage.{prompt_tokens, completion_tokens};
    anthropic 形状:         usage.{input_tokens, output_tokens}。
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    if provider == "anthropic":
        p, c = usage.get("input_tokens"), usage.get("output_tokens")
    else:
        p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
    return (p if isinstance(p, int) else None), (c if isinstance(c, int) else None)


def estimate_cost(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> float | None:
    """费率表命中才给成本(USD,微美元精度);未知模型/token 缺失 → None。"""
    if prompt_tokens is None or completion_tokens is None:
        return None
    rates = _load_pricing().get(model)
    if rates is None:
        return None
    usd = (prompt_tokens * rates["input_per_m"] + completion_tokens * rates["output_per_m"]) / 1_000_000
    return round(usd, 6)


def record_call(
    *,
    capability: str,
    provider: str,
    model: str,
    system_chars: int,
    user_chars: int,
    data: dict | None,
    latency_ms: int,
    outcome: str,
    error_kind: str | None = None,
) -> None:
    """append 一行账;落账失败只 warning,永不阻断 LLM 调用。

    data: API 原始响应 dict(ok 时,用于 usage);error 时传 None。
    outcome: "ok" | "error";error 时 error_kind 记 HTTP 码/异常类名,不记正文。
    """
    prompt_tokens, completion_tokens = parse_usage(provider, data or {})
    token_source = "api"
    if prompt_tokens is None and completion_tokens is None:
        prompt_tokens = (system_chars + user_chars) // 4 or None
        completion_tokens = None
        token_source = "estimated"
    now = datetime.now().astimezone()
    event: dict = {
        "ts": now.isoformat(timespec="seconds"),
        "capability": capability,
        "provider": provider,
        "model": model,
        "caller": _caller.get(),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "token_source": token_source,
        "cost_usd": estimate_cost(model, prompt_tokens, completion_tokens),
        "latency_ms": latency_ms,
        "outcome": outcome,
    }
    if error_kind:
        event["error_kind"] = error_kind
    try:
        path = _ledger_dir() / f"llm_cost_{now:%Y%m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("llm_ledger: failed to write cost event: %s", exc)
