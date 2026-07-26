"""Holdout 金库(LOOP_ENGINEERING.md §5.2)。

一段 loop **从未、永不**用于搜索的数据(date >= boundary)。研究/搜索回测必须
全部落在 < boundary;仅晋级前 validate_on_holdout **唯一一次**校验,且记账防重复偷看。
这是唯一能戳穿「过拟合到适应度函数」的东西——loop 自己不得触碰。

boundary 来自 app_config/settings.yaml::holdout.start(缺省 2025-01-01)。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

# current_data_fingerprint 的定义层权威在 lake.fingerprint(manifest vintage 口径,
# 2026-07-21 架构 P1-1③ 下沉);此处 re-export 兼容既有消费方(run_daily /
# strategy_truth_screen / 各 holdout 测试),candidate 身份语义不变。
from lake.fingerprint import current_data_fingerprint as current_data_fingerprint

_DEFAULT_BOUNDARY = "2025-01-01"
_VALIDATIONS = Path(__file__).resolve().parents[1] / "data_lake" / "governance" / "holdout_validations.jsonl"
_VALIDATION_PROCESS_LOCK = threading.RLock()


class HoldoutBreach(Exception):
    """搜索/研究回测触碰了 holdout 金库(date >= boundary)。"""


class HoldoutAlreadyConsumed(RuntimeError):
    """同一候选/spec/data/boundary 身份已主动消费过金库。"""


class HoldoutIdentityMismatch(RuntimeError):
    """同一 candidate_id 被复用于不同 spec 或数据 vintage。"""


class HoldoutHistoryCorrupt(RuntimeError):
    """Holdout 边界历史账本损坏或包含不可审计记录。"""


_SETTINGS_YAML = Path(__file__).resolve().parents[1] / "app_config" / "settings.yaml"
_ROOT = Path(__file__).resolve().parents[1]


def boundary() -> pd.Timestamp:
    """金库起点:date >= 此为 holdout,搜索只能用 < 此。

    直读 settings.yaml::holdout.start(Settings dataclass 是固定 schema 不含此 section),
    缺省 2025-01-01。
    """
    start = _DEFAULT_BOUNDARY
    try:
        import yaml
        cfg = yaml.safe_load(_SETTINGS_YAML.read_text()) or {}
        start = (cfg.get("holdout") or {}).get("start", start)
    except Exception as exc:
        raise RuntimeError(f"cannot load holdout boundary from {_SETTINGS_YAML}: {exc}") from exc
    return pd.Timestamp(start)


def is_holdout(date) -> bool:
    return pd.Timestamp(date) >= boundary()


# ── boundary 迁移机制(ADR-023):只进不退 + 旧金库作废 ──
# 边界历史账本(append-only,**git 跟踪**于 app_config/ —— data_lake/ 被 gitignore,放那 CI 守卫
# 跨机器拿不到)。每条 = 一次边界设定 {boundary, recorded_at, reason, kind}。强制不变量:
#   ① 严格递增(只进不退):移早 = 复活已被偷看的金库段 → 禁;
#   ② settings.holdout.start 必须 == 历史最大值(active);手改前进必须先经 migrate 记录。
# active 金库 = max(history);superseded(已作废)金库 = 所有 < max 的历史边界。
_BOUNDARY_HISTORY = Path(__file__).resolve().parents[1] / "app_config" / "holdout_boundary_history.jsonl"


class HoldoutBoundaryRegression(RuntimeError):
    """企图把 holdout 边界后移(复活已偷看金库段),违反只进不退。"""


def boundary_history(path: Path | None = None) -> list[dict]:
    """读边界历史账本(按记录顺序)。文件缺失 → 空列表(由守卫据 settings 兜底)。"""
    p = path or _BOUNDARY_HISTORY
    if not p.exists():
        return []
    out: list[dict] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise HoldoutHistoryCorrupt(
                    f"holdout boundary history line {lineno} is invalid JSON: {p}"
                ) from exc
            if not isinstance(record, dict) or not record.get("boundary"):
                raise HoldoutHistoryCorrupt(
                    f"holdout boundary history line {lineno} must be an object with boundary: {p}"
                )
            out.append(record)
    return out


def latest_boundary(path: Path | None = None) -> pd.Timestamp:
    """历史账本里的 active 金库起点 = 最大边界。账本空 → 退回 settings.boundary()。"""
    hist = boundary_history(path)
    if not hist:
        return boundary()
    return max(pd.Timestamp(h["boundary"]) for h in hist)


def superseded_boundaries(path: Path | None = None) -> set[str]:
    """已作废金库边界 = 所有 < active(max) 的历史边界(字符串 date)。

    针对这些边界做过的 holdout 校验不再计入 active 金库的多重检验负担(新金库是新数据)。
    """
    hist = boundary_history(path)
    if not hist:
        return set()
    active = max(pd.Timestamp(h["boundary"]) for h in hist)
    return {str(pd.Timestamp(h["boundary"]).date()) for h in hist
            if pd.Timestamp(h["boundary"]) < active}


def migrate_holdout_boundary(new_boundary, *, reason: str, recorded_at: str | None = None,
                             apply: bool = True, path: Path | None = None) -> dict:
    """**唯一**合法的边界推进入口(ADR-023):只进不退,记账,旧金库自动作废。

    强制 new_boundary 严格大于历史最大值(后移/相等 → HoldoutBoundaryRegression)。
    apply=True 追加历史记录(append-only,不删旧)。返回 {new, previous, superseded:[...]}。
    迁移后须**人工**同步:① settings.yaml::holdout.start;② check_holdout_compliance.py 的
    EXPECTED_BOUNDARY[_HASH] pin —— 两者由 hash 锁(ADR-021)+ 单调守卫共同强制。
    """
    p = path or _BOUNDARY_HISTORY
    new_ts = pd.Timestamp(new_boundary)
    hist = boundary_history(p)
    prev = max((pd.Timestamp(h["boundary"]) for h in hist), default=None)
    if prev is not None and new_ts <= prev:
        raise HoldoutBoundaryRegression(
            f"金库边界只进不退:新 {new_ts.date()} 必须 > 当前 {prev.date()}。"
            f"后移会复活已被偷看的金库段(2025+ 已进 phase2/3 报告/校验记录)。")
    superseded = [str(pd.Timestamp(h["boundary"]).date()) for h in hist] if prev is not None else []
    rec = {
        "boundary": str(new_ts.date()),
        "recorded_at": recorded_at or datetime.now(UTC).date().isoformat(),
        "reason": reason,
        "kind": "migration",
        "supersedes": superseded,
    }
    if apply:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"new": rec["boundary"], "previous": str(prev.date()) if prev is not None else None,
            "superseded": superseded}


def candidate_identity(base_id: str, spec_hash: str, data_fingerprint: str) -> str:
    """Create a new candidate identity whenever spec or data vintage changes."""
    return f"{base_id}::{str(spec_hash)[:12]}::{str(data_fingerprint)[:12]}"


def assert_search_clean(dates, *, label: str = "") -> None:
    """自查门:搜索/研究回测用到的日期必须全部 < boundary,否则抛 HoldoutBreach。

    dates:回测收益/面板的 DatetimeIndex,或单个最大日期。供 screener/factory 自纠。
    """
    if isinstance(dates, (pd.Series, pd.DataFrame, pd.DatetimeIndex)):
        idx = dates.index if hasattr(dates, "index") and not isinstance(dates, pd.DatetimeIndex) else dates
        max_date = pd.Timestamp(pd.DatetimeIndex(idx).max())
    else:
        max_date = pd.Timestamp(dates)
    b = boundary()
    if max_date >= b:
        raise HoldoutBreach(
            f"{label or '搜索回测'} 触碰 holdout 金库:数据末日 {max_date.date()} >= 金库起点 {b.date()}。"
            f" 搜索必须截到 < {b.date()};holdout 仅 validate_on_holdout 唯一一次校验。"
        )


_MIN_HOLDOUT_OBS = 20  # holdout 段至少这么多观测才算得动 DSR(短段 DSR 不可靠,留 None)


@contextmanager
def _validation_transaction(path: Path):
    """序列化身份检查、trial 计数和 append，防并发重复消费金库。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with _VALIDATION_PROCESS_LOCK:
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_validation_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"holdout validation ledger line {lineno} is invalid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(f"holdout validation ledger line {lineno} must be an object")
        records.append(record)
    return records


def holdout_trials(path: Path | None = None, *, boundary_filter=None) -> int:
    """已在金库上验证过的**不同候选**数 = 金库的跨候选多重检验负担(§5.2 缝②)。

    每多一个候选在同一段金库上验证,金库就更接近"第二个样本内";holdout_dsr 用此累计数
    惩罚,防止规模化 p-hack 金库(跑够多候选总有几个靠运气过 sharpe 门)。

    boundary_filter(ADR-023):只数针对该 boundary 的校验。新金库迁移后,旧(superseded)金库的
    peek 不再计入新金库的多重检验负担——新金库是新数据不背旧债。缺省数全部(向后兼容)。
    """
    p = path or _VALIDATIONS
    bf = str(pd.Timestamp(boundary_filter).date()) if boundary_filter is not None else None
    with _validation_transaction(p):
        ids = set()
        for rec in _read_validation_records(p):
            cid = rec.get("candidate_id")
            rec_b = rec.get("holdout_boundary") or rec.get("boundary", "")
            if bf is not None and rec_b != bf:
                continue
            if cid:
                ids.add((cid, rec.get("spec_hash", ""), rec.get("data_fingerprint", ""), rec_b))
    return len(ids)


def _return_hash(returns: pd.Series) -> str:
    series = pd.Series(returns).dropna().sort_index()
    payload = pd.util.hash_pandas_object(series, index=True).values.tobytes()
    return hashlib.sha256(payload).hexdigest()


def _result_from_record(rec: dict, *, idempotent_retry: bool = False) -> dict:
    out = dict(rec.get("holdout_metrics") or {})
    for key in ("peek_count", "holdout_trials", "holdout_dsr_p", "holdout_dsr_sig"):
        out[key] = rec.get(key)
    if idempotent_retry:
        out["idempotent_retry"] = True
    return out


def validate_on_holdout(
    candidate_id: str,
    returns: pd.Series,
    *,
    spec_hash: str,
    data_fingerprint: str,
    holdout_boundary: str | pd.Timestamp | None = None,
    idempotent_retry: bool = True,
    ts: str | None = None,
    path: Path | None = None,
) -> dict:
    """晋级前**唯一一次**在金库上校验(date >= boundary 段)。记账防重复偷看。

    返回 holdout 段绩效 + peek_count(同候选重复偷看)+ holdout_trials(跨候选多重检验数)
    + holdout_dsr_p/sig(按累计验证数惩罚后的 deflated Sharpe)。**仅 sharpe 高不够**——必须
    扛过"已有 N 个不同候选在同段金库上试过"的多重检验,否则金库退化成第二个样本内(§5.2 缝②)。
    """
    from engine.metrics import metrics
    if not str(spec_hash).strip() or not str(data_fingerprint).strip():
        raise ValueError("holdout identity requires spec_hash and data_fingerprint")
    b = pd.Timestamp(holdout_boundary) if holdout_boundary is not None else boundary()
    r = pd.Series(returns).dropna()
    r_ho = r[r.index >= b]
    m = metrics(r_ho) if len(r_ho) else {"n": 0, "note": "holdout 段无数据"}
    return_hash = _return_hash(r_ho)

    p = path or _VALIDATIONS
    identity = (
        candidate_id,
        str(spec_hash),
        str(data_fingerprint),
        str(b.date()),
    )
    # ADR-023:多重检验与身份检查都**只看 active 金库(本次 b)**的记录。旧(superseded)金库的
    # peek 既不计入新金库的 n_trials,也不阻挡同一候选对新金库的合法重校验(新金库=新数据)。
    cur_b = str(b.date())
    with _validation_transaction(p):
        seen_identities: set[tuple[str, str, str, str]] = set()
        same_candidate: list[dict] = []
        for d in _read_validation_records(p):
            rec_b = str(d.get("holdout_boundary") or d.get("boundary", ""))
            if rec_b != cur_b:
                continue  # 跨金库(旧/作废)记录不参与 active 金库的检验与计数
            cid = d.get("candidate_id")
            if cid:
                seen_identities.add((
                    cid,
                    str(d.get("spec_hash", "")),
                    str(d.get("data_fingerprint", "")),
                    rec_b,
                ))
            if cid == candidate_id:
                same_candidate.append(d)

        exact = next((
            rec for rec in same_candidate
            if (
                rec.get("candidate_id"),
                str(rec.get("spec_hash", "")),
                str(rec.get("data_fingerprint", "")),
                str(rec.get("holdout_boundary") or rec.get("boundary", "")),
            ) == identity
        ), None)
        if exact is not None:
            if exact.get("return_hash") != return_hash:
                raise HoldoutAlreadyConsumed(
                    f"holdout identity {candidate_id} 已消费且本次 return_hash 不同"
                )
            if not idempotent_retry:
                raise HoldoutAlreadyConsumed(
                    f"holdout identity {candidate_id} 已消费;禁止第二次主动评估"
                )
            return _result_from_record(exact, idempotent_retry=True)
        if same_candidate:
            raise HoldoutIdentityMismatch(
                f"candidate_id={candidate_id!r} 已绑定其他 spec/data/boundary;"
                "语义或数据变化必须创建新 candidate identity"
            )

        peek = 1
        n_trials = len(seen_identities | {identity})

        # 按累计验证数惩罚的 holdout DSR(短段算不动留 None,退回 sharpe 门兜底)
        dsr_p, dsr_sig = None, None
        if isinstance(m.get("sharpe"), (int, float)) and m.get("n", 0) >= _MIN_HOLDOUT_OBS:
            from core.analysis.walk_forward import deflated_sharpe
            rep = deflated_sharpe(observed_sr=m["sharpe"], n_trials=n_trials, n_periods=m["n"],
                                  skew=m.get("skew", 0.0), kurt=m.get("kurtosis_excess", 0.0) + 3.0,
                                  annualized=True)
            dsr_p, dsr_sig = round(float(rep["p_value"]), 4), bool(rep["significant_05"])

        rec = {
            "ts": ts or datetime.now(UTC).isoformat(),
            "consumed_at": ts or datetime.now(UTC).isoformat(),
            "candidate_id": candidate_id,
            "spec_hash": str(spec_hash),
            "data_fingerprint": str(data_fingerprint),
            "holdout_boundary": str(b.date()),
            "boundary": str(b.date()),
            "return_hash": return_hash,
            "peek_count": peek,
            "holdout_trials": n_trials,
            "holdout_dsr_p": dsr_p,
            "holdout_dsr_sig": dsr_sig,
            "holdout_metrics": {
                key: (
                    float(m[key])
                    if isinstance(m.get(key), (int, float, np.floating))
                    else m.get(key)
                )
                for key in ("annual", "sharpe", "maxdd", "n")
                if key in m
            },
        }
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return _result_from_record(rec)
