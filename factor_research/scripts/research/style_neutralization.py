"""CNE6 风格中性化审计:任意 alpha 剥掉 Barra 风格暴露后还剩多少特质 alpha。

借 Barra CNE6 中性化机制(1 国家 + 风格 + 行业),复用 research_toolkit.Alpha Audit
(NW 重叠校正 + RidgeCV 联合增量 + 置换);结论本地重算(借机制不照搬)。

**诚实边界**:数据湖无总市值/股本 → 建不了干净 Barra Size,只能用 -log(amount) 作
'规模-流动性'代理(与 small_cap 同源)。故 small_cap 对它的中性化半定义性(量化非证伪);
illiquidity/momentum 对全风格块(含基本面)的中性化是干净的。

复用:审任何在册因子 → 改 `candidates` 字典一行即可。

Run:
    cd factor_research && python3 scripts/research/style_neutralization.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

START, HORIZON = "2018-01-01", 20


def build_cne6_styles(close: pd.DataFrame, amount: pd.DataFrame,
                      total_mv: pd.DataFrame = None,
                      turnover: pd.DataFrame = None) -> dict[str, pd.DataFrame]:
    """本地可建的 CNE6 风格子集(全防未来、横截面 z-score)。

    Size=ln(total_mv)[**真市值,独立于 amount**;无则退 -log(amount60) 代理],
    Liquidity=turnover_rate[真换手;无则退 -log(amount)],
    Beta(60d vs 等权市场), Momentum(250-skip20), ResidVol(60d),
    Value(BP/EP), Growth(营收+利润 yoy), Quality(ROE+毛利)。
    传 total_mv 后 Size 与 small_cap(-log amount)解耦,中性化不再半定义性。
    """
    from factors.fundamental import (
        bp_proxy,
        ep_proxy,
        gross_margin,
        net_profit_yoy,
        revenue_yoy,
        roe,
    )
    from factors.momentum import mom_n, volatility
    from factors.utils import mad_clip, safe_zscore
    Z = lambda p: safe_zscore(mad_clip(p))
    ret = close.pct_change(fill_method=None)
    mkt = ret.mean(axis=1)
    beta = ret.rolling(60).cov(mkt).div(mkt.rolling(60).var(), axis=0)

    if total_mv is not None:
        size = Z(np.log(total_mv.reindex_like(close).replace(0, np.nan)))   # 真 Barra Size
    else:
        size = Z(-np.log(amount.rolling(60).mean().replace(0, np.nan)))     # 退代理(与 small_cap 同源)
    if turnover is not None:
        liq = Z(turnover.reindex_like(close))                              # 真换手
    else:
        liq = Z(-np.log(amount.rolling(60).mean().replace(0, np.nan)))

    return {
        "Size":     size,
        "Liquidity": liq,
        "Beta":     Z(beta),
        "Momentum": Z(mom_n(close, 250, skip=20)),
        "ResidVol": Z(volatility(close, 60)),
        "Value_BP": Z(bp_proxy(close)),
        "Value_EP": Z(ep_proxy(close)),
        "Growth":   Z(0.5 * revenue_yoy(close) + 0.5 * net_profit_yoy(close)),
        "Quality":  Z(0.5 * roe(close) + 0.5 * gross_margin(close)),
    }


def style_loadings(alpha: pd.DataFrame, styles: dict[str, pd.DataFrame],
                   dates, top_n: int = 4) -> list[tuple[str, float]]:
    """alpha 对各风格的平均横截面相关(稳健,避开多重共线 beta 的符号抵消)。"""
    cors = {}
    for k, s in styles.items():
        cs = []
        for d in dates:
            if d in alpha.index and d in s.index:
                df = pd.DataFrame({"a": alpha.loc[d], "s": s.loc[d]}).dropna()
                if len(df) > 100:
                    cs.append(df["a"].corr(df["s"]))
        cors[k] = float(np.mean(cs)) if cs else 0.0
    return sorted(cors.items(), key=lambda x: -abs(x[1]))[:top_n]


def audit_style_neutral(candidates: dict[str, pd.DataFrame], styles: dict, fwd,
                        close, *, horizon: int = HORIZON):
    """对每个 candidate:独立 NW-ICIR、风格后真增量、R²、判决、主风格暴露。"""
    from research_toolkit import audit_factor, corrected_icir
    dates = close.index[::20]
    rows = []
    for name, a in candidates.items():
        ci = corrected_icir(a, fwd, horizon=horizon)
        rep = audit_factor(a, fwd, styles, candidate_id=name, horizon=horizon)
        # R²: 逐日 OLS alpha~styles 的平均
        keys = list(styles); r2s = []
        for d in dates:
            if d not in a.index:
                continue
            df = pd.DataFrame({k: styles[k].loc[d] for k in keys if d in styles[k].index})
            df["y"] = a.loc[d]; df = df.dropna()
            if len(df) < 100:
                continue
            X = np.column_stack([np.ones(len(df)), df[keys].values])
            b, *_ = np.linalg.lstsq(X, df["y"].values, rcond=None)
            yhat = X @ b; y = df["y"].values
            r2s.append(1 - ((y - yhat) ** 2).sum() / ((y - y.mean()) ** 2).sum())
        rows.append({
            "name": name, "nw_icir": ci["nw_icir"], "true_inc": rep.true_increment,
            "r2": float(np.mean(r2s)) if r2s else float("nan"),
            "verdict": rep.verdict.value, "loadings": style_loadings(a, styles, dates),
        })
    return rows


def main():
    from factors.momentum import mom_n
    from factors.small_cap import small_cap_factor
    from factors.utils import mad_clip, safe_zscore
    from lake.load_lake import load_daily_basic_panel
    from services.actions.autoresearch import _load_validation_data

    close, volume, amount, _ = _load_validation_data(START)
    fwd = close.pct_change(HORIZON, fill_method=None).shift(-HORIZON)
    db = load_daily_basic_panel(close.index, fields=["total_mv", "turnover_rate"])  # 真市值/换手
    styles = build_cne6_styles(close, amount, total_mv=db["total_mv"], turnover=db["turnover_rate"])
    ret = close.pct_change(fill_method=None)
    candidates = {
        "small_cap":   small_cap_factor(amount, 60),
        "illiquidity": safe_zscore(mad_clip((ret.abs() / (amount.replace(0, np.nan) + 1)).rolling(20).mean())),
        "momentum60":  mom_n(close, 60),
    }

    print("=== CNE6 风格中性化审计(2018-2026, horizon=20)===\n")
    print(f"{'alpha':<13}{'独立NW-ICIR':>11}{'风格后真增量':>13}{'R²':>7}  判决")
    for r in audit_style_neutral(candidates, styles, fwd, close):
        top = ", ".join(f"{k}{v:+.2f}" for k, v in r["loadings"][:3])
        print(f"{r['name']:<13}{r['nw_icir']:>+11.3f}{r['true_inc']:>+13.4f}{r['r2']:>6.0%}   {r['verdict'].upper()}")
        print(f"             主风格暴露: {top}")
    print("\n注: Size=ln(total_mv) 真市值,独立于 small_cap(-log amount)→ 中性化已非半定义性,可证伪。")


if __name__ == "__main__":
    main()
