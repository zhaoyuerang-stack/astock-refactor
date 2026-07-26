"""knowledge/directions.py — 方向级研究教训登记簿(机器可读、证据门控)。

与 knowledge/graph.py 的分工:
  findings.json = 机器自长(单候选验证结论 + pending 机械教训,record_from_validation 现场生长);
  direction_registry.json = 人/强模型策展的**方向级**结论(因子族/数据源粒度)。
  来源是 LESSONS/DECISIONS/research_ledger 的研究级证伪——此前只存在于自然语言,
  生成器不消费,算力反复烧回死路(例:generator._SEEDS 仍置顶北向/holder,
  而 research_ledger e6e655401623899d 已证该族 top25 long-only 太弱)。

铁律边界(R-LLM-001 / LOOP_ENGINEERING §3):本模块只影响**生成端**的搜索空间分配
(跳过/降权/算力倾斜),绝不参与有效性判断;候选无论来源仍走完整 L0-L3/9-Gate/holdout。
生成端 steering 一律 fail-open:登记簿/簇文件读不出 → 不过滤 + 警告,绝不阻断搜索。
证据门控:evidence 为空的条目一律忽略 + 警告(防"顺手编方向"污染搜索空间)。
保质期:expires 到期 → 条目自动失效 = 复活重测(参照 revival_condition)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app_config.log import get_logger

logger = get_logger(__name__)

_VALID_ACTIONS = {"SKIP", "DEPRIORITIZE", "BOOST", "NOTE"}
_STATUS_LABEL = {"falsified": "已证伪", "weak": "太弱", "frontier": "空白区"}

_HERE = Path(__file__).resolve().parent
# 注意:这三个默认路径是**搜索行为的仓库态输入**——固定 rng_seed 的搜索单测必须把
# 它们钉到不存在路径(hermetic 基线),否则编辑 direction_registry.json 会漂移单测轨迹
# (见 tests/test_autoresearch_engine.py 顶部钉死;steering 行为测试归 test_direction_registry.py)。
DEFAULT_REGISTRY = str(_HERE / "direction_registry.json")
# metasearch 机械产物(月度刷新,见 scheduled_weekly_maintenance):fail-open 消费
DEFAULT_CLUSTERS = str(_HERE.parent / "metasearch" / "redundancy_clusters.json")
DEFAULT_FRONTIER = str(_HERE.parent / "metasearch" / "frontier.json")


@dataclass(frozen=True)
class DirectionEntry:
    """一条方向级结论(因子族/数据源粒度,带证据与复活条件)。"""

    id: str
    direction: str
    status: str          # falsified / weak / frontier
    action: str          # SKIP / DEPRIORITIZE / BOOST / NOTE
    scope_factors: tuple # 白名单因子名;NOTE 类可为空(仅 prompt)
    evidence: tuple      # 非空(证据门控);ADR/LESSONS/report/run_id 指针
    revival_condition: str = ""
    created: str = ""
    expires: str = ""    # ISO date;空 = 永不过期
    prompt_note: str = ""

    @property
    def is_active(self) -> bool:
        if not self.expires:
            return True
        return date.today().isoformat() <= self.expires


def load_direction_entries(path: str | None = None) -> list[DirectionEntry]:
    """加载并校验登记簿;非法/无证据条目忽略+警告;文件缺失/解析失败 → 空表(fail-open)。"""
    p = Path(path or DEFAULT_REGISTRY)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[directions] 登记簿解析失败,忽略(fail-open): {e}")
        return []
    entries: list[DirectionEntry] = []
    for raw in data.get("entries", []):
        if not isinstance(raw, dict):
            continue
        eid = str(raw.get("id", "")).strip()
        action = str(raw.get("action", "")).strip().upper()
        evidence = tuple(str(x) for x in raw.get("evidence", []) if str(x).strip())
        if not eid or action not in _VALID_ACTIONS:
            logger.warning(f"[directions] 条目非法(缺 id 或 action∉{sorted(_VALID_ACTIONS)}),忽略: {eid or raw}")
            continue
        if not evidence:
            logger.warning(f"[directions] 条目无 evidence 指针,忽略(证据门控): {eid}")
            continue
        entries.append(DirectionEntry(
            id=eid,
            direction=str(raw.get("direction", "")),
            status=str(raw.get("status", "")),
            action=action,
            scope_factors=tuple(str(f) for f in raw.get("scope_factors", []) if str(f).strip()),
            evidence=evidence,
            revival_condition=str(raw.get("revival_condition", "")),
            created=str(raw.get("created", "")),
            expires=str(raw.get("expires", "")),
            prompt_note=str(raw.get("prompt_note", "")),
        ))
    return entries


def active_entries(path: str | None = None) -> list[DirectionEntry]:
    """未过期条目(过期 = 复活重测,不再产生任何 steering)。"""
    return [e for e in load_direction_entries(path) if e.is_active]


def seed_action(
    factor_names,
    *,
    entries: list[DirectionEntry] | None = None,
    registry_path: str | None = None,
) -> tuple[str, str]:
    """种子(因子名集合)命中的最强机械动作:SKIP > DEPRIORITIZE > ""。

    BOOST/NOTE 不在此返回(BOOST 走 boost_factors 排序,NOTE 只进 prompt)。
    """
    ents = entries if entries is not None else active_entries(registry_path)
    names = {str(n) for n in factor_names}
    hit_action, hit_reason = "", ""
    for e in ents:
        if e.action not in ("SKIP", "DEPRIORITIZE"):
            continue
        if not (names & set(e.scope_factors)):
            continue
        if e.action == "SKIP":
            return "SKIP", f"{e.id}: {e.direction}"
        hit_action, hit_reason = "DEPRIORITIZE", f"{e.id}: {e.direction}"
    return hit_action, hit_reason


def frontier_factors(path: str | None = None) -> set[str]:
    """metasearch/frontier.json 的空白区因子(fail-open:缺失/坏文件 → 空集)。"""
    p = Path(path or DEFAULT_FRONTIER)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(f) for f in data.get("factors", []) if str(f).strip()}
    except Exception:
        return set()


def boost_factors(
    *,
    entries: list[DirectionEntry] | None = None,
    registry_path: str | None = None,
    frontier_path: str | None = None,
) -> set[str]:
    """算力倾斜集 = 登记簿 BOOST 条目 ∪ metasearch frontier.json。"""
    ents = entries if entries is not None else active_entries(registry_path)
    out: set[str] = set()
    for e in ents:
        if e.action == "BOOST":
            out |= set(e.scope_factors)
    out |= frontier_factors(frontier_path)
    return out


def redundancy_clusters(path: str | None = None) -> list[set[str]]:
    """MI 冗余簇(>1 成员;fail-open:缺失/坏文件 → 空表)。"""
    p = Path(path or DEFAULT_CLUSTERS)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [
            {str(m) for m in c}
            for c in data.get("factor_clusters", data.get("clusters", []))
            if isinstance(c, (list, tuple)) and len(c) > 1
        ]
    except Exception:
        return []


def same_cluster(
    f1: str,
    f2: str,
    *,
    clusters: list[set[str]] | None = None,
    clusters_path: str | None = None,
) -> bool:
    """两因子是否同一 MI 冗余簇(同信息源投影,组合=同一信息算两遍)。"""
    cl = clusters if clusters is not None else redundancy_clusters(clusters_path)
    return any(f1 in c and f2 in c for c in cl)


def prompt_block(
    *,
    entries: list[DirectionEntry] | None = None,
    registry_path: str | None = None,
) -> str:
    """给 LLM 播种的方向级教训块(证据门控条目的 prompt_note);空登记簿 → 空串。"""
    ents = entries if entries is not None else active_entries(registry_path)
    notes = [e for e in ents if e.prompt_note]
    if not notes:
        return ""
    lines = ["方向级研究教训(每条均有台账/ADR 证据,优先级高于你的先验,冲突方向直接放弃):"]
    for e in notes:
        tag = _STATUS_LABEL.get(e.status, e.status or "结论")
        lines.append(f"- [{tag}] {e.prompt_note}")
    return "\n".join(lines)


def direction_findings(
    *,
    entries: list[DirectionEntry] | None = None,
    registry_path: str | None = None,
) -> list:
    """转 knowledge.graph.Finding(gate 用 term_factor 匹配)供 load_graph 内存合并。

    term_factor 匹配是必要的:ast_to_hypothesis 把所有 DSL 候选的 factor_fn_name
    统一写成 compute_dsl_factor,因子级 factor_fn_name gate 对 autoresearch 候选永远失配。
    """
    from knowledge.graph import Finding, SearchGate

    ents = entries if entries is not None else active_entries(registry_path)
    out = []
    for e in ents:
        if e.action not in ("SKIP", "DEPRIORITIZE") or not e.scope_factors:
            continue
        reason = f"方向登记簿 {e.id}: {e.direction}(证据: {e.evidence[0]})"
        gates = [
            SearchGate(match={"term_factor": f}, action=e.action, reason=reason)
            for f in e.scope_factors
        ]
        out.append(Finding(
            id=f"direction_{e.id}",
            statement=f"[{e.status}] {e.direction}",
            domain="direction",
            confidence=0.9,
            evidence=list(e.evidence),
            created=e.created,
            expires=e.expires,
            depends_on=[],
            gates=gates,
            metrics={"revival_condition": e.revival_condition},
        ))
    return out
