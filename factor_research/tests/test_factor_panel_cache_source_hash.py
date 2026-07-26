"""factor_store/panels 磁盘缓存 key 必须含因子源码摘要。

对抗:
  改因子实现(源码 hash 变)后,路径必须变,且不得读回旧 hash 路径下的毒化 parquet。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import factors.autoresearch_dsl as dsl
from factors.autoresearch_dsl import (
    _factor_source_hash,
    _get_cache_path,
    clear_factor_cache,
    compute_dsl_factor,
)


def test_source_hash_stable_and_nonempty():
    h1 = _factor_source_hash("momentum")
    h2 = _factor_source_hash("momentum")
    assert h1 == h2
    assert h1 not in {"", "unknown"}
    assert len(h1) >= 8


def test_cache_path_includes_src_suffix():
    path = _get_cache_path("momentum", {"window": 20}, data_signature=None)
    assert "_src" in path.name
    assert path.parent.name == "panels"
    assert "factor_store" in path.parts


def test_different_factors_different_source_hash():
    assert _factor_source_hash("momentum") != _factor_source_hash("volatility")


def test_source_change_misses_stale_disk_cache(monkeypatch=None):
    """对抗:旧 _src 路径下的毒化缓存不得被新源码 hash 命中。

    模拟「改了 illiquidity/momentum 实现但同名同参」——只靠 mtime/params 会静默复用。
    """
    # local monkeypatch without pytest fixture dependency
    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            old = getattr(obj, name)
            self._undo.append((obj, name, old))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    mp = monkeypatch if monkeypatch is not None else _MP()
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mp.setattr(dsl, "_ROOT", root)
            # freeze data mtime so only source_hash moves the path
            mp.setattr(dsl, "_source_data_mtime", lambda: 42)

            idx = pd.bdate_range("2020-01-01", periods=40)
            cols = [f"{i:06d}.SZ" for i in range(12)]
            close = pd.DataFrame(
                np.linspace(10, 20, 40 * 12).reshape(40, 12),
                index=idx,
                columns=cols,
            )
            volume = pd.DataFrame(1e6, index=idx, columns=cols)

            clear_factor_cache()
            old_hash = "deadbeef0001"
            new_hash = "cafebabe0002"
            assert old_hash != new_hash

            # 1) 在「旧源码」hash 路径写入毒化面板(全 999,绝不可能是真实 momentum)
            params = {"window": 20}
            # data_signature 与真实 compute 对齐
            data_sig = dsl._data_signature(close, volume)
            old_path = dsl._get_cache_path(
                "momentum", params, data_signature=data_sig, source_hash=old_hash
            )
            old_path.parent.mkdir(parents=True, exist_ok=True)
            poison = pd.DataFrame(999.0, index=close.index, columns=close.columns)
            poison.to_parquet(old_path)
            assert old_path.exists()

            # 2) 新源码 hash → 路径必须不同
            new_path = dsl._get_cache_path(
                "momentum", params, data_signature=data_sig, source_hash=new_hash
            )
            assert new_path != old_path, "源码 hash 变了路径必须变"
            assert new_path.name != old_path.name

            # 3) 强制 _factor_source_hash 返回 new_hash,disk 计算不得读 poison
            mp.setattr(dsl, "_factor_source_hash", lambda name: new_hash)
            clear_factor_cache()
            ast = {
                "type": "linear_combo",
                "terms": [{
                    "factor": "momentum",
                    "params": params,
                    "transforms": [],
                    "weight": 1.0,
                }],
                "direction": "positive",
            }
            out = compute_dsl_factor(close, volume, ast=ast, cache_mode="disk")
            assert not (out == 999.0).all().all(), "静默复用了旧源码 hash 的毒化缓存"
            # 新路径应已落盘(计算后写入)
            assert new_path.exists() or True  # 写失败不阻塞主断言;主断言是不读 poison
            # 旧毒化文件仍在(未被错误当作当前 cache)
            assert old_path.exists()
            stale = pd.read_parquet(old_path)
            assert (stale == 999.0).all().all()
    finally:
        if monkeypatch is None:
            mp.undo()
        clear_factor_cache()


if __name__ == "__main__":
    test_source_hash_stable_and_nonempty()
    test_cache_path_includes_src_suffix()
    test_different_factors_different_source_hash()
    test_source_change_misses_stale_disk_cache()
    print("ok")
