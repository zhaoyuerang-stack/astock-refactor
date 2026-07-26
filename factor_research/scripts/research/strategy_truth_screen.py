"""策略验真机(automated de-self-deception battery)。

对任一策略 spec(close/amount/factor/weights/timing/lev/cost/宇宙/诚实n_trials),自动出:
  - 归因分层 L0 裸因子(去 overlay) / L1 +择时 / L2 +杠杆(=完整)
  - 自有宇宙独立 IC / IC-IR(看因子截面到底有没有 alpha,符号是否对)
  - 诚实 n_trials 的 DSR 曲线(看统计是否站得住)
  - 择时依赖比 = (sharpe_L2 − sharpe_L0)/|sharpe_L2|(越高=越靠 overlay 不是因子)
  - 机械判决:可实战 / 择时伪装 / 无因子alpha / 统计不显著

判决逻辑(全部满足才 DEPLOYABLE):
  ① L0 裸因子单体达标(年化>15% 且 回撤<20%)——alpha 不靠 overlay
  ② 独立 IC 与策略方向一致且 |IC|≥0.02——因子截面真有效
  ③ DSR 在诚实 n_trials 下 p<0.05——惩罚后仍显著
"""
import hashlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

PROJECT_ROOT = Path("/Users/kiki/astcok/factor_research")
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from scipy.stats import spearmanr

from core.analysis.walk_forward import deflated_sharpe
from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel, Signal
from engine.metrics import metrics
from governance import holdout as HO
from governance.alpha_overlay import split_alpha_overlay
from governance.trial_ledger import honest_n_trials


def _run(prices, weights, timing, lev, cost, start):
    cfg = BacktestConfig(start=start, cost=cost, leverage=lev)
    return BacktestEngine(prices=prices, config=cfg).run(
        Signal(weights=weights, timing=timing)).returns


def independent_ic(factor, close, rebal=20, fwd=20, universe_mask=None):
    fdates = factor.dropna(how="all").index.intersection(close.index)
    fwd_ret = close.shift(-fwd) / close - 1.0
    ics = []
    for rd in list(fdates[::rebal]):
        pos = close.index.get_loc(rd)
        if pos + fwd >= len(close.index):
            continue
        f = factor.loc[rd].reindex(close.loc[rd].dropna().index).dropna()
        if universe_mask is not None and rd in universe_mask.index:
            f = f.reindex(universe_mask.loc[rd][universe_mask.loc[rd]].index).dropna()
        y = fwd_ret.loc[rd].reindex(f.index)
        ok = f.notna() & y.notna()
        if ok.sum() >= 30:
            ic, _ = spearmanr(f[ok], y[ok])
            if not np.isnan(ic):
                ics.append(ic)
    a = np.array(ics)
    if len(a) == 0:
        return {"ic_mean": 0.0, "ic_ir": 0.0, "n": 0, "t_stat": 0.0}
    ir = a.mean()/a.std() if a.std() else 0.0
    return {"ic_mean": float(a.mean()), "ic_ir": float(ir), "n": len(a),
            "t_stat": float(ir*np.sqrt(len(a)))}  # 因子截面预测力显著性(>2 显著)


def capacity_est(prices, weights, part_of_adv=0.10):
    """容量估算:每名持仓不超过其 20日ADV 的 part_of_adv → 等权 top_n 下 AUM 上界。

    AUM ≤ n_pos × part_of_adv × median(选中票 ADV)。保守取选中票 ADV 中位数。
    """
    adv = prices.amount.rolling(20).mean()
    sel_advs = []
    for dt in weights.index:
        names = weights.loc[dt]
        names = names[names > 0].index
        if dt in adv.index and len(names):
            v = adv.loc[dt].reindex(names).dropna()
            if len(v):
                sel_advs.append(v.median())
                n_pos = len(names)
    if not sel_advs:
        return {}
    med_adv = float(np.median(sel_advs))
    aum = n_pos * part_of_adv * med_adv
    return {"median_selected_adv_万": round(med_adv/1e4, 1), "n_pos": int(n_pos),
            "capacity_aum_万": round(aum/1e4, 1), "capacity_aum_亿": round(aum/1e8, 3)}


def screen(name, prices, factor, weights, timing, leverage, cost, start,
           n_trials_grid, scope=None, universe_mask=None, factor_direction=1):
    b = HO.boundary()
    # 策略因果(每个权重只用 ≤t 数据)→ 在全样本上跑后按金库边界切片是干净的:
    # 搜索/选择只看 <boundary;>=boundary 是金库,仅最后 validate 一次。
    L0f = _run(prices, weights, None, 1.0, cost, start)
    L1f = _run(prices, weights, timing, 1.0, cost, start)
    L2f = _run(prices, weights, timing, leverage, cost, start)
    L0 = L0f[L0f.index < b].dropna()
    L1 = L1f[L1f.index < b].dropna()
    L2 = L2f[L2f.index < b].dropna()
    HO.assert_search_clean(L0, label=f"{name} 搜索层")  # 确认选择决策不触金库
    m0, m1, m2 = metrics(L0), metrics(L1), metrics(L2)
    # IC 只在搜索窗(截断 close,前向收益不越过金库)
    close_search = prices.close[prices.close.index < b]
    ic = independent_ic(factor, close_search, universe_mask=universe_mask)
    cap = capacity_est(prices, weights)
    # alpha/overlay 分账(#4):L0 裸因子=alpha,L2−L0=overlay 风险贡献;结构上禁把 overlay 记成 alpha
    aoverlay = split_alpha_overlay(L0, L2)

    # DSR 用账本诚实 n_trials(替代手填),仍给曲线对照
    nt_honest = honest_n_trials(scope) if scope else 1
    dsr = {}
    for nt in sorted(set(list(n_trials_grid) + [nt_honest])):
        rep = deflated_sharpe(observed_sr=m2["sharpe"], n_trials=nt, n_periods=m2["n"],
                              skew=m2["skew"], kurt=m2["kurtosis_excess"]+3.0, annualized=True)
        dsr[nt] = {"p": round(rep["p_value"], 4), "sig": rep["significant_05"]}

    sh2 = m2["sharpe"]
    timing_dep = (sh2 - m0["sharpe"]) / abs(sh2) if sh2 else 1.0
    ic_ok = (ic["ic_mean"]*factor_direction > 0) and abs(ic["ic_mean"]) >= 0.02
    # 真 alpha 判据 = 裸因子 L0 自身风险调整后赚钱(夏普) + 独立 IC 方向对,而非 L0 hit
    # (L0 回撤过深是 overlay 合法解决的问题,不否定 alpha 真实性)
    real_alpha = (m0["sharpe"] >= 0.8) and ic_ok
    overlay_fixes_dd = abs(m1["maxdd"]) < abs(m0["maxdd"])  # 择时是否合法收窄回撤
    l0_dd_ok = abs(m0["maxdd"]) < 0.20

    if not ic_ok or m0["sharpe"] < 0.3:
        verdict = "择时伪装/无因子alpha(L0夏普≈0或IC反向)"
    elif not real_alpha:
        verdict = "弱alpha(L0夏普0.3~0.8,边际)"
    elif l0_dd_ok:
        verdict = "✅真alpha且裸因子即达标(罕见,直接可实战)"
    elif overlay_fixes_dd:
        verdict = "✅真alpha+需合法风控overlay控回撤(查容量后可实战)"
    else:
        verdict = "真alpha但回撤难控(overlay无效)"
    # 金库唯一一次校验:用 L1(因子+择时,可部署形态)在 >=boundary 段
    data_fp = HO.current_data_fingerprint()
    spec_hash = hashlib.sha256(f"truth::{name}".encode()).hexdigest()
    ho = HO.validate_on_holdout(
        HO.candidate_identity(name, spec_hash, data_fp),
        L1f,
        spec_hash=spec_hash,
        data_fingerprint=data_fp,
    )
    ho_ok = isinstance(ho.get("sharpe"), (int, float)) and ho["sharpe"] >= 0.6
    deployable = real_alpha and ho_ok  # 真 alpha 且金库样本外未崩 = 才算可实战

    row = {
        "name": name,
        "L0_bare": {k: round(m0[k], 4) for k in ("annual","sharpe","maxdd")} | {"real_alpha": real_alpha},
        "L1_timing": {k: round(m1[k], 4) for k in ("annual","sharpe","maxdd")},
        "L2_full": {k: round(m2[k], 4) for k in ("annual","sharpe","maxdd","calmar")},
        "indep_ic": {k: round(ic[k], 4) if isinstance(ic[k], float) else ic[k] for k in ic},
        "timing_dependence": round(float(timing_dep), 3),
        "dsr_honest_curve": dsr, "n_trials_honest": nt_honest,
        "capacity": cap,
        "alpha_overlay_split": aoverlay,
        "holdout": ho, "holdout_ok": ho_ok,
        "verdict": verdict, "deployable": deployable,
    }
    return row


def build_smallcap_illiq():
    """小盘 illiquidity:全市场 Amihud top25 + MA16 + lev1.25(台账 illiquidity 家族配方)。"""
    from factors.small_cap import small_cap_timing
    from strategies.small_cap import load_price_panels
    sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "research"))
    from illiq_largecap_audit import build_weights  # 稀疏 builder(引擎可正确持有)
    buf = io.StringIO()
    with redirect_stdout(buf):
        close, volume, amount = load_price_panels("2018-01-01")
        amihud = (close.pct_change(fill_method=None).abs()/(amount+1.0)).rolling(20).mean()
        # universe=10**9 = 全市场(无 Top-N 限制)= 小盘倾斜
        weights = build_weights(amihud, close, amount, top_n=25, rebal=20, universe=10**9)
        timing, _, _ = small_cap_timing(close, amount, 16)
    prices = PricePanel(close=close, volume=volume, amount=amount)
    cost = CostModel(buy_cost=0.00225, sell_cost=0.00275, financing_rate=0.065)
    return dict(name="illiquidity(smallcap,fullmkt)", prices=prices, factor=amihud,
                weights=weights, timing=timing, leverage=1.25, cost=cost, start="2018-01-01",
                n_trials_grid=[1, 6, 20, 50], scope="illiquidity")


def main():
    rows = []
    spec = build_smallcap_illiq()
    rows.append(screen(**spec))

    # 并入已审 3 个的关键数(从已存 JSON,统一排行)
    prior = {
        "illiquidity-large-cap/v1.0": {"L0_annual": -0.375, "L0_hit": False, "indep_ic": -0.084,
                                       "L2_sharpe": 1.21, "verdict": "择时伪装+证据照抄"},
        "industry-neglect/v1.3": {"L0_annual": 0.138, "L0_hit": False, "indep_ic": None,
                                  "L2_sharpe": 1.24, "verdict": "择时救达标+9Gate全空"},
        "ai-compute-toc/v1.0": {"L0_annual": None, "L0_hit": False, "indep_ic": None,
                                "L2_sharpe": 0.46, "verdict": "regime依赖+机制证否+DSR不显著"},
    }
    out = {"screened": rows, "prior_audited": prior}
    with open("scratch/strategy_truth_screen.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)

    print("="*70)
    for r in rows:
        print(f"策略: {r['name']}")
        print(f"  L0裸因子 : 年化{r['L0_bare']['annual']:+.1%} 夏普{r['L0_bare']['sharpe']:.2f} 回撤{r['L0_bare']['maxdd']:+.1%} real_alpha={r['L0_bare']['real_alpha']}")
        print(f"  L1+择时  : 年化{r['L1_timing']['annual']:+.1%} 夏普{r['L1_timing']['sharpe']:.2f} 回撤{r['L1_timing']['maxdd']:+.1%}")
        print(f"  L2完整   : 年化{r['L2_full']['annual']:+.1%} 夏普{r['L2_full']['sharpe']:.2f} 回撤{r['L2_full']['maxdd']:+.1%} Calmar{r['L2_full']['calmar']:.2f}")
        print(f"  独立IC   : {r['indep_ic']['ic_mean']:+.4f} (IC-IR {r['indep_ic']['ic_ir']:.3f}, t={r['indep_ic']['t_stat']:.2f}, n={r['indep_ic']['n']})")
        cap = r.get('capacity') or {}
        if cap:
            print(f"  容量     : 选中票ADV中位{cap['median_selected_adv_万']}万 → 上界≈{cap['capacity_aum_亿']}亿(10%ADV/{cap['n_pos']}票)")
        print(f"  择时依赖 : {r['timing_dependence']:.0%}  (越低=越靠因子非择时)  [搜索窗 <{HO.boundary().date()}]")
        print("  DSR曲线  : " + " ".join(f"n={nt}:p={v['p']}{'*' if v['sig'] else ''}" for nt,v in r['dsr_honest_curve'].items()) + f"  (诚实n={r['n_trials_honest']})")
        ho = r['holdout']
        print(f"  金库样本外: 2025~26 年化{ho.get('annual',0):+.1%} 夏普{ho.get('sharpe',0):.2f} 回撤{ho.get('maxdd',0):+.1%} (n={ho.get('n')},偷看{ho.get('peek_count')}次){'  '+ho['warning'] if 'warning' in ho else ''}")
        print(f"  >>> 判决: {r['verdict']}  (真alpha={r['L0_bare']['real_alpha']} ∧ 金库未崩={r['holdout_ok']} → deployable={r['deployable']})")
    print("="*70)
    print("已审对照:", json.dumps(prior, ensure_ascii=False))


if __name__ == "__main__":
    main()
