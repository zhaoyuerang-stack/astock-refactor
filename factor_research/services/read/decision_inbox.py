"""Decision-Inbox Read Service —— 决策收件箱(产品主界面,「系统找人」)。

回答唯一问题:**今天有哪几件事需要人裁决?每件事的证据和 canonical 动作是什么?**

产品定位(承 DECISION_COCKPITS.md 决策导向):单人 shop 最稀缺的是注意力,
巡视式看板假设人每天主动巡查;本视图翻转为推送式——系统把「只有人能做的决定」
装配好证据后推到人面前,其余一切自动、可追溯。

诚实护栏(必须遵守):
- **只聚合权威事实源,不做任何新判定**:逐版本裁决 = ``validation_gate.get_gate_verdicts``
  (权威 decide_nine_gate);部署三态 = ``system_truth.get_system_truth``(fail-closed 校验);
  review 待办 = ``factory.autoresearch.ReviewQueue.pending``;衰减 = ``reports/decay_status.json``;
  数据 = ``state.data_quality``;研究重心 = ``promotion_readiness``(advisory);
  研究枯竭 = ``research_exhaustion``(机械判据 advisory:连续 N 次搜索无产出 → 外探还是调向)。
- **空箱三态严格区分**:「无待裁决」只能在**全部事实源可读**时宣称;任一源不可读
  → 该源以 ``source_error`` 事项显式入箱(attention),headline 禁止称"无需介入"。
  控制路径禁静默吞异常(check_control_exceptions 同精神):异常必须成为可见事项。
- **actions 是 advisory 导航**(R-LLM-001 / ADR-030):allowed/entrypoint 来自
  ``action_policy.can_agent_do``,指向 canonical 入口由人执行;本视图零写动作。
- info 级事项(常设研究重心建议)不计入「待裁决数」,不制造虚假紧迫感。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import DecisionAction, DecisionInboxView, DecisionItem

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")
DECAY_STATUS = ROOT / "reports" / "decay_status.json"
RECOMPOSE_LATEST = ROOT / "reports" / "research" / "portfolio_recompose.json"

_REGISTERED = {"在册", "ACTIVE", "active"}
_SEVERITY_ORDER = {"blocked": 0, "attention": 1, "info": 2}
_REVIEW_ITEM_CAP = 10  # 收件箱逐条展示上限,超出部分聚合为计数(注意力保护)


def _policy_action(label: str, action: str, target: str, entrypoint_fallback: str) -> DecisionAction:
    """经 action_policy 装配一个 advisory 动作;policy 不可用时如实降级(不臆造 allowed)。"""
    try:
        from services.read.action_policy import can_agent_do

        d = can_agent_do(action, target)
        return DecisionAction(
            label=label,
            entrypoint=d.required_entrypoint or entrypoint_fallback,
            allowed=d.allowed,
            reason=d.reason,
        )
    except Exception as exc:  # noqa: BLE001 —— 降级为不可判,绝不默认 allowed=True
        return DecisionAction(
            label=label, entrypoint=entrypoint_fallback,
            allowed=False, reason=f"action_policy 不可用:{exc}",
        )


def _source_error_item(source: str, exc: Exception) -> DecisionItem:
    """事实源不可读 → 显式入箱(fail-closed:读不到 ≠ 无事)。"""
    return DecisionItem(
        key=f"source_error:{source}",
        kind="source_error",
        severity="attention",
        title=f"事实源「{source}」不可读——先修复可观测性,期间不得假定该面无事。",
        evidence=[f"{type(exc).__name__}: {exc}"],
        consequence="该面处于盲区:任何『系统健康』结论都不完整。",
        actions=[],
        authority=source,
    )


# ── 各事实源 → 待裁决事项 ────────────────────────────────────────────────

def _items_registered_failed(verdicts) -> list[DecisionItem]:
    """在册版本权威 FAILED/RUN_FAILED → 必须人裁决(退役还是重审),blocked 级。"""
    out: list[DecisionItem] = []
    for gr in verdicts:
        if gr.stage in _REGISTERED and gr.verdict in ("FAILED", "RUN_FAILED"):
            out.append(DecisionItem(
                key=f"registered_failed:{gr.family}/{gr.version}",
                kind="registered_failed",
                severity="blocked",
                title=f"在册策略 {gr.family}/{gr.version} 权威 9-Gate {gr.verdict}——退役还是重审?",
                evidence=[
                    f"权威裁决 decide_nine_gate = {gr.verdict}({gr.verdict_label})",
                    f"阻断根因:{gr.register_blocker or '见验证闸门逐门诊断'}",
                    f"dsr_p={gr.dsr_p} pbo={gr.pbo} n_trials={gr.n_trials}",
                ],
                consequence="在册身份与权威裁决矛盾:任何据其实战/展示都属 over-trust。",
                actions=[
                    DecisionAction(
                        label="退役(保留历史,记录失效归因)",
                        entrypoint="strategy_registry(唯一写入口;按 §7.4 退役纪律)",
                        allowed=True, reason="退役是台账状态变更,走唯一写入口",
                    ),
                    _policy_action("重审(重跑完整 9-Gate)", "run_validation",
                                   f"{gr.family}/{gr.version}", "workflow.nine_gate_runner"),
                ],
                authority="core.analysis.nine_gate_policy.decide_nine_gate",
                drilldown="/governance/gate-verdicts",
            ))
    return out


def _items_deployment(truth) -> list[DecisionItem]:
    """部署清单声明了身份但 fail-closed 校验不过 → 换腿/停用,blocked 级。"""
    if not truth.declared_present or truth.verified:
        return []
    legs = ", ".join(f"{l.family}/{l.version}" for l in truth.declared_legs) or "(无腿)"
    return [DecisionItem(
        key="deployment:fail-closed",
        kind="deployment",
        severity="blocked",
        title="部署清单指向不可部署版本(fail-closed 已拦截)——换腿还是停用清单?",
        evidence=[
            f"declared:{truth.declared_deployment_id or '未知'} → {legs}",
            f"fail-closed 根因:{truth.verify_error or ';'.join(truth.blocking_reasons) or '未知'}",
            f"production_allowed={truth.production_allowed}",
        ],
        consequence="信号/模拟盘停更是正确 fail-closed;但换腿决定只有人能做,拖延=系统停摆。",
        actions=[
            _policy_action("更新部署清单(换腿/停用)", "update_deployment",
                           "deployments/manifest", "deployments/(canonical manifest 入口)"),
        ],
        authority="services.read.system_truth(fail-closed 校验)",
        drilldown="/system/truth",
    )]


def _items_review_queue(pending: list[dict]) -> list[DecisionItem]:
    """autoresearch 待人工复核候选 → 每条一张卡(上限内),approve/reject 只有人能做。"""
    out: list[DecisionItem] = []
    for rec in pending[:_REVIEW_ITEM_CAP]:
        fp = rec.get("fingerprint", "")
        out.append(DecisionItem(
            key=f"review:{fp}",
            kind="review",
            severity="attention",
            title=f"候选 {fp[:12]}… 已过自动闸门等待人工复核——批准进入 shadow 还是驳回?",
            evidence=[
                f"candidate: {rec.get('candidate', '')[:120]}",
                f"自动闸决定:{rec.get('decision', '?')}({rec.get('reason', '')})",
                f"metrics: {json.dumps(rec.get('metrics') or {}, ensure_ascii=False)[:200]}",
            ],
            consequence="不裁决则该候选停在队列,自动环无法继续推进它。",
            actions=[
                _policy_action("批准(approve,不写 LIVE 台账)", "promote_candidate",
                               fp, f"POST /experiments/autoresearch/review/{fp}"),
                DecisionAction(label="驳回(reject,留痕)",
                               entrypoint=f"POST /experiments/autoresearch/review/{fp}",
                               allowed=True, reason="人工复核是 R-WF-001 通道内动作"),
            ],
            authority="factory.autoresearch.ReviewQueue(L0-L3 自动闸已过,人是最后一道)",
            drilldown="/experiments/autoresearch/review-queue",
        ))
    if len(pending) > _REVIEW_ITEM_CAP:
        out.append(DecisionItem(
            key="review:overflow",
            kind="review",
            severity="attention",
            title=f"另有 {len(pending) - _REVIEW_ITEM_CAP} 个候选同样待复核(已按队列截断展示)。",
            evidence=[f"待复核总数 {len(pending)},收件箱逐条上限 {_REVIEW_ITEM_CAP}。"],
            consequence="队列积压越久,自动研究环产出转化越慢。",
            actions=[],
            authority="factory.autoresearch.ReviewQueue",
            drilldown="/experiments/autoresearch/review-queue",
        ))
    return out


def _items_decay(decay: dict | None) -> list[DecisionItem]:
    """decay 报告标红 → 人确认处置/归因。报告缺失不在此造事项(信任缺口归 trust_calibration)。"""
    if not isinstance(decay, dict):
        return []
    status = str(decay.get("status", "")).lower()
    strategies = decay.get("strategies") or []
    decayed = [s.get("strategy") for s in strategies if isinstance(s, dict) and s.get("decayed")]
    flagged = bool(decay.get("decaying")) or status == "red" or bool(decayed)
    if not flagged:
        return []
    return [DecisionItem(
        key="decay:red",
        kind="decay",
        severity="attention",
        title=f"衰减监控标红({len(decayed) or '?'} 个策略)——确认退役归因还是维持观察?",
        evidence=[
            f"decay_status={status or 'red(decaying 标记)'} as_of={decay.get('as_of_date', '?')}",
            f"标红策略:{', '.join(str(x) for x in decayed) or '(见报告)'}",
        ],
        consequence="衰减确认拖延 = 失效策略继续占据台账身份与注意力(§7.4 退役纪律)。",
        actions=[
            DecisionAction(label="确认退役(记录失效归因)",
                           entrypoint="strategy_registry(唯一写入口;§7.4)",
                           allowed=True, reason="退役走台账唯一写入口"),
            DecisionAction(label="维持观察(在报告中留痕)",
                           entrypoint="reports/decay_status.json 生成管线",
                           allowed=True, reason="观察决定应留痕,不得静默"),
        ],
        authority="reports/decay_status.json(实时衰减唯一权威)",
        drilldown="/governance/trust-calibration",
    )]


def _items_data(dq) -> list[DecisionItem]:
    """数据质量裁决非「可用」→ 先修数据再谈研究(R-DATA 系)。"""
    if dq.verdict == "可用":
        return []
    return [DecisionItem(
        key="data:quality",
        kind="data",
        severity="blocked" if dq.verdict == "不建议回测" else "attention",
        title=f"数据质量裁决「{dq.verdict}」——先修数据还是带伤研究(须显式记录)?",
        evidence=[
            f"severe={dq.severe_count}(负价/OHLC 错,真问题) jump={dq.jump_count}(多为除权/涨跌停,正常现象)",
            f"clean_ratio={dq.clean_ratio}",
        ],
        consequence="带伤口径出的一切结论按 R-DATA 系可作废;静默使用半截数据违反 §9。",
        actions=[
            DecisionAction(label="查看质量报告并修复",
                           entrypoint="data_lake/quality_report.json + validate_final.py",
                           allowed=True, reason="数据修复走 canonical 校验管线"),
        ],
        authority="services.read.state.data_quality(data_lake/quality_report.json)",
        drilldown="/data/quality",
    )]


def _items_steer(promo) -> list[DecisionItem]:
    """常设研究重心建议(info 级,不计入待裁决数):下一轮算力投给谁。"""
    if not promo.lead_candidate:
        return []
    return [DecisionItem(
        key="steer:promotion",
        kind="steer",
        severity="info",
        title=f"研究重心建议:{promo.lead_candidate}(距入册最近)——继续推进还是换向?",
        evidence=[
            f"唯一卡点:{promo.lead_blocker or '(已就绪)'}",
            f"边际动作:{promo.research_steer or '(无建议)'}",
        ],
        consequence="常设建议,非紧急;但算力持续投向拥挤簇 = 白涨 n_trials(见 metasearch 发现)。",
        actions=[
            _policy_action("按 R-WF-001 通道推进", "promote_candidate",
                           promo.lead_candidate, "workflow.promote"),
        ],
        authority="services.read.promotion_readiness(排序键=距入册,advisory 非裁决)",
        drilldown="/experiments/promotion-readiness",
    )]


def _items_recompose(rec) -> list[DecisionItem]:
    """组合再构成提案(info 级常设建议,不计入待裁决数):多策略组合权重是否重排。

    只在 artifact 新鲜(≤14 天,周度 job 两个周期)且有有效提案时入箱;缺失/过期/
    空提案不制造事项(周度 job 未跑/无可组合腿都不是"待裁决",不假紧迫)。
    """
    if not isinstance(rec, dict):
        return []
    prop = rec.get("proposal") or {}
    if prop.get("status") != "ok" or not prop.get("weights"):
        return []
    try:
        gen = datetime.fromisoformat(str(rec.get("generated_at", "")))
        age_days = (datetime.now(CHINA_TZ) - gen).days
    except Exception:
        return []  # 无法判新鲜度 → 不入箱(过期提案比没提案更误导)
    if age_days > 14:
        return []
    w = ", ".join(f"{k}={v}" for k, v in prop["weights"].items())
    cm = prop.get("composite_metrics") or {}
    return [DecisionItem(
        key="recompose:proposal",
        kind="portfolio_recompose",
        severity="info",
        title="组合再构成提案(周度):采纳新权重/paper 名单,还是维持现状?",
        evidence=[
            f"提案权重:{w}(排名口径 {rec.get('ranking_version')},多目标非单一收益,R-OBJECTIVE-001)",
            f"组合体检:sharpe={cm.get('sharpe')} maxdd={cm.get('maxdd')} "
            f"组合衰减={prop.get('composite_decay', {}).get('decayed')}",
            f"paper 名单(top-N,R-PROD-001):{rec.get('paper_candidates')}",
        ],
        consequence="常设建议,非紧急;组合不随台账/衰减状态重估 = 权重停留在上次人工实验的假设上。",
        actions=[
            DecisionAction(
                label="查看完整排名与提案",
                entrypoint="reports/research/portfolio_recompose.json(latest+按日期归档)",
                allowed=True, reason="后端确定性产出的持久化排名(R-PROD-001),只读",
            ),
        ],
        authority="portfolio.recompose(确定性内核)+ scheduled_portfolio_recompose(周度,advisory)",
        drilldown="/portfolio-risk",
    )]


def _items_knowledge_expiry(ke) -> list[DecisionItem]:
    """研究结论过保质期(info 级常设建议):复测还是带新证据续期,只有人能决定。

    过期 gate 已自动失效(SearchGate/方向条目停止生效)——不透出的话,失效是静默的,
    「到期重测」的设计初衷落空(孤岛回收:check_expiry 此前只在 CLI 手动命令里)。
    """
    if not isinstance(ke, dict) or ke.get("state") != "retest_due":
        return []
    ef = ke.get("expired_findings") or []
    ed = ke.get("expired_directions") or []
    evidence = [f"过期总数 {ke.get('n_expired')}(findings {len(ef)} + 方向条目 {len(ed)});过期 = gate 已自动失效"]
    for f in ef[:5]:
        evidence.append(f"finding {f.get('id')}: {str(f.get('statement'))[:60]}(expires {f.get('expires')})")
    for d in ed[:5]:
        evidence.append(f"方向 {d.get('id')}: 复活条件 = {str(d.get('revival_condition'))[:80]}")
    return [DecisionItem(
        key="knowledge:expiry",
        kind="knowledge_expiry",
        severity="info",
        title=f"{ke.get('n_expired')} 条研究结论已过保质期——重测还是带新证据续期?",
        evidence=evidence,
        consequence="常设建议,非紧急;但过期 gate 静默失效 = 死路可能重新被搜索/被 LLM 重提,"
                    "而「复活条件是否满足」只有人能判。",
        actions=[
            DecisionAction(
                label="重测(probe / L0 重跑)",
                entrypoint="skill:probe-signal-source 或 apps/factory_cli.py knowledge",
                allowed=True, reason="重测产 L0-L3 证据,结论回写登记簿(证据门控)",
            ),
            DecisionAction(
                label="续期(须带新证据指针改 expires)",
                entrypoint="knowledge/direction_registry.json / knowledge/findings.json",
                allowed=True, reason="无新证据的裸续期 = 把保质期机制改成永久墓碑,禁止",
            ),
        ],
        authority="services.read.knowledge_expiry(check_expiry+方向登记簿,advisory)",
        drilldown="/experiments/autoresearch",
    )]


def _items_exhaustion(exh) -> list[DecisionItem]:
    """研究枯竭信号 → 外探(新数据/文献)还是调整搜索空间,只有人能决定(LOOP §6)。

    只在权威读层判 exhausted 时入箱;healthy / insufficient_evidence 不制造事项
    (刚接上仪表/样本不足时不得假报枯竭——那是 research_exhaustion 的诚实三态)。
    """
    if not isinstance(exh, dict) or exh.get("state") != "exhausted":
        return []
    backlog = exh.get("data_source_backlog") or []
    top3 = "; ".join(
        f"{b.get('id')}({b.get('playbook', '')})" for b in backlog[:3]
    )
    return [DecisionItem(
        key="research:exhausted",
        kind="research_exhaustion",
        severity="attention",
        title=(f"自动研究环连续 {exh.get('window')} 次搜索无产出——"
               f"启动外部探索(新数据/文献)还是调整搜索空间?"),
        evidence=[
            f"判据(机械):{exh.get('criterion', '')}",
            f"runs 明细:{exh.get('detail', '')}",
            f"候选数据源清单 top3:{top3 or '(knowledge/data_source_backlog.json 缺失)'}",
        ],
        consequence="继续在耗尽的搜索空间烧算力 = 白涨 n_trials 加重 DSR 惩罚;"
                    "外探是生成端扩张,启动须人批准(LOOP §6),系统绝不自动抓取。",
        actions=[
            DecisionAction(
                label="启动数据体检剧本(probe-signal-source)",
                entrypoint="skill:probe-signal-source + knowledge/data_source_backlog.json",
                allowed=True,
                reason="外探产物只进 L0-L3 证据,不判有效(R-LLM-001);结论须回写方向登记簿",
            ),
            DecisionAction(
                label="启动文献扫描剧本(literature-scan)",
                entrypoint="docs/agent_skills/literature_scan.md(产出带出处 Hypothesis 草案入候选队列)",
                allowed=True,
                reason="只提假设不判有效(R-LLM-001);草案仍走完整 L0-L3/9-Gate(R-WF-001)",
            ),
            DecisionAction(
                label="调整搜索空间(方向登记簿策展)",
                entrypoint="knowledge/direction_registry.json(证据门控,须带 evidence 指针)",
                allowed=True,
                reason="生成端 steering,不触验真;无证据条目会被 directions.py 忽略",
            ),
        ],
        authority="services.read.research_exhaustion(机械判据 advisory,非裁决)",
        drilldown="/experiments/autoresearch",
    )]


# ── 装配 ────────────────────────────────────────────────────────────────

def _load_decay() -> dict | None:
    if not DECAY_STATUS.exists():
        return None
    return json.loads(DECAY_STATUS.read_text(encoding="utf-8"))


def get_decision_inbox(
    *,
    gate_verdicts=None,
    system_truth=None,
    review_pending: list[dict] | None = None,
    decay: dict | None = ...,  # ... 哨兵 = 从磁盘读;None = 显式「无报告」
    data_quality_view=None,
    promotion=None,
    exhaustion: dict | None = None,
    recompose: dict | None = ...,  # ... 哨兵 = 从磁盘读;None = 显式「无提案」
    knowledge_expiry: dict | None = None,
) -> DecisionInboxView:
    """装配收件箱。关键字参数仅供测试注入事实源;生产路径全部走权威读服务。"""
    items: list[DecisionItem] = []
    all_readable = True

    # 每个事实源独立 try:失败 → source_error 显式入箱,绝不静默跳过。
    try:
        if gate_verdicts is None:
            from services.read.validation_gate import get_gate_verdicts
            gate_verdicts = get_gate_verdicts().verdicts
        items += _items_registered_failed(gate_verdicts)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("validation_gate.get_gate_verdicts", exc))

    try:
        if system_truth is None:
            from services.read.system_truth import get_system_truth
            system_truth = get_system_truth()
        items += _items_deployment(system_truth)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("system_truth.get_system_truth", exc))

    try:
        if review_pending is None:
            from factory.autoresearch import ReviewQueue
            review_pending = ReviewQueue().pending()
        items += _items_review_queue(review_pending)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("autoresearch.ReviewQueue.pending", exc))

    try:
        if decay is ...:
            decay = _load_decay()
        items += _items_decay(decay)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("reports/decay_status.json", exc))

    try:
        if data_quality_view is None:
            from services.read.state import data_quality
            data_quality_view = data_quality(with_duckdb=False)
        items += _items_data(data_quality_view)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("state.data_quality", exc))

    try:
        if promotion is None:
            from services.read.promotion_readiness import get_promotion_readiness
            promotion = get_promotion_readiness()
        items += _items_steer(promotion)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("promotion_readiness", exc))

    try:
        if exhaustion is None:
            from services.read.research_exhaustion import get_research_exhaustion
            exhaustion = get_research_exhaustion()
        items += _items_exhaustion(exhaustion)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("research_exhaustion", exc))

    try:
        if recompose is ...:
            recompose = (json.loads(RECOMPOSE_LATEST.read_text(encoding="utf-8"))
                         if RECOMPOSE_LATEST.exists() else None)
        items += _items_recompose(recompose)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("portfolio_recompose", exc))

    try:
        if knowledge_expiry is None:
            from services.read.knowledge_expiry import get_knowledge_expiry
            knowledge_expiry = get_knowledge_expiry()
        items += _items_knowledge_expiry(knowledge_expiry)
    except Exception as exc:  # noqa: BLE001
        all_readable = False
        items.append(_source_error_item("knowledge_expiry", exc))

    items.sort(key=lambda i: (_SEVERITY_ORDER.get(i.severity, 9), i.key))
    pending = sum(1 for i in items if i.severity in ("blocked", "attention"))

    if pending > 0:
        headline = f"今天需要你裁决 {pending} 件事;最高优先级:{items[0].title}"
    elif all_readable:
        headline = "今天无需你介入:所有事实源已确认,无待裁决事项(空收件箱 = 系统健康)。"
    else:
        # 理论不可达(源错误本身是 attention 事项),防御性保留:禁在盲区宣称无事。
        headline = "部分事实源不可读,收件箱不完整——不得视为无事。"

    return DecisionInboxView(
        as_of=datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
        headline=headline,
        pending_count=pending,
        all_sources_readable=all_readable,
        items=items,
        truth_sources={
            "verdict_authority": "core.analysis.nine_gate_policy.decide_nine_gate",
            "deployment": "services.read.system_truth(fail-closed)",
            "review_queue": "factory.autoresearch.ReviewQueue",
            "decay_live": str(DECAY_STATUS),
            "data": "data_lake/quality_report.json",
            "steer": "services.read.promotion_readiness(advisory)",
            "research_exhaustion": "services.read.research_exhaustion(机械判据 advisory)",
            "portfolio_recompose": str(RECOMPOSE_LATEST) + "(周度确定性排名,advisory)",
            "knowledge_expiry": "services.read.knowledge_expiry(保质期复核,advisory)",
        },
        honesty="本视图只聚合权威事实源,不做新判定;actions 为 advisory 导航,由人经 canonical "
                "入口执行(R-LLM-001/ADR-030);任一事实源不可读即显式入箱,空箱只在全源可读时宣称。",
    )
