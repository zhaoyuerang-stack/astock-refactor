"""LLM 驱动的 AutoResearch 候选生成。

LLM 只产出受控 JSON AST 文本;每个候选仍要过 validate_candidate_ast(白名单)
+ 泄露守卫 + fingerprint 去重 + 真实 L0~L3 验证线。LLM 写不了一行可执行代码。
LLM 未配置 → 明确报错,绝不静默降级成种子(口径透明)。
"""
from __future__ import annotations

import json
from dataclasses import replace

from app_config.log import get_logger
from contracts.views import AutoResearchLLMGenResponse
from factory.autoresearch import CandidateRepository, validate_candidate_ast
from factory.autoresearch.guards import LeakageGuardError, run_leakage_guard
from factory.autoresearch.registry import ALLOWED_FACTORS, ALLOWED_TRANSFORMS
from factory.autoresearch.validator import DSLValidationError

from .autoresearch import _run_candidates

logger = get_logger(__name__)


def _dsl_system_prompt() -> str:
    factor_lines = []
    for name, spec in sorted(ALLOWED_FACTORS.items()):
        if spec.params:
            ps = ", ".join(f"{p} ∈ [{lo}, {hi}]" for p, (lo, hi) in spec.params.items())
        else:
            ps = "无参数"
        factor_lines.append(f"  - {name}: {ps}")
    return (
        "你是 A 股全市场日频量化因子研究员。你的任务是提出新的候选因子,"
        "以受控 JSON AST 表达——你不能也不需要写任何代码。\n"
        "只输出一个 JSON 数组,不要任何解释文字。每个元素 schema:\n"
        '{"type": "linear_combo", "terms": [{"factor": "<白名单因子>", "params": {...}, '
        '"transforms": ["mad_clip", "zscore", "rank"], "weight": <数值>}], '
        '"direction": "positive"|"negative", '
        '"thesis": {"mechanism": "<经济机制,一句话>", "citation": "<出处或经验观察>"}}\n'
        "白名单因子(参数必须全部给出且在范围内):\n" + "\n".join(factor_lines) + "\n"
        f"白名单 transforms(子集,按序应用): {sorted(ALLOWED_TRANSFORMS)}\n"
        "硬约束:terms 数量 1~3(复杂度预算);|weight| ≤ 2;不允许 neutralize 字段;"
        "thesis.mechanism 必填且要给真实的经济机制,不是指标描述;"
        "禁止任何 future/forward_return/label/target/next_/tomorrow 字样(泄露守卫会拦截)。\n"
        "目标:与已尝试候选机制上互补(不是换参数微调),先想机制再组表达式。"
    )


def _feedback_lines(repository: CandidateRepository, limit: int = 30) -> str:
    """近期已尝试候选 + 结局,让 LLM 避开重复、从被证伪的方向学习。"""
    lines = []
    for c in repository.all()[-limit:]:
        terms = ",".join(
            f"{t.get('factor')}({t.get('params', {}).get('window', '')})"
            for t in c.ast.get("terms", [])
        )
        lines.append(f"- [{c.status.value}] {terms} :: {c.notes[:60]}")
    return "\n".join(lines) if lines else "(还没有历史候选)"


def _parse_ast_array(text: str) -> list[dict]:
    """从 LLM 输出里抠 JSON 数组(容忍 markdown 代码栅栏 / 思考前后缀)。"""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中找不到 JSON 数组")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("LLM 输出不是 JSON 数组")
    return parsed


def generate_llm_candidates(
    *,
    n: int = 5,
    theme: str = "",
    adapter=None,
    repository: CandidateRepository | None = None,
    experiment_log=None,
):
    """LLM 生成 → 白名单校验 → 泄露守卫 → 去重。返回 (accepted, rejected_reasons, model)。

    experiment_log 提供时注入结构化失败台账(P3 反思):聚合死因 + 证据门控教训,
    优先级高于逐条历史,让生成端绕开已被系统性证伪的形态。
    """
    if adapter is None:
        from providers.llm_adapter import get_adapter

        adapter = get_adapter()
    if not adapter.available():
        raise ValueError("LLM 未配置(系统设置页填 provider/model/key);候选生成不做静默降级")
    repository = repository or CandidateRepository()

    ledger_block = ""
    if experiment_log is not None:
        from factory.autoresearch.reflection import build_failure_ledger, ledger_to_prompt

        ledger_block = ledger_to_prompt(build_failure_ledger(experiment_log, repository))

    # 方向级教训回流(knowledge/direction_registry.json,证据门控):已证伪/太弱方向勿再提,
    # 空白区优先。fail-open:方向层故障不阻断生成(生成端 steering,非验真)。
    direction_block = ""
    try:
        from knowledge.directions import prompt_block

        direction_block = prompt_block()
    except Exception as e:
        logger.warning(f"[llm_gen] 方向登记簿读取失败(fail-open): {e}")

    user = (
        f"请提出 {n} 个候选因子。"
        + (f"研究主题:{theme}。" if theme else "")
        + (f"\n{direction_block}" if direction_block else "")
        + (f"\n{ledger_block}\n上述教训优先级最高,与之冲突的方向直接放弃。" if ledger_block else "")
        + f"\n已尝试候选与结局(避开重复,绕开已被证伪的方向):\n{_feedback_lines(repository)}"
    )
    # 思考型模型(如 DeepSeek)会先烧 token 推理再产出 JSON,留足余量防 content 为空;
    # 同温度下输出偶发非 JSON(思考前缀/拒答),重试一次,错误带原文头部便于诊断
    asts, last_err = None, None
    for _ in range(2):
        text = adapter.complete(_dsl_system_prompt(), user, max_tokens=6000)
        if not text:
            last_err = ValueError(f"LLM({adapter.model})未返回内容")
            continue
        try:
            asts = _parse_ast_array(text)
            break
        except ValueError as e:
            last_err = ValueError(f"{e}(head={text[:80]!r})")
    if asts is None:
        raise last_err

    accepted, rejected = [], []
    seen: set[str] = set()
    for idx, ast in enumerate(asts):
        try:
            candidate = validate_candidate_ast(ast)
            run_leakage_guard(candidate)
        except (DSLValidationError, LeakageGuardError, TypeError) as e:
            rejected.append(f"#{idx}: {type(e).__name__}: {str(e)[:120]}")
            continue
        if candidate.fingerprint in seen or repository.get(candidate.fingerprint) is not None:
            rejected.append(f"#{idx}: duplicate fingerprint {candidate.fingerprint[:10]}")
            continue
        seen.add(candidate.fingerprint)
        accepted.append(replace(candidate, source="llm"))
    return accepted, rejected, adapter.model


def run_autoresearch_llm(
    *,
    n: int = 5,
    theme: str = "",
    max_stage: str = "l1",
    adapter=None,
    repository: CandidateRepository | None = None,
    **run_kw,
) -> AutoResearchLLMGenResponse:
    """LLM 生成候选并走真实 L0~L3 验证线。"""
    accepted, rejected, model = generate_llm_candidates(
        n=n, theme=theme, adapter=adapter, repository=repository,
        experiment_log=run_kw.get("experiment_log"),
    )
    run = _run_candidates(accepted, max_stage=max_stage, repository=repository, **run_kw)
    return AutoResearchLLMGenResponse(
        model=model,
        requested=n,
        accepted=len(accepted),
        rejected=rejected,
        run=run,
    )
