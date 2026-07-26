"""small-cap-size/v2.0 纸面前向实验跟踪器(人工 override,非达标台账)。

背景:small-cap-size/v2.0 回测+净化CV 都过、经济学站得住,但 DSR=0.086 过不了 ADR-020
门(多重检验下不显著)。所有者明知风险,决定**纸面前向**收集真实样本外证据(零真金,
靠策略自带 MA16 择时熊市清仓为唯一防守),主仓继续防守。详见 DECISIONS ADR-024。

铁律(防自欺):
  · 本脚本**不改台账/不改 settings/不部署**——它是隔离的旁路跟踪,不污染生产。
  · small-cap-size/v2.0 仍是「参考」、DSR 仍 0.086;本实验**不**让它「过门」。
  · point-in-time:canonical 引擎 + 因子 shift(1)/MA16 用过去数据,前向段是真实 OOS。
  · 评估窗 = EXPERIMENT_START 起的前向段;此前历史只作上下文,不算前向证据。

用法:数据日更后跑一次,累积前向轨迹。`python3 scripts/research/paper_forward_smallcap.py`
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXPERIMENT_START = "2026-06-22"        # 实验起点(所有者决定 paper-forward 之日)
DSR_AT_START = 0.086                    # 起点时的 DSR(诚实留痕:实验就是想看它前向是否兑现)
LOG = ROOT / "reports" / "experiments" / "smallcap_v2_paper_forward.jsonl"


def _metrics(ret: pd.Series) -> dict:
    ret = ret.dropna()
    if len(ret) == 0:
        return {"n_days": 0}
    cum = float((1 + ret).prod() - 1)
    ann = float(ret.mean() * 252)
    vol = float(ret.std() * np.sqrt(252))
    sharpe = ann / vol if vol > 0 else 0.0
    nav = (1 + ret).cumprod()
    maxdd = float((nav / nav.cummax() - 1).min())
    return {"n_days": int(len(ret)), "cum_return": round(cum, 4), "annual": round(ann, 4),
            "sharpe": round(sharpe, 2), "maxdd": round(maxdd, 4)}


def main() -> int:
    from strategies.small_cap import run_small_cap_strategy
    res = run_small_cap_strategy()           # 默认 config == small-cap-size/v2.0
    ret = pd.Series(res["returns"]).dropna()
    ret.index = pd.to_datetime(ret.index)
    start = pd.Timestamp(EXPERIMENT_START)

    fwd = ret[ret.index >= start]
    fm = _metrics(fwd)
    full = _metrics(ret)

    print("=" * 70)
    print("small-cap-size/v2.0 纸面前向实验(人工 override · 非达标台账 · 零真金)")
    print(f"  起点 {EXPERIMENT_START}(DSR={DSR_AT_START},未过 ADR-020 门;靠 MA16 自带防守)")
    print(f"  数据末日 {ret.index[-1].date()}")
    print("-" * 70)
    print(f"  【前向段(>={EXPERIMENT_START})= 真实 OOS 证据】")
    if fm["n_days"] == 0:
        print("    尚无前向交易日(实验今日启动,从此累积)。")
    else:
        print(f"    {fm['n_days']} 日 | 累计 {fm['cum_return']:+.2%} | 年化 {fm['annual']:+.1%} "
              f"| 夏普 {fm['sharpe']} | 回撤 {fm['maxdd']:+.2%}")
    print(f"  【全历史(上下文,非前向证据)】{full['n_days']} 日 | 年化 {full['annual']:+.1%} "
          f"| 夏普 {full['sharpe']} | 回撤 {full['maxdd']:+.2%}")
    print("=" * 70)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    snap = {
        "run_at": datetime.now(UTC).isoformat(),
        "data_asof": str(ret.index[-1].date()),
        "experiment_start": EXPERIMENT_START,
        "dsr_at_start": DSR_AT_START,
        "kind": "human_override_paper_forward",
        "strategy": "small-cap-size/v2.0",
        "registry_status": "参考",            # 诚实:它没在册、没过门
        "forward": fm,
    }
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")
    print(f"快照已追加 → {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
