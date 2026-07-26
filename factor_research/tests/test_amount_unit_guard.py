"""Regression tests for the amount unit (share × raw) guard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci.check_amount_units import scan_source


def test_rejects_volume_times_100_times_raw():
    src = "amount = volume * 100 * raw"
    violations = scan_source(src, rel="services/actions/example.py")
    assert len(violations) == 1
    assert "volume×100×price" in violations[0]


def test_rejects_px_volume_times_100_times_raw_close():
    src = 'amount = px["volume"] * 100.0 * raw_close'
    violations = scan_source(src, rel="strategies/example.py")
    assert len(violations) == 1


def test_allows_implied_amount():
    src = "from lake.units import implied_amount\namount = implied_amount(volume, raw)"
    assert scan_source(src, rel="workflow/example.py") == []


def test_allows_share_times_raw_without_100():
    src = "amount = volume * raw_close"
    assert scan_source(src, rel="workflow/example.py") == []


def test_allows_ingest_vol_to_shares_not_named_amount():
    # lake/sources style: convert 手 → 股 into the volume field, not amount
    src = 'volume = float(r.get("vol") or 0) * 100'
    assert scan_source(src, rel="lake/sources/tushare_price.py") == []


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
