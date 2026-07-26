"""把 holder_count_chg 单因子推过 canonical workflow(R-WF-001)到诚实 DSR。

单因子,不靠 blending。timing = house MA16 二值(ma_trend),无 holder 专属调参;
leverage=1.0(诚实无杠杆,与 executable_spec 无 leverage 字段一致)。

复现单一真相:phase1-3 的 callable builder 与 9-Gate 的 executable_spec 都解析到
strategies.catalog.build_holder_count_chg / build_ma_trend → 因子帧与 timing 逐位恒等
(advisor 盲点①:两路径必须产同一序列,否则 DSR 算在另一条序列上=证据作废)。

family id 全程同一串 = FAMILY(advisor 盲点②:register_family / executable_spec.family /
trial_ledger scope / _family_n_trials key 必须完全相等,否则 honest_n_trials→0→阻断)。

阶段:
  --phase123        只跑 phase1-3 看裸因子过不过防未来 + hit(便宜闸,零台账写入)
  (默认同 --phase123;DSR/注册阶段在确认 phase1-3 后再加)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


from core.engine import PricePanel  # noqa: E402
from strategies.catalog import build_holder_count_chg, build_ma_trend  # noqa: E402

# ── 全程同一身份串(盲点②)──
FAMILY = "holder-concentration"
VERSION = "v1.0"

# ── 单因子部署单元参数(house 标准,无 holder 专属调参)──
WINDOW = 60          # probe 实测最强窗口
TOP_N = 25
REBAL = 20
MA = 16              # house 定值二值择时,不按 holder 调 → 不进搜索自由度

FACTOR_PARAMS = {"type": "holder_count_chg", "window": WINDOW}
TIMING_PARAMS = {"type": "ma_trend", "ma": MA}

# leverage=1.0:不靠杠杆抬收益(诚实),且与在册 executable_spec 无 leverage 字段一致
CONFIG = {
    "top_n": TOP_N, "rebalance_days": REBAL, "leverage": 1.0,
    "buy_cost": 0.00225, "sell_cost": 0.00275, "financing_rate": 0.065,
}

HYPOTHESIS = ("股东户数环比减少 → 筹码集中(机构/大户吸筹)→ 未来正超额。"
              "与价量/规模/流动性簇正交的独立数据族(anndate PIT)。")


# ── FactorSpec adapter builders:委托 catalog(单一真相,与 9-Gate 同路径)──
def factor_builder(close, volume, amount, dates):
    return build_holder_count_chg(PricePanel(close=close, volume=volume, amount=amount),
                                  FACTOR_PARAMS)


def timing_builder(close, amount):
    t, _ = build_ma_trend(PricePanel(close=close, volume=close, amount=amount), TIMING_PARAMS)
    return t


def _seg(segments: dict, key: str) -> dict:
    s = segments.get(key) or {}
    return {"annual": s.get("annual"), "maxdd": s.get("maxdd"),
            "sharpe": s.get("sharpe"), "calmar": s.get("calmar")}


def run_phase123(warmup_start: str = "2010-01-01") -> dict:
    from engine.metrics import compute_hit
    from workflow.phase1_synthetic import Phase1Checker
    from workflow.phase2_backtest import Phase2Runner
    from workflow.phase3_wf import WF3Runner

    print(f"\n{'='*64}\n  holder_count_chg standalone → phase1-3 (family={FAMILY})\n{'='*64}",
          flush=True)
    print(f"  factor={FACTOR_PARAMS}  timing={TIMING_PARAMS}  cfg={CONFIG}", flush=True)

    # ── phase1 合成防未来审计 ──
    print("\n[phase1] 合成防未来审计...", flush=True)
    p1 = Phase1Checker(factor_builder, timing_builder, FAMILY, CONFIG).run_all(
        use_clean=True, save_lessons=False)
    fails = [r for r in p1 if getattr(r, "is_fail", False)]
    print(f"  → {'PASS' if not fails else 'FAIL ' + str([r.check_id for r in fails])}", flush=True)

    # ── phase2 三段回测(IS/OOS/压力)+ 成本敏感性 ──
    print("\n[phase2] 三段回测(IS/OOS/压力)...", flush=True)
    p2 = Phase2Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start=warmup_start)
    segs = p2.get("segments", {})
    for k in segs:
        s = _seg(segs, k)
        print(f"    {k:8s}: annual={s['annual']!s:>8} maxdd={s['maxdd']!s:>8} "
              f"sharpe={s['sharpe']!s:>6}", flush=True)
    cs = p2.get("cost_sensitivity", {})
    print(f"    cost_sens: {cs.get('verdict','?')} decay={cs.get('decay_pct')}", flush=True)
    corr = p2.get("correlation", {})
    print(f"    corr: {corr.get('verdict','?')} {corr.get('detail','')[:80]}", flush=True)

    # ── phase3 walk-forward ──
    print("\n[phase3] walk-forward...", flush=True)
    p3 = WF3Runner(factor_builder, timing_builder, FAMILY, CONFIG).run(warmup_start=warmup_start)
    agg = p3.get("aggregate", {})

    # ── hit 判定(走唯一权威 compute_hit;phase4 用 WF aggregate 口径)──
    wf_hit = compute_hit(agg.get("annual"), agg.get("maxdd"))
    oos = _seg(segs, "oos")
    oos_hit = (compute_hit(oos["annual"], oos["maxdd"])
               if oos["annual"] is not None and oos["maxdd"] is not None else None)

    print(f"\n{'='*64}", flush=True)
    print(f"  phase1 防未来: {'PASS' if not fails else 'FAIL'}", flush=True)
    print(f"  WF aggregate : annual={agg.get('annual')} maxdd={agg.get('maxdd')} "
          f"sharpe={agg.get('sharpe')} verdict={agg.get('verdict')}", flush=True)
    print(f"  OOS segment  : annual={oos['annual']} maxdd={oos['maxdd']}", flush=True)
    print(f"  hit(WF)={wf_hit}  hit(OOS)={oos_hit}", flush=True)
    print(f"  → 裸因子{'够到 standalone hit,值得投入 DSR 阶段' if wf_hit else '未达 hit,standalone 轨够不到'}",
          flush=True)
    print(f"{'='*64}", flush=True)

    return {"phase1_pass": not fails, "phase2": p2, "phase3": p3,
            "wf_hit": wf_hit, "oos_hit": oos_hit}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--warmup", default="2010-01-01")
    ap.add_argument("--phase123", action="store_true", help="只跑 phase1-3(默认)")
    args = ap.parse_args()
    run_phase123(warmup_start=args.warmup)
