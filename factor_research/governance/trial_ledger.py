"""持久化诚实 trial 账本(LOOP_ENGINEERING.md §5.1)。

每次搜索事件(factory 生成 / 参数扫 / 候选评估)append 一条;DSR 用**累计**计数,
而非手填 n_trials。append-only,防自欺:搜得越多,累计 n_trials 越大,DSR 惩罚越重。

scope = 候选的「搜索血缘」键(默认母策略/家族 id;跨家族择优时用 "global")。
某候选的诚实 n_trials = 其 scope 下的累计搜索配置数。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_LEDGER = Path(__file__).resolve().parents[1] / "data_lake" / "governance" / "trial_ledger.jsonl"


def _path() -> Path:
    _LEDGER.parent.mkdir(parents=True, exist_ok=True)
    return _LEDGER


def record_trials(scope: str, n_configs: int, context: str = "",
                  spec_hash: str | None = None, ts: str | None = None,
                  path: Path | None = None) -> int:
    """记一次搜索事件(尝试了 n_configs 个配置),返回该 scope 的新累计值。

    n_configs:本次事件试了多少配置(参数网格大小 / 候选批量 / 1 次单评估)。
    """
    if n_configs < 1:
        raise ValueError("n_configs 必须 ≥ 1(每次搜索至少 1 个配置)")
    p = path or _path()
    rec = {
        "ts": ts or datetime.now(UTC).isoformat(),
        "scope": scope,
        "n_configs": int(n_configs),
        "context": context,
        "spec_hash": spec_hash,
    }
    with open(p, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return cumulative_trials(scope, path=p)


def cumulative_trials(scope: str | None = None, path: Path | None = None) -> int:
    """累计搜索配置数。scope=None → 全局(所有 scope 之和);否则只该 scope。"""
    p = path or _path()
    if not p.exists():
        return 0
    total = 0
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if scope is None or rec.get("scope") == scope:
            total += int(rec.get("n_configs", 0))
    return total


def honest_n_trials(scope: str | None = None, path: Path | None = None) -> int:
    """喂给 DSR 的诚实 n_trials = 实验账本累计搜索数。

    用法:deflated_sharpe(..., n_trials=honest_n_trials(family_id)),
    替代手填。返回 0 表示 trial_count_unknown；调用方必须阻断审批，
    不得用地板值伪装已知的多重检验负担。
    """
    return cumulative_trials(scope, path=path)
