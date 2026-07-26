"""跨资产防御腿发现(组合层常规发现流程的一环)。

在统一**边际透镜**下搜索 {ETF × 趋势窗口},按对在册 ACTIVE 组合的边际贡献
(Δsharpe)排序——**不按单独 Sharpe**(防御腿单独 Sharpe 可低但边际价值高)。
与 AutoResearch 的截面选股 DSL 是不同 artifact;统一层是目标函数,不是 DSL 白名单。

reusable 契约:search_cross_asset_legs() 返回排序后的候选腿(含 shadow_recommend),
供 portfolio_cli --discover-legs 和 scripts/research/cross_asset_leg_search 共用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ETFS = {"511010": "国债", "518880": "黄金", "159920": "恒生", "510880": "红利", "513100": "纳指"}
MA_GRID = [20, 40, 60, 120, 240]


def _signed_max_corr(cand: pd.Series, refs: list[pd.Series]) -> float:
    """候选与各在册腿的有符号最大相关(与 AutoResearch 边际项同义,此处自包含)。"""
    best = None
    for rr in refs:
        a, b = cand.align(rr, join="inner")
        m = a.notna() & b.notna()
        a, b = a[m], b[m]
        if len(a) < 5 or a.std() == 0 or b.std() == 0:
            continue
        c = float(a.corr(b))
        if c == c:
            best = c if best is None else max(best, c)
    return best if best is not None else 0.0


def search_cross_asset_legs(
    start: str = "2018-01-01",
    *,
    shadow_min_dsharpe: float = 0.10,
    shadow_max_corr: float = 0.20,
) -> dict:
    """搜索跨资产防御腿,返回 {baseline, legs(按 Δsharpe 降序), recommended}。

    每条腿:单独 sharpe/ann/mdd + 对在册有符号相关 + 2018 逆风 + 崩盘日捕获 + Δsharpe/Δcalmar。
    shadow_recommend = Δsharpe≥阈值 且 对在册相关≤阈值(真分散且有边际)。
    """
    from governance.holdout import assert_search_clean, boundary  # §5.2 缝③:择优不得碰金库
    from portfolio.composer import compose
    from portfolio.composer import metrics as pm
    from portfolio.strategy_runners import _load_etf_close, _run_etf_trend, run_active

    HOLDOUT = boundary()
    a_ret = {k: v[v.index < HOLDOUT] for k, v in run_active(start=start).items()}
    book_returns = list(a_ret.values())
    book_eq = pd.DataFrame(a_ret).dropna().mean(axis=1)
    assert_search_clean(book_eq.index, label="跨资产防御腿搜索")  # 自查门:择优数据越界即报错
    worst = book_eq <= book_eq.quantile(0.20)
    base_rp, _ = compose(a_ret, method="risk_parity")
    mb = pm(base_rp)

    legs = []
    for code, name in ETFS.items():
        _load_etf_close(code)  # 校验数据存在;缺失则 _run_etf_trend 抛错被下方捕获
        for ma in MA_GRID:
            try:
                r = _run_etf_trend(code, ma=ma, start=start).dropna()
            except Exception:
                continue
            r = r[r.index < HOLDOUT]  # §5.2 缝③:腿收益也截到搜索窗,Δsharpe 不含金库
            if len(r) < 200:
                continue
            ann = float(r.mean() * 252)
            sh = ann / (r.std() * np.sqrt(252) + 1e-9)
            cum = (1 + r).cumprod()
            mdd = float((cum / cum.cummax() - 1).min())
            corr = _signed_max_corr(r, book_returns)
            combo_rp, _ = compose({**a_ret, f"ETF_{code}_{ma}": r}, method="risk_parity")
            mc = pm(combo_rp)
            d_sharpe = mc["sharpe"] - mb["sharpe"]
            legs.append({
                "code": code, "name": name, "ma": ma, "leg": f"{code}_{name}_MA{ma}",
                "standalone_sharpe": round(sh, 3), "ann": round(ann, 4), "mdd": round(mdd, 4),
                "corr_to_book": round(corr, 3),
                "ret_2018": round(float((1 + r[r.index.year == 2018]).prod() - 1), 4),
                "down_capture": round(float(r.reindex(book_eq.index)[worst].mean()), 6),
                "d_sharpe": round(d_sharpe, 3), "d_calmar": round(mc["calmar"] - mb["calmar"], 3),
                "passes_threshold": bool(d_sharpe >= shadow_min_dsharpe and corr <= shadow_max_corr),
                "shadow_recommend": False,  # 下方按每 ETF 最佳窗口去重后置位
            })

    legs.sort(key=lambda x: x["d_sharpe"], reverse=True)
    # SHADOW 推荐 = 每个 ETF 仅取过阈值的**最佳 Δsharpe 窗口**(不重复推荐同资产的近邻窗口)
    best_per_code: dict[str, dict] = {}
    for l in legs:
        if l["passes_threshold"] and l["code"] not in best_per_code:
            best_per_code[l["code"]] = l  # legs 已按 Δsharpe 降序,首个即该 ETF 最佳
    for l in best_per_code.values():
        l["shadow_recommend"] = True
    return {"baseline": mb, "legs": legs,
            "recommended": [l for l in legs if l["shadow_recommend"]]}
