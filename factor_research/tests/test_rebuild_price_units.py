"""Dry-run-first historical price-unit rebuild tests."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from scripts.repair.rebuild_price_units import rebuild_price_units


def _write_fixture_lake(root: Path) -> None:
    daily = root / "price" / "daily"
    raw_daily = root / "price" / "daily_raw"
    moneyflow = root / "moneyflow"
    daily_basic = root / "daily_basic"
    adj_factor = root / "adj_factor"
    daily.mkdir(parents=True)
    raw_daily.mkdir(parents=True)
    moneyflow.mkdir(parents=True)
    daily_basic.mkdir(parents=True)
    adj_factor.mkdir(parents=True)

    dates = pd.to_datetime(["2026-06-12", "2026-06-15", "2026-06-16"])
    fixtures = {
        "000001": {
            "volume": [100.0, 10000.0, 12000.0],
            "amount": [5000.0, 101000.0, 121000.0],
            "raw_close": [10.0, 10.0, 10.0],
        },
        "688256": {
            "volume": [20000.0, 21000.0, 22000.0],
            "amount": [220000.0, 231000.0, 242000.0],
            "raw_close": [10.0, 10.0, 10.0],
        },
    }
    compact_frames = []
    raw_frames = []
    for code, values in fixtures.items():
        price = pd.DataFrame(
            {
                "date": dates,
                "open": [10.0, 10.0, 10.0],
                "close": [10.0, 10.0, 10.0],
                "high": [10.0, 10.0, 10.0],
                "low": [10.0, 10.0, 10.0],
                "volume": values["volume"],
                "amount": values["amount"],
            }
        )
        raw = pd.DataFrame(
            {
                "date": dates,
                "raw_open": values["raw_close"],
                "raw_high": values["raw_close"],
                "raw_low": values["raw_close"],
                "raw_close": values["raw_close"],
            }
        )
        price.to_parquet(daily / f"{code}.parquet", index=False)
        raw.to_parquet(raw_daily / f"{code}.parquet", index=False)
        compact_frames.append(price.assign(code=code))
        raw_frames.append(raw.assign(code=code))

    pd.concat(compact_frames, ignore_index=True).to_parquet(
        root / "price" / "daily_all.parquet", index=False
    )
    pd.concat(raw_frames, ignore_index=True).to_parquet(
        root / "price" / "daily_raw_all.parquet", index=False
    )

    mf_rows = []
    for date in ("20260612", "20260615", "20260616"):
        for ts_code, amount in (("000001.SZ", 100000.0), ("688256.SH", 200000.0)):
            quarter = amount / 10000.0 / 4.0
            mf_rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": date,
                    "buy_sm_amount": quarter,
                    "buy_md_amount": quarter,
                    "buy_lg_amount": quarter,
                    "buy_elg_amount": quarter,
                    "sell_sm_amount": quarter,
                    "sell_md_amount": quarter,
                    "sell_lg_amount": quarter,
                    "sell_elg_amount": quarter,
                }
            )
    pd.DataFrame(mf_rows).to_parquet(
        moneyflow / "moneyflow_all.parquet", index=False
    )

    db_rows = []
    for date in ("20260612", "20260615", "20260616"):
        for ts_code, volume in (("000001.SZ", 10000.0), ("688256.SH", 20000.0)):
            db_rows.append(
                {
                    "ts_code": ts_code,
                    "trade_date": date,
                    "float_share": volume / 100.0,
                    "turnover_rate": 1.0,
                }
            )
    pd.DataFrame(db_rows).to_parquet(
        daily_basic / "daily_basic_all.parquet", index=False
    )
    pd.DataFrame(
        [
            {
                "ts_code": ts_code,
                "trade_date": date,
                "adj_factor": 1.0,
            }
            for date in ("20260612", "20260615", "20260616")
            for ts_code in ("000001.SZ", "688256.SH")
        ]
    ).to_parquet(adj_factor / "adj_factor_all.parquet", index=False)


def test_rebuild_dry_run_does_not_modify_source_files(tmp_path):
    lake = tmp_path / "data_lake"
    _write_fixture_lake(lake)
    before = (lake / "price" / "daily" / "000001.parquet").read_bytes()

    report = rebuild_price_units(lake, apply=False, skip_periodic=True)

    assert report["mode"] == "dry_run"
    assert report["planned_changes"]["volume_rows"] > 0
    assert report["planned_changes"]["amount_rows"] > 0
    assert (lake / "price" / "daily" / "000001.parquet").read_bytes() == before
    assert not (lake / "backups").exists()


def test_rebuild_apply_updates_both_stores_creates_backup_and_is_idempotent(tmp_path):
    lake = tmp_path / "data_lake"
    _write_fixture_lake(lake)

    report = rebuild_price_units(lake, apply=True, skip_periodic=True)

    assert report["mode"] == "apply"
    backup = Path(report["backup_path"])
    assert backup.exists()
    backed_up = pd.read_parquet(backup / "daily" / "000001.parquet")
    assert backed_up.loc[0, "volume"] == 100.0

    stock = pd.read_parquet(lake / "price" / "daily" / "000001.parquet")
    compact = pd.read_parquet(lake / "price" / "daily_all.parquet")
    compact_stock = compact[compact["code"] == "000001"].reset_index(drop=True)
    assert stock.loc[0, "volume"] == 10000.0
    assert stock.loc[0, "amount"] == 100000.0
    pd.testing.assert_frame_equal(
        stock.reset_index(drop=True),
        compact_stock.drop(columns=["code"]).reset_index(drop=True),
        check_dtype=False,
    )

    second = rebuild_price_units(lake, apply=False, skip_periodic=True)
    assert second["planned_changes"]["volume_rows"] == 0
    assert second["planned_changes"]["amount_rows"] == 0


def test_rebuild_report_is_json_serializable(tmp_path):
    lake = tmp_path / "data_lake"
    _write_fixture_lake(lake)

    report = rebuild_price_units(lake, apply=False, skip_periodic=True)

    assert json.loads(json.dumps(report))["source_coverage"]["moneyflow_rows"] == 3


def test_install_partition_merges_multiple_duckdb_files(tmp_path):
    from scripts.repair.rebuild_price_units import _install_partition

    partition = tmp_path / "code=000001"
    partition.mkdir()
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-12"]),
            "open": [10.0],
            "close": [10.0],
            "high": [10.0],
            "low": [10.0],
            "volume": [1000.0],
            "amount": [10000.0],
        }
    ).to_parquet(partition / "data_0.parquet", index=False)
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-13"]),
            "open": [11.0],
            "close": [11.0],
            "high": [11.0],
            "low": [11.0],
            "volume": [1100.0],
            "amount": [12100.0],
        }
    ).to_parquet(partition / "data_1.parquet", index=False)
    target = tmp_path / "000001.parquet"

    con = duckdb.connect()
    try:
        _install_partition(con, partition, target)
    finally:
        con.close()

    installed = pd.read_parquet(target)
    assert installed["date"].tolist() == list(
        pd.to_datetime(["2026-06-12", "2026-06-13"])
    )


def test_audit_applies_start_to_both_compact_and_per_stock_stores(tmp_path):
    from scripts.repair.audit_price_units import audit_price_units

    lake = tmp_path / "data_lake"
    _write_fixture_lake(lake)

    report = audit_price_units(lake, start="2026-06-15")

    assert report["compact_per_stock_consistency"]["passed"] is True
    assert report["compact_per_stock_consistency"]["only_per_stock"] == 0


def test_audit_does_not_treat_missing_volume_crosscheck_as_unit_failure(tmp_path):
    from scripts.repair.audit_price_units import audit_price_units

    lake = tmp_path / "data_lake"
    _write_fixture_lake(lake)
    rebuild_price_units(lake, apply=True, skip_periodic=True)
    daily_basic = pd.read_parquet(
        lake / "daily_basic" / "daily_basic_all.parquet"
    )
    daily_basic["float_share"] = float("nan")
    daily_basic.to_parquet(
        lake / "daily_basic" / "daily_basic_all.parquet",
        index=False,
    )

    report = audit_price_units(lake, start="2026-06-12")

    assert report["first_bad_date"] is None
    assert report["bad_date_count"] == 0
