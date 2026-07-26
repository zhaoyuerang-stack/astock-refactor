"""WF验证器 — DSR + PBO 接入工厂 L3.

在工厂 L3 跑完基础 WF 后, 回答两个关键问题:
  1. DSR: 试了 M 个候选后找到的最好 Sharpe, 是真实信号还是多重比较噪音?
  2. PBO: 当前选出的最优参数组合, 在 WF 里还是最优吗?

用法:
  from factory.analysis.wf_validator import validate_candidates
  report = validate_candidates({candidate_name: daily_returns}, n_trials=74)
  print(report)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app_config.log import get_logger
from core.analysis.walk_forward import deflated_sharpe, pbo_cscv

logger = get_logger(__name__)


@dataclass
class WFValidationReport:
    """工厂级 WF 验证报告."""
    n_candidates: int           # 候选总数
    n_periods: int              # 回测天数
    best_name: str              # 最优候选名称
    best_sharpe: float          # 最优候选夏普(年化)
    best_annual: float
    best_maxdd: float

    # DSR
    dsr: float                  # Deflated Sharpe Ratio (>1 = 显著)
    dsr_p_value: float          # p-value (<0.05 = 显著)
    dsr_significant: bool
    e_max_sr: float             # 纯噪音下期望最大夏普(M次试验)

    # PBO
    pbo: float                  # Probability of Backtest Overfitting (<0.10 = 低风险)
    pbo_risk: str               # "low" | "moderate" | "high"
    is_best_mean_oos_rank: float  # IS最优候选在OOS中的平均排名

    # 总结
    verdict: str                # "通过" | "待验证" | "高风险"

    def summary(self) -> str:
        lines = [
            "WF Validation Report",
            f"  Candidates: {self.n_candidates}, days: {self.n_periods}",
            f"  Best: {self.best_name} (SR={self.best_sharpe:.2f}, ann={self.best_annual:+.1%})",
            f"  DSR: {self.dsr:.2f} (p={self.dsr_p_value:.3f}, {'sig' if self.dsr_significant else 'not sig'})",
            f"    E[max SR | H0] = {self.e_max_sr:.2f}  ← noise ceiling",
            f"  PBO: {self.pbo:.2f} (risk={self.pbo_risk})",
            f"    Best IS ranks #{self.is_best_mean_oos_rank:.0f} avg in OOS",
            f"  Verdict: {self.verdict}",
        ]
        return "\n".join(lines)


def validate_candidates(
    returns_dict: dict[str, pd.Series],
    top_n: int = 5,
    verbose: bool = True,
) -> WFValidationReport:
    """对工厂产出的候选做 DSR + PBO 验证.

    Args:
        returns_dict: {candidate_name: daily_returns}
        top_n: 取前 N 个最优候选做 PBO
        verbose: 打印进度

    Returns:
        WFValidationReport
    """
    M = len(returns_dict)
    if M < 2:
        return WFValidationReport(
            n_candidates=M, n_periods=0, best_name="?", best_sharpe=0,
            best_annual=0, best_maxdd=0, dsr=0, dsr_p_value=1,
            dsr_significant=False, e_max_sr=0, pbo=0, pbo_risk="insufficient",
            is_best_mean_oos_rank=0, verdict="候选不足",
        )

    # 找最优候选
    sharpes = {}
    for name, ret in returns_dict.items():
        r = ret.dropna()
        if len(r) < 100:
            continue
        ann = float(r.mean() * 252)
        vol = float(r.std() * np.sqrt(252))
        sharpes[name] = ann / vol if vol > 0 else 0.0

    best_name = max(sharpes, key=sharpes.get)
    best_sharpe = sharpes[best_name]
    best_ret = returns_dict[best_name].dropna()
    best_annual = float(best_ret.mean() * 252)
    best_maxdd = float(((1 + best_ret).cumprod() / (1 + best_ret).cumprod().cummax() - 1).min())
    n_periods = len(best_ret)

    # DSR
    dsr_result = deflated_sharpe(
        observed_sr=best_sharpe,
        n_trials=M,
        n_periods=n_periods,
    )

    # PBO (用前 top_n 个候选, 避免噪声候选干扰)
    top_candidates = dict(sorted(sharpes.items(), key=lambda x: x[1], reverse=True)[:top_n])
    pbo_result = pbo_cscv(
        {name: returns_dict[name] for name in top_candidates},
        n_splits=100,
    )

    # 判定
    dsr_ok = dsr_result["significant_05"]
    pbo_ok = pbo_result["risk_level"] in ("low",)
    if dsr_ok and pbo_ok:
        verdict = "✅ 通过 — DSR显著, PBO低风险"
    elif dsr_ok:
        verdict = "⚠️ 待验证 — DSR显著但PBO偏高, 需更多OOS证据"
    elif pbo_ok:
        verdict = "⚠️ 待验证 — PBO低风险但DSR不显著, 样本量不足?"
    else:
        verdict = "❌ 高风险 — DSR不显著, PBO偏高, 候选可能过拟合"

    report = WFValidationReport(
        n_candidates=M, n_periods=n_periods,
        best_name=best_name, best_sharpe=best_sharpe,
        best_annual=best_annual, best_maxdd=best_maxdd,
        dsr=dsr_result["dsr"], dsr_p_value=dsr_result["p_value"],
        dsr_significant=dsr_ok,
        e_max_sr=dsr_result["e_max_sr"],
        pbo=pbo_result["pbo"],
        pbo_risk=pbo_result["risk_level"],
        is_best_mean_oos_rank=pbo_result.get("mean_oos_rank", 0),
        verdict=verdict,
    )

    if verbose:
        logger.info(report.summary())

    return report
