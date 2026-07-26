"""pledge_stat 信号源覆盖率与正交性体检。

只做数据/暴露诊断,不判断 alpha 有效性,不登记策略。
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factors.pledge import load_pledge_risk_signals  # noqa: E402
from lake.load_lake import load_pledge_stat_panel  # noqa: E402


def _board(code: str) -> str:
    if code.startswith("688"):
        return "STAR_688"
    if code.startswith("300"):
        return "CHINEXT_300"
    if code.startswith(("000", "001", "002", "003")):
        return "SZ_MAIN"
    if code.startswith("60"):
        return "SH_MAIN"
    if code.startswith(("4", "8", "9")):
        return "BSE_OR_OTHER"
    return "OTHER"


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    common = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(common) < 50:
        return None
    return float(common.iloc[:, 0].corr(common.iloc[:, 1], method="spearman"))


def _latest_daily_basic(latest: str) -> pd.DataFrame:
    cols = ["ts_code", "trade_date", "total_mv", "circ_mv", "turnover_rate", "pb"]
    db = pd.read_parquet("data_lake/daily_basic/daily_basic_all.parquet", columns=cols)
    db = db[db["trade_date"].astype(str) == latest].copy()
    db["code"] = db["ts_code"].str.split(".").str[0]
    db["board"] = db["code"].map(_board)
    return db.set_index("code")


def main():
    cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")
    trade_idx = pd.DatetimeIndex(pd.to_datetime(cal["date"]).sort_values())
    latest = trade_idx.max().strftime("%Y%m%d")
    trade_idx = trade_idx[-160:]

    latest_db = _latest_daily_basic(latest)
    codes = list(latest_db.index)
    pledge = load_pledge_stat_panel(trade_idx, codes=codes, max_stale_days=30)
    signals = load_pledge_risk_signals(trade_idx, codes=codes, max_stale_days=30)

    last_day = trade_idx[-1]
    state = pledge["pledge_coverage_state"].loc[last_day]
    ratio = pledge["pledge_ratio"].loc[last_day]
    stale_days = pledge["pledge_stale_days"].loc[last_day]
    high = signals["pledge_high_risk"].loc[last_day]
    worsen4 = signals["pledge_worsening_4w"].loc[last_day]
    improve4 = signals["pledge_improvement_4w"].loc[last_day]

    state_counts = state.value_counts(dropna=False).to_dict()
    board = latest_db.join(state.rename("state")).groupby(["board", "state"]).size().unstack(fill_value=0)
    board["universe"] = board.sum(axis=1)
    for col in ["current", "stale", "never_seen", "unknown"]:
        if col not in board:
            board[col] = 0
        board[f"{col}_rate"] = board[col] / board["universe"]

    covariates = pd.DataFrame({
        "pledge_ratio": ratio,
        "pledge_high_risk": high,
        "pledge_stale_days": stale_days,
        "log_total_mv": np.log(latest_db["total_mv"] + 1.0),
        "log_circ_mv": np.log(latest_db["circ_mv"] + 1.0),
        "turnover_rate": latest_db["turnover_rate"],
        "pb": latest_db["pb"],
    })
    corr = {
        "ratio_vs_log_total_mv": _spearman(covariates["pledge_ratio"], covariates["log_total_mv"]),
        "ratio_vs_log_circ_mv": _spearman(covariates["pledge_ratio"], covariates["log_circ_mv"]),
        "ratio_vs_turnover_rate": _spearman(covariates["pledge_ratio"], covariates["turnover_rate"]),
        "ratio_vs_pb": _spearman(covariates["pledge_ratio"], covariates["pb"]),
        "high_risk_vs_log_total_mv": _spearman(covariates["pledge_high_risk"], covariates["log_total_mv"]),
        "stale_days_vs_log_total_mv": _spearman(covariates["pledge_stale_days"], covariates["log_total_mv"]),
    }

    current = state.eq("current")
    summary = {
        "asof": latest,
        "universe": int(len(codes)),
        "coverage_state_counts": {str(k): int(v) for k, v in state_counts.items()},
        "current_rate": float(current.mean()),
        "high_risk_current_count": int(high.eq(1.0).sum()),
        "worsening_4w_count": int(worsen4.eq(1.0).sum()),
        "improvement_4w_count": int(improve4.eq(1.0).sum()),
        "current_ratio_median": float(ratio[current].median()),
        "current_ratio_p95": float(ratio[current].quantile(0.95)),
        "spearman_exposure": corr,
        "board_coverage": board.reset_index().to_dict(orient="records"),
    }

    out = Path("scratch/pledge_signal_source_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print("=" * 88)
    print(f"pledge_stat signal source probe asof={latest} universe={len(codes)}")
    print("coverage:", summary["coverage_state_counts"])
    print(f"current_rate={summary['current_rate']:.2%} high_risk_current={summary['high_risk_current_count']}")
    print(f"worsening_4w={summary['worsening_4w_count']} improvement_4w={summary['improvement_4w_count']}")
    print(f"current ratio median={summary['current_ratio_median']:.2f} p95={summary['current_ratio_p95']:.2f}")
    print("\nSpearman exposure checks:")
    for k, v in corr.items():
        print(f"  {k}: {v if v is None else round(v, 4)}")
    print("\nBoard coverage:")
    cols = ["universe", "current", "stale", "never_seen", "current_rate", "stale_rate", "never_seen_rate"]
    print(board[cols].sort_values("universe", ascending=False).round(4).to_string())
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
