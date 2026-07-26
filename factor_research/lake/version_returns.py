"""version_returns 唯一写/校验入口(守卫审计 #5)。

背景:recompose 排名(→ paper 账户 → 实测证据展示)唯一输入是
data_lake/version_returns/*.csv;此前任何代码可直写,无身份绑定——可投毒排名。

本模块强制:
  · write:必填身份(spec_hash 或 config_hash)→ 落 CSV + sidecar provenance.json
  · load_verified:读 CSV+sidecar,校验 family/version/series_hash;失败返 reason 不抛不静默

身份规则(owner 拍板:存量硬切 + config-only 降级身份):
  · 有 spec_hash → identity_tier="spec"
  · 否则必须有 config_hash → identity_tier="config-only"
  · 两者皆无 → raise(fail-closed 写)

series_hash = sha256(落盘 CSV 字节),防「换 CSV 留旧 sidecar」投毒。
cost_hash 复用 lake.cost 的 cost_hash(cost_snapshot())(同层定义)。
data_fingerprint 复用 lake.fingerprint.current_data_fingerprint(manifest 口径)。

root 可注入(测试 hermetic,照 paper_engine/meta 参数化先例)。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from lake.cost import cost_hash as _lake_cost_hash
from lake.cost import cost_snapshot as _lake_cost_snapshot
from lake.fingerprint import current_data_fingerprint as _lake_data_fingerprint

_DEFAULT_ROOT = Path(__file__).resolve().parents[1]  # factor_research/


def config_hash(config: Mapping[str, Any] | None) -> str:
    """稳定 config 指纹:sha256(json.dumps sort_keys + 紧凑分隔符)。"""
    payload = config if config is not None else {}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else _DEFAULT_ROOT


def _store_dir(root: Path) -> Path:
    return root / "data_lake" / "version_returns"


def paths_for(family: str, version: str, *, root: Path | str | None = None) -> tuple[Path, Path]:
    """返回 (csv_path, provenance_path)。"""
    base = _store_dir(_resolve_root(root))
    stem = f"{family}__{version}"
    return base / f"{stem}.csv", base / f"{stem}.provenance.json"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_cost_hash() -> str:
    """复用 lake.cost 的 cost_hash(cost_snapshot()) 口径(同层定义)。"""
    return _lake_cost_hash(_lake_cost_snapshot())


def _default_data_fingerprint(root: Path) -> str:
    """复用 lake.fingerprint 的 data_fingerprint 口径(manifest data_vintage.fingerprint)。"""
    return _lake_data_fingerprint(root=root)


def write_version_returns(
    family: str,
    version: str,
    returns: pd.Series,
    *,
    source: str,
    spec_hash: str | None = None,
    config_hash: str | None = None,
    data_fingerprint: str | None = None,
    cost_hash: str | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """写 version_returns CSV + provenance sidecar。

    身份必填:spec_hash 或 config_hash 至少一个,否则 raise。
    返回写入的 provenance dict(已落盘)。
    """
    fam = str(family).strip()
    ver = str(version).strip()
    if not fam or not ver:
        raise ValueError("write_version_returns requires non-empty family and version")
    if not str(source).strip():
        raise ValueError("write_version_returns requires non-empty source")

    sh = str(spec_hash).strip() if spec_hash is not None else ""
    ch = str(config_hash).strip() if config_hash is not None else ""
    if sh:
        identity_tier = "spec"
    elif ch:
        identity_tier = "config-only"
    else:
        raise ValueError(
            "write_version_returns fail-closed: require spec_hash or config_hash "
            "(identity_tier=spec|config-only); both absent is forbidden"
        )

    base_root = _resolve_root(root)
    csv_path, prov_path = paths_for(fam, ver, root=base_root)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    series = pd.Series(returns).dropna()
    if series.empty:
        raise ValueError(f"write_version_returns: empty returns for {fam}/{ver}")
    # 统一列名 + 稳定 CSV 字节(index 为日期)
    out = series.rename("ret")
    out.index = pd.to_datetime(out.index)
    out.to_csv(csv_path, header=True)
    csv_bytes = csv_path.read_bytes()
    series_hash = _sha256_bytes(csv_bytes)

    if data_fingerprint is None:
        data_fingerprint = _default_data_fingerprint(base_root)
    if cost_hash is None:
        cost_hash = _current_cost_hash()

    provenance: dict[str, Any] = {
        "family": fam,
        "version": ver,
        "identity_tier": identity_tier,
        "spec_hash": sh or None,
        "config_hash": ch or None,
        "data_fingerprint": str(data_fingerprint),
        "cost_hash": str(cost_hash),
        "source": str(source),
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": int(len(series)),
        "series_hash": series_hash,
    }
    prov_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return provenance


def load_verified_version_returns(
    family: str,
    version: str,
    *,
    root: Path | str | None = None,
) -> tuple[pd.Series | None, dict[str, Any] | None, str]:
    """读 CSV+sidecar 并校验。

    成功:(series, provenance, "")
    失败:(None, None, reason)——不抛异常、不静默吞掉。
    """
    fam = str(family).strip()
    ver = str(version).strip()
    csv_path, prov_path = paths_for(fam, ver, root=root)

    if not csv_path.exists():
        return None, None, f"missing_csv: {csv_path.name}"
    if not prov_path.exists():
        # 存量硬切:无 sidecar → 选择路径排除(不做 grandfather / 批量补戳)
        return None, None, f"missing_provenance_sidecar: {prov_path.name}"

    try:
        provenance = json.loads(prov_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, None, f"provenance_unreadable: {exc}"

    if not isinstance(provenance, dict):
        return None, None, "provenance_not_object"

    if str(provenance.get("family", "")) != fam:
        return None, None, (
            f"family_mismatch: sidecar={provenance.get('family')!r} expected={fam!r}"
        )
    if str(provenance.get("version", "")) != ver:
        return None, None, (
            f"version_mismatch: sidecar={provenance.get('version')!r} expected={ver!r}"
        )

    expected_hash = str(provenance.get("series_hash") or "").strip()
    if not expected_hash:
        return None, None, "provenance_missing_series_hash"

    try:
        csv_bytes = csv_path.read_bytes()
    except OSError as exc:
        return None, None, f"csv_unreadable: {exc}"

    actual_hash = _sha256_bytes(csv_bytes)
    if actual_hash != expected_hash:
        return None, None, (
            f"series_hash_mismatch: sidecar={expected_hash[:16]}… "
            f"file={actual_hash[:16]}… (CSV 已被覆写或 sidecar 陈旧)"
        )

    try:
        df = pd.read_csv(csv_path, index_col=0)
        if "ret" not in df.columns:
            return None, None, "csv_missing_ret_column"
        series = df["ret"].copy()
        series.index = pd.to_datetime(series.index)
        series = series.dropna()
    except Exception as exc:  # noqa: BLE001 — 读盘解析失败转 reason
        return None, None, f"csv_parse_failed: {exc}"

    if series.empty:
        return None, None, "csv_empty_after_dropna"

    return series, provenance, ""
