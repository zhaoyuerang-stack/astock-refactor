"""Audit canonical price units and compact/per-stock consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import duckdb


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_price_units(
    lake_root: str | Path,
    *,
    start: str = "2010-01-01",
    price_path: str | Path | None = None,
    daily_dir: str | Path | None = None,
    output: str | Path | None = None,
) -> dict:
    lake_root = Path(lake_root)
    output = Path(output) if output is not None else None
    price = Path(price_path) if price_path is not None else lake_root / "price/daily_all.parquet"
    raw = lake_root / "price/daily_raw_all.parquet"
    daily_basic = lake_root / "daily_basic/daily_basic_all.parquet"
    moneyflow = lake_root / "moneyflow/moneyflow_all.parquet"
    daily_glob = (
        Path(daily_dir) / "*.parquet"
        if daily_dir is not None
        else lake_root / "price/daily/*.parquet"
    )
    for path in (price, raw, daily_basic, moneyflow):
        if not path.exists():
            raise FileNotFoundError(path)

    con = duckdb.connect()
    joined = f"""
    WITH
    px AS (
        SELECT date, code, volume, amount
        FROM read_parquet('{_sql_path(price)}')
        WHERE date >= DATE '{start}'
    ),
    raw AS (
        SELECT date, code, raw_close
        FROM read_parquet('{_sql_path(raw)}')
    ),
    db AS (
        SELECT
            strptime(trade_date, '%Y%m%d')::DATE AS date,
            split_part(ts_code, '.', 1) AS code,
            float_share * 100.0 * turnover_rate AS volume_check
        FROM read_parquet('{_sql_path(daily_basic)}')
    ),
    mf AS (
        SELECT
            strptime(trade_date, '%Y%m%d')::DATE AS date,
            split_part(ts_code, '.', 1) AS code
        FROM read_parquet('{_sql_path(moneyflow)}')
    )
    SELECT
        px.date,
        px.code,
        CASE
            WHEN starts_with(px.code, '688') OR starts_with(px.code, '689')
                THEN 'star'
            WHEN starts_with(px.code, '300') OR starts_with(px.code, '301')
                THEN 'chinext'
            ELSE 'main'
        END AS board,
        px.amount / nullif(px.volume * raw.raw_close, 0) AS amount_ratio,
        px.volume / nullif(db.volume_check, 0) AS volume_ratio,
        db.code IS NOT NULL AS has_daily_basic,
        mf.code IS NOT NULL AS has_moneyflow
    FROM px
    LEFT JOIN raw USING (date, code)
    LEFT JOIN db USING (date, code)
    LEFT JOIN mf USING (date, code)
    """
    daily = con.execute(
        f"""
        SELECT
            date,
            board,
            count(*) AS n,
            approx_quantile(amount_ratio, 0.05) AS amount_p05,
            approx_quantile(amount_ratio, 0.50) AS amount_median,
            approx_quantile(amount_ratio, 0.95) AS amount_p95,
            approx_quantile(volume_ratio, 0.05) AS volume_p05,
            approx_quantile(volume_ratio, 0.50) AS volume_median,
            approx_quantile(volume_ratio, 0.95) AS volume_p95
        FROM ({joined})
        WHERE amount_ratio > 0
        GROUP BY date, board
        ORDER BY date, board
        """
    ).fetchdf()
    daily_records = []
    bad_dates = set()
    for row in daily.to_dict("records"):
        rec = {
            key: (
                str(value.date())
                if key == "date"
                else None
                if value is None
                else float(value)
                if key not in {"board", "n"}
                else int(value)
                if key == "n"
                else value
            )
            for key, value in row.items()
        }
        amount_bad = not (0.90 <= rec["amount_median"] <= 1.10)
        volume_median = rec["volume_median"]
        volume_bad = (
            volume_median is not None
            and math.isfinite(volume_median)
            and not (0.90 <= volume_median <= 1.10)
        )
        if rec["n"] < 100:
            rec["status"] = "insufficient_sample"
            rec["passed"] = False
        else:
            rec["status"] = "passed" if not (amount_bad or volume_bad) else "failed"
            rec["passed"] = rec["status"] == "passed"
        if rec["status"] == "failed":
            bad_dates.add(rec["date"])
        daily_records.append(rec)

    coverage = con.execute(
        f"""
        SELECT
            count(*) AS price_rows,
            count(*) FILTER (WHERE has_daily_basic) AS daily_basic_rows,
            count(*) FILTER (WHERE has_moneyflow) AS moneyflow_rows,
            min(date) AS min_date,
            max(date) AS max_date
        FROM ({joined})
        """
    ).fetchone()

    per_stock = f"""
    SELECT
        date,
        regexp_extract(filename, '([^/]+)\\.parquet$', 1) AS code,
        volume,
        amount
    FROM read_parquet('{_sql_path(daily_glob)}', filename=true)
    WHERE date >= DATE '{start}'
    """
    consistency = con.execute(
        f"""
        WITH
        compact AS (
            SELECT date, code, volume, amount
            FROM read_parquet('{_sql_path(price)}')
            WHERE date >= DATE '{start}'
        ),
        stock AS ({per_stock})
        SELECT
            count(*) FILTER (WHERE compact.code IS NULL) AS only_per_stock,
            count(*) FILTER (WHERE stock.code IS NULL) AS only_compact,
            count(*) FILTER (
                WHERE compact.code IS NOT NULL
                  AND stock.code IS NOT NULL
                  AND (
                      abs(compact.volume - stock.volume)
                          > greatest(1.0, abs(compact.volume) * 0.000001)
                      OR abs(compact.amount - stock.amount)
                          > greatest(1.0, abs(compact.amount) * 0.000001)
                  )
            ) AS value_mismatches
        FROM compact
        FULL OUTER JOIN stock USING (date, code)
        """
    ).fetchone()
    con.close()

    first_bad = min(bad_dates) if bad_dates else None
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start": start,
        "min_date": str(coverage[3]),
        "max_date": str(coverage[4]),
        "price_rows": int(coverage[0]),
        "source_coverage": {
            "daily_basic_rows": int(coverage[1]),
            "moneyflow_rows": int(coverage[2]),
        },
        "first_bad_date": first_bad,
        "bad_date_count": len(bad_dates),
        "daily_board_stats": daily_records,
        "compact_per_stock_consistency": {
            "only_per_stock": int(consistency[0]),
            "only_compact": int(consistency[1]),
            "value_mismatches": int(consistency[2]),
            "passed": not any(int(x) for x in consistency),
        },
        "daily_all_sha256": _sha256(price),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-root", default="data_lake")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--price-path")
    parser.add_argument("--daily-dir")
    parser.add_argument("--output", default="reports/data/price_unit_audit.json")
    args = parser.parse_args()
    report = audit_price_units(
        args.lake_root,
        start=args.start,
        price_path=args.price_path,
        daily_dir=args.daily_dir,
        output=args.output,
    )
    print(
        json.dumps(
            {
                "first_bad_date": report["first_bad_date"],
                "bad_date_count": report["bad_date_count"],
                "source_coverage": report["source_coverage"],
                "consistency": report["compact_per_stock_consistency"],
                "output": args.output,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
