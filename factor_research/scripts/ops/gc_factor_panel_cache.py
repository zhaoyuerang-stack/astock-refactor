#!/usr/bin/env python3
"""GC data_lake/factor_store/panels/ 裸缓存(默认 dry-run)。

panels/ 是 **cache 区**,不是资产区(资产 = manifests/ + scores/)。
历史问题:autoresearch_dsl 按 name+params+mtime 落盘,无源码版本、无 GC,
mtime 代翻新后旧代永不删(~30GB+)。

本脚本:
  · 默认 dry-run:只报告将删文件数/体积
  · --apply:删除「非当前数据 mtime 代」的 parquet(保留当前代,含带/不带 _src 的路径)
  · 绝不碰 manifests/ 与 scores/

用法:
  cd factor_research
  python3 scripts/ops/gc_factor_panel_cache.py
  python3 scripts/ops/gc_factor_panel_cache.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_MT_RE = re.compile(r"_mt(\d+)\.parquet$")


def _current_mtime() -> int:
    from factors.autoresearch_dsl import _source_data_mtime

    return int(_source_data_mtime())


def plan_gc(panels_dir: Path, current_mt: int) -> tuple[list[Path], list[Path]]:
    """返回 (keep, delete)。无法解析 mtime 的文件默认 keep(保守)。"""
    keep: list[Path] = []
    delete: list[Path] = []
    if not panels_dir.is_dir():
        return keep, delete
    for p in sorted(panels_dir.glob("*.parquet")):
        m = _MT_RE.search(p.name)
        if m is None:
            keep.append(p)
            continue
        mt = int(m.group(1))
        if current_mt and mt != current_mt:
            delete.append(p)
        else:
            keep.append(p)
    return keep, delete


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真正删除;默认只 dry-run")
    ap.add_argument(
        "--panels-dir",
        default=str(ROOT / "data_lake" / "factor_store" / "panels"),
        help="panels 缓存目录",
    )
    args = ap.parse_args()
    panels = Path(args.panels_dir)
    current_mt = _current_mtime()
    keep, delete = plan_gc(panels, current_mt)
    del_bytes = sum(p.stat().st_size for p in delete if p.exists())
    keep_bytes = sum(p.stat().st_size for p in keep if p.exists())
    print(f"panels_dir={panels}")
    print(f"current_data_mtime={current_mt}")
    print(f"keep={len(keep)} ({keep_bytes / 1e9:.2f} GB)")
    print(f"delete={len(delete)} ({del_bytes / 1e9:.2f} GB)")
    if not args.apply:
        print("dry-run only; re-run with --apply to delete stale mtime generations")
        return 0
    n = 0
    for p in delete:
        try:
            p.unlink()
            n += 1
        except OSError as e:
            print(f"  fail {p.name}: {e}")
    print(f"deleted={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
