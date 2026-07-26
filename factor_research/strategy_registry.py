"""
策略版本登记 —— 两层结构：母策略(family) → 版本(version)

核心理念：具体策略默认会失效，真正要管理的是「母策略生命周期」。
  · 母策略(family)：一个独立 alpha 家族，记 核心假设 / 适用市场 / 失效信号 / status
  · 版本(version)：该家族下的参数与数据口径变体，记 配置 / 绩效 / status / 备注
  · 数据口径(data_full/data_lake) 是版本属性，不再占版本号语义（防"喂出来的高收益"被当真）

版本 status 约定：候选 / 在册 / 退役 / 参考
母策略 status 约定：active / paused / retired

用法：
  python3 strategy_registry.py                      # 打印母策略分组对比表
  from strategy_registry import register_family, register
  register_family("momentum", "截面动量", hypothesis=..., regime=..., decay_signal=...)
  register("momentum", "v1.0", desc, config, data_scope, metrics, status="候选", notes=...)
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, date
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from engine.metrics import compute_hit

REGISTRY: Path = Path(__file__).parent / "strategy_versions.json"

# 双轨准入：唯一允许长期占用「在册」的两条轨。
#   standalone  —— 单体达标（hit=True：年化>15% 且 回撤<20%）+ DSR 多重测试惩罚下显著（dsr_p<DSR_ALPHA）
#   diversifier —— 单体不达标但作为组合分流器（负相关 / 对组合夏普有正增量），须 rationale 佐证
# DSR 仅卡 standalone：diversifier 凭组合边际而非单体统计显著性入册，不受此门约束。
ADMISSION_TRACKS: tuple[str, ...] = ("standalone", "diversifier")

# standalone 准入的 DSR（Deflated Sharpe Ratio）多重测试惩罚显著性阈值。
# 来源 R-OBJECTIVE-001 / 9-Gate G8：单体达标(hit)不等于经得起搜索惩罚，dsr_p>=此值即多重测试下不显著。
DSR_ALPHA: float = 0.05

# 设计上即为「对冲分流器」的母策略：单体低收益 + 与主力负相关，靠组合层增量入册（diversifier 轨）。
# 仅这两族的对冲假设里显式写了「等额做空…对冲 Beta」并实测负相关，故迁移时自动归类为 diversifier。
DIVERSIFIER_FAMILIES: set[str] = {"large-cap-growth-hedged", "hq-momentum-hedged"}

# 版本 status 词表(守卫审计 #3):机械冻结 strategy_versions.json 存量值 + 规范「在册」。
# 枚举日 2026-07-17 存量:候选/参考/已证伪/退役;「在册」为双轨准入规范态(台账当日无在册版,
# 但仍是 register 合法目标,必须收纳)。「条件假设/观察」= research_toolkit 观察轨默认态
# (artifacts.py::registry_status,经 veto_filter_marginal 落台账;冻结时只枚举了台账存量、
# 漏了"代码在用但台账暂无"的状态,主仓全量回归 test_veto_filter 揪出)。
# register_family 的 family status 是另一字段,不在此列。
ALLOWED_VERSION_STATUS: frozenset[str] = frozenset({"候选", "参考", "已证伪", "退役", "在册", "条件假设/观察"})
# 英文同义词会让 status≠「在册」从而绕过 register 双轨准入门,同时被证据守卫 ACTIVE_STATUS
# 宽认——两层网眼错位。一律拒绝并提示用「在册」(枚举日存量词表中无此四词,无需从 ALLOWED 剔除)。
VERSION_STATUS_SYNONYMS_BLOCKED: frozenset[str] = frozenset({"active", "ACTIVE", "APPROVED", "registered"})


# ── 台账 JSON Schema（契约声明）──────────────────────────────────────────────
# 本文件是台账唯一写入口,schema 跟着写者走:消费方(services/read、守卫、前端)按此形状读。
# 必填键 = 写入路径必然产生的键;其余历史/可选键经 total=False 声明。
# 审计可用"实际 json 键 ⊆ 声明键"的键集合比对做机械核查(schema drift 检测)。

class MetricsDict(TypedDict, total=False):
    # 规范键词表(compute_hit 与双轨准入门只用这些)。真实台账另含样本切片/压力扩展键——
    # {annual,maxdd,sharpe,hit}_{2010,search_2018,holdout_2025,...}、wf_*、
    # {family}_delta_annual_is_oos_stress 等(2026-07 枚举),故运行期字段类型按 dict[str, Any]
    # 声明,本类仅作规范键文档;drift 比对以"规范键 + 前缀/后缀豁免规则"核查。
    annual: float
    maxdd: float
    sharpe: float
    calmar: float
    hit: bool            # 一律由 engine.metrics.compute_hit 重算覆盖,禁止调用方手填
    n: int


class AdmissionDict(TypedDict, total=False):
    track: str           # "standalone" | "diversifier"(见 ADMISSION_TRACKS)
    rationale: str
    note: str


class EvidenceDict(TypedDict, total=False):
    hypothesis_id: str
    experiment_ids: list[str]
    data_incidents: list[dict[str, Any]]
    production_blocked: bool
    retirement: dict[str, Any]       # retire_version 写入:reason/evidence_refs/retired_at/actor
    spec_migration: dict[str, Any]   # attach_executable_spec 写入:spec_hash/requires_revalidation
    seed_provenance: dict[str, Any]      # phase4_register._build_evidence 写入(ADR-022 种子溯源)
    semantic_seed_review: dict[str, Any] # 同上:LLM 种子需人工审视标记


class DataScopeDict(TypedDict, total=False):
    source: str
    period: str
    survivorship_bias: bool
    wf_validated: bool               # phase4_register 写入:Phase3 WF 聚合 verdict==PASS
    phase1_audited: bool             # 同上:Phase1 检查无 FAIL
    reproducibility: dict[str, Any]  # 同上:reproducibility_meta() 元信息
    holdout: dict[str, Any]          # 同上:§5.2 holdout 闸摘要


class _VersionRequired(TypedDict):
    version: str


class VersionRecord(_VersionRequired, total=False):
    date: str
    desc: str
    config: dict[str, Any]
    data_scope: DataScopeDict | str  # 存量两种形态(show() 兼容 str 直写)
    metrics: dict[str, Any]        # 规范键见 MetricsDict;含样本切片/压力扩展键(见该类注释)
    status: str                      # 词表见 ALLOWED_VERSION_STATUS
    notes: str
    evidence: EvidenceDict
    admission: AdmissionDict
    nine_gate: dict[str, Any]        # NineGatesReport.summarize() 摘要(dsr_p/psr/n_trials/pbo/wf_sharpe/...)
    executable_spec: dict[str, Any]  # {"spec": ExecutableStrategySpec.to_dict(), "spec_hash": str}
    decay_check: dict[str, Any]      # governance/decay.py 结果 + checked_at(版本级,别于家族级 decay_signal)
    catalog_status: dict[str, Any]   # {"status": "ACTIVE"|"SHADOW", "marginal": {...}, "changed_at": str}
    dsr_demotion: dict[str, Any]     # demote_dsr_insignificant_standalone 的降级审计块


class _FamilyRequired(TypedDict):
    id: str
    versions: list[VersionRecord]


class FamilyRecord(_FamilyRequired, total=False):
    name: str
    hypothesis: str
    regime: str
    decay_signal: str
    status: str                      # active / paused / retired(家族生命周期,别于版本 status)
    style_betas: dict[str, float]
    capacity_m: float
    failure_boundaries: dict[str, Any]


class RegistryDoc(TypedDict):
    families: list[FamilyRecord]


def _load() -> RegistryDoc:
    if not REGISTRY.exists():
        return {"families": []}
    data = json.loads(REGISTRY.read_text())
    if isinstance(data, list):
        # 旧扁平格式禁止静默视为空:返回空 dict 后任何 attach_*/register 触发 _save
        # 会把整本台账清空覆盖(退役纪律 R-7.4:不得删历史)。必须人工迁移。
        raise ValueError(
            f"{REGISTRY} 是旧扁平 list 格式,拒绝加载(防 _save 静默清空台账);"
            f"请先人工迁移为 {{'families': [...]}} 结构。")
    return data


def _version_key(version: str) -> tuple[int | str, ...]:
    """版本自然序:'v1.10' > 'v1.2'(字符串序会排反)。数字段转 int 比较,非数字兜底字符串。"""
    parts = re.split(r"(\d+)", str(version))
    return tuple(int(p) if p.isdigit() else p for p in parts)


def _save(data: RegistryDoc) -> None:
    data["families"].sort(key=lambda f: f["id"])
    # 原子写:临时文件 + os.replace,进程中途挂掉不会留下半截 JSON 损坏台账
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = REGISTRY.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(payload)
    os.replace(tmp, REGISTRY)


def register_family(id: str, name: str, hypothesis: str = "", regime: str = "",
                    decay_signal: str = "", status: str = "active",
                    style_betas: dict[str, float] | None = None,
                    capacity_m: float = 0.0,
                    failure_boundaries: dict[str, Any] | None = None) -> str:
    """登记/更新一个母策略（同 id 覆盖元信息，保留其下版本）"""
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == id), None)
    if fam is None:
        fam = {"id": id, "versions": []}
        data["families"].append(fam)

    fam.update({
        "name": name,
        "hypothesis": hypothesis,
        "regime": regime,
        "decay_signal": decay_signal,
        "status": status,
        "style_betas": style_betas or {},
        "capacity_m": float(capacity_m),
        "failure_boundaries": failure_boundaries or {}
    })
    _save(data)
    return id


def register(family: str, version: str, desc: str, config: dict[str, Any],
             data_scope: DataScopeDict | str, metrics: dict[str, Any] | None,
             status: str = "候选", notes: str = "",
             evidence: EvidenceDict | None = None, admission: AdmissionDict | None = None,
             nine_gate: dict[str, Any] | None = None, date_str: str | None = None,
             spec: dict[str, Any] | None = None, spec_hash: str | None = None) -> str:
    """登记/更新某母策略下的一个版本（同 family+version 覆盖）

    hit：一律由 ``engine.metrics.compute_hit`` 按 metrics 里的 annual/maxdd 重算并覆盖，
         禁止调用方手填——这是「修代码不修记分牌」铁律的机械执行点。
    admission：双轨准入声明 {"track": "standalone"|"diversifier", "rationale": str, ...}。
         status="在册" 必须通过准入：standalone 轨要求 hit=True；diversifier 轨要求 rationale。
         单体达标(hit=True)且未显式声明 → 自动补 standalone 轨（向后兼容）。
    nine_gate：Nine-Gate R2P 审计摘要 {dsr_p, psr, n_trials, pbo, wf_sharpe, cv_sharpe, ...}。
    evidence：证据链锚点 {"hypothesis_id": str, "experiment_ids": [str,...]}（默认空 dict）。
    date_str：保留/指定登记日期；缺省时同号覆盖保留原日期，新增则取今日。
    """
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记，请先 register_family('{family}', ...)")

    # 0a) 版本 status 词表(审计 #3):禁英文同义词绕过「在册」双轨门;禁任意自造 status。
    #     不碰 register_family 的 family status(那是家族生命周期标记)。
    if status in VERSION_STATUS_SYNONYMS_BLOCKED:
        raise ValueError(
            f"{family}/{version} 版本在册状态必须用「在册」——英文同义词 {status!r} "
            f"会绕过双轨准入门(审计 #3)"
        )
    if status not in ALLOWED_VERSION_STATUS:
        raise ValueError(
            f"{family}/{version} 未知 version status={status!r}；"
            f"允许 {sorted(ALLOWED_VERSION_STATUS)}"
        )

    # 0) ExecutableStrategySpec 校验(Task 5)：提供 spec 则身份必须自洽——
    #    spec_hash 与重算一致、family/version 与登记参数一致。不提供则向后兼容(历史/手动登记)。
    spec_record = None
    if spec is not None:
        from core.strategy_spec import ExecutableStrategySpec
        parsed = ExecutableStrategySpec.from_dict(spec)
        parsed.validate()
        if spec_hash is not None and parsed.spec_hash != spec_hash:
            raise ValueError(
                f"{family}/{version} spec_hash 不匹配：重算={parsed.spec_hash[:12]} 传入={str(spec_hash)[:12]}")
        if parsed.family != family or parsed.version != version:
            raise ValueError(
                f"策略身份不匹配：spec={parsed.family}/{parsed.version} 登记={family}/{version}")
        spec_record = {"spec": parsed.to_dict(), "spec_hash": parsed.spec_hash}

    # 1) hit 由公式重算覆盖——杜绝手填记分牌
    metrics = dict(metrics or {})
    a, dd = metrics.get("annual"), metrics.get("maxdd")
    if a is not None and dd is not None:
        metrics["hit"] = compute_hit(a, dd)
    hit = bool(metrics.get("hit", False))

    # 2) 「在册」必须通过双轨准入
    adm = cast(AdmissionDict, dict(admission or {}))
    if status == "在册":
        track = adm.get("track")
        if track is None and hit:
            adm = {"track": "standalone", "rationale": "单体达标（年化>15% 且 回撤<20%）"}
        elif track is None and not hit:
            raise ValueError(
                f"{family}/{version} 不能登记为「在册」：单体不达标"
                f"（hit=False，年化={a}，回撤={dd}），且未声明 diversifier 准入。"
                f"请传 admission={{'track':'diversifier','rationale':...}} 或降级 status。")
        elif track == "standalone" and not hit:
            raise ValueError(
                f"{family}/{version} standalone 准入要求 hit=True，但单体不达标（年化={a}，回撤={dd}）。")
        elif track == "diversifier" and not (adm.get("rationale") or "").strip():
            raise ValueError(
                f"{family}/{version} diversifier 准入要求 rationale（负相关 / 组合夏普增量依据）。")
        elif track not in ADMISSION_TRACKS:
            raise ValueError(
                f"{family}/{version} 未知 admission.track={track!r}（应为 {ADMISSION_TRACKS}）。")

        # standalone 轨：hit 之外，必须通过 DSR 多重测试惩罚（R-OBJECTIVE-001 / G8）。
        # 覆盖「显式声明 standalone」与上面「hit=True 自动补 standalone」两条路径。
        if adm.get("track") == "standalone":
            dsr_p = (nine_gate or {}).get("dsr_p")
            if dsr_p is None:
                raise ValueError(
                    f"{family}/{version} standalone 准入必须有 nine_gate.dsr_p——"
                    f"请先跑 9-Gate 审计（workflow/promote.py 或 run_nine_gates_all.py）。"
                    f"若靠组合边际入册，请改 admission={{'track':'diversifier','rationale':...}}；"
                    f"否则降 status='候选'。")
            if dsr_p >= DSR_ALPHA:
                raise ValueError(
                    f"{family}/{version} standalone 准入要求 DSR p<{DSR_ALPHA}，"
                    f"当前 dsr_p={dsr_p:.4f}——多重测试惩罚下不显著（hit 达标≠搜索后显著）。"
                    f"可改 diversifier 轨（需 rationale）或降 status='候选'/'参考'。")

    existing = next((v for v in fam["versions"] if v["version"] == version), None)
    reg_date = date_str or (existing.get("date") if existing else None) or str(date.today())
    fam["versions"] = [v for v in fam["versions"] if v["version"] != version]   # 同号覆盖
    rec: VersionRecord = {
        "version": version, "date": reg_date, "desc": desc,
        "config": config, "data_scope": data_scope, "metrics": metrics,
        "status": status, "notes": notes,
        "evidence": evidence or {},
        "admission": adm,
        "nine_gate": nine_gate or {},
    }
    if spec_record:
        rec["executable_spec"] = spec_record
    fam["versions"].append(rec)
    fam["versions"].sort(key=lambda v: _version_key(v["version"]))
    _save(data)
    return f"{family}/{version}"


def migrate_two_track_admission(apply: bool = True) -> list[dict[str, Any]]:
    """一次性迁移：用 compute_hit 重算全台账 hit，并按双轨准入规则裁定「在册」去留。

    规则（与 register() 闸门一致）：
      · 在册 且 hit=True            → 保留在册，admission=standalone
      · 在册 且 hit=False 且 对冲族   → 保留在册，admission=diversifier（rationale 取自 notes）
      · 在册 且 hit=False 且 非对冲族 → 降级（autoresearch_*/fundamental-momentum → 候选；其余 → 参考）
      · 非在册                       → 仅刷新 hit 与占位字段，不动 status

    幂等：再次运行不产生新变更。返回逐版本 transition 列表（供 CLI 打印 / 审计）。
    本函数在 registry 模块内，经 _save 走唯一写入口，未绕过台账写入纪律。
    """
    data = _load()
    transitions = []
    for fam in data["families"]:
        fid = fam["id"]
        for v in fam["versions"]:
            m = dict(v.get("metrics") or {})
            a, dd = m.get("annual"), m.get("maxdd")
            old_hit = m.get("hit")
            new_hit = compute_hit(a, dd) if (a is not None and dd is not None) else bool(old_hit)
            if a is not None and dd is not None:
                m["hit"] = new_hit
            v["metrics"] = m
            v["evidence"] = v.get("evidence") or {}
            v["nine_gate"] = v.get("nine_gate") or {}

            old_status = v.get("status")
            new_status, track = old_status, (v.get("admission") or {}).get("track", "—")

            if old_status == "在册":
                if new_hit:
                    v["admission"] = {"track": "standalone",
                                      "rationale": "单体达标（年化>15% 且 回撤<20%）"}
                    new_status, track = "在册", "standalone"
                elif fid in DIVERSIFIER_FAMILIES:
                    rationale = (v.get("notes") or "").strip()[:200] or "对冲分流器：等额做空对冲 Beta，与主力负相关"
                    v["admission"] = {"track": "diversifier", "rationale": rationale,
                                      "note": "迁移自动归类：对冲母策略，单体低收益+负相关，靠组合层增量入册"}
                    new_status, track = "在册", "diversifier"
                else:
                    new_status = "候选" if (fid.startswith("autoresearch_") or fid == "fundamental-momentum") else "参考"
                    v["status"] = new_status
                    v["admission"] = {}
                    track = "—（降级）"
            else:
                v["admission"] = v.get("admission") or {}

            transitions.append({
                "id": f"{fid}/{v['version']}",
                "old_status": old_status, "new_status": new_status,
                "old_hit": old_hit, "new_hit": new_hit, "track": track,
                "annual": a, "maxdd": dd,
            })

    if apply:
        _save(data)
    return transitions


def demote_dsr_insignificant_standalone(threshold: float = DSR_ALPHA,
                                        apply: bool = True) -> list[dict[str, Any]]:
    """一次性治理迁移（ADR-020 / R-OBJECTIVE-001）：把 DSR 多重测试惩罚下不显著的
    「在册 standalone」降为「参考」，保留历史绩效/配置/nine_gate，仅移出有效 alpha 池。

    判定：status=='在册' 且 admission.track=='standalone' 且 (dsr_p is None 或 dsr_p>=threshold)。
      · status → '参考'，admission → {}（不再占用准入轨）
      · metrics / evidence / nine_gate 原样保留（R-7.4 退役纪律：不得删历史）
      · 写入 dsr_demotion 审计块，记降级前轨道 / dsr_p / 阈值 / 依据

    幂等：降级后 status 变「参考」，再次运行不再命中。经 _save 走台账唯一写入口。
    返回逐版本 transition 列表（供 CLI 打印 / 审计）。
    """
    data = _load()
    transitions = []
    for fam in data["families"]:
        fid = fam["id"]
        for v in fam["versions"]:
            adm = v.get("admission") or {}
            if v.get("status") != "在册" or adm.get("track") != "standalone":
                continue
            dsr_p = (v.get("nine_gate") or {}).get("dsr_p")
            if dsr_p is not None and dsr_p < threshold:
                continue  # DSR 达标，留任在册 standalone
            v["status"] = "参考"
            v["admission"] = {}
            v["dsr_demotion"] = {
                "from_status": "在册", "from_track": "standalone",
                "dsr_p": dsr_p, "threshold": threshold,
                "rule": "R-OBJECTIVE-001 / 9-Gate G8（DSR 多重测试惩罚下不显著）",
                "date": str(date.today()),
            }
            transitions.append({
                "id": f"{fid}/{v['version']}", "dsr_p": dsr_p,
                "new_status": "参考", "reason": "DSR 不显著（None 或 >=阈值）",
            })
    if apply:
        _save(data)
    return transitions


def attach_nine_gate(family: str, version: str, summary: dict[str, Any] | None,
                     evidence: EvidenceDict | None = None) -> str:
    """把一次 Nine-Gate 审计摘要（NineGatesReport.summarize()）写入指定版本的 nine_gate 字段。

    可选 evidence 同步绑定证据链 {"hypothesis_id":..., "experiment_ids":[...]}。
    经 _save 走台账唯一写入口；不改 status/metrics。
    """
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    v = next((x for x in fam["versions"] if x["version"] == version), None)
    if v is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")
    v["nine_gate"] = dict(summary or {})
    if evidence:
        ev = cast(EvidenceDict, dict(v.get("evidence") or {}))
        ev.update(evidence)
        v["evidence"] = ev
    _save(data)
    return f"{family}/{version}"


def attach_path_metrics(
    family,
    version,
    metrics,
    *,
    provenance,
    window=None,
    n=None,
    returns_sha256=None,
    path=None,
    material_delta_keys=None,
    note=None,
):
    """用一次 **live path re-run** 的事实绩效覆盖 version.metrics（纠偏误导数字）。

    铁律：
      · 唯一写入口经本函数 + _save（R-REG-001）
      · hit 由 compute_hit(annual, maxdd) 重算，禁止手填
      · **不改** status / admission / config（生命周期与入册另走 register/workflow）
      · 旧 metrics 归档到 evidence.metrics_history（可审计，不静默抹掉）
      · provenance 必须标明 live_rerun（拒绝把 stale_csv 写进台账冒充事实）
      · 若与旧 metrics 实质偏差且已有 nine_gate：标记 nine_gate.stale_vs_path_metrics，
        **不**伪造新门禁结果（门禁须另跑 attach_nine_gate）

    用于：路径报告发现台账 metrics 与可复现重跑不一致时，把「系统里的数字」改成事实。
    """
    from datetime import datetime

    if provenance != "live_rerun":
        raise ValueError(
            f"attach_path_metrics 只接受 provenance=live_rerun，收到 {provenance!r}；"
            f"陈旧 CSV 不得写回台账 metrics（防二次误导）"
        )
    metrics = dict(metrics or {})
    a, dd = metrics.get("annual"), metrics.get("maxdd")
    if a is None or dd is None:
        raise ValueError("attach_path_metrics 需要 metrics.annual 与 metrics.maxdd")
    metrics["hit"] = compute_hit(a, dd)
    # 钉死来源字段（读侧可据此拒绝「无 provenance 的陈旧数」）
    metrics["source"] = "path_live_rerun"
    metrics["as_of"] = str(date.today())
    if window:
        metrics["window"] = window
    if n is not None:
        metrics["n"] = int(n)
    if returns_sha256:
        metrics["returns_sha256"] = str(returns_sha256)
    if path:
        metrics["path"] = str(path)

    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    v = next((x for x in fam["versions"] if x["version"] == version), None)
    if v is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")

    old = dict(v.get("metrics") or {})
    # 实质偏差键（调用方可传入；否则本地粗算）
    mat_keys = list(material_delta_keys or [])
    if not mat_keys and old:
        for key, tol in (("annual", 0.01), ("maxdd", 0.01), ("sharpe", 0.10)):
            if key in old and key in metrics:
                try:
                    if abs(float(metrics[key]) - float(old[key])) > tol:
                        mat_keys.append(key)
                except (TypeError, ValueError):
                    mat_keys.append(key)
        if old.get("hit") is not None and bool(old.get("hit")) != bool(metrics.get("hit")):
            mat_keys.append("hit")

    ev = dict(v.get("evidence") or {})
    hist = list(ev.get("metrics_history") or [])
    if old:
        hist.append({
            "replaced_at": datetime.now(UTC).isoformat(),
            "metrics": old,
            "reason": "path_live_rerun_refresh",
            "note": note or "路径 live re-run 与台账 metrics 不一致，以可复现重跑为事实",
        })
    # 保留有限历史，避免无限膨胀
    ev["metrics_history"] = hist[-20:]
    ev["metrics_refresh"] = {
        "at": datetime.now(UTC).isoformat(),
        "provenance": provenance,
        "path": path,
        "window": window,
        "returns_sha256": returns_sha256,
        "material_delta_keys": mat_keys,
        "note": note or "",
    }
    v["evidence"] = ev
    v["metrics"] = metrics

    # 门禁快照相对新 metrics 可能已过期：标记，不删除历史
    ng = dict(v.get("nine_gate") or {})
    if ng and mat_keys:
        ng["stale_vs_path_metrics"] = True
        ng["stale_reason"] = (
            "path_metrics_refresh_material_delta:" + ",".join(mat_keys)
        )
        ng["stale_as_of"] = str(date.today())
        v["nine_gate"] = ng

    # 备注追加一行事实指针（不覆盖原 notes 正文）
    stamp = (
        f"[metrics@path_live_rerun {date.today()} "
        f"annual={float(a):.2%} maxdd={float(dd):.2%} hit={metrics['hit']}]"
    )
    prev_notes = (v.get("notes") or "").rstrip()
    if stamp not in prev_notes:
        v["notes"] = (prev_notes + "\n" + stamp).strip() if prev_notes else stamp

    _save(data)
    return {
        "id": f"{family}/{version}",
        "material_delta_keys": mat_keys,
        "hit": metrics["hit"],
        "nine_gate_marked_stale": bool(ng and mat_keys),
    }


def attach_data_incident(family: str, version: str, incident: dict[str, Any] | None) -> str:
    """Append a data incident to version evidence without changing lifecycle status."""
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    item = next((v for v in fam["versions"] if v["version"] == version), None)
    if item is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")
    evidence = cast(EvidenceDict, dict(item.get("evidence") or {}))
    incidents = list(evidence.get("data_incidents") or [])
    incident = dict(incident or {})
    incident_id = incident.get("incident_id")
    incidents = [
        existing for existing in incidents
        if not incident_id or existing.get("incident_id") != incident_id
    ]
    incidents.append(incident)
    evidence["data_incidents"] = incidents
    evidence["production_blocked"] = any(not row.get("resolved") for row in incidents)
    item["evidence"] = evidence
    _save(data)
    return f"{family}/{version}"


def attach_decay_check(family: str, version: str, result: dict[str, Any] | None, *,
                       checked_at: str | None = None) -> str:
    """把一次 governance/decay.py::decay_check() 的结果写入指定版本的 decay_check 字段。

    版本级(不是家族级):decay_check 按 run_active() 的每条腿算,与既有家族级静态
    decay_signal(注册时写的失效条件文字)是两件事,不互相覆盖。只读监控信号,
    不改 status/admission——是否退役仍走 retire_version() 人工/workflow 决策。
    经 _save 走台账唯一写入口。
    """
    from datetime import datetime
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    v = next((x for x in fam["versions"] if x["version"] == version), None)
    if v is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")
    v["decay_check"] = {
        **dict(result or {}),
        "checked_at": checked_at or datetime.now(UTC).isoformat(),
    }
    _save(data)
    return f"{family}/{version}"


def attach_catalog_status(family: str, version: str, status: str, *,
                          marginal: dict[str, Any] | None = None,
                          changed_at: str | None = None) -> str:
    """把一次边际贡献定级(governance/marginal.py::marginal_alpha 的残差法判决)写入
    指定版本的 catalog_status 字段。

    portfolio/strategy_runners.py::RESEARCH_STRATEGY_CATALOG 模块加载时读这个字段
    覆盖写死的 status,取代过去"算完只打印,人工去改代码里的字符串"的流程
    (workflow/promote.py::_run_marginal 调用本函数)。

    status 只表示"是否并入组合权重计算"(ACTIVE/SHADOW),不是准入闸——不改
    version 的 status/admission(那是 strategy_registry.register() 的事)。
    经 _save 走台账唯一写入口。
    """
    from datetime import datetime
    if status not in {"ACTIVE", "SHADOW"}:
        raise ValueError(f"catalog_status 只能是 ACTIVE/SHADOW,收到 {status!r}")
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    v = next((x for x in fam["versions"] if x["version"] == version), None)
    if v is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")
    v["catalog_status"] = {
        "status": status,
        "marginal": dict(marginal or {}),
        "changed_at": changed_at or datetime.now(UTC).isoformat(),
    }
    _save(data)
    return f"{family}/{version}"


def attach_executable_spec(family: str, version: str, spec: dict[str, Any],
                           spec_hash: str, *, require_revalidation: bool = True) -> str:
    """Attach a validated executable identity without copying old evidence to it."""
    from core.strategy_spec import ExecutableStrategySpec

    parsed = ExecutableStrategySpec.from_dict(spec)
    parsed.validate()
    if parsed.family != family or parsed.version != version:
        raise ValueError("strategy identity mismatch")
    if parsed.spec_hash != spec_hash:
        raise ValueError("strategy spec hash mismatch")
    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    item = next((v for v in fam["versions"] if v["version"] == version), None)
    if item is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")
    item["executable_spec"] = {"spec": parsed.to_dict(), "spec_hash": parsed.spec_hash}
    evidence = cast(EvidenceDict, dict(item.get("evidence") or {}))
    evidence["spec_migration"] = {
        "spec_hash": parsed.spec_hash,
        "requires_revalidation": bool(require_revalidation),
    }
    if require_revalidation:
        evidence["production_blocked"] = True
    item["evidence"] = evidence
    _save(data)
    return f"{family}/{version}"


def retire_version(family: str, version: str, *, reason: str,
                   evidence_refs: Iterable[str] = (), actor: str = "workflow",
                   control_event_path: Path | str | None = None) -> str:
    """唯一的版本退役通道(ADR-017 处置 + Task 15 状态机接线)。

    经状态机校验合法转换(非 REGISTERED/DEPLOYED/SUSPENDED 源状态拒绝,不落盘)、追加
    control_events 链式审计、把退役原因与证据指针写入 evidence.retirement(不删除历史字段)。
    """
    import uuid
    from datetime import datetime

    from governance.control_events import DEFAULT_LOG as CE_DEFAULT_LOG
    from governance.control_events import append_event
    from governance.state_machine import CN_TO_STATE

    data = _load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"母策略 '{family}' 未登记")
    item = next((v for v in fam["versions"] if v["version"] == version), None)
    if item is None:
        raise ValueError(f"版本 '{family}/{version}' 不存在")

    from_state = CN_TO_STATE.get(item.get("status"), item.get("status"))
    spec_hash = ((item.get("executable_spec") or {}).get("spec_hash")) or ""

    append_event(
        event_id=str(uuid.uuid4()), timestamp=datetime.now(UTC).isoformat(),
        actor=actor, family=family, version=version, spec_hash=spec_hash,
        from_state=from_state, to_state="RETIRED", reason_code=reason,
        evidence_refs=tuple(evidence_refs),
        path=control_event_path or CE_DEFAULT_LOG,
    )  # 非法转换在此抛 IllegalTransition,不落盘注册表

    evidence = cast(EvidenceDict, dict(item.get("evidence") or {}))
    evidence["retirement"] = {
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "retired_at": datetime.now(UTC).isoformat(),
        "actor": actor,
    }
    item["evidence"] = evidence
    item["status"] = "退役"
    _save(data)
    return f"{family}/{version}"


def show() -> None:
    """按母策略分组打印台账"""
    data = _load()
    if not data["families"]:
        print("暂无登记母策略"); return
    for fam in data["families"]:
        print(f"\n■ 母策略 {fam['id']}（{fam.get('name','')}）  status={fam.get('status','')}")
        text_fields: tuple[tuple[Literal["hypothesis", "regime", "decay_signal"], str], ...] = (
            ("hypothesis", "假设"), ("regime", "适用"), ("decay_signal", "失效信号"))
        for k, label in text_fields:
            if fam.get(k):
                print(f"    {label}：{fam[k]}")

        # New fields formatting
        if fam.get("style_betas"):
            betas_str = ", ".join(f"{k}: {v:+.2f}" for k, v in fam["style_betas"].items())
            print(f"    风格暴露：{betas_str}")
        if fam.get("capacity_m", 0.0) > 0.0:
            print(f"    估计容量：{fam['capacity_m']:.1f} M (百万 CNY)")
        if fam.get("failure_boundaries"):
            bounds_str = ", ".join(f"{k}: {v}" for k, v in fam["failure_boundaries"].items())
            print(f"    失效边界：{bounds_str}")

        print(f"  {'版本':<6}{'数据口径':<26}{'年化':>8}{'回撤':>8}{'夏普':>6}{'达标':>5}{'状态':>6}  备注")
        print("  " + "-" * 100)
        for v in fam["versions"]:
            m: dict[str, Any] = v.get("metrics", {})
            ds = cast(DataScopeDict | str, v.get("data_scope", {}))
            if isinstance(ds, str):
                scope = ds
            else:
                source = ds.get("source", "unknown")
                period = ds.get("period", "unknown")
                scope = f"{source}·{period}{'·幸存者偏差' if ds.get('survivorship_bias') else ''}"
            print(f"  {v['version']:<6}{scope:<26}{m.get('annual', 0.0):>7.1%}{m.get('maxdd', 0.0):>8.1%}"
                  f"{m.get('sharpe', 0.0):>6.2f}{'✅' if m.get('hit') else '❌':>5}{v.get('status',''):>6}  {v.get('notes','')}")
            ev = v.get("evidence") or {}
            if ev.get("hypothesis_id"):
                print(f"  {'':<6}↳ 证据：hyp={ev['hypothesis_id'][:8]} / {len(ev.get('experiment_ids', []))} 实验")
            adm = v.get("admission") or {}
            if v.get("status") == "在册" and adm.get("track"):
                print(f"  {'':<6}↳ 准入：{adm['track']}")
            ng = v.get("nine_gate") or {}
            if ng:
                print(f"  {'':<6}↳ Nine-Gate：DSR_p={ng.get('dsr_p','?')} PSR={ng.get('psr','?')} "
                      f"PBO={ng.get('pbo','?')} WF_sharpe={ng.get('wf_sharpe','?')}")


# ── 命令行入口 ──
if __name__ == "__main__":
    import os; os.chdir(Path(__file__).parent)

    ap = argparse.ArgumentParser()
    ap.add_argument("--migrate", action="store_true",
                    help="重算全台账 hit + 双轨准入裁定（apply）")
    ap.add_argument("--dry-run", action="store_true", help="配合 --migrate：只预览不落盘")
    args = ap.parse_args()

    if args.migrate:
        trans = migrate_two_track_admission(apply=not args.dry_run)
        changed = [t for t in trans if t["new_status"] != t["old_status"] or bool(t["new_hit"]) != bool(t["old_hit"])]
        mode = "DRY-RUN（未落盘）" if args.dry_run else "已落盘"
        print(f"双轨准入迁移 {mode}：{len(trans)} 版本，其中 {len(changed)} 个状态/hit 变更\n")
        print(f"  {'版本':<38}{'旧状态':>6}{'→新状态':>9}{'旧hit':>7}{'→新hit':>7}  轨")
        print("  " + "-" * 92)
        for t in trans:
            mark = " *" if (t in changed) else "  "
            print(f"{mark}{t['id']:<38}{str(t['old_status']):>6}{str(t['new_status']):>9}"
                  f"{str(t['old_hit']):>7}{str(t['new_hit']):>7}  {t['track']}")
        print()
        show()
    else:
        print("当前母策略台账：\n")
        show()
