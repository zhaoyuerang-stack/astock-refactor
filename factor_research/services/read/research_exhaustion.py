"""Research-Exhaustion Read Service —— 研究枯竭信号(确定性 advisory)。

回答唯一问题:**自动研究环最近 window 次搜索是否毫无产出(枯竭)?**

机械判据(零 LLM,承 R-LLM-001):
  productive run = 该次搜索有候选通过 L3 且 holdout 校验 OK(n_holdout_ok > 0)。
  连续 window 次**非失败**运行全不 productive → exhausted。
诚实三态(不假绿不假红):
  exhausted / healthy / insufficient_evidence(非失败样本 < window 或摘要文件缺失——
  系统刚接上仪表时不得假报枯竭,搜索环自身连挂也不得错记为"搜过没产出")。

数据源 = scheduled_factor_search 每次运行落的 append-only 摘要
``reports/research/factor_search_runs.jsonl``。此前零晋级只打印即退出,
「连续几周无产出」这一枯竭事实系统自己看不见,更不会触发外探。

用途:决策收件箱第七源——exhausted 时把「启动外部探索(probe-signal-source
数据体检 / 文献扫描)还是调整搜索空间(方向登记簿)」推给人裁决。
本服务**只报信号,绝不自动启动任何外探**(生成端扩张须人批准,LOOP §6)。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS_PATH = ROOT / "reports" / "research" / "factor_search_runs.jsonl"
DATA_BACKLOG = ROOT / "knowledge" / "data_source_backlog.json"

_DEFAULT_WINDOW = 4


def _load_runs(path: Path) -> tuple[list[dict], int]:
    """读运行摘要 JSONL。返回 (有效记录, 坏行数);文件缺失 → ([], 0)。

    坏行跳过但计数并透出(detail 里可见),整文件不可读则向上抛
    (收件箱会转为 source_error 显式入箱,不静默)。
    """
    if not path.exists():
        return [], 0
    runs: list[dict] = []
    corrupt = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                runs.append(rec)
            else:
                corrupt += 1
        except Exception:
            corrupt += 1
    return runs, corrupt


def _load_backlog(path: Path) -> list[dict]:
    """候选数据源清单(按 priority 升序);缺失/坏文件 → 空表(advisory,fail-open)。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = [e for e in data.get("entries", []) if isinstance(e, dict)]
        return sorted(entries, key=lambda e: e.get("priority", 999))
    except Exception:
        return []


def get_research_exhaustion(
    *,
    window: int = _DEFAULT_WINDOW,
    runs_path: str | Path | None = None,
    backlog_path: str | Path | None = None,
) -> dict:
    """研究枯竭信号。返回 dict(纯 advisory,消费方 = 决策收件箱/研究页)。"""
    runs, corrupt = _load_runs(Path(runs_path or RUNS_PATH))
    non_failed = [r for r in runs if r.get("status") != "search_failed"]
    recent = non_failed[-window:]

    if len(recent) < window:
        state = "insufficient_evidence"
        detail = (
            f"非失败运行样本 {len(recent)}/{window} 不足以判枯竭"
            f"(总运行 {len(runs)},失败 {len(runs) - len(non_failed)},坏行 {corrupt})"
        )
    elif any(int(r.get("n_holdout_ok") or 0) > 0 for r in recent):
        state = "healthy"
        detail = f"最近 {window} 次非失败运行中存在产出(n_holdout_ok>0)"
    else:
        state = "exhausted"
        detail = "; ".join(
            f"{r.get('ts', '?')[:10]}:{r.get('status')}(eval={r.get('evaluated', '?')},ho_ok=0)"
            for r in recent
        )

    return {
        "state": state,
        "window": window,
        "runs_total": len(runs),
        "runs_considered": len(recent),
        "corrupt_lines": corrupt,
        "last_run": (runs[-1] if runs else None),
        "detail": detail,
        "criterion": "productive = 有候选过 L3 且 holdout 校验 OK;连续 window 次非失败运行全不 productive → exhausted",
        "data_source_backlog": _load_backlog(Path(backlog_path or DATA_BACKLOG)),
        "honesty": "机械判据 advisory,非裁决;exhausted 只建议外探,启动须人批准(LOOP §6);"
                   "样本不足/文件缺 → insufficient_evidence,不假报枯竭。",
    }
