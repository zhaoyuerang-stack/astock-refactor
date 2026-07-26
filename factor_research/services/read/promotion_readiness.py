"""Promotion-Readiness Read Service —— Alpha 工厂「晋级就绪」驾驶舱。

回答唯一决策:**下一个该推进哪个候选?卡它的那一个约束是什么?**(``DECISION_COCKPITS.md`` 驾驶舱①)

诚实护栏(必须遵守):
- **权威裁决 = ``core.analysis.nine_gate_policy.decide_nine_gate``**(只认 passed_all)。
  本服务的逐门诊断(GateDiag)是**派生·非裁决**,只用于定位「卡在哪一门」,绝不改写 passed_all。
- registry 存的是**扁平** nine_gate 摘要(无 per-gate 富结构),故逐门状态由扁平字段映射而来,
  字段缺失即标 ``unknown``,不臆测通过。
- 从未审计(nine_gate=={})的版本如实标「需先跑 9-Gate」,门距记最大值 9,不伪造。
- 排序键 = ``distance_to_register``(距入册门数),**不是** Sharpe/年化(避诱导过拟合)。
- ``marginal_action`` 是启发式 advisory;不构成自动晋级(``R-LLM-001``)。
"""
from __future__ import annotations

import json
from datetime import datetime
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

from contracts.views import CandidateReadiness, GateDiag, PromotionReadinessView

ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")
CORR_AUDIT = ROOT / "reports" / "research" / "registry_correlation_audit.json"

# 评估池:正在推进或可复活的版本。退役/已证伪不进就绪榜。
POOL_STATUSES = {"候选", "参考"}
CROWD_THRESHOLD = 0.7  # 家族间相关 > 此值视为同信息簇(拥挤)


def _gate(gate: str, name: str, status: str, actual, threshold: str, field: str) -> GateDiag:
    return GateDiag(
        gate=gate, name=name, status=status,
        actual="" if actual is None else str(actual),
        threshold=threshold, source_field=field,
    )


def _derive_gates(ng: dict, top: dict) -> list[GateDiag]:
    """从扁平 nine_gate 字段 + 版本顶层字段派生逐门诊断(非裁决)。

    每门:字段缺失→unknown;有值→按阈值判 passed/failed。source_field 可追溯。
    """
    def num(x):
        return x if isinstance(x, (int, float)) else None

    nw = num(ng.get("nw_icir"))
    retention = num(ng.get("icir_retention"))
    wf = num(ng.get("wf_sharpe"))
    bear = num(ng.get("bear_sharpe"))
    cap = num(ng.get("capacity_limit_aum"))
    pbo = num(ng.get("pbo"))
    dsr_sig = ng.get("dsr_significant")
    dsr_p = num(ng.get("dsr_p"))

    # G8 DSR:优先 dsr_significant,缺则按 dsr_p<0.05
    if dsr_sig is True:
        g8 = "passed"
    elif dsr_sig is False:
        g8 = "failed"
    elif dsr_p is not None:
        g8 = "passed" if dsr_p < 0.05 else "failed"
    else:
        g8 = "unknown"

    return [
        _gate("G1_DATA", "数据可用", "passed" if top.get("data_scope") else "unknown",
              "有 data_scope" if top.get("data_scope") else None, "data_scope 存在", "data_scope"),
        _gate("G2_IC", "防未来/IC", "unknown" if nw is None else ("passed" if nw > 0 else "failed"),
              nw, "nw_icir>0", "nw_icir"),
        _gate("G3_NEUT", "中性化/成本", "unknown" if retention is None else ("passed" if retention >= 0.3 else "failed"),
              retention, "icir_retention≥0.3", "icir_retention"),
        _gate("G4_OOS", "样本外", "unknown" if wf is None else ("passed" if wf > 0 else "failed"),
              wf, "wf_sharpe>0", "wf_sharpe"),
        _gate("G5_STRESS", "压力/regime", "unknown" if bear is None else ("passed" if bear > -1.0 else "failed"),
              bear, "bear_sharpe>-1.0", "bear_sharpe"),
        _gate("G6_CAPACITY", "容量/换手", "unknown" if cap is None else ("passed" if cap > 0 else "failed"),
              cap, "capacity_limit_aum>0", "capacity_limit_aum"),
        _gate("G7_PBO", "中性化/相关(PBO)", "unknown" if pbo is None else ("passed" if pbo < 0.5 else "failed"),
              pbo, "pbo<0.5", "pbo"),
        _gate("G8_DSR", "DSR/多重检验", g8, dsr_sig if dsr_sig is not None else dsr_p, "dsr_p<0.05", "dsr_significant/dsr_p"),
        _gate("G9_MATERIAL", "材料完整", "passed" if (top.get("desc") and top.get("config")) else "failed",
              "齐全" if (top.get("desc") and top.get("config")) else "缺 desc/config", "desc+config 齐全", "desc/config"),
    ]


def _load_corr() -> dict:
    """读家族×家族相关矩阵(corr_full)。缺失/不可读 → 空,拥挤度记 None(诚实未知)。"""
    if not CORR_AUDIT.exists():
        return {}
    try:
        return json.loads(CORR_AUDIT.read_text(encoding="utf-8")).get("corr_full") or {}
    except Exception:
        return {}


def _fam_key(family: str) -> str:
    return str(family).replace("-", "_")


def _crowding(family: str, corr: dict) -> tuple[float | None, str]:
    """家族对其它家族的最大正相关 + 同簇家族集合。无相关数据 → (None, '未知')。"""
    key = _fam_key(family)
    row = corr.get(key)
    if not isinstance(row, dict):
        return None, "未知(无相关数据)"
    others = {k: v for k, v in row.items() if k != key and isinstance(v, (int, float))}
    if not others:
        return None, "未知"
    crowd = max(others.values())
    peers = [k for k, v in others.items() if v > CROWD_THRESHOLD]
    cluster = "+".join([key] + peers) if peers else f"{key}(独立)"
    return crowd, cluster


def _marginal_action(verdict: str, blocker: str, crowd: float | None) -> str:
    crowded = crowd is not None and crowd > CROWD_THRESHOLD
    if verdict == "PASSED":
        return "已就绪:可走入册闸门(register)"
    if "未审计" in blocker:
        return "先跑完整 9-Gate 产出门禁证据"
    if "DSR" in blocker:
        return (f"簇内已拥挤(corr>{crowd:.2f}),微调边际≈0,建议换信息源") if crowded \
            else "扩样本/增信息源,提升样本外显著性"
    if "PBO" in blocker:
        return "降搜索自由度/简化参数,抑过拟合"
    if "容量" in blocker:
        return "缩目标规模或限低流动性分位"
    return "点开证据链定位卡点门"


def _assess(family: str, v: dict, corr: dict) -> CandidateReadiness:
    from core.analysis.nine_gate_policy import decide_nine_gate

    ng = v.get("nine_gate") or {}
    top = v
    decision = decide_nine_gate(ng)
    verdict = decision.code  # PASSED | FAILED | PENDING | RUN_FAILED —— 唯一权威
    audited = decision.audited
    gates = _derive_gates(ng, top)
    not_passed = [g for g in gates if g.status != "passed"]

    # 门距与卡点:权威裁决主导 headline,逐门诊断定位
    if verdict == "PASSED":
        distance, blocker = 0, ""
    elif not ng:
        distance, blocker = 9, "未审计:需先跑完整 9-Gate"
    elif verdict == "RUN_FAILED":
        distance, blocker = len(not_passed) or 9, "9-Gate 运行失败"
    elif verdict == "PENDING":
        distance = len(not_passed) or 9
        blocker = "9-Gate 未跑完整(passed_all 未定)"
    else:  # FAILED:优先采用权威 reasons,否则取第一道未过门
        distance = len(not_passed)
        reasons = decision.blocking_reasons
        if "dsr_not_significant" in reasons:
            blocker = "G8 DSR 多重检验不显著"
        elif "pbo_high" in reasons:
            blocker = "G7 PBO 过高(过拟合风险)"
        elif not_passed:
            g = not_passed[0]
            blocker = f"{g.gate} {g.name}({g.status})"
        else:
            blocker = "passed_all=False(综合未过)"

    crowd, cluster = _crowding(family, corr)
    return CandidateReadiness(
        family=family, version=v.get("version", ""), stage=v.get("status", ""),
        authoritative_verdict=verdict, audited=audited,
        distance_to_register=distance, single_blocker=blocker,
        marginal_action=_marginal_action(verdict, blocker, crowd),
        gate_diag=gates, info_cluster=cluster, crowding=crowd,
        dsr_p=ng.get("dsr_p"), pbo=ng.get("pbo"), n_trials=ng.get("n_trials"),
    )


def _cluster_map(corr: dict) -> dict:
    """家族级拥挤度概览:冗余率 + 最拥挤家族对。缺信息簇 taxonomy 暂不臆造(见 DECISION_COCKPITS §8)。"""
    fams = [k for k, v in corr.items() if isinstance(v, dict)]
    pairs = list(combinations(fams, 2))
    if not pairs:
        return {"n_families": len(fams), "redundancy_rate": None, "most_crowded": None,
                "note": "无相关矩阵数据"}
    vals = [(a, b, corr[a].get(b)) for a, b in pairs if isinstance(corr[a].get(b), (int, float))]
    high = [t for t in vals if t[2] is not None and t[2] > CROWD_THRESHOLD]
    most = max(vals, key=lambda t: t[2]) if vals else None
    return {
        "n_families": len(fams),
        "redundancy_rate": round(len(high) / len(vals), 3) if vals else None,
        "most_crowded": ({"a": most[0], "b": most[1], "corr": round(most[2], 3)} if most else None),
        "crowded_pairs": [{"a": a, "b": b, "corr": round(c, 3)} for a, b, c in high],
        "note": "信息簇 taxonomy(行业轮动/事件/资金流…)尚未定义,'最缺簇'待 SPEC 立项后补",
    }


def get_promotion_readiness() -> PromotionReadinessView:
    import strategy_registry

    corr = _load_corr()
    data = strategy_registry._load()
    rows: list[CandidateReadiness] = []
    for fam in data.get("families", []):
        fid = fam.get("id") or fam.get("family_id")
        for v in fam.get("versions", []):
            if v.get("status") in POOL_STATUSES:
                rows.append(_assess(fid, v, corr))

    # 按「距入册」升序;同距按 dsr_p 升序(None 殿后)
    rows.sort(key=lambda r: (r.distance_to_register, r.dsr_p if r.dsr_p is not None else 9.9))

    lead = rows[0] if rows else None
    research_steer = ""
    if lead is not None:
        research_steer = f"重心:{lead.family}/{lead.version} —— {lead.marginal_action}"

    return PromotionReadinessView(
        as_of=datetime.now(CHINA_TZ).date().isoformat(),
        lead_candidate=(f"{lead.family}/{lead.version}" if lead else ""),
        lead_blocker=(lead.single_blocker if lead else ""),
        research_steer=research_steer,
        candidates=rows,
        cluster_map=_cluster_map(corr),
        truth_sources={
            "registry": str(ROOT / "strategy_versions.json"),
            "correlation": str(CORR_AUDIT),
            "verdict_authority": "core.analysis.nine_gate_policy.decide_nine_gate",
        },
    )
