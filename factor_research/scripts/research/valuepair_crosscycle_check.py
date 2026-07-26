#!/usr/bin/env python3
"""价值对(bp_proxy / ep_proxy)跨周期反例检验 —— 分日历年残差 IC(L0 证据,非 alpha)。

背景(daily-round-7/8):
  round7 `probe_round7_fundamental_bp_proxy.json` 与 round8
  `fundamental_raw_ratio_probe_round8_addendum.md` 发现 bp_proxy/ep_proxy 均呈现同一
  不对称形态 —— IS(2018-2022)残差 ICIR 弱(0.08 / 0.10)、OOS(2023-2024)残差 ICIR
  骤强(0.59 / 0.20),且同步与流动性负相关。round8 怀疑这是 2023-2024 A股价值风格
  占优 regime 的共同贝塔,而非独立结构性正交 alpha,建议做覆盖 2018-2020(成长占优期,
  round8 措辞)的跨周期反例检验 —— 本脚本即产出该检验证据。

口径一致性声明(不得另起口径,否则与 round7/8 不可比):
  本脚本**逐函数原样 import** `scripts/research/signal_source_probe.py` 的
  `_load_close / _load_controls / _monthly_rebalance / _forward_returns /
  _neutralize / _xcorr / _seg_ic`,零改写、零改参数。唯一新增逻辑是把该脚本原有的
  "IS/cutoff/OOS 两段切分" 换成 "逐日历年切分"(用同一个 `_seg_ic` 对每年
  [year-01-01, year-12-31] 调用一次),因此每年数字与 round7/8 的 IS/OOS 数字在
  同一残差化定义下可比:controls = size(log circ_mv) + liquidity(turnover_rate),
  standard 截面 OLS 残差(与 core/engine.py 的 neutralize 同法 lstsq)。

预登记证伪判据(写在结果之前,不得看结果后再调整 —— R-LLM-001 边界,本脚本只产描述性
统计,不下"有效/无效"结论,该判据只是"哪种读数支持怀疑被加强 vs 削弱"的映射规则,
最终解读仍需人工):
  (a) 若正残差 IC 集中在 2023-2024、而 2018-2020 各年 ≤0 或明显转弱(量级/ICIR 显著低于
      2023-2024)—— "2023-2024 regime 共同贝塔"怀疑被【加强】。
  (b) 若 2018-2024 各年残差 IC 均匀为正、无 2018-2020 塌陷 —— 怀疑被【削弱】。
  两种结果都如实写入报告,不做取舍。

绝对红线(holdout 金库,app_config/settings.yaml::holdout.start = 2025-01-01,见
app_config/holdout_boundary_history.jsonl genesis 记录):
  数据加载后立即截断到 <= 2024-12-31。任何计算窗口不得触及 2025-01-01 及以后。
  本脚本以断言机械强制该红线(见 `_enforce_holdout_boundary`)。

边界:L0 描述性统计(原始/残差 rank-IC、Newey-West ICIR、风格相关),不扣成本、无
DSR/PBO/容量/9-Gate,不构成"alpha 已验证/已证伪"结论。判断入册/有效性归确定性门禁 +
workflow(R-WF-001 / R-LLM-001)。

用法:
  python factor_research/scripts/research/valuepair_crosscycle_check.py \
      --json factor_research/reports/research/valuepair_crosscycle_check_20260720.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 逐函数原样复用 signal_source_probe 的残差化口径(不重写/不改参数)。
from governance.holdout import assert_search_clean, boundary  # noqa: E402
from scripts.research.signal_source_probe import (  # noqa: E402
    _forward_returns,
    _load_close,
    _load_controls,
    _monthly_rebalance,
    _neutralize,
    _seg_ic,
    _xcorr,
)

YEARS = list(range(2018, 2025))  # 2018..2024


def _enforce_holdout_boundary(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """立即截断到 holdout 边界之前;边界唯一真相源 = governance.holdout.boundary()。"""
    truncated = df[df.index < boundary()]
    assert not truncated.empty, (
        f"{label}: 截断后为空(输入全部落在 holdout 金库内),P0 违规"
    )
    assert_search_clean(truncated.index, label=label)
    return truncated


def _year_seg(fac: pd.DataFrame, fwd: pd.DataFrame, year: int) -> dict | None:
    """对同一个 `_seg_ic`(round7/8 原函数)按日历年调用,保证口径逐位一致。"""
    return _seg_ic(fac, fwd, f"{year}-01-01", f"{year}-12-31")


def _year_xcorr(fac: pd.DataFrame, ctrl: pd.DataFrame, year: int) -> float | None:
    idx = [t for t in fac.index if t.year == year]
    if not idx:
        return None
    return _xcorr(fac.reindex(idx), ctrl.reindex(idx))


def crosscycle_check(factor_ref: str, universe: str = "all") -> dict:
    mod_name, fn_name = factor_ref.split(":")
    factor_fn = getattr(importlib.import_module(mod_name), fn_name)

    close = _load_close(universe)
    close = _enforce_holdout_boundary(close, "close")

    fac_full = factor_fn(close)
    fac_full = _enforce_holdout_boundary(fac_full, "factor")

    rb = _monthly_rebalance(close, "2018-01-01", "2024-12-31")
    fwd = _forward_returns(close, rb)
    fwd = _enforce_holdout_boundary(fwd, "forward_returns")
    rb2 = [t for t in rb if t in fwd.index]
    fac = fac_full.reindex(rb2)

    controls_full = _load_controls(close)
    controls = {k: _enforce_holdout_boundary(v, f"control:{k}").reindex(rb2) for k, v in controls_full.items()}
    resid = _neutralize(fac, [controls["size"], controls["liquidity"]])

    by_year = {}
    for y in YEARS:
        raw = _year_seg(fac, fwd, y)
        res = _year_seg(resid, fwd, y)
        liq_corr = _year_xcorr(fac, controls["liquidity"], y)
        by_year[str(y)] = {
            "raw_ic": raw,
            "residual_ic_size_liq": res,
            "liquidity_corr": liq_corr,
        }

    return {
        "factor": factor_ref,
        "universe": universe,
        "window": {"start": "2018-01-01", "end": "2024-12-31"},
        "holdout_boundary": str(boundary().date()),
        "by_year": by_year,
        # 全窗与 round7/8 同定义的 IS/OOS 复算(交叉核对口径一致性用,非新证据)。
        "cross_check_vs_round78": {
            "raw_ic": {
                "IS_2018_2022": _seg_ic(fac, fwd, "2018-01-01", "2022-12-31"),
                "OOS_2023_2024": _seg_ic(fac, fwd, "2023-01-01", "2024-12-31"),
            },
            "residual_ic_size_liq": {
                "IS_2018_2022": _seg_ic(resid, fwd, "2018-01-01", "2022-12-31"),
                "OOS_2023_2024": _seg_ic(resid, fwd, "2023-01-01", "2024-12-31"),
            },
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="价值对 bp_proxy/ep_proxy 跨周期反例检验(L0,确定性)")
    ap.add_argument("--universe", default="all", choices=["all", "northbound"])
    ap.add_argument("--json", default="", help="落 JSON 路径(可选)")
    a = ap.parse_args()

    factors = ["factors.fundamental:bp_proxy", "factors.fundamental:ep_proxy"]
    out = {
        "check": "valuepair_crosscycle_check",
        "date": "2026-07-20",
        "purpose": (
            "反例检验 round7/8 对 bp_proxy/ep_proxy 的 regime 共同贝塔怀疑:"
            "覆盖 2018-2020(成长占优期,round8 措辞)的分日历年残差 IC"
        ),
        "results": {f: crosscycle_check(f, a.universe) for f in factors},
    }

    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\nJSON 落 {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
