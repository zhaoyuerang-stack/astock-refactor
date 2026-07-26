"""Dry-run-first rebuild of canonical price ``volume`` and ``amount`` fields.

Canonical units:
  - volume: shares
  - amount: CNY

Historical source facts:
  - Tencent rows through 2026-06-12 store main/ChiNext volume in lots and
    STAR volume in shares.
  - 2026-06-15 is a mixed transition day.
  - Local Tushare moneyflow covers nearly all history; total buy/sell buckets
    reconstruct actual traded amount in CNY.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import duckdb

LEGACY_END = "2026-06-12"
MIXED_DATE = "2026-06-15"
MARKER_REL = Path("governance/price_unit_rebuild.json")


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_completed(lake_root: Path) -> bool:
    marker = lake_root / MARKER_REL
    if not marker.exists():
        return False
    try:
        return json.loads(marker.read_text()).get("status") == "complete"
    except Exception:
        return False


def _corrected_query(lake_root: Path, *, already_rebuilt: bool) -> str:
    price = _sql_path(lake_root / "price/daily_all.parquet")
    raw = _sql_path(lake_root / "price/daily_raw_all.parquet")
    moneyflow = _sql_path(lake_root / "moneyflow/moneyflow_all.parquet")
    daily_basic = _sql_path(lake_root / "daily_basic/daily_basic_all.parquet")
    adj_factor = _sql_path(lake_root / "adj_factor/adj_factor_all.parquet")
    rebuilt = "TRUE" if already_rebuilt else "FALSE"
    return f"""
WITH
px AS (
    SELECT date, code, open, close, high, low, volume, amount
    FROM read_parquet('{price}')
),
raw AS (
    SELECT date, code, raw_open, raw_high, raw_low, raw_close
    FROM read_parquet('{raw}')
),
mf0 AS (
    SELECT
        strptime(trade_date, '%Y%m%d')::DATE AS date,
        split_part(ts_code, '.', 1) AS code,
        (
            coalesce(buy_sm_amount, 0)
            + coalesce(buy_md_amount, 0)
            + coalesce(buy_lg_amount, 0)
            + coalesce(buy_elg_amount, 0)
        ) * 10000.0 AS buy_amount,
        (
            coalesce(sell_sm_amount, 0)
            + coalesce(sell_md_amount, 0)
            + coalesce(sell_lg_amount, 0)
            + coalesce(sell_elg_amount, 0)
        ) * 10000.0 AS sell_amount
    FROM read_parquet('{moneyflow}')
),
db AS (
    SELECT
        strptime(trade_date, '%Y%m%d')::DATE AS date,
        split_part(ts_code, '.', 1) AS code,
        float_share * 100.0 * turnover_rate AS volume_check
    FROM read_parquet('{daily_basic}')
),
adj AS (
    SELECT
        strptime(trade_date, '%Y%m%d')::DATE AS date,
        split_part(ts_code, '.', 1) AS code,
        adj_factor
    FROM read_parquet('{adj_factor}')
),
joined AS (
    SELECT
        px.*,
        coalesce(raw.raw_open, px.open / nullif(adj.adj_factor, 0)) AS raw_open,
        coalesce(raw.raw_high, px.high / nullif(adj.adj_factor, 0)) AS raw_high,
        coalesce(raw.raw_low, px.low / nullif(adj.adj_factor, 0)) AS raw_low,
        coalesce(raw.raw_close, px.close / nullif(adj.adj_factor, 0)) AS raw_close,
        mf0.buy_amount,
        mf0.sell_amount,
        db.volume_check,
        px.amount / nullif(px.volume * raw.raw_close, 0) AS old_amount_ratio
    FROM px
    LEFT JOIN raw USING (date, code)
    LEFT JOIN mf0 USING (date, code)
    LEFT JOIN db USING (date, code)
    LEFT JOIN adj USING (date, code)
),
volumes AS (
    SELECT
        *,
        CASE
            WHEN volume_check > 0
                 AND volume / volume_check BETWEEN 0.5 AND 1.5
                THEN volume
            WHEN volume_check > 0
                 AND volume * 100.0 / volume_check BETWEEN 0.5 AND 1.5
                THEN volume * 100.0
            WHEN {rebuilt}
                THEN volume
            WHEN date <= DATE '{LEGACY_END}'
                 AND NOT (starts_with(code, '688') OR starts_with(code, '689'))
                THEN volume * 100.0
            WHEN date = DATE '{MIXED_DATE}'
                 AND old_amount_ratio NOT BETWEEN 0.5 AND 1.5
                 AND NOT (starts_with(code, '688') OR starts_with(code, '689'))
                THEN volume * 100.0
            ELSE volume
        END AS volume_final
    FROM joined
),
amount_candidates AS (
    SELECT
        *,
        CASE
            WHEN buy_amount > 0
                 AND buy_amount / nullif(volume_final, 0)
                     BETWEEN raw_low * 0.98 AND raw_high * 1.02
                THEN TRUE ELSE FALSE
        END AS buy_valid,
        CASE
            WHEN sell_amount > 0
                 AND sell_amount / nullif(volume_final, 0)
                     BETWEEN raw_low * 0.98 AND raw_high * 1.02
                THEN TRUE ELSE FALSE
        END AS sell_valid
    FROM volumes
),
corrected AS (
    SELECT
        date,
        open,
        close,
        high,
        low,
        volume_final AS volume,
        CASE
            WHEN buy_valid AND sell_valid THEN (buy_amount + sell_amount) / 2.0
            WHEN buy_valid THEN buy_amount
            WHEN sell_valid THEN sell_amount
            WHEN old_amount_ratio BETWEEN 0.5 AND 1.5
                 AND abs(volume_final - volume)
                     <= greatest(1.0, abs(volume) * 0.000001)
                 AND amount / nullif(volume_final, 0)
                     BETWEEN raw_low * 0.98 AND raw_high * 1.02
                THEN amount
            ELSE volume_final * raw_close
        END AS amount,
        code,
        volume AS volume_old,
        amount AS amount_old,
        raw_close,
        raw_low,
        raw_high,
        volume_check,
        CASE
            WHEN buy_valid AND sell_valid THEN 'moneyflow_average'
            WHEN buy_valid THEN 'moneyflow_buy'
            WHEN sell_valid THEN 'moneyflow_sell'
            WHEN old_amount_ratio BETWEEN 0.5 AND 1.5
                 AND abs(volume_final - volume)
                     <= greatest(1.0, abs(volume) * 0.000001)
                 AND amount / nullif(volume_final, 0)
                     BETWEEN raw_low * 0.98 AND raw_high * 1.02
                THEN 'existing_canonical'
            ELSE 'close_proxy'
        END AS amount_source
    FROM amount_candidates
)
SELECT * FROM corrected
"""


def _stats(con: duckdb.DuckDBPyConnection, query: str) -> dict:
    row = con.execute(
        f"""
        SELECT
            count(*) AS row_count,
            count(*) FILTER (
                WHERE abs(volume - volume_old)
                    > greatest(1.0, abs(volume_old) * 0.000001)
            ) AS volume_changes,
            count(*) FILTER (
                WHERE abs(amount - amount_old)
                    > greatest(1.0, abs(amount_old) * 0.000001)
            ) AS amount_changes,
            count(*) FILTER (WHERE volume_check IS NOT NULL) AS volume_check_rows,
            count(*) FILTER (WHERE amount_source LIKE 'moneyflow%') AS moneyflow_rows,
            count(*) FILTER (WHERE amount_source = 'existing_canonical') AS existing_rows,
            count(*) FILTER (WHERE amount_source = 'close_proxy') AS close_proxy_rows,
            count(*) FILTER (WHERE amount IS NULL OR volume IS NULL) AS unresolved_rows
        FROM ({query})
        """
    ).fetchone()
    keys = (
        "row_count",
        "volume_changes",
        "amount_changes",
        "volume_check_rows",
        "moneyflow_rows",
        "existing_rows",
        "close_proxy_rows",
        "unresolved_rows",
    )
    return {key: int(value) for key, value in zip(keys, row, strict=True)}


def _validation(con: duckdb.DuckDBPyConnection, query: str) -> dict:
    breaches = con.execute(
        f"""
        WITH rows AS (
            SELECT
                date,
                CASE
                    WHEN starts_with(code, '688') OR starts_with(code, '689')
                        THEN 'star'
                    WHEN starts_with(code, '300') OR starts_with(code, '301')
                        THEN 'chinext'
                    ELSE 'main'
                END AS board,
                amount / nullif(volume * raw_close, 0) AS amount_ratio,
                amount / nullif(volume, 0) AS implied_price,
                raw_low,
                raw_high,
                volume / nullif(volume_check, 0) AS volume_ratio
            FROM ({query})
        ),
        daily AS (
            SELECT
                date,
                board,
                count(*) FILTER (WHERE amount_ratio > 0) AS n,
                median(amount_ratio) FILTER (WHERE amount_ratio > 0) AS amount_median,
                quantile_cont(abs(amount_ratio - 1.0), 0.95)
                    FILTER (WHERE amount_ratio > 0) AS amount_p95_error,
                avg(
                    CASE
                        WHEN implied_price BETWEEN raw_low * 0.98 AND raw_high * 1.02
                            THEN 0.0
                        ELSE 1.0
                    END
                ) FILTER (
                    WHERE implied_price > 0 AND raw_low > 0 AND raw_high > 0
                ) AS price_range_violation_fraction,
                median(volume_ratio) FILTER (WHERE volume_ratio > 0) AS volume_median
            FROM rows
            GROUP BY date, board
        )
        SELECT
            date,
            board,
            n,
            amount_median,
            amount_p95_error,
            price_range_violation_fraction,
            volume_median
        FROM daily
        WHERE n >= 100
          AND (
              amount_median NOT BETWEEN 0.90 AND 1.10
              OR price_range_violation_fraction > 0.01
              OR (
                  volume_median IS NOT NULL
                  AND volume_median NOT BETWEEN 0.90 AND 1.10
              )
          )
        ORDER BY date, board
        LIMIT 20
        """
    ).fetchall()
    return {
        "passed": not breaches,
        "breach_count_shown": len(breaches),
        "breaches": [
            {
                "date": str(row[0]),
                "board": row[1],
                "n": int(row[2]),
                "amount_median": float(row[3]),
                "amount_p95_error": float(row[4]),
                "price_range_violation_fraction": float(row[5]),
                "volume_median": None if row[6] is None else float(row[6]),
            }
            for row in breaches
        ],
    }


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _backup_prices(lake_root: Path, backup_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = backup_root / f"price_units_{stamp}"
    daily_src = lake_root / "price/daily"
    daily_dst = backup / "daily"
    daily_dst.mkdir(parents=True)
    for src in sorted(daily_src.glob("*.parquet")):
        _link_or_copy(src, daily_dst / src.name)
    _link_or_copy(
        lake_root / "price/daily_all.parquet",
        backup / "daily_all.parquet",
    )
    return backup


def _find_reusable_backup(
    backup_root: Path,
    *,
    compact_sha256: str,
    expected_daily_files: int,
) -> Path | None:
    if not backup_root.exists():
        return None
    for backup in sorted(backup_root.glob("price_units_*"), reverse=True):
        compact = backup / "daily_all.parquet"
        daily = backup / "daily"
        if not compact.exists() or not daily.exists():
            continue
        if len(list(daily.glob("*.parquet"))) != expected_daily_files:
            continue
        if _sha256(compact) == compact_sha256:
            return backup
    return None


def _install_partition(
    con: duckdb.DuckDBPyConnection,
    partition: Path,
    target: Path,
) -> None:
    """Merge one DuckDB partition into one canonical per-stock parquet."""
    sources = sorted(partition.glob("*.parquet"))
    if not sources:
        raise RuntimeError(f"{partition.name} staging 分区为空")
    temp_target = target.parent / f".{target.stem}.price-unit-rebuild.tmp.parquet"
    temp_target.unlink(missing_ok=True)
    if len(sources) == 1:
        shutil.copy2(sources[0], temp_target)
    else:
        source_glob = _sql_path(partition / "*.parquet")
        con.execute(
            f"""
            COPY (
                SELECT date, open, close, high, low, volume, amount
                FROM read_parquet('{source_glob}')
                ORDER BY date
            ) TO '{_sql_path(temp_target)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    os.replace(temp_target, target)


def _write_marker(lake_root: Path, report: dict) -> None:
    marker = lake_root / MARKER_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "complete",
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "backup_path": report["backup_path"],
        "row_count": report["row_count"],
        "compact_sha256_after": report["compact_sha256_after"],
        "amount_sources": report["amount_sources"],
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


def _write_report(report: dict, report_path: Path | None) -> None:
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))


def rebuild_price_units(
    lake_root: str | Path,
    *,
    apply: bool = False,
    skip_periodic: bool = False,
    report_path: str | Path | None = None,
    backup_root: str | Path | None = None,
) -> dict:
    """Plan or apply an idempotent historical price-unit rebuild."""
    lake_root = Path(lake_root)
    report_path = Path(report_path) if report_path is not None else None
    backup_root = (
        Path(backup_root) if backup_root is not None else lake_root / "backups"
    )
    required = [
        lake_root / "price/daily_all.parquet",
        lake_root / "price/daily_raw_all.parquet",
        lake_root / "moneyflow/moneyflow_all.parquet",
        lake_root / "daily_basic/daily_basic_all.parquet",
        lake_root / "adj_factor/adj_factor_all.parquet",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"历史价量重建缺少事实源: {missing}")

    already_rebuilt = _is_completed(lake_root)
    query = _corrected_query(lake_root, already_rebuilt=already_rebuilt)
    con = duckdb.connect()
    stats = _stats(con, query)
    validation = _validation(con, query)
    report = {
        "mode": "apply" if apply else "dry_run",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lake_root": str(lake_root.resolve()),
        "already_rebuilt": already_rebuilt,
        "row_count": stats["row_count"],
        "planned_changes": {
            "volume_rows": stats["volume_changes"],
            "amount_rows": stats["amount_changes"],
        },
        "source_coverage": {
            "volume_check_rows": stats["volume_check_rows"],
            "moneyflow_rows": stats["moneyflow_rows"],
        },
        "amount_sources": {
            "moneyflow": stats["moneyflow_rows"],
            "existing_canonical": stats["existing_rows"],
            "close_proxy": stats["close_proxy_rows"],
        },
        "unresolved_rows": stats["unresolved_rows"],
        "validation": validation,
        "compact_sha256_before": _sha256(lake_root / "price/daily_all.parquet"),
    }
    if stats["unresolved_rows"]:
        raise RuntimeError(f"历史价量重建存在 {stats['unresolved_rows']} 行无法恢复")
    if not validation["passed"]:
        raise RuntimeError(
            "历史价量重建方案未通过物理量纲校验: "
            + json.dumps(validation["breaches"], ensure_ascii=False)
        )
    if not apply:
        _write_report(report, report_path)
        return report

    lock = lake_root / ".price_unit_rebuild.lock"
    if lock.exists():
        raise RuntimeError(f"历史价量重建锁已存在: {lock}")
    lock.write_text(
        json.dumps(
            {"pid": os.getpid(), "started_at": datetime.now().isoformat()},
            ensure_ascii=False,
        )
    )
    staging = Path(
        tempfile.mkdtemp(prefix="price_unit_rebuild_", dir=str(lake_root.parent))
    )
    try:
        corrected_all = staging / "daily_all.parquet"
        con.execute(
            f"""
            COPY (
                SELECT date, open, close, high, low, volume, amount, code
                FROM ({query})
                ORDER BY code, date
            ) TO '{_sql_path(corrected_all)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        check = con.execute(
            f"""
            SELECT
                count(*) AS n,
                count(*) FILTER (
                    WHERE volume IS NULL OR amount IS NULL
                       OR volume < 0 OR amount < 0
                ) AS invalid
            FROM read_parquet('{_sql_path(corrected_all)}')
            """
        ).fetchone()
        if int(check[0]) != stats["row_count"] or int(check[1]) != 0:
            raise RuntimeError(
                f"staging 校验失败: rows={check[0]}, invalid={check[1]}"
            )

        partitioned = staging / "daily"
        con.execute(
            f"""
            COPY (
                SELECT date, open, close, high, low, volume, amount, code
                FROM read_parquet('{_sql_path(corrected_all)}')
                ORDER BY code, date
            ) TO '{_sql_path(partitioned)}'
            (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (code))
            """
        )

        daily_dir = lake_root / "price/daily"
        daily_file_count = len(list(daily_dir.glob("*.parquet")))
        backup = _find_reusable_backup(
            backup_root,
            compact_sha256=report["compact_sha256_before"],
            expected_daily_files=daily_file_count,
        )
        backup_reused = backup is not None
        if backup is None:
            backup = _backup_prices(lake_root, backup_root)
        partitions = sorted(partitioned.glob("code=*"))
        if len(partitions) != daily_file_count:
            raise RuntimeError(
                f"分区文件数不一致: staging={len(partitions)}, "
                f"daily={daily_file_count}"
            )
        for part in partitions:
            code = part.name.split("=", 1)[1]
            target = daily_dir / f"{code}.parquet"
            _install_partition(con, part, target)

        compact_target = lake_root / "price/daily_all.parquet"
        compact_temp = compact_target.with_suffix(".price-unit-rebuild.tmp.parquet")
        shutil.copy2(corrected_all, compact_temp)
        os.replace(compact_temp, compact_target)

        report["backup_path"] = str(backup.resolve())
        report["backup_reused"] = backup_reused
        report["compact_sha256_after"] = _sha256(compact_target)
        _write_marker(lake_root, report)

        if not skip_periodic:
            from lake.aggregate import build_periodic

            build_periodic(str(daily_dir))
            report["periodic_rebuilt"] = True
        else:
            report["periodic_rebuilt"] = False
        _write_report(report, report_path)
        return report
    finally:
        con.close()
        shutil.rmtree(staging, ignore_errors=True)
        lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-root", default="data_lake")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-periodic", action="store_true")
    parser.add_argument(
        "--report",
        default="reports/data/price_unit_rebuild.json",
    )
    args = parser.parse_args()
    report = rebuild_price_units(
        args.lake_root,
        apply=args.apply,
        skip_periodic=args.skip_periodic,
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
