"""Regime-gated LIVE 模式(默认关)——小盘失宠时把 equity 仓位切到 large-cap-growth。

风险偏好选项,**不是免费午餐,默认关**:
- 信号:小盘 PureTrend MA16(既有、未拟合;in=受宠 / out=失宠),不引入可调参数。
- 行为:受宠期持 equity book(小盘+illiquidity),失宠期切 large-cap-growth v1.1。
- 实测(equity 子组合,2018-2026):年化 29.7→34.1%、夏普 1.88→2.01;**典型回撤更好**
  (中位 -3.4% vs -4.0%、水下天数 35% vs 40%),但**尾部更肥**(深回撤段 5 vs 1、
  最深 -19.5% 落 2018 熊市底,仍 <20% bar)。= +4.4pp 年化换更肥尾部的风险偏好选择。
- **给收益不给容量**:受宠期资金仍在小盘(~2千万 顶);容量来自纯 large-cap(弱 edge),不可兼得。
- 缺陷:equity 内轮动(小盘→大盘),broad 下跌时两头都跌不护跌;真护跌靠跨资产防御腿。

接口:`live_returns(regime_gated=False)` 默认走标准 capped 30% 组合;True 才跑 large-cap 切换。
"""
from __future__ import annotations

import pandas as pd

REGIME_GATED_DEFAULT = False  # 默认关:不改既有 LIVE 行为


def small_cap_regime(close: pd.DataFrame, amount: pd.DataFrame, ma: int = 16) -> pd.Series:
    """小盘受宠(1)/失宠(0)regime = PureTrend MA16(既有未拟合信号)。"""
    from factors.small_cap import small_cap_exposure_signal

    timing, _, _ = small_cap_exposure_signal(close, amount, ma)
    return (timing > 0).astype(int)


def apply_regime_gate(equity_ret: pd.Series, regime: pd.Series, large_cap_ret: pd.Series) -> pd.Series:
    """受宠(regime>0)→ equity_ret;失宠(regime==0)→ large_cap_ret。纯函数,对齐到交集。"""
    idx = equity_ret.index.intersection(large_cap_ret.index).intersection(regime.index)
    br = equity_ret.reindex(idx)
    lr = large_cap_ret.reindex(idx).fillna(0.0)
    r = regime.reindex(idx).fillna(1)
    return br.where(r > 0, lr)


def live_returns(start: str = "2018-01-01", *, regime_gated: bool = REGIME_GATED_DEFAULT,
                 cap: float = 0.30):
    """研究组合(基线)日收益。

    注意:这是研究编排的组合收益,不是生产事实源 ——「现在在跑什么」看 DeploymentManifest。
    regime_gated=False(默认):capped 30% 组合(研究目录 equity ACTIVE + 防御腿)。
    regime_gated=True:equity 子组合按小盘 regime 在 小盘↔large-cap 间切换,再与防御腿
      按 (1-cap)/cap 混合。仅此模式才跑 large-cap(较慢)。
    返回 (daily_returns, weights_or_meta)。
    """
    from portfolio.composer import compose
    from portfolio.strategy_runners import defensive_strategies, run_active

    book = run_active(start)
    defe = defensive_strategies()
    if not regime_gated:
        return compose(book, method="capped", defensive=defe, cap=cap)

    # regime-gated:equity 子组合切换 + 防御腿混合
    from strategies.large_cap import StrategyConfig, run_large_cap_strategy
    from strategies.small_cap import load_price_panels

    equity = {k: v for k, v in book.items() if k not in defe}
    defensive = {k: v for k, v in book.items() if k in defe}
    equity_ret, _ = compose(equity, method="equal_weight")
    close, _, amount = load_price_panels(start)
    regime = small_cap_regime(close, amount)
    lc = run_large_cap_strategy(StrategyConfig(w_cpv_max=0.5, version="v1.1"))["returns"]
    gated_equity = apply_regime_gate(equity_ret, regime, lc)

    defensive_ret, _ = compose(defensive, method="equal_weight") if len(defensive) >= 2 else (
        list(defensive.values())[0] if defensive else pd.Series(dtype=float), None)
    idx = gated_equity.index
    if len(defensive):
        defensive_ret = defensive_ret.reindex(idx).fillna(0.0)
        final = (1.0 - cap) * gated_equity + cap * defensive_ret
    else:
        final = gated_equity
    meta = {"mode": "regime_gated", "cap": cap, "regime": "small_cap PT-MA16"}
    return final.dropna(), meta
