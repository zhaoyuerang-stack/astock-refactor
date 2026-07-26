"""衰减 / 遗忘监控(LOOP_ENGINEERING.md §5.4)。

alpha 默认会失效(项目铁律)。在册策略须主动复测 + 退役,而非只进不退。
对接母策略 decay_signal:滚动 3 年夏普 <0.5 / 因子 Rank IC 连续 4 季 <0 → 触发退役复核。

口径全透明:只用日收益(+可选季度 IC 序列)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROLL_SHARPE_FLOOR = 0.5   # 滚动 3 年夏普低于此 → 衰减
IC_NEG_QUARTERS = 4       # 连续 N 季 IC<0 → 衰减


def rolling_3y_sharpe(returns: pd.Series, window_days: int = 756) -> pd.Series:
    r = pd.Series(returns).dropna()
    mu = r.rolling(window_days).mean() * 252
    sd = r.rolling(window_days).std() * np.sqrt(252)
    return (mu / sd).replace([np.inf, -np.inf], np.nan)


def decay_check(returns: pd.Series, ic_quarterly: pd.Series | None = None) -> dict:
    """查在册策略是否触发衰减信号。返回 {decayed, reasons, 最新滚动夏普, 连续负IC季数}。"""
    reasons = []
    r = pd.Series(returns).dropna()
    out = {"n_days": int(len(r))}

    roll = rolling_3y_sharpe(r)
    latest = roll.dropna()
    latest_sh = float(latest.iloc[-1]) if len(latest) else None
    out["rolling_3y_sharpe_latest"] = round(latest_sh, 3) if latest_sh is not None else None
    if latest_sh is not None and latest_sh < ROLL_SHARPE_FLOOR:
        reasons.append(f"滚动3年夏普 {latest_sh:.2f} < {ROLL_SHARPE_FLOOR}")

    neg_streak = 0
    if ic_quarterly is not None and len(ic_quarterly.dropna()):
        icq = ic_quarterly.dropna()
        for v in reversed(list(icq)):
            if v < 0:
                neg_streak += 1
            else:
                break
        out["ic_neg_streak_quarters"] = neg_streak
        if neg_streak >= IC_NEG_QUARTERS:
            reasons.append(f"Rank IC 连续 {neg_streak} 季 <0")

    out["decayed"] = bool(reasons)
    out["reasons"] = reasons
    out["action"] = "触发退役复核(workflow 标退役,非删除)" if reasons else "健康,继续持有"
    return out
