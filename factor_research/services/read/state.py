"""状态层只读视图:数据质量 / 策略健康 / 持仓状态。

数据源(只读 JSON 产物,不触发重算):
- data_lake/quality_report.json  ← validate_final.py
- reports/factor_health.json     ← scripts/ops/generate_factor_health.py
- signals/state.json             ← run_daily

铁律#7:区分真问题(负价/OHLC错)与 A股正常现象(跳变>50% 多为除权/涨跌停)。
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.views import DataQualityView, FactorHealthView, MarketStateView

ROOT = Path(__file__).resolve().parents[2]  # factor_research/


def _read_json(rel: str):
    p = ROOT / rel
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def data_quality(with_duckdb: bool = True) -> DataQualityView:
    d = _read_json("data_lake/quality_report.json") or {}
    triage = _read_json("reports/data/data_issue_triage.json") or {}
    triage_summary = triage.get("summary") or {}
    breakdown = d.get("issue_breakdown", {}) or {}
    # 真问题:键含 负价格 / OHLC;正常现象:跳变
    severe = sum(c for k, c in breakdown.items() if ("负价" in k or "OHLC" in k))
    jump = sum(c for k, c in breakdown.items() if "跳变" in k)
    if severe > 50:
        verdict = "不建议回测"
    elif severe > 0:
        verdict = "关注"
    else:
        verdict = "可用"
    flagged = d.get("flagged", []) or []
    view = DataQualityView(
        total=d.get("total", 0),
        clean=d.get("clean", 0),
        clean_ratio=d.get("clean_ratio", 0.0),
        issue_breakdown=breakdown,
        n_flagged=len(flagged),
        flagged_sample=flagged[:20],
        severe_count=severe,
        jump_count=jump,
        verdict=verdict,
        triage_summary=triage_summary,
        production_blocked=bool(triage_summary.get("production_blocked")),
        backtest_blocked=bool(triage_summary.get("backtest_blocked")),
    )
    if with_duckdb:
        view.duckdb = _duckdb_scan()
    return view


def _duckdb_scan() -> dict:
    """DuckDB 即席复核 daily_all.parquet(读现有 parquet,不动防未来加载器)。

    优雅降级:duckdb 未安装则返回 available=False。
    """
    parquet = ROOT / "data_lake" / "price" / "daily_all.parquet"
    try:
        import duckdb  # 受控接缝:仅即席 QA 用
    except ImportError:
        return {"available": False, "note": "duckdb 未安装(pip install duckdb)"}
    if not parquet.exists():
        return {"available": False, "note": "daily_all.parquet 不存在"}
    from lake.cleaning import load_quarantine, quarantine_sql_not_clause
    not_clause = quarantine_sql_not_clause()   # 排除已隔离区间 → 只扫"实际服务"的数据
    q = f"""
        SELECT COUNT(*) AS rows,
               COUNT(DISTINCT code) AS codes,
               MIN(date) AS d0, MAX(date) AS d1,
               SUM(CASE WHEN close <= 0 THEN 1 ELSE 0 END) AS nonpos_close
        FROM read_parquet('{parquet.as_posix()}')
        WHERE {not_clause}
    """
    try:
        con = duckdb.connect()
        row = con.execute(q).fetchone()
        con.close()
        return {
            "available": True,
            "rows": int(row[0]),
            "codes": int(row[1]),
            "date_range": f"{row[2]}~{row[3]}",
            "nonpositive_close": int(row[4]),
            "quarantined_ranges": len(load_quarantine()),
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "note": f"扫描失败:{e}"}


def strategy_health() -> list[FactorHealthView]:
    d = _read_json("reports/factor_health.json") or {}
    as_of = str(d.get("updated", ""))  # 报告数据截至日,透出供前端明示时效(周期生成,非实时)
    out: list[FactorHealthView] = []
    for name, m in d.items():
        if not isinstance(m, dict):
            continue  # skip "updated"
        out.append(FactorHealthView(
            name=name,
            sharpe=m.get("sharpe", 0.0),
            momentum_6m=m.get("momentum_6m", 0.0),
            trend=m.get("trend", ""),
            as_of=as_of,
        ))
    return out


def market_state() -> MarketStateView:
    d = _read_json("signals/state.json") or {}
    return MarketStateView(
        current_position=d.get("current_position", ""),
        last_action=d.get("last_action", ""),
        last_signal_date=d.get("last_signal_date"),
        last_rebalance_date=d.get("last_rebalance_date"),
        n_holdings=len(d.get("last_holdings", []) or []),
    )
