"""Factor-level MI Audit — L-1 关在 L0 之前的冗余过滤器。

设计哲学:
  · L0 IC scan 跑 5s/hyp 测因子方向
  · L-1 MI audit 跑 ~50ms/hyp 测因子独立信息含量
  · MI 高度冗余的候选直接跳过 L0,节省算力

实现:
  对每个 hypothesis,算因子值 → IC 时间序列 → 与现有 LIVE 因子的 IC 时间序列算 MI
  IC 时间序列两两 MI 高 = 因子在不同时段产生同样方向的预测 = 冗余
"""
import importlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.factor_analysis import calc_ic
from factory.ontology import HypothesisStatus
from factory.pool import HypothesisPool
from metasearch.mi_auditor import mi


def _resolve_factor_fn(fn_name: str):
    module_path, fn = fn_name.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), fn)


def _dispatch_args(deps, close, volume, amount):
    s = {d for d in deps if not d.startswith("fundamental/")}
    if "price/close" in s and "price/volume" in s:
        return [close, volume]
    if "price/close" in s:
        return [close]
    if "price/amount" in s:
        return [amount]
    if "price/volume" in s:
        return [volume]
    raise ValueError(f"未识别数据依赖: {deps}")


def compute_factor_ic(hyp, close, volume, amount, horizon=20):
    """跑一次因子 → IC 时间序列."""
    fn = _resolve_factor_fn(hyp.factor_fn_name)
    args = _dispatch_args(hyp.data_dependencies, close, volume, amount)
    factor = fn(*args, **hyp.factor_params)
    forward_ret = close.pct_change(horizon).shift(-horizon)
    ic = calc_ic(factor, forward_ret, method="rank").dropna()
    return ic


def audit_hypothesis_pool(close, volume, amount,
                          live_factor_names=None,
                          max_hyps=100):
    """对池里所有 L0+ hypothesis 算 IC,然后两两 MI."""
    pool = HypothesisPool()

    # 取 L0+ 通过的 (有意义的因子方向已确认)
    candidates = []
    for status in [HypothesisStatus.L0_PASSED, HypothesisStatus.L1_PASSED,
                   HypothesisStatus.L2_PASSED, HypothesisStatus.L3_PASSED]:
        candidates.extend(pool.list_by_status(status))
    if max_hyps:
        candidates = candidates[:max_hyps]

    print(f"Computing IC time-series for {len(candidates)} candidates...")
    t0 = time.time()
    ics = {}
    for h in candidates:
        try:
            ic = compute_factor_ic(h, close, volume, amount)
            if len(ic) >= 100:
                ics[h.name] = ic
        except Exception as e:
            print(f"  ⚠ {h.name}: {type(e).__name__}: {str(e)[:60]}")
    print(f"  {time.time()-t0:.1f}s, {len(ics)} ICs computed")
    return ics


def mi_matrix(ic_dict: dict) -> pd.DataFrame:
    """两两 MI 矩阵 (bits)."""
    names = list(ic_dict.keys())
    mat = pd.DataFrame(0.0, index=names, columns=names)
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i > j:
                continue
            v = mi(ic_dict[a], ic_dict[b], n_bins=8)
            mat.loc[a, b] = v
            mat.loc[b, a] = v
    return mat


def cluster_by_redundancy(mat: pd.DataFrame, threshold=2.0) -> list[list[str]]:
    """简单 single-linkage cluster: MI > threshold 视为同簇 (冗余)."""
    names = mat.index.tolist()
    # Build adjacency
    adj = {n: set() for n in names}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if mat.iloc[i, j] >= threshold:
                adj[names[i]].add(names[j])
                adj[names[j]].add(names[i])

    # BFS clusters
    visited = set()
    clusters = []
    for n in names:
        if n in visited:
            continue
        stack = [n]
        cluster = []
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            cluster.append(cur)
            stack.extend(adj[cur] - visited)
        clusters.append(sorted(cluster))
    return sorted(clusters, key=lambda c: -len(c))


def _pool_factor_base_map() -> dict:
    """hypothesis 名 → 基础因子名(factor_fn_name 末段),供 DSL 白名单粒度消费。"""
    pool = HypothesisPool()
    out = {}
    for h in pool.all():
        fn = getattr(h, "factor_fn_name", "") or ""
        if fn:
            out[h.name] = fn.rsplit(".", 1)[-1]
    return out


def factor_clusters_from(clusters: list[list[str]], name_to_base: dict) -> list[list[str]]:
    """hypothesis 簇 → 基础因子名簇(去重;映射不到/单成员簇丢弃)。

    产物供 knowledge.directions.redundancy_clusters 在生成端做"同簇两腿=同一信息
    算两遍 → 排尾"(L-1 教训机械回流);不参与任何有效性判断。
    """
    out = []
    for c in clusters:
        mapped = sorted({name_to_base.get(n, "") for n in c} - {""})
        if len(mapped) > 1:
            out.append(mapped)
    return out


def write_clusters_json(clusters: list[list[str]], *, threshold: float,
                        n_hypotheses: int, out_path=None) -> Path:
    """落机器可读冗余簇(metasearch/redundancy_clusters.json,月度刷新)。"""
    from datetime import datetime

    out = Path(out_path or Path(__file__).resolve().parent / "redundancy_clusters.json")
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "threshold": threshold,
        "n_hypotheses": n_hypotheses,
        "clusters": clusters,
        "factor_clusters": factor_clusters_from(clusters, _pool_factor_base_map()),
        "consumer": "knowledge.directions.redundancy_clusters(生成端 steering,fail-open)",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    import argparse

    from lake.load_lake import load_prices, load_raw_close
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="落机器可读冗余簇到 metasearch/redundancy_clusters.json")
    ap.add_argument("--json-path", default=None, help="自定义 JSON 输出路径")
    args = ap.parse_args()
    from lake.units import implied_amount

    print("Loading data lake...")
    px = load_prices(start="2018-01-01", fields=("close", "volume"))
    raw = load_raw_close(start="2018-01-01")
    close, volume = px["close"], px["volume"]
    amount = implied_amount(volume, raw)
    print(f"  {close.shape}")

    ics = audit_hypothesis_pool(close, volume, amount, max_hyps=30)
    if not ics:
        print("⚠ no candidates with valid IC")
        return

    print("\nComputing pairwise MI matrix...")
    t0 = time.time()
    mat = mi_matrix(ics)
    print(f"  {time.time()-t0:.1f}s, {mat.shape[0]}×{mat.shape[1]} matrix")

    # 自相关上限 (bins=8 → log2(8) = 3 bits)
    print("\n=== MI 分布概览 ===")
    off_diag = []
    for i in range(len(mat)):
        for j in range(i+1, len(mat)):
            off_diag.append(mat.iloc[i,j])
    off_diag = pd.Series(off_diag)
    print(f"  对角自 MI:     {np.diag(mat.values).mean():.2f} bits (上限 3.0)")
    print(f"  非对角 MI 分布: min {off_diag.min():.2f} / "
          f"med {off_diag.median():.2f} / max {off_diag.max():.2f}")
    print(f"  高冗余 pair (MI>2.0): {(off_diag>2.0).sum()}")
    print(f"  独立 pair (MI<0.5):   {(off_diag<0.5).sum()}")

    # Clustering
    print("\n=== 信息冗余簇 (MI > 2.0 = 同信息源) ===")
    clusters = cluster_by_redundancy(mat, threshold=2.0)
    for i, c in enumerate(clusters):
        if len(c) > 1:
            print(f"  Cluster {i+1} (n={len(c)}, 共享 ≥2 bit):")
            for n in c[:5]:
                print(f"    - {n}")
            if len(c) > 5:
                print(f"    ... +{len(c)-5} more")
        else:
            print(f"  独立 {i+1}: {c[0]}")

    # 节省算力估算
    n_total = len(ics)
    n_keep = len(clusters)
    saving = 1 - n_keep / n_total
    print("\n💡 L−1 MI 过滤效果估算:")
    print(f"  原 hypothesis: {n_total}")
    print(f"  独立簇: {n_keep}")
    print(f"  算力节省: {saving:.0%} (每簇保留 1 个,其他可跳过 L1)")

    if args.json or args.json_path:
        out = write_clusters_json(clusters, threshold=2.0, n_hypotheses=n_total,
                                  out_path=args.json_path)
        print(f"\n✓ 机器可读冗余簇已落盘: {out}")


if __name__ == "__main__":
    main()
