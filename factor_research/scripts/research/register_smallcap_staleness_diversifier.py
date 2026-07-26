"""small-cap-staleness 走 diversifier 轨入册(R-REG-001 唯一写入口 register())。

依据:phase1-3 验证通过(防未来 PASS / 成本 PASS / WF 9-11 PASS),且对 small-cap 核心组合边际
已证(Sharpe IS 0.77→1.09、OOS 0.48→0.60)。diversifier 轨凭组合边际而非单体 hit/DSR 准入。
诚实:hit=False(OOS 年化 9.8%<15% + 2015 压力回撤),故走 diversifier 非 standalone;
n_trials 诚实记搜索自由度(流动性族 4 因子 × λ 网格 3 = 12)。
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)

from scripts.research.promote_smallcap_staleness import (  # noqa: E402
    CONFIG,
    FAMILY,
    HYPOTHESIS,
    LAMBDA,
    VERSION,
    WIN,
    factor_builder,
    timing_builder,
)

N_TRIALS = 12  # 诚实:流动性族 4 因子(zero_vol/zero_ret/liq_vol/amihud)× λ 网格{0,0.5,1.0}


def main():
    from engine.metrics import compute_hit
    from strategy_registry import register, register_family
    from workflow.phase2_backtest import Phase2Runner
    from workflow.phase3_wf import WF3Runner

    print("phase2/3 取诚实 metrics...", flush=True)
    p2 = Phase2Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start="2010-01-01")
    p3 = WF3Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start="2010-01-01")
    agg = p3.get("aggregate", {})
    segs = p2.get("segments", {})
    is_seg = next((segs[k] for k in segs if k.startswith("IS")), {})
    oos_seg = next((segs[k] for k in segs if "OOS" in k or k == "oos"), {})

    metrics = {
        "annual": agg.get("annual"), "maxdd": agg.get("maxdd"),
        "sharpe": agg.get("sharpe"), "calmar": agg.get("calmar"),
        "hit": compute_hit(agg.get("annual"), agg.get("maxdd")),
    }
    nine_gate = {
        "run_date": date.today().isoformat(),
        "passed_all": False,
        "dsr_p": None,  # diversifier 轨不卡 DSR;未跑 9-Gate 完整审计(诚实标 None)
        "n_trials": N_TRIALS,
        "wf_sharpe": agg.get("sharpe"),
        "wf_positive_ratio": (p3.get("aggregate", {}) or {}).get("positive_ratio")
                              or (sum(1 for w in p3.get("windows", []) if (w.get("oos_annual") or 0) > 0)
                                  / max(len(p3.get("windows", [])), 1)),
        "is_sharpe": is_seg.get("sharpe"), "oos_sharpe": oos_seg.get("sharpe"),
        "cost_decay_rate": (p2.get("cost_sensitivity", {}) or {}).get("decay_pct"),
        "note": "phase1-3 验证(防未来/成本/WF PASS);diversifier 轨,DSR 未跑(只卡 standalone)",
    }
    admission = {
        "track": "diversifier",
        "rationale": ("组合边际已证:对 small-cap 核心 Sharpe IS 0.77→1.09 / OOS 0.48→0.60;"
                      "zero_ret_days size 正交(截面 corr 0.057)→ 真分散非 size 代理。"
                      "phase1-3 验证通过(防未来/成本敏感/WF 9-11 正窗)。"),
        "note": "L1 证据(phase1-3+组合边际 probe);未跑 9-Gate/DSR;λ=0.5 与流动性族筛选计入 n_trials=12",
    }
    config = {
        "factor": "zscore(small_cap_factor) + 0.5*zscore(zero_ret_days)",
        "lambda": LAMBDA, "small_cap_window": WIN, "zero_ret_window": WIN,
        "timing": "small_cap MA16 binary", **CONFIG,
        "reproduce": "scripts/research/promote_smallcap_staleness.py",
    }
    data_scope = {
        "source": "data_lake", "period": "2010-2024", "survivorship_bias": False,
        "wf_validated": agg.get("verdict") == "PASS", "phase1_audited": True,
    }
    evidence = {
        "reports": ["reports/discovery/liquidity_family_findings.md",
                    "reports/discovery/smallcap_staleness_workflow_findings.md"],
        "seed_provenance": {"source": "liquidity_family_probe", "signal": "zero_ret_days(Lesmond)"},
    }

    print(f"register_family + register({FAMILY}/{VERSION}, diversifier, 在册)...", flush=True)
    register_family(FAMILY, "Small-Cap × 价格停滞(zero-return-days)增强",
                    hypothesis=HYPOTHESIS, regime="小盘有效期 + 低关注溢价",
                    decay_signal="小盘溢价反转 / zero_ret 拥挤")
    register(FAMILY, VERSION,
             desc="small-cap 核心 + 价格停滞(Lesmond zero-return-days)正交 tilt",
             config=config, data_scope=data_scope, metrics=metrics,
             status="在册", notes="diversifier 轨;phase1-3 验证;hit=False(OOS年化/2015压力回撤)靠组合边际入册",
             evidence=evidence, admission=admission, nine_gate=nine_gate)
    print("  ✓ 已写入台账", flush=True)


if __name__ == "__main__":
    main()
