"""version_returns 身份信封对抗测试(守卫审计 #5 / Commit 1)。

全程 hermetic:root 注入 tmp,不读不写真实 data_lake。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.version_returns import (  # noqa: E402
    config_hash,
    load_verified_version_returns,
    paths_for,
    write_version_returns,
)


def _series(n: int = 10) -> pd.Series:
    idx = pd.bdate_range("2020-01-02", periods=n)
    return pd.Series([0.001 * (i + 1) for i in range(n)], index=idx, name="ret")


def test_write_without_identity_raises(tmp_path):
    with pytest.raises(ValueError, match="spec_hash or config_hash"):
        write_version_returns(
            "fam", "v1", _series(),
            source="test",
            root=tmp_path,
            data_fingerprint="fp-test",
            cost_hash="cost-test",
        )


def test_config_only_writes_tier_and_sidecar(tmp_path):
    ch = config_hash({"top_n": 25, "factor": "amihud"})
    prov = write_version_returns(
        "illiquidity", "v1.0", _series(),
        source="unit-test",
        config_hash=ch,
        data_fingerprint="fp-abc",
        cost_hash="cost-xyz",
        root=tmp_path,
    )
    assert prov["identity_tier"] == "config-only"
    assert prov["config_hash"] == ch
    assert prov["spec_hash"] is None
    assert prov["family"] == "illiquidity"
    assert prov["version"] == "v1.0"
    assert prov["data_fingerprint"] == "fp-abc"
    assert prov["cost_hash"] == "cost-xyz"
    assert prov["source"] == "unit-test"
    assert prov["rows"] == 10
    assert len(prov["series_hash"]) == 64

    csv_path, prov_path = paths_for("illiquidity", "v1.0", root=tmp_path)
    assert csv_path.exists() and prov_path.exists()
    disk = json.loads(prov_path.read_text(encoding="utf-8"))
    assert disk["identity_tier"] == "config-only"
    assert disk["series_hash"] == prov["series_hash"]


def test_spec_hash_wins_over_config_hash(tmp_path):
    prov = write_version_returns(
        "f", "v2", _series(),
        source="t",
        spec_hash="specdeadbeef",
        config_hash=config_hash({"a": 1}),
        data_fingerprint="fp",
        cost_hash="c",
        root=tmp_path,
    )
    assert prov["identity_tier"] == "spec"
    assert prov["spec_hash"] == "specdeadbeef"


def test_roundtrip_load_verified_ok(tmp_path):
    write_version_returns(
        "fam", "v1", _series(5),
        source="roundtrip",
        spec_hash="abc123",
        data_fingerprint="fp",
        cost_hash="c",
        root=tmp_path,
    )
    series, prov, reason = load_verified_version_returns("fam", "v1", root=tmp_path)
    assert reason == ""
    assert series is not None and prov is not None
    assert len(series) == 5
    assert prov["identity_tier"] == "spec"
    assert float(series.iloc[0]) == pytest.approx(0.001)


def test_poison_csv_overwrite_detected(tmp_path):
    """投毒:合法写入后直接覆写 CSV → series_hash 不符,load_verified 必拒。"""
    write_version_returns(
        "poison", "v1", _series(8),
        source="before-poison",
        config_hash=config_hash({"k": 1}),
        data_fingerprint="fp",
        cost_hash="c",
        root=tmp_path,
    )
    csv_path, _ = paths_for("poison", "v1", root=tmp_path)
    # 直接覆写 CSV(绕过 write_version_returns)——模拟投毒
    evil = _series(8) * -1.0
    evil.rename("ret").to_csv(csv_path, header=True)

    series, prov, reason = load_verified_version_returns("poison", "v1", root=tmp_path)
    assert series is None and prov is None
    assert "series_hash_mismatch" in reason
    print(f"POISON_DETECT_OUTPUT: {reason}")


def test_missing_sidecar_rejected(tmp_path):
    csv_path, _ = paths_for("orphan", "v0", root=tmp_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _series(3).rename("ret").to_csv(csv_path, header=True)

    series, prov, reason = load_verified_version_returns("orphan", "v0", root=tmp_path)
    assert series is None and prov is None
    assert "missing_provenance_sidecar" in reason


def test_family_version_mismatch_rejected(tmp_path):
    write_version_returns(
        "real-fam", "v1", _series(4),
        source="t",
        spec_hash="s",
        data_fingerprint="fp",
        cost_hash="c",
        root=tmp_path,
    )
    # 篡改 sidecar 的 family 字段
    _, prov_path = paths_for("real-fam", "v1", root=tmp_path)
    data = json.loads(prov_path.read_text(encoding="utf-8"))
    data["family"] = "wrong-fam"
    prov_path.write_text(json.dumps(data), encoding="utf-8")

    series, prov, reason = load_verified_version_returns("real-fam", "v1", root=tmp_path)
    assert series is None
    assert "family_mismatch" in reason


def test_config_hash_stable():
    a = config_hash({"b": 2, "a": 1})
    b = config_hash({"a": 1, "b": 2})
    assert a == b
    assert len(a) == 64


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-s"]))
