"""Marginal contribution reports for host-scoped control artifacts."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .artifacts import ArtifactType, HostSpec


def _metrics(returns: pd.Series, detail: pd.DataFrame) -> dict[str, float]:
    r = returns.fillna(0.0)
    annual = float(r.mean() * 252)
    vol = float(r.std() * np.sqrt(252))
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min()) if len(cum) else 0.0
    return {
        "annual": annual,
        "vol": vol,
        "sharpe": annual / vol if vol > 0 else 0.0,
        "maxdd": maxdd,
        "turnover_annual": float(detail["turnover"].fillna(0.0).mean() * 252),
        "cost_annual": float(detail["cost"].fillna(0.0).mean() * 252),
    }


def _yearly(base_returns: pd.Series, controlled_returns: pd.Series) -> dict[str, dict[str, float]]:
    years = sorted(set(base_returns.index.year).intersection(set(controlled_returns.index.year)))
    out = {}
    for year in years:
        b = base_returns[base_returns.index.year == year].fillna(0.0)
        c = controlled_returns[controlled_returns.index.year == year].fillna(0.0)
        if len(b) < 20 or len(c) < 20:
            continue
        base_ret = float((1 + b).prod() - 1)
        controlled_ret = float((1 + c).prod() - 1)
        out[str(year)] = {
            "base_return": base_ret,
            "controlled_return": controlled_ret,
            "delta_return": controlled_ret - base_ret,
        }
    return out


@dataclass(frozen=True)
class MarginalReport:
    artifact_id: str
    artifact_type: ArtifactType
    host: HostSpec
    base: dict[str, float]
    controlled: dict[str, float]
    summary: dict[str, float]
    yearly: dict[str, dict[str, float]]

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "host": self.host.to_dict(),
            "base": self.base,
            "controlled": self.controlled,
            "summary": self.summary,
            "yearly": self.yearly,
        }


def compute_marginal_report(
    *,
    base_returns: pd.Series,
    controlled_returns: pd.Series,
    base_detail: pd.DataFrame,
    controlled_detail: pd.DataFrame,
    artifact_id: str,
    host: HostSpec,
    artifact_type: ArtifactType = ArtifactType.VETO_FILTER,
) -> MarginalReport:
    base = _metrics(base_returns, base_detail)
    controlled = _metrics(controlled_returns, controlled_detail)
    summary = {
        "delta_annual": controlled["annual"] - base["annual"],
        "delta_maxdd": controlled["maxdd"] - base["maxdd"],
        "delta_sharpe": controlled["sharpe"] - base["sharpe"],
        "delta_turnover_annual": controlled["turnover_annual"] - base["turnover_annual"],
        "delta_cost_annual": controlled["cost_annual"] - base["cost_annual"],
    }
    return MarginalReport(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        host=host,
        base=base,
        controlled=controlled,
        summary={k: float(v) for k, v in summary.items()},
        yearly=_yearly(base_returns, controlled_returns),
    )
