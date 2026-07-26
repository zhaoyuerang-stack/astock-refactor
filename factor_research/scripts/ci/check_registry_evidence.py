"""台账 9-Gate 证据完整性守卫(防自欺)。

两道机械门(对应治理草稿 C.1 / C.3,源案见 illiquidity-large-cap 与 industry-neglect v1.3):
  G1 跨家族 IC 证据照抄:不同 family 的版本共享逐位相同的 IC 证据块 = 违规。
     (同 family 多版本共享 IC 合法——同因子同宇宙 IC 不该变。)
  G2 standalone 证据缺失/跳门:active 在册且 admission.track=standalone 的版本,
     - nine_gate 与 evidence 皆空 → 违规(无任何门禁证据就准入,绕过 9-Gate)。
     - passed_all=true 但必算门(dsr_p / pbo)为 None → 违规(跳门却标通过)。

只读 strategy_versions.json,违规则 exit 1。检测函数吃 dict,便于 fixture 测试。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / "strategy_versions.json"

IC_KEYS = ["ic_mean", "nw_icir", "neut_nw_icir", "icir_retention",
           "monotonicity_corr", "ic_win_rate", "ic_decay"]
ACTIVE_STATUS = {"在册", "active", "APPROVED"}
REQUIRED_GATES = ["dsr_p", "pbo"]  # passed_all=true 时必须实算(非 None)
DSR_ALPHA = 0.05  # standalone 准入要求 DSR 多重测试惩罚下显著(dsr_p<此值)；与 strategy_registry.DSR_ALPHA 对齐

# 已知病灶基线(2026-06 回扫发现,grandfather 待 workflow 处置;**修复后须从此处移除**)。
# 守卫只对「新」违规 exit 1;下列项仅打印为待处置警告,不阻塞。处置清单见
# scratch/illiq_largecap_governance_DRAFT.md(退役/补审/降级)。
PENDING_REMEDIATION: dict[str, str] = {
    # 2026-06-30 对抗性审查发现:composite-portfolio v1.0 / size_mix_v1.0 均含 3 个配比分量
    # 却写 n_trials=1(R-EVIDENCE-001 ④ 低报),DSR 多重测试惩罚被骗松。台账受 R-REG-001 保护
    # (不可手改),须走 register/promote_composite 重审如实计入搜索自由度后从此处移除。
    # 跟踪:TASKS.md「composite-portfolio n_trials 诚实重审」。两版皆 status=候选、未部署,故基线警告不阻塞。
    "EV4-trials:composite-portfolio/v1.0": "待 register 重审计入真实搜索自由度",
    "EV4-trials:composite-portfolio/size_mix_v1.0": "待 register 重审计入真实搜索自由度",
}
# 已修复并移除(ADR-017 处置,2026-06):
#   G1:illiquidity,illiquidity-large-cap —— illiq-large-cap/v1.0 已退役 + nine_gate 重写为
#       INVALIDATED_EVIDENCE_PLAGIARIZED,原照抄 IC 块归档于 evidence.archived_invalid_nine_gate。
#   G2-skip:illiquidity-large-cap/v1.0:pbo —— 同上,passed_all 已改 False,不再「跳门标通过」。
#   G2-empty:industry-neglect-rotation/v1.3 —— 补 L0/L1/L2 归因 nine_gate(passed_all=False,
#       DSR/PBO 因 trial_count_unknown 明确标 None,非伪造)+ evidence.admission_caveat 标注
#       standalone 资格实际靠 MA16 择时覆盖,裸因子不达标。


def extract_versions(ledger: dict) -> list[dict]:
    """展平为 [{family, version, status, version_status, track, nine_gate, evidence}]。

    status: version 自带,缺省回退家族 status(历史 G2/G3 行为保持)。
    version_status: **仅** version 自带 status,无家族回退——供 G2-no-track 使用
      (家族 status=active 是家族生命周期标记,非版本准入声明;审计 #3)。
    """
    fams: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("id") and "versions" in o:
                fams.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(ledger)
    rows = []
    for fam in fams:
        vers = fam["versions"]
        vers = vers if isinstance(vers, list) else list(vers.values())
        for v in vers:
            ng = v.get("nine_gate") or {}
            adm = v.get("admission") or {}
            rows.append({
                "family": fam["id"],
                "version": v.get("version"),
                "status": v.get("status") or fam.get("status"),
                "version_status": v.get("status"),  # 无家族回退
                "track": adm.get("track"),
                "nine_gate": ng,
                "evidence": v.get("evidence") or {},
                "config": v.get("config") or {},
            })
    return rows


def find_active_without_track(rows: list[dict]) -> list[tuple[str, str]]:
    """G2-no-track:version **自身** status ∈ ACTIVE_STATUS 但 admission.track 缺失。

    只认 version_status(自带),不认家族 status 回退——家族 status=active 是
    家族生命周期标记,不是版本准入声明(审计 #3 网眼对齐)。
    """
    out = []
    for r in rows:
        vs = r.get("version_status")
        if vs not in ACTIVE_STATUS:
            continue
        if r.get("track"):
            continue
        tag = f"{r['family']}/{r['version']}"
        out.append((
            f"G2-no-track:{tag}",
            f"[G2 无 admission.track] {tag} version status={vs!r} ∈ ACTIVE "
            f"但 admission.track 缺失 — 绕过双轨准入门(审计 #3);"
            f"须补 track 或改 status 非在册态",
        ))
    return out


def find_understated_trials(rows: list[dict]) -> list[tuple[str, str]]:
    """R-EVIDENCE-001 ④:组合/混合版本 n_trials 不得低于其配比分量数(机械下界)。

    组合版本的搜索自由度至少含「各子策略各自的搜索 + 配比网格 + 任何择时/熔断参数」,
    故 n_trials < 分量数 = 必然漏报搜索自由度、骗松 DSR 多重测试惩罚。
    这是 *下界*,不是真实自由度:过了此门也仅说明没把 n_trials 写成 1 这种显式造假,
    真实自由度仍须 register 重审如实计入。与 status 无关——候选也不许带造假证据值
    (R-EVIDENCE-001 不限定 standalone;低报的 n_trials 会随降级/回流污染后续判断)。
    """
    out = []
    for r in rows:
        alloc = (r.get("config") or {}).get("allocation")
        if not isinstance(alloc, dict) or len(alloc) < 2:
            continue
        n = r["nine_gate"].get("n_trials")
        if isinstance(n, (int, float)) and not isinstance(n, bool) and n < len(alloc):
            tag = f"{r['family']}/{r['version']}"
            out.append((f"EV4-trials:{tag}",
                        f"[④ n_trials 低报] {tag} 组合含 {len(alloc)} 个配比分量但 n_trials={n} — "
                        f"低于分量数即必然漏报搜索自由度(配比网格/择时熔断参数/子策略搜索),"
                        f"DSR 多重测试惩罚被骗松;须按真实搜索自由度重算 n_trials(走 register 重审)"))
    return out


def _ic_sig(ng: dict) -> str | None:
    sub = {k: ng.get(k) for k in IC_KEYS if k in ng}
    if not sub:
        return None
    return hashlib.md5(json.dumps(sub, sort_keys=True, default=str).encode()).hexdigest()[:12]


def find_cross_family_ic_copies(rows: list[dict]) -> list[tuple[str, str]]:
    """G1:同一 IC 块 hash 跨 >1 个 family = 照抄。返回 [(key, msg)]。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        sig = _ic_sig(r["nine_gate"])
        if sig is not None:
            groups.setdefault(sig, []).append(r)
    out = []
    for sig, members in groups.items():
        fams = sorted({m["family"] for m in members})
        if len(fams) > 1:
            who = ", ".join(f"{m['family']}/{m['version']}" for m in members)
            key = "G1:" + ",".join(fams)
            out.append((key, f"[G1 跨家族IC照抄] IC块#{sig} 被多个 family 共享: {who} — 须各宇宙独立重算"))
    return out


def find_standalone_evidence_gaps(rows: list[dict]) -> list[tuple[str, str]]:
    """active standalone 的证据缺失 / 跳门却标通过 / DSR 不显著。返回 [(key, msg)]。

    G2 证据完整性：nine_gate 与 evidence 皆空 = 绕过准入；passed_all=true 却跳必算门 = 造假。
    G3 DSR 显著性：dsr_p 缺失(None)或 >=DSR_ALPHA = 多重测试惩罚下不显著的 standalone 在册
       (源 R-OBJECTIVE-001；与 strategy_registry.register() 的 DSR 门同口径，防降级后回流)。
    """
    out = []
    for r in rows:
        if r["status"] not in ACTIVE_STATUS or r["track"] != "standalone":
            continue
        tag = f"{r['family']}/{r['version']}"
        ng = r["nine_gate"]
        if not ng and not r["evidence"]:
            out.append((f"G2-empty:{tag}",
                        f"[G2 证据全空] {tag} active+standalone 但 nine_gate 与 evidence 皆空 — 绕过 9-Gate 准入"))
            continue
        if ng.get("passed_all") is True:
            skipped = [g for g in REQUIRED_GATES if ng.get(g) is None]
            if skipped:
                out.append((f"G2-skip:{tag}:{','.join(skipped)}",
                            f"[G2 跳门标通过] {tag} passed_all=true 但 {','.join(skipped)} 未实算(None)"))
        dsr_p = ng.get("dsr_p")
        if dsr_p is None:
            out.append((f"G3-dsr-none:{tag}",
                        f"[G3 DSR未实算] {tag} active+standalone 但 nine_gate.dsr_p 缺失(None) — "
                        f"standalone 须有 DSR 多重测试惩罚证据，降 diversifier 或 status='候选'"))
        elif dsr_p >= DSR_ALPHA:
            out.append((f"G3-dsr-fail:{tag}",
                        f"[G3 DSR不显著] {tag} active+standalone 但 dsr_p={dsr_p:.4f}>={DSR_ALPHA} — "
                        f"多重测试惩罚下不显著，须降级"))
    return out


def check(ledger: dict | None = None) -> int:
    if ledger is None:
        ledger = json.loads(LEDGER.read_text())
    rows = extract_versions(ledger)
    all_v = (find_cross_family_ic_copies(rows)
             + find_standalone_evidence_gaps(rows)
             + find_understated_trials(rows)
             + find_active_without_track(rows))
    keys = {k for k, _ in all_v}

    new_v = [(k, m) for k, m in all_v if k not in PENDING_REMEDIATION]
    pending = [(k, m) for k, m in all_v if k in PENDING_REMEDIATION]
    stale = [k for k in PENDING_REMEDIATION if k not in keys]  # 已修复但还挂在基线

    for _, m in pending:
        print(f"  ⚠️ 待处置(基线): {m}")
    for k in stale:
        print(f"  ℹ️ 基线项已修复,请从 PENDING_REMEDIATION 移除: {k}")

    if new_v:
        print("台账证据完整性检查发现【新】违规:")
        for _, m in new_v:
            print(f"  {m}")
        return 1
    print(f"台账证据完整性检查通过(无新违规;{len(pending)} 项待处置已基线)。")
    return 0


if __name__ == "__main__":
    sys.exit(check())
