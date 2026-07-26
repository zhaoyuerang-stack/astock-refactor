"""Phase 4: Strategy registration with reproducibility metadata.

Takes Phase 1-3 results, auto-generates registry entry, records:
  - data snapshot version
  - engine version
  - git commit
  - python/dependency versions

Also handles lesson feedback loop: FAIL/WARN results from earlier phases
automatically generate structured lesson drafts in pending_lessons/.

Usage:
  >>> from workflow.phase4_register import Phase4Register
  >>> reg = Phase4Register(family='my-strategy')
  >>> reg.register(phase1_results, phase2_results, phase3_results)
"""
from __future__ import annotations

from app_config.log import get_logger

logger = get_logger(__name__)

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from strategy_registry import AdmissionDict, EvidenceDict
    from workflow.phase1_synthetic import CheckResult

import pandas as pd

ROOT: Path = Path(__file__).resolve().parent.parent
logger = get_logger(__name__)
LESSONS_DIR: Path = ROOT / "workflow" / "pending_lessons"
LESSONS_DIR.mkdir(parents=True, exist_ok=True)
_HOLDOUT_VALIDATIONS: Path = ROOT / "data_lake" / "governance" / "holdout_validations.jsonl"


def _extract_nine_gate_summary(phase2_data: dict[str, Any] | None,
                               phase3_data: dict[str, Any] | None) -> dict[str, Any]:
    """从 workflow phase2/phase3 产出抽取可得的审计摘要写入台账（缺键安全跳过）。

    注意：完整 DSR/PSR/PBO 来自独立的 9-Gate 审计（run_nine_gates_all），工厂通道只产
    walk-forward / OOS 摘要。本函数只落工厂能提供的部分，DSR 等由 nine_gates 回填。
    """
    p3 = phase3_data if isinstance(phase3_data, dict) else {}
    agg = p3.get("aggregate", {}) if isinstance(p3.get("aggregate"), dict) else {}
    out = {
        "wf_sharpe": agg.get("sharpe"),
        "wf_annual": agg.get("annual"),
        "wf_positive_windows": agg.get("positive_windows"),
        "wf_total_windows": agg.get("total_windows"),
        "wf_verdict": agg.get("verdict"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _holdout_gate(holdout_id: str, *, min_sharpe: float = 0.6) -> tuple[str | None, dict[str, Any]]:
    """§5.2 登记前金库闸:holdout_id 提供时,要求存在一条**通过**的 holdout 校验记录。

    读 data_lake/governance/holdout_validations.jsonl(由 governance.holdout.validate_on_holdout
    写入),按 candidate_id 取最新一条。返回 (block_reason 或 None, holdout_summary)。
    无记录 / 夏普 < min_sharpe → 返回 block_reason(触碰金库或金库段崩,§5.2 拒绝登记)。
    holdout_id 为空 → (None, {}),由调用方决定软告警(向后兼容历史/手动登记)。
    """
    if not holdout_id:
        return None, {}
    rec = None
    if _HOLDOUT_VALIDATIONS.exists():
        for line in _HOLDOUT_VALIDATIONS.read_text().splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                # 坏行跳过继续扫(不把整文件当失败),但必须留痕
                logger.warning("skip bad holdout_validations.jsonl line: %s", exc)
                continue
            if d.get("candidate_id") == holdout_id:
                rec = d  # 取最后一条 = 最新
    if rec is None:
        return (f"无 holdout 校验记录(candidate_id={holdout_id});§5.2 要求晋级前唯一一次金库校验", {})
    m = rec.get("holdout_metrics", {})
    sh = m.get("sharpe")
    dsr_sig = rec.get("holdout_dsr_sig")
    summary = {"holdout_sharpe": sh, "holdout_n": m.get("n"),
               "holdout_trials": rec.get("holdout_trials"), "holdout_dsr_p": rec.get("holdout_dsr_p"),
               "peek_count": rec.get("peek_count"), "boundary": rec.get("boundary")}
    if not isinstance(sh, (int, float)) or sh < min_sharpe:
        return (f"holdout 校验未通过(夏普={sh} < {min_sharpe});§5.2 金库段崩→拒绝登记", summary)
    if dsr_sig is False:
        return (f"holdout DSR 不显著(已 {rec.get('holdout_trials')} 候选试过同段金库,多重检验后不成立);§5.2 缝②", summary)
    return None, summary


# ---------------------------------------------------------------------------
# Reproducibility metadata
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), text=True,
        ).strip()
    except Exception:
        return "unknown"


def _data_snapshot() -> str:
    """Check key parquet files for last-modified info."""
    files = [
        ROOT / "data_lake" / "price" / "daily_all.parquet",
        ROOT / "data_lake" / "price" / "daily_raw_all.parquet",
        ROOT / "data_lake" / "fundamental_batch.parquet",
    ]
    snapshots = {}
    for fp in files:
        if fp.exists():
            mtime = pd.Timestamp(fp.stat().st_mtime, unit="s")
            snapshots[fp.name] = str(mtime.date())
    return json.dumps(snapshots)


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def _dependency_versions() -> dict[str, str]:
    deps = {}
    for lib in ["pandas", "numpy", "scipy"]:
        try:
            mod = __import__(lib)
            deps[lib] = getattr(mod, "__version__", "unknown")
        except Exception:
            deps[lib] = "not found"
    return deps


def reproducibility_meta() -> dict[str, Any]:
    return {
        "date": str(date.today()),
        "git_commit": _git_commit(),
        "python": _python_version(),
        "dependencies": _dependency_versions(),
        "data_snapshot": _data_snapshot(),
        "engine": "core.engine.BacktestEngine",
    }


# ---------------------------------------------------------------------------
# Lesson generation
# ---------------------------------------------------------------------------

def _fingerprint(trigger: str, pattern: str) -> str:
    return hashlib.sha256(f"{trigger}:{pattern}".encode()).hexdigest()[:8]


def _suggest_fix(check_id: str) -> str:
    return {
        "timing_peek": (
            "Add .shift(1) to timing signal: timing = (condition).shift(1). "
            "Verify timing[T] only uses data through T-1."
        ),
        "amount_formula": (
            "Use raw_close (not adjusted close) for amount: "
            "amount = volume * raw_close (canonical share × CNY/share). "
            "Adjusted close embeds future corporate actions in historical prices."
        ),
        "fund_alignment": (
            "Use load_fundamental_panel() which aligns by avail_date->ffill. "
            "Do not load raw fundamental values without date alignment."
        ),
        "warmup": (
            "Load price data from >=2 years before backtest start. "
            "For typical window=60, load from at least 2016 for a 2018 start."
        ),
        "delisted": (
            "Ensure data pipeline includes delisted stocks. "
            "Check data_lake/price/daily_all.parquet against meta/delisted_codes.parquet."
        ),
        "cost_sensitivity": (
            "Strategy too sensitive to trading costs. "
            "Reduce turnover (longer rebalance interval, fewer stocks) "
            "or find stronger alpha to absorb cost drag."
        ),
        "oos_is_decay": (
            "Significant performance decay from IS to OOS — possible overfitting. "
            "Simplify factor, reduce parameters, or add walk-forward validation."
        ),
        "correlation": (
            "Strategy highly correlated with existing strategy. "
            "Find orthogonal alpha source or accept role as diversification component."
        ),
        "wf_aggregate": (
            "Walk-Forward OOS aggregate failed. Strategy does not generalize. "
            "Simplify factor construction, reduce lookback windows, "
            "or verify no data leakage in factor/timing builders."
        ),
    }.get(check_id, "Manual review needed.")


def save_lessons_from_phases(phase1: list[CheckResult] | None, phase2: dict[str, Any] | None,
                             phase3: dict[str, Any] | None, family: str) -> int:
    """Extract FAIL/WARN results from all phases and save as lesson drafts."""
    lessons_saved = 0

    # Phase 1
    for r in (phase1 or []):
        if hasattr(r, 'verdict') and r.verdict in ("FAIL", "WARN"):
            _upsert_lesson(r.check_id, r.name, r.detail, family)
            lessons_saved += 1

    # Phase 2
    for check_key in ["cost_sensitivity", "oos_is_decay", "correlation"]:
        c = (phase2 or {}).get(check_key, {})
        if c.get("verdict") in ("FAIL", "WARN"):
            _upsert_lesson(check_key, check_key, c.get("detail", str(c)), family)
            lessons_saved += 1

    # Phase 2 segments
    for label, seg in (phase2 or {}).get("segments", {}).items():
        if seg.get("annual", 1) <= 0:
            _upsert_lesson("segment_negative", f"segment {label}",
                           f"Segment {label} annual={seg['annual']:+.1%}", family)
            lessons_saved += 1

    # Phase 3
    agg = (phase3 or {}).get("aggregate", {})
    if agg.get("verdict") == "FAIL":
        _upsert_lesson("wf_aggregate", "WF aggregate FAIL",
                       f"WF OOS annual={agg.get('annual',0):+.1%}, "
                       f"positive={agg.get('positive_windows',0)}/{agg.get('total_windows',0)}",
                       family)
        lessons_saved += 1

    for w in (phase3 or {}).get("windows", []):
        if w.get("oos_annual", 1) <= 0:
            _upsert_lesson("wf_negative_window",
                           f"WF negative window {w.get('test_start_year')}",
                           f"OOS annual={w['oos_annual']:+.1%} in {w['test_start_year']}",
                           family)
            lessons_saved += 1

    if lessons_saved:
        try:
            from knowledge.graph import sync_pending_lessons_to_graph
            summary = sync_pending_lessons_to_graph()
            logger.info(
                f"  Knowledge graph synced: findings={summary['findings_written']} "
                f"gates={summary['gates_written']}",
                flush=True,
            )
        except Exception as exc:
            logger.info(f"  Knowledge graph sync failed: {exc}")

    return lessons_saved


def _upsert_lesson(check_id: str, name: str, detail: str, family: str) -> None:
    """Create or merge a lesson draft."""
    fp = _fingerprint(check_id, name)
    lf = LESSONS_DIR / f"{check_id}_{fp}.json"

    if lf.exists():
        ex = json.loads(lf.read_text())
        ex["hit_count"] = ex.get("hit_count", 1) + 1
        ex["last_seen"] = str(date.today())
        if family not in ex.get("strategies", []):
            ex.setdefault("strategies", []).append(family)
        lf.write_text(json.dumps(ex, ensure_ascii=False, indent=2))
    else:
        lf.write_text(json.dumps({
            "fingerprint": fp,
            "trigger": f"Phase_{check_id}",
            "pattern": name,
            "detail": detail,
            "fix": _suggest_fix(check_id),
            "hit_count": 1,
            "first_seen": str(date.today()),
            "last_seen": str(date.today()),
            "strategies": [family],
        }, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@dataclass
class RegistrationReport:
    family: str
    version: str
    registered: bool
    repro_meta: dict[str, Any]
    lessons_saved: int
    detail: str = ""
    status: str = ""
    phase_summary: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        icon = "✅" if self.registered else "❌"
        status = self.status or ("registered" if self.registered else "blocked")
        lines = [
            f"Phase 4 Register: {self.family}/{self.version}",
            f"  Registered: {icon} {self.detail}",
            f"  Status: {status}",
            f"  Lessons saved: {self.lessons_saved}",
            f"  Git: {self.repro_meta.get('git_commit','?')}",
            f"  Python: {self.repro_meta.get('python','?')}",
            f"  Data snapshot: {self.repro_meta.get('data_snapshot','?')}",
        ]
        return "\n".join(lines)


class Phase4Register:
    """Strategy registration with reproducibility tracking."""

    def __init__(self, family: str, version: str = "v1.0") -> None:
        self.family = family
        self.version = version

    def register(
        self,
        phase1_results: list[CheckResult] | None,
        phase2_data: dict[str, Any],
        phase3_data: dict[str, Any],
        hypothesis: str = "",
        regime: str = "",
        decay_signal: str = "",
        force: bool = False,
        hypothesis_id: str = "",
        evidence_experiment_ids: list[str] | None = None,
        target_status: str = "",
        holdout_id: str = "",
        seed_provenance: dict[str, Any] | None = None,
    ) -> RegistrationReport:
        """Register strategy. Saves lessons regardless; registers only if all clear or forced."""

        # Always save lessons
        n_lessons = save_lessons_from_phases(
            phase1_results, phase2_data, phase3_data, self.family
        )
        logger.info(f"  Lessons saved: {n_lessons}")

        # Reproducibility metadata
        repro = reproducibility_meta()

        # Check if registration is allowed
        blocked = self._check_blocked(phase1_results, phase2_data, phase3_data)
        if blocked and not force:
            logger.info(f"  Registration BLOCKED: {blocked}")
            logger.info("  Use force=True to override.")
            return RegistrationReport(
                family=self.family, version=self.version,
                registered=False, repro_meta=repro, lessons_saved=n_lessons,
                detail=f"Blocked: {blocked}", status="blocked",
            )

        # §5.2 holdout 金库闸:holdout_id 提供时强制要求通过记录;缺省软告警(向后兼容)。
        ho_block, ho_summary = _holdout_gate(holdout_id)
        if holdout_id and ho_block and not force:
            logger.info(f"  Registration BLOCKED (holdout §5.2): {ho_block}")
            logger.info("  Use force=True to override.")
            return RegistrationReport(
                family=self.family, version=self.version,
                registered=False, repro_meta=repro, lessons_saved=n_lessons,
                detail=f"Blocked: {ho_block}", status="blocked",
            )
        if not holdout_id:
            logger.warning("  ⚠️ 无 holdout_id:跳过 §5.2 金库校验(手动/历史登记路径,不阻断)")

        # Build metrics from Phase 2+3
        metrics = self._build_metrics(phase2_data, phase3_data)
        config = self._build_config(phase2_data)

        # Build ExecutableStrategySpec dynamically (Task 6)
        from core.strategy_spec import ExecutableStrategySpec
        ast = config.get("ast", {})
        execution = ast.get("execution", {})
        try:
            rebal_str = str(execution.get("rebalance_freq", config.get("rebalance_days", 20)))
            rebal_days = int(rebal_str.replace("D", "").replace("W", ""))
        except ValueError:
            rebal_days = 20

        strategy_spec = ExecutableStrategySpec(
            family=self.family,
            version=self.version,
            universe={"type": "small_cap"},
            data={
                "dependencies": list(config.get("data_dependencies", ["price/close"]))
            },
            factor={
                "type": config.get("factor_fn_name", "factors.autoresearch_dsl.compute_dsl_factor"),
                "shift": 1,
                "params": config.get("factor_params", {})
            },
            selection={
                "top_n": int(execution.get("portfolio_size", config.get("top_n", 25))),
                "rebalance_days": rebal_days
            },
            timing={
                "type": "factors.small_cap.small_cap_timing",
                "params": {"ma_window": 16}
            },
            policy={
                "veto": "none"
            },
            execution={
                "portfolio_size": int(execution.get("portfolio_size", config.get("top_n", 25))),
                "rebalance_freq": str(execution.get("rebalance_freq", "20D")),
                "smoothing_window": int(execution.get("smoothing_window", 0)),
                "fill": "T_PLUS_1_CLOSE",
                "cost_model": "A_SHARE_STANDARD_V1"
            }
        )
        spec_dict = strategy_spec.to_dict()
        spec_hash = strategy_spec.spec_hash

        # Write to registry
        try:
            from engine.metrics import compute_hit
            from strategy_registry import register, register_family

            # 自动入册只走 standalone 轨：单体达标(hit=True)才考虑「在册」，否则入「候选」。
            # diversifier 轨需人工判断组合契合度，不在工厂自动通道里授予。
            # ADR-020：standalone 入「在册」除 hit 外还须 DSR 多重测试惩罚下显著(dsr_p<0.05)。
            # 但工厂通道的 nine_gate 摘要不含 dsr_p(DSR 由独立 9-Gate 回填，见 _extract_nine_gate_summary)，
            # 故 hit 候选先入「候选」；待 run_nine_gate_after_registration 回填 DSR 后，由人工/workflow
            # 据 dsr_p<0.05 升「在册」。这既堵住「DSR 未知就自动入册 standalone」的洞，也避免触 register() 的 DSR 门。
            auto_hit = compute_hit(metrics.get("annual"), metrics.get("maxdd"))
            shadow_target = str(target_status or "").upper() == "SHADOW"
            ng_summary = _extract_nine_gate_summary(phase2_data, phase3_data)
            _dsr_p = ng_summary.get("dsr_p")
            dsr_ok = isinstance(_dsr_p, (int, float)) and not isinstance(_dsr_p, bool) and _dsr_p < 0.05
            reg_status = "候选" if shadow_target else (
                "在册" if (not blocked and auto_hit and dsr_ok) else "候选")
            reg_admission: AdmissionDict = (
                {"track": "standalone",
                 "rationale": "Workflow Phase1-3 验证 + 单体达标 + DSR 显著"}
                if reg_status == "在册" else {})

            register_family(
                self.family,
                self.family,
                hypothesis=hypothesis,
                regime=regime,
                decay_signal=decay_signal,
            )
            register(
                self.family, self.version,
                desc=f"{self.family} v{self.version} — auto-registered via workflow",
                config=config,
                data_scope={
                    "source": "data_lake",
                    "period": "2010-2026",
                    "survivorship_bias": False,
                    "wf_validated": phase3_data.get("aggregate", {}).get("verdict") == "PASS",
                    "phase1_audited": not any(
                        hasattr(r, 'is_fail') and r.is_fail
                        for r in (phase1_results or [])
                    ),
                    "reproducibility": repro,
                    "holdout": ho_summary,
                },
                metrics=metrics,
                status=reg_status,
                admission=reg_admission,
                notes=(
                    f"Workflow Phase 1-3 validated. "
                    f"WF: {phase3_data.get('aggregate',{}).get('annual',0):+.1%} ann / "
                    f"{phase3_data.get('aggregate',{}).get('maxdd',0):+.1%} dd."
                    f"{' Target=SHADOW, auto-held as 候选.' if shadow_target else ''}"
                ),
                evidence=self._build_evidence(hypothesis_id, evidence_experiment_ids, seed_provenance),
                nine_gate=ng_summary,
                spec=spec_dict,
                spec_hash=spec_hash,
            )
            logger.info(f"  Registered: {self.family}/{self.version}")
            return RegistrationReport(
                family=self.family, version=self.version,
                registered=True, repro_meta=repro, lessons_saved=n_lessons,
                detail="Registered successfully", status=reg_status,
            )
        except Exception as e:
            logger.info(f"  Registration error: {e}")
            return RegistrationReport(
                family=self.family, version=self.version,
                registered=False, repro_meta=repro, lessons_saved=n_lessons,
                detail=str(e), status="error",
            )

    @staticmethod
    def _build_evidence(hypothesis_id: str, evidence_experiment_ids: list[str] | None,
                        seed_provenance: dict[str, Any] | None) -> EvidenceDict:
        """组装 evidence 块,把种子溯源(ADR-022)落进台账。

        LLM 种子起源(origin==llm_seed,或 derived 的 ancestor_origins 含 llm_seed)→ LLM 先验
        可能含金库期(2025+)行情认知,不可机械证否 → 打 semantic_seed_review 标记供人工额外审视。
        """
        evidence: EvidenceDict = {
            "hypothesis_id": hypothesis_id,
            "experiment_ids": list(evidence_experiment_ids or []),
        }
        prov = dict(seed_provenance or {})
        if prov:
            evidence["seed_provenance"] = prov
            origin = prov.get("origin")
            ancestors = prov.get("ancestor_origins", [])
            if origin == "llm_seed" or "llm_seed" in ancestors:
                evidence["semantic_seed_review"] = {
                    "required": True,
                    "reason": "种子源自 LLM(先验可能含金库期行情认知),需人工审视搜索空间是否含语义泄露",
                    "llm_ancestors": prov.get("llm_ancestors") or (
                        [{k: prov.get(k) for k in ("theme", "model") if prov.get(k)}] if origin == "llm_seed" else []
                    ),
                }
        return evidence

    def _check_blocked(self, p1: list[CheckResult] | None, p2: dict[str, Any] | None,
                       p3: dict[str, Any] | None) -> str | None:
        """Return reason if registration should be blocked, or None."""
        # Phase 1: any FAIL?
        for r in (p1 or []):
            if hasattr(r, 'is_fail') and r.is_fail:
                return f"Phase 1 FAIL: {r.check_id}"

        # Phase 2: any segment negative?
        for label, seg in (p2 or {}).get("segments", {}).items():
            if seg.get("annual", 0) <= 0:
                return f"Phase 2 segment {label} annual ≤ 0"

        # Phase 2: cost sensitivity FAIL?
        if (p2 or {}).get("cost_sensitivity", {}).get("verdict") == "FAIL":
            return "Phase 2 cost sensitivity FAIL"

        # Phase 2: correlation FAIL?
        if (p2 or {}).get("correlation", {}).get("verdict") == "FAIL":
            return "Phase 2 correlation FAIL"

        # Phase 3: WF FAIL?
        if (p3 or {}).get("aggregate", {}).get("verdict") == "FAIL":
            return "Phase 3 WF aggregate FAIL"

        # Phase 2: offset sensitivity FAIL?
        if (p2 or {}).get("offset_sensitivity", {}).get("verdict") == "FAIL":
            return "Phase 2 offset sensitivity FAIL (调仓偏移过拟合)"

        # Physical Half-Life Constraint
        config = self._build_config(p2 or {})
        ast = config.get("ast", {})
        execution = ast.get("execution", {})
        rebalance_days = config.get("rebalance_days", 20)
        try:
            rebal_str = str(execution.get("rebalance_freq", rebalance_days))
            rebal_days = int(rebal_str.replace("D", "").replace("W", ""))
        except ValueError:
            rebal_days = rebalance_days

        windows = []
        for term in ast.get("terms", []):
            win = term.get("params", {}).get("window")
            if win:
                windows.append(int(win))
        max_win = max(windows) if windows else 20
        if rebal_days > 2.0 * max_win:
            return f"Execution rebalance_days={rebal_days} exceeds 2x of factor's max window={max_win} (僵尸持仓避滑点过拟合)"

        return None

    @staticmethod
    def _segments_by_role(p2: dict[str, Any] | None) -> dict[str, Any]:
        """取结构化段字典;旧报告无 segments_by_role 时按显示标签前缀归类回退。

        禁止精确匹配完整标签字符串:OOS 标签终点年随 holdout boundary 变
        (如 "OOS 2023-2024"),硬编码 "OOS 2023-2026" 曾导致 OOS 指标静默丢失
        (2026-07-11 review)。
        """
        roles = dict((p2 or {}).get("segments_by_role") or {})
        if roles:
            return roles
        for label, seg in (p2 or {}).get("segments", {}).items():
            if label.startswith("IS"):
                roles["is"] = seg
            elif label.startswith("OOS"):
                roles["oos"] = seg
            elif label.startswith("压力"):
                roles["stress"] = seg
        return roles

    def _build_metrics(self, p2: dict[str, Any], p3: dict[str, Any]) -> dict[str, Any]:
        """Build metrics dict from Phase 2+3 data."""
        m = {}
        roles = self._segments_by_role(p2)
        for role, key in [("is", "2018"), ("oos", "2023"), ("stress", "2010")]:
            s = roles.get(role, {})
            if s:
                m[f"annual_{key}"] = s.get("annual", 0)
                m[f"maxdd_{key}"] = s.get("maxdd", 0)
                m[f"sharpe_{key}"] = s.get("sharpe", 0)

        # Top-level = IS 段(样本内口径):auto_hit 因此基于样本内绩效——保持既有行为,
        # 改为 OOS/全样本口径是研究决策,需另立 ADR(2026-07-11 review 标记)。
        is_seg = roles.get("is", {})
        m["annual"] = is_seg.get("annual", 0)
        m["maxdd"] = is_seg.get("maxdd", 0)
        m["sharpe"] = is_seg.get("sharpe", 0)
        m["calmar"] = is_seg.get("calmar", 0)

        # Hit check —— 走唯一权威 compute_hit(严格不等号),与 register() 口径一致
        from engine.metrics import compute_hit
        m["hit"] = compute_hit(m["annual"], m["maxdd"])

        # WF metrics
        wf = p3.get("aggregate", {})
        if wf:
            m["wf_annual"] = wf.get("annual", 0)
            m["wf_maxdd"] = wf.get("maxdd", 0)
            m["wf_sharpe"] = wf.get("sharpe", 0)

        return {k: round(float(v), 4) if isinstance(v, (int, float)) else v
                for k, v in m.items()}

    def _build_config(self, p2: dict[str, Any]) -> dict[str, Any]:
        """Build config dict."""
        cfg = dict(p2.get("config", {}))
        from core.engine import CostModel
        base = CostModel()  # 费率唯一权威(R-COST-001),禁止在此写字面量
        cfg["cost"] = {
            "buy": base.buy_cost,
            "sell": base.sell_cost,
            "financing_rate": base.financing_rate,
        }
        return cfg
