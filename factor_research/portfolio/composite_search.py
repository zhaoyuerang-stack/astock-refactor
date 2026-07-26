"""Composite Allocation Search — 组合发现层(WS2, ADR-034)。

对**已验真的在册腿**搜「配权方法 × 腿子集」的组合配置,按对基线(risk_parity 全腿,
与 cross_asset 同口径)的边际 Δsharpe 排序、标 SHADOW 推荐,供人工复核。
发现 ≠ 晋级:晋级唯一通道仍是 `workflow/promote_composite.py`(9-Gate + Gate7B +
holdout)+ 人工确认部署(R-PROD-001)。

宪法守卫:
- **组合不得洗白**(R-EVIDENCE-001):腿池默认 = `run_active()` 已验真在册腿,
  本层不接受台账外腿(`legs` 注入仅供测试);每条腿的验真发生在入册前,组合层
  只做配权,不给不达标腿新名分。
- **§5.2 金库**:全部择优只在 <holdout boundary 段(截断 + `assert_search_clean`),
  Δsharpe 不含金库。
- **§5.1 诚实多重检验**:`len(configs)` 记入 trial 账本(scope="composite_search",
  函数内强制,扫了必记)。
- **小盘 reload 披露**:配置收益与小盘参考腿 corr>0.9 → WARN 且不推荐(披露口径:
  收益相关是弱判据,持仓重叠判据留作增强——见正交审计经验)。
"""
from __future__ import annotations

import pandas as pd

_SMALLCAP_RELOAD_CORR = 0.90  # 披露级:组合与小盘参考腿相关超此 = 疑似小盘 beta 重包装


def _clip_to_boundary(legs: dict[str, pd.Series]) -> dict[str, pd.Series]:
    from governance.holdout import boundary

    cut = boundary()
    return {k: v[v.index < cut].dropna() for k, v in legs.items()}


def _configs(legs: dict[str, pd.Series], regime_signal, defensive) -> list[dict]:
    """候选配置:全腿 × 可用方法 + leave-one-out × risk_parity(判拖累腿)。"""
    names = sorted(legs)
    out: list[dict] = [{"method": m, "legs": names} for m in ("equal_weight", "risk_parity")]
    if regime_signal is not None:
        out.append({"method": "regime_adaptive", "legs": names})
    if defensive:
        out.append({"method": "capped", "legs": names})
    if len(names) > 2:
        out.extend({"method": "risk_parity", "legs": [n for n in names if n != drop],
                    "dropped": drop} for drop in names)
    return out


def search_composite_allocations(
    legs: dict[str, pd.Series] | None = None,
    *,
    start: str = "2018-01-01",
    regime_signal: pd.Series | None = None,
    smallcap_ref: pd.Series | None = None,
    defensive: set | None = None,
    shadow_min_dsharpe: float = 0.05,
    ledger_path=None,
) -> dict:
    """返回 {baseline, configs(按 Δsharpe 降序), recommended, n_trials_recorded}。

    legs=None → `run_active()`(唯一正式腿源);注入仅测试。regime_signal(0/1,
    bull=1;须已 lag)启用 regime_adaptive 配权(WS6 路由落点)。
    """
    from governance.holdout import assert_search_clean
    from governance.trial_ledger import record_trials
    from portfolio.composer import compose
    from portfolio.composer import metrics as pm

    if legs is None:
        from portfolio.strategy_runners import run_active

        legs = run_active(start=start)
    legs = _clip_to_boundary(legs)
    legs = {k: v for k, v in legs.items() if len(v) >= 200}
    if len(legs) < 2:
        return {"baseline": None, "configs": [], "recommended": [],
                "note": "在册腿不足 2 条,组合搜索无意义"}

    base_ret, _ = compose(legs, method="risk_parity")
    assert_search_clean(base_ret.index, label="组合配置搜索")  # 自查门:择优数据越界即报错
    mb = pm(base_ret)

    if regime_signal is not None:
        regime_signal = regime_signal[regime_signal.index < max(base_ret.index) + pd.Timedelta(days=1)]

    configs = _configs(legs, regime_signal, defensive)
    # §5.1:best-of-k 配置择优是多重检验,函数内强制记账(扫了必记)
    n_recorded = record_trials("composite_search", len(configs),
                               context="composite allocation sweep", path=ledger_path)

    rows: list[dict] = []
    for cfg in configs:
        sub = {n: legs[n] for n in cfg["legs"]}
        try:
            ret, _ = compose(sub, method=cfg["method"], regime_signal=regime_signal,
                             defensive=defensive, cap=0.30)
        except Exception as e:
            rows.append({**cfg, "error": f"{type(e).__name__}"})
            continue
        m = pm(ret)
        sc_corr = None
        if smallcap_ref is not None:
            aligned = smallcap_ref.reindex(ret.index).dropna()
            if len(aligned) >= 200:
                sc_corr = round(float(ret.reindex(aligned.index).corr(aligned)), 3)
        reload_warn = sc_corr is not None and sc_corr > _SMALLCAP_RELOAD_CORR
        d_sharpe = round(m["sharpe"] - mb["sharpe"], 3)
        rows.append({
            **cfg,
            "sharpe": round(m["sharpe"], 3), "annual": round(m["annual"], 4),
            "maxdd": round(m["maxdd"], 4), "calmar": round(m["calmar"], 3),
            "d_sharpe": d_sharpe,
            "d_calmar": round(m["calmar"] - mb["calmar"], 3),
            "d_maxdd": round(m["maxdd"] - mb["maxdd"], 4),
            "smallcap_corr": sc_corr,
            "smallcap_reload": bool(reload_warn),
            "shadow_recommend": bool(d_sharpe >= shadow_min_dsharpe and not reload_warn),
        })

    rows.sort(key=lambda r: r.get("d_sharpe", -9e9), reverse=True)
    return {
        "baseline": {**mb, "method": "risk_parity", "legs": sorted(legs)},
        "configs": rows,
        "recommended": [r for r in rows if r.get("shadow_recommend")],
        "n_trials_recorded": n_recorded,
        "note": ("发现层:SHADOW 推荐仅供人工复核;晋级唯一通道 = workflow/promote_composite.py"
                 "(9-Gate+Gate7B+holdout)+ 人工确认(R-PROD-001)"),
    }
