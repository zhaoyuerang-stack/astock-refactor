"""失败台账反思(P3):把验证线死因聚合成结构化教训,喂回 LLM 播种。

学习层闭环:实验 → 死因 → 反思 → 下一代播种。
教训全部由证据门控——只有日志里真实出现的失败模式才会产出对应教训,
本模块绝不硬编码无证据的"经验",也不改任何主线状态。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import CandidateDecision
from .repositories import CandidateRepository, ExperimentLog

_STAGE_LABEL = {
    "l0_ic_scan": "L0(截面无信息)",
    "l1_quick_bt": "L1(成本后不可变现)",
    "l2_multi_regime": "L2(行情依赖)",
    "l3_walk_forward": "L3(样本外失效)",
    "marginal_eval": "边际(对在册无增量)",
}


@dataclass(frozen=True)
class FailureLedger:
    """结构化失败台账:死因分布 + 因子级死亡频次 + 证据门控教训。"""

    total_failed: int = 0
    deaths_by_stage: dict = field(default_factory=dict)      # protocol -> count
    factor_deaths: dict = field(default_factory=dict)        # protocol -> {factor: count}
    veto_form_deaths: int = 0                                # L1 死但 |L0 ICIR| 强(货币化形态死因)
    lessons: tuple = ()


def _death_protocol(metrics: dict) -> str | None:
    """候选死在哪一关:最后一个 decision=discard 的 experiment protocol。"""
    for exp in reversed(metrics.get("experiments", [])):
        if exp.get("decision") == CandidateDecision.DISCARD.value:
            return exp.get("protocol")
    return None


def _candidate_factors(repository: CandidateRepository | None, fingerprint: str) -> list[str]:
    if repository is None:
        return []
    candidate = repository.get(fingerprint)
    if candidate is None:
        return []
    return [t.get("factor", "") for t in candidate.ast.get("terms", []) if t.get("factor")]


def build_failure_ledger(
    experiment_log: ExperimentLog | None = None,
    repository: CandidateRepository | None = None,
    *,
    min_pattern_count: int = 2,
) -> FailureLedger:
    """聚合 ExperimentLog 的死因;repository 提供时按因子名细分死亡频次。"""
    experiment_log = experiment_log or ExperimentLog()

    deaths_by_stage: Counter = Counter()
    factor_deaths: dict[str, Counter] = {}
    veto_form_deaths = 0
    total_failed = 0

    for result in experiment_log.iter_all():
        if result.decision != CandidateDecision.DISCARD:
            continue
        protocol = _death_protocol(result.metrics)
        if protocol is None:
            continue
        total_failed += 1
        deaths_by_stage[protocol] += 1
        if result.metrics.get("veto_review_candidate"):
            veto_form_deaths += 1
        for factor in _candidate_factors(repository, result.fingerprint):
            factor_deaths.setdefault(protocol, Counter())[factor] += 1

    lessons: list[str] = []
    if veto_form_deaths >= min_pattern_count:
        lessons.append(
            f"已观测 {veto_form_deaths} 次「L0 截面信息强但 L1 成本后年化不过线」:"
            "alpha 集中在空头侧,做多 top-N 收割不到。A 股空头侧 alpha 既不能做空套利,"
            "也不能靠排除变现(否决器双宿主证伪:全截面分位的负超额在『宿主已选中』的"
            "条件分布上不成立)。不要再提交以『识别输家』为机制的做多候选;"
            "机制必须在多头侧可变现。"
        )
    for protocol, counter in factor_deaths.items():
        for factor, count in counter.most_common(3):
            if count >= min_pattern_count:
                lessons.append(
                    f"因子 {factor} 已 {count} 次死于 {_STAGE_LABEL.get(protocol, protocol)};"
                    "换参数微调大概率重蹈覆辙,需要机制级不同的变体或放弃该方向。"
                )

    return FailureLedger(
        total_failed=total_failed,
        deaths_by_stage=dict(deaths_by_stage),
        factor_deaths={k: dict(v) for k, v in factor_deaths.items()},
        veto_form_deaths=veto_form_deaths,
        lessons=tuple(lessons),
    )


def ledger_to_prompt(ledger: FailureLedger, *, max_lessons: int = 6) -> str:
    """渲染为提示词片段;无失败记录时返回空串(不注入噪音)。"""
    if ledger.total_failed == 0:
        return ""
    stage_summary = ", ".join(
        f"{_STAGE_LABEL.get(p, p)}×{n}" for p, n in sorted(ledger.deaths_by_stage.items())
    )
    lines = [f"失败台账(累计 {ledger.total_failed} 个候选被证伪;死因分布:{stage_summary}):"]
    for lesson in ledger.lessons[:max_lessons]:
        lines.append(f"- {lesson}")
    if not ledger.lessons:
        lines.append("- (暂无重复性失败模式,但避开与已证伪候选同机制的表达)")
    return "\n".join(lines)
