"""Factor Store persistence primitives.

The store owns reusable factor panels as first-class research assets:

    data_lake/factor_store/
      panels/<factor_id>.parquet
      manifests/<factor_id>.json

Each panel is a wide ``date x code`` DataFrame. The manifest records the factor
recipe metadata and a lightweight panel fingerprint so later audits can tell
which exact values a strategy used.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from lake.fingerprint import panel_fingerprint

DEFAULT_STORE_ROOT = Path("data_lake/factor_store")


@dataclass(frozen=True)
class FactorManifest:
    factor_id: str
    factor_name: str
    version: str
    params: dict[str, Any]
    data_vintage: str
    dependencies: list[str]
    description: str
    panel_path: str
    fingerprint: str
    start: str
    end: str
    shape: list[int]
    created_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FactorManifest:
        return cls(
            factor_id=str(data["factor_id"]),
            factor_name=str(data["factor_name"]),
            version=str(data.get("version", "")),
            params=dict(data.get("params", {})),
            data_vintage=str(data.get("data_vintage", "")),
            dependencies=list(data.get("dependencies", [])),
            description=str(data.get("description", "")),
            panel_path=str(data["panel_path"]),
            fingerprint=str(data["fingerprint"]),
            start=str(data["start"]),
            end=str(data["end"]),
            shape=list(data["shape"]),
            created_at=str(data["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_factor_id(
    factor_name: str,
    params: dict[str, Any] | None = None,
    *,
    version: str = "",
    feature_mask_version: str = "",
) -> str:
    """Build a stable id from factor name, optional version, and parameters.

    ``feature_mask_version`` (Phase-2 upstream-contamination hygiene) is folded
    into the digest when non-empty / not ``none``. Dirty default leaves the id
    bit-identical to pre-Phase-2 callers that omit the kwarg.
    """
    params = dict(params or {})
    fm = str(feature_mask_version or "").strip()
    if fm and fm.lower() not in {"none", "off", "dirty", "default"}:
        # Canonical key so callers cannot fork the same mask under different param names.
        params = {**params, "feature_mask_version": fm}
    slug = _slugify(factor_name)
    payload = {
        "factor_name": factor_name,
        "version": version,
        "params": _json_stable(params),
    }
    body = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"{slug}__{digest}"


def write_panel_cache(panel: pd.DataFrame, cache_path: str | Path) -> Path:
    """Thin canonical write for AutoResearch DSL panel cache (ADR-038 决策三).

    Caller owns cache key / path / schema; this only centralizes the parquet write
    under the factor_store scoped lake zone. Bit-equivalent to
    ``panel.to_parquet(cache_path)`` after ensuring parent dirs exist.
    """
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path)
    return path


def save_factor_panel(
    panel: pd.DataFrame,
    *,
    factor_name: str,
    params: dict[str, Any] | None = None,
    data_vintage: str,
    version: str = "",
    dependencies: list[str] | None = None,
    description: str = "",
    feature_mask_version: str = "",
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> FactorManifest:
    """Persist a factor panel and its manifest.

    Pass ``feature_mask_version`` when the panel was built under a feature-tradable
    clean path so the factor_id cannot collide with a dirty sibling.
    """
    clean = _validate_panel(panel)
    factor_id = build_factor_id(
        factor_name,
        params,
        version=version,
        feature_mask_version=feature_mask_version,
    )
    root = Path(store_root)
    panels_dir = root / "panels"
    manifests_dir = root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    panel_path = panels_dir / f"{factor_id}.parquet"
    write_panel_cache(clean, panel_path)

    stored_params = dict(params or {})
    fm = str(feature_mask_version or "").strip()
    if fm and fm.lower() not in {"none", "off", "dirty", "default"}:
        stored_params["feature_mask_version"] = fm

    manifest = FactorManifest(
        factor_id=factor_id,
        factor_name=factor_name,
        version=version,
        params=stored_params,
        data_vintage=data_vintage,
        dependencies=list(dependencies or []),
        description=description,
        panel_path=str(panel_path.relative_to(root)),
        fingerprint=panel_fingerprint(clean),
        start=clean.index[0].date().isoformat(),
        end=clean.index[-1].date().isoformat(),
        shape=[int(clean.shape[0]), int(clean.shape[1])],
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )
    _write_json(manifests_dir / f"{factor_id}.json", manifest.to_dict())
    return manifest


def load_factor_manifest(
    factor_id: str,
    *,
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> FactorManifest:
    path = Path(store_root) / "manifests" / f"{factor_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"factor manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return FactorManifest.from_dict(json.load(fh))


def load_factor_panel(
    factor_id: str,
    *,
    start: str | None = None,
    end: str | None = None,
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> pd.DataFrame:
    root = Path(store_root)
    manifest = load_factor_manifest(factor_id, store_root=root)
    panel = pd.read_parquet(root / manifest.panel_path)
    if not isinstance(panel.index, pd.DatetimeIndex):
        panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    if start is not None or end is not None:
        panel = panel.loc[start:end]
    return panel


def _validate_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(panel, pd.DataFrame):
        raise TypeError("factor panel must be a pandas DataFrame")
    if panel.empty:
        raise ValueError("factor panel must not be empty")
    if not isinstance(panel.index, pd.DatetimeIndex):
        raise ValueError("factor panel index must be a DatetimeIndex")
    if not panel.index.is_unique:
        raise ValueError("factor panel index must be unique")
    if not panel.columns.is_unique:
        raise ValueError("factor panel columns must be unique")

    try:
        clean = panel.sort_index().astype("float64")
    except (TypeError, ValueError) as exc:
        raise ValueError("factor panel values must be numeric") from exc

    if np.isinf(clean.to_numpy(copy=False)).any():
        raise ValueError("factor panel contains non-finite values")
    clean.index.name = panel.index.name
    return clean


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug[:48] or "factor"


def _json_stable(value: Any) -> Any:
    """Return a JSON-stable representation for hashing and manifest params."""
    try:
        json.dumps(value, sort_keys=True, ensure_ascii=True)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_stable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_stable(v) for v in value]
        return str(value)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=True, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(path)
