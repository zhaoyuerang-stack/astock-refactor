"""knowledge/graph.py — 机器可读知识图谱(空容器版)。

核心理念:
  pending_lessons 是"模式型"学习记录(timing_peek 发生过),不绑定候选身份、不 gate。
  知识图谱把每条验证结论绑定到候选身份(Hypothesis),并产出机器可读 SearchGate,
  让搜索引擎在花算力之前先跳过/降权已证伪的候选。

  每条 Finding 带:
    - 保质期 expires(到期自动重测,避免"已解决的问题成为探索的坟墓")
    - 父依赖 depends_on(父结论失效 → 级联重测)
    - SearchGate(机器可读搜索约束:SKIP / DEPRIORITIZE / REQUIRE_RETEST)

  与 alpha_engine 的差异(借机制不照搬结论):
    - 零预置结论;findings 全部由 record_from_validation() 现场生长
    - 默认 DEPRIORITIZE 而非 SKIP;仅 phase1 合成审计 FAIL(真坏候选)才 SKIP
    - 不施加边际律门槛(那条忽略相关性符号);边际真值归 line3_marginal
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from app_config.log import get_logger

logger = get_logger(__name__)

# 失败候选的默认动作:按 stage 分级。
# phase1 合成审计失败 = 候选真坏(leaky/未来函数)→ 永久 SKIP 同参数。
# 其余阶段(L0~L3 / phase2 / phase3)"alpha 弱" = regime/区间依赖 → DEPRIORITIZE + 保质期。
_HARD_FAIL_STAGES = {"phase1", "synthetic", "phase1_synthetic"}

_PRIORITY = {"SKIP": 0.0, "DEPRIORITIZE": 0.3, "REQUIRE_RETEST": 1.0}


# ── 候选身份提取(duck-typed Hypothesis)────────────────────────────────────

def _atom_attrs(hyp) -> dict:
    """从 Hypothesis 抽出可匹配属性(字符串化,便于稳定比较)。

    _term_factors:DSL 候选(autoresearch)的 factor_fn_name 恒为 compute_dsl_factor
    (ast_to_hypothesis 统一封装),因子级 gate 靠 factor_fn_name 对它们永远失配——
    从 factor_params["ast"]["terms"] 抽出成分因子名,供 term_factor 匹配(方向登记簿用)。
    """
    attrs = {
        "id": getattr(hyp, "id", ""),
        "name": getattr(hyp, "name", ""),
        "factor_fn_name": getattr(hyp, "factor_fn_name", ""),
        "timing_fn_name": getattr(hyp, "timing_fn_name", "") or "",
    }
    params = getattr(hyp, "factor_params", {}) or {}
    for k, v in params.items():
        attrs[str(k)] = str(v)
    ast = params.get("ast")
    if isinstance(ast, dict):
        attrs["_term_factors"] = tuple(
            str(t.get("factor", "")) for t in ast.get("terms", [])
            if isinstance(t, dict) and t.get("factor")
        )
    return attrs


# ── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SearchGate:
    """机器可读的搜索空间约束。"""
    match: dict                 # {"factor_fn_name": "...", "window": "60"} —— 子集匹配
    action: str                 # "SKIP" / "DEPRIORITIZE" / "REQUIRE_RETEST"
    reason: str = ""

    def matches(self, hyp) -> bool:
        attrs = _atom_attrs(hyp)
        for k, v in self.match.items():
            if k == "term_factor":
                # 成员匹配:候选任一成分因子命中即命中(方向登记簿的因子族 gate)
                if str(v) not in attrs.get("_term_factors", ()):
                    return False
                continue
            if str(attrs.get(k)) != str(v):
                return False
        return True


@dataclass
class Finding:
    """一条带保质期的研究结论。"""
    id: str
    statement: str
    domain: str = "factor"
    confidence: float = 0.8
    evidence: list = field(default_factory=list)
    created: str = ""
    expires: str = ""           # ISO date;空 = 永不过期
    depends_on: list = field(default_factory=list)
    gates: list = field(default_factory=list)   # list[SearchGate]
    metrics: dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if not self.expires:
            return False
        return date.today().isoformat() > self.expires

    @property
    def is_valid(self) -> bool:
        return not self.is_expired


# ── 知识图谱 ────────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """研究结论的有向图(节点=Finding,边=depends_on)。"""

    def __init__(self, store_path: str | None = None):
        self._findings: dict[str, Finding] = {}
        self.store_path = store_path
        if store_path and os.path.exists(store_path):
            self.load(store_path)

    # ── 增查 ──
    def add(self, finding: Finding) -> None:
        self._findings[finding.id] = finding
        if self.store_path:
            self.save(self.store_path)

    def get(self, finding_id: str) -> Finding | None:
        return self._findings.get(finding_id)

    def all_valid(self) -> list:
        return [f for f in self._findings.values() if f.is_valid]

    def expired(self) -> list:
        return [f for f in self._findings.values() if f.is_expired]

    # ── 搜索 gate ──
    def get_gates(self) -> list:
        gates = []
        for f in self.all_valid():
            gates.extend(f.gates)
        return gates

    def should_skip(self, hyp) -> tuple[bool, str]:
        """仅 SKIP 动作生效;DEPRIORITIZE 不在此短路(走 priority_adjustment)。"""
        for gate in self.get_gates():
            if gate.action == "SKIP" and gate.matches(hyp):
                return True, gate.reason
        return False, ""

    def priority_adjustment(self, hyp) -> float:
        """1.0 正常 / 0.3 降权 / 0.0 跳过。命中多 gate 取最严(最小)。"""
        adj = 1.0
        for gate in self.get_gates():
            if gate.matches(hyp):
                adj = min(adj, _PRIORITY.get(gate.action, 1.0))
        return adj

    # ── 保质期 ──
    def check_expiry(self) -> list:
        """返回需重测的结论(自身过期 + 父节点过期级联)。每轮搜索开始时调。"""
        need = []
        for f in self._findings.values():
            if f.is_expired:
                need.append(f)
                continue
            for pid in f.depends_on:
                parent = self._findings.get(pid)
                if parent and parent.is_expired:
                    need.append(f)
                    break
        return need

    # ── 从验证结果现场生长 finding ──
    def record_from_validation(
        self, hyp, passed: bool, metrics: dict,
        stage: str = "", action: str | None = None, expiry_days: int = 180,
    ) -> Finding:
        """把一次验证结果转成 Finding(失败也记,避免重复尝试)。

        passed=True  → 记录通过结论,不产 gate。
        passed=False → 产 gate:phase1 合成失败默认 SKIP,其余默认 DEPRIORITIZE。
                       到期后 check_expiry 触发重测(DEPRIORITIZE 类),SKIP 类同样带保质期。
        """
        fid = f"validated_{getattr(hyp, 'id', 'unknown')}"
        tag = "✓" if passed else "✗"
        name = getattr(hyp, "name", getattr(hyp, "factor_fn_name", "?"))
        statement = (f"{tag} {name} [{stage}] "
                     f"sharpe={metrics.get('wf_sharpe', metrics.get('sharpe', 0)):.2f} "
                     f"annual={metrics.get('annual', 0):.1%}")

        gates = []
        if not passed:
            if action is None:
                action = "SKIP" if stage in _HARD_FAIL_STAGES else "DEPRIORITIZE"
            match = {"factor_fn_name": getattr(hyp, "factor_fn_name", "")}
            for k, v in (getattr(hyp, "factor_params", {}) or {}).items():
                match[str(k)] = str(v)
            reason = (f"{stage} 验证未过({statement});"
                      f"{'真坏候选,永久跳过' if action == 'SKIP' else f'{expiry_days}天后到期重测'}")
            gates.append(SearchGate(match=match, action=action, reason=reason))

        finding = Finding(
            id=fid, statement=statement, domain="factor",
            confidence=0.9 if passed else 0.8,
            evidence=[getattr(hyp, "id", "")],
            created=date.today().isoformat(),
            expires=(date.today() + timedelta(days=expiry_days)).isoformat(),
            depends_on=[], gates=gates, metrics=dict(metrics),
        )
        self.add(finding)
        return finding

    # ── 持久化 ──
    def save(self, path: str) -> None:
        data = {
            fid: {
                "id": f.id, "statement": f.statement, "domain": f.domain,
                "confidence": f.confidence, "evidence": f.evidence,
                "created": f.created, "expires": f.expires,
                "depends_on": f.depends_on, "metrics": f.metrics,
                "gates": [{"match": g.match, "action": g.action, "reason": g.reason}
                          for g in f.gates],
            }
            for fid, f in self._findings.items()
        }
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)

    def load(self, path: str) -> None:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
        for fid, d in data.items():
            gates = [SearchGate(match=g["match"], action=g["action"], reason=g.get("reason", ""))
                     for g in d.get("gates", [])]
            self._findings[fid] = Finding(
                id=d["id"], statement=d["statement"], domain=d.get("domain", "factor"),
                confidence=d.get("confidence", 0.8), evidence=d.get("evidence", []),
                created=d.get("created", ""), expires=d.get("expires", ""),
                depends_on=d.get("depends_on", []), gates=gates,
                metrics=d.get("metrics", {}),
            )

    def summary(self) -> str:
        total = len(self._findings)
        valid = len(self.all_valid())
        exp = len(self.expired())
        active_gates = sum(len(f.gates) for f in self.all_valid())
        return (f"KnowledgeGraph: {total} findings "
                f"({valid} valid, {exp} expired, {active_gates} active gates)")


LESSON_CATEGORIES = {
    "TIMING_PEEK",
    "FUND_ALIGNMENT",
    "AMOUNT_FORMULA",
    "WARMUP",
    "DELISTED",
    "WF_NEGATIVE_WINDOW",
    "CORRELATION",
}

_LESSON_HARD_ACTION = {
    "TIMING_PEEK": "SKIP",
    "FUND_ALIGNMENT": "SKIP",
    "AMOUNT_FORMULA": "SKIP",
    "DELISTED": "SKIP",
    "WARMUP": "DEPRIORITIZE",
    "WF_NEGATIVE_WINDOW": "DEPRIORITIZE",
    "CORRELATION": "DEPRIORITIZE",
}


def classify_pending_lesson(lesson: dict) -> str:
    """Classify a pending lesson into the canonical machine-action categories."""
    text = " ".join(str(lesson.get(k, "")) for k in ("trigger", "pattern", "detail", "fix")).lower()
    if "timing_peek" in text or "timing shift" in text or "shift(1)" in text:
        return "TIMING_PEEK"
    if "fund_alignment" in text or "avail_date" in text or "report_date" in text:
        return "FUND_ALIGNMENT"
    if "amount_formula" in text or "raw_close" in text or "adjusted close" in text:
        return "AMOUNT_FORMULA"
    if "warmup" in text or "预热" in text or "leading nan" in text:
        return "WARMUP"
    if "delisted" in text or "退市" in text:
        return "DELISTED"
    if "wf_negative_window" in text or "negative window" in text:
        return "WF_NEGATIVE_WINDOW"
    if "correlation" in text or "相关" in text:
        return "CORRELATION"
    return "CORRELATION" if "corr" in text else "WARMUP"


def _lesson_key(lesson: dict) -> tuple[str, str]:
    return str(lesson.get("fingerprint", "")), str(lesson.get("pattern", ""))


def _merge_lesson(existing: dict, incoming: dict) -> dict:
    out = dict(existing)
    out["hit_count"] = int(out.get("hit_count", 0) or 0) + int(incoming.get("hit_count", 0) or 0)
    first_values = [str(v) for v in (out.get("first_seen"), incoming.get("first_seen")) if v]
    last_values = [str(v) for v in (out.get("last_seen"), incoming.get("last_seen")) if v]
    if first_values:
        out["first_seen"] = min(first_values)
    if last_values:
        out["last_seen"] = max(last_values)
    strategies = set(out.get("strategies", []) or [])
    strategies.update(incoming.get("strategies", []) or [])
    out["strategies"] = sorted(str(s) for s in strategies if str(s))
    for key in ("detail", "fix", "trigger"):
        if len(str(incoming.get(key, ""))) > len(str(out.get(key, ""))):
            out[key] = incoming.get(key, "")
    return out


def load_pending_lessons(pending_dir: str | Path) -> tuple[list[dict], int]:
    """Load and merge pending lesson JSON files by (fingerprint, pattern)."""
    root = Path(pending_dir)
    merged: dict[tuple[str, str], dict] = {}
    files_read = 0
    if not root.exists():
        return [], files_read
    for fp in sorted(root.glob("*.json")):
        try:
            lesson = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        files_read += 1
        key = _lesson_key(lesson)
        if key in merged:
            merged[key] = _merge_lesson(merged[key], lesson)
        else:
            merged[key] = dict(lesson)
    return list(merged.values()), files_read


def _lesson_finding_id(category: str, lesson: dict) -> str:
    fp = str(lesson.get("fingerprint", "unknown"))
    pattern_hash = hashlib.sha1(str(lesson.get("pattern", "")).encode("utf-8")).hexdigest()[:8]
    return f"lesson_{category.lower()}_{fp}_{pattern_hash}"


def pending_lesson_to_finding(lesson: dict) -> Finding:
    """Convert one merged pending lesson into a durable Finding."""
    category = classify_pending_lesson(lesson)
    action = _LESSON_HARD_ACTION.get(category, "DEPRIORITIZE")
    pattern = str(lesson.get("pattern", ""))
    detail = str(lesson.get("detail", ""))
    fix = str(lesson.get("fix", ""))
    hit_count = int(lesson.get("hit_count", 0) or 0)
    reason = f"{category}: {pattern}; hits={hit_count}; {fix or detail}"
    gates = []
    for strategy in sorted(set(lesson.get("strategies", []) or [])):
        strategy = str(strategy)
        if not strategy:
            continue
        gates.append(SearchGate(match={"name": strategy}, action=action, reason=reason))
        gates.append(SearchGate(match={"factor_fn_name": strategy}, action=action, reason=reason))
    return Finding(
        id=_lesson_finding_id(category, lesson),
        statement=f"{category}: {pattern} ({hit_count} hits)",
        domain="lesson",
        confidence=0.9 if action == "SKIP" else 0.75,
        evidence=sorted(set(lesson.get("strategies", []) or [])),
        created=str(lesson.get("first_seen") or date.today().isoformat())[:10],
        expires=(date.today() + timedelta(days=180)).isoformat(),
        depends_on=[],
        gates=gates,
        metrics={
            "lesson_category": category,
            "hit_count": hit_count,
            "last_seen": str(lesson.get("last_seen", "")),
            "detail": detail,
            "fix": fix,
        },
    )


def sync_pending_lessons_to_graph(
    pending_dir: str | Path | None = None,
    store_path: str | Path | None = None,
) -> dict:
    """Merge pending lesson drafts into durable knowledge/findings.json gates."""
    root = Path(pending_dir) if pending_dir is not None else Path(__file__).resolve().parents[1] / "workflow" / "pending_lessons"
    store = str(store_path or DEFAULT_STORE)
    lessons, files_read = load_pending_lessons(root)
    kg = KnowledgeGraph(store)
    gates_written = 0
    for lesson in lessons:
        finding = pending_lesson_to_finding(lesson)
        gates_written += len(finding.gates)
        kg.add(finding)
    return {
        "files_read": files_read,
        "merged_lessons": len(lessons),
        "findings_written": len(lessons),
        "gates_written": gates_written,
        "store_path": store,
    }


# 默认 store:git-tracked 的 durable 知识(对照 pending_lessons),非 data_lake 临时态
DEFAULT_STORE = os.path.join(os.path.dirname(__file__), "findings.json")


def load_graph(store_path: str | None = None, include_directions: bool = True) -> KnowledgeGraph:
    """加载知识图谱(缺省用 knowledge/findings.json)。空文件 → 空图。

    include_directions=True 时内存合并方向登记簿(direction_registry.json)的 gates:
    策展知识与机器自长知识分离存储、消费端统一——promote/pipeline/factory_cli 经
    本入口自动获得方向级 SKIP/DEPRIORITIZE。只合并不落盘(不污染机器自长文件);
    合并失败 fail-open(生成端 steering 不得阻断验证主线)。
    """
    kg = KnowledgeGraph(store_path or DEFAULT_STORE)
    if include_directions:
        try:
            from knowledge.directions import direction_findings
            for f in direction_findings():
                # setdefault + 直插:不经 add()(add 会把策展条目写回 findings.json)
                kg._findings.setdefault(f.id, f)
        except Exception as e:
            logger.warning(f"[knowledge] 方向登记簿合并失败(fail-open): {e}")
    return kg
