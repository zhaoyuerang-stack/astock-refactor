"""Information Map (Level 2) — MI 距离驱动的因子空间可视化。

输入: 所有 candidates 的 IC time-series + LIVE strategies 的 returns
处理: MI matrix → distance matrix d_ij = max(0, max_MI - MI_ij) → MDS 2D embed
输出: CLI 表 (簇 + 距离) + 可选 HTML matplotlib 散点图

帮助回答:
  · 我现在在哪里? (已覆盖哪些信息簇)
  · 哪里是空白? (未挖的信息区域)
  · 下一个候选应该往哪里走? (远离已饱和区域)

用法:
  python3 -m metasearch.information_map [--with-live] [--output map.png]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factory.pool import HypothesisPool
from lake.load_lake import load_prices, load_raw_close
from metasearch.factor_mi_audit import audit_hypothesis_pool, mi_matrix

STATUS_COLORS = {
    "live_active": ("🟢", "ACTIVE LIVE"),
    "live_shadow": ("🟡", "SHADOW LIVE"),
    "l3_passed":   ("🔵", "L3 PASSED (候选)"),
    "l2_passed":   ("🔷", "L2 PASSED"),
    "l1_passed":   ("⚪", "L1 PASSED"),
    "l0_passed":   ("⚫", "L0 PASSED"),
    "discarded":   ("❌", "DISCARDED"),
    "drafted":     ("🟣", "DRAFTED"),
}


def build_distance_matrix(mi_mat: pd.DataFrame) -> pd.DataFrame:
    """MI → distance. 自相关 = max MI (diag); 完全独立 = max distance."""
    max_mi = np.diag(mi_mat.values).mean()  # ≈ 3 bits (8 bins)
    return (max_mi - mi_mat).clip(lower=0.0)


def mds_2d_embed(dist: pd.DataFrame) -> pd.DataFrame:
    """Classical MDS 2D embed using SVD on double-centered distance² matrix."""
    n = dist.shape[0]
    D = dist.values
    D2 = D * D
    # double centering
    J = np.eye(n) - np.ones((n, n)) / n
    B = -0.5 * J @ D2 @ J
    # eigendecomposition
    eigvals, eigvecs = np.linalg.eigh(B)
    # take top 2
    idx = np.argsort(eigvals)[::-1][:2]
    coords = eigvecs[:, idx] * np.sqrt(np.maximum(eigvals[idx], 0))
    return pd.DataFrame(coords, index=dist.index, columns=["x", "y"])


def _print_distance_table(dist: pd.DataFrame, max_rows=15):
    """打印 MI 距离表."""
    print(f"\n=== Information Distance Matrix (top {max_rows} × {max_rows}) ===")
    print(dist.iloc[:max_rows, :max_rows].round(2).to_string())


def _print_neighbors(dist: pd.DataFrame, target: str, k=5):
    """打印某个 target 的最近邻 + 最远邻."""
    if target not in dist.index:
        return
    d = dist.loc[target].drop(target).sort_values()
    print(f"\n  {target}")
    print("  最相似 (近邻, 信息冗余):")
    for n, v in d.head(k).items():
        print(f"    {v:.2f}  {n}")
    print("  最独立 (远邻, 信息互补):")
    for n, v in d.tail(k).iloc[::-1].items():
        print(f"    {v:.2f}  {n}")


def _print_coverage_summary(coords: pd.DataFrame, status_map: dict):
    """已覆盖区域 + 空白象限."""
    print("\n=== 信息地图分布 ===")
    print(f"  X range: [{coords['x'].min():+.2f}, {coords['x'].max():+.2f}]")
    print(f"  Y range: [{coords['y'].min():+.2f}, {coords['y'].max():+.2f}]")

    # Quadrant breakdown
    x_mid = coords["x"].median()
    y_mid = coords["y"].median()
    print(f"\n  象限分布 (中位线 x={x_mid:+.2f}, y={y_mid:+.2f}):")
    for qx, qy, label in [(1, 1, "右上"), (-1, 1, "左上"), (-1, -1, "左下"), (1, -1, "右下")]:
        mask = ((coords["x"] - x_mid) * qx > 0) & ((coords["y"] - y_mid) * qy > 0)
        items = coords[mask].index.tolist()
        statuses = [status_map.get(n, "drafted") for n in items]
        live_count = sum(1 for s in statuses if "live" in s)
        print(f"    {label}: n={len(items)}, LIVE={live_count}")
        for n in items[:3]:
            print(f"        {STATUS_COLORS.get(status_map.get(n, 'drafted'), ('?', '?'))[0]} {n}")
        if len(items) > 3:
            print(f"        ... +{len(items)-3} more")


def _print_legend():
    print("\n  Legend:")
    for _, (icon, label) in STATUS_COLORS.items():
        print(f"    {icon} {label}")


def frontier_from_distances(dist: pd.DataFrame, status_map: dict,
                            name_to_base: dict, k: int = 8):
    """空白区 = 距全部 LIVE 锚最远的候选(信息互补度最高);无 LIVE 锚退回平均两两距离。

    返回 (ranked[{name,distance}], 基础因子名列表)。产物供
    knowledge.directions.frontier_factors 在生成端做算力倾斜(BOOST 排头),
    不参与任何有效性判断(候选仍走完整 L0-L3/9-Gate/holdout)。
    """
    live = [n for n in dist.index if "live" in str(status_map.get(n, ""))]
    rows = []
    for name in dist.index:
        if name in live:
            continue
        if live:
            d = float(dist.loc[name, live].min())
        else:
            others = dist.loc[name].drop(name)
            if others.empty:
                continue
            d = float(others.mean())
        rows.append({"name": str(name), "distance": round(d, 3)})
    rows.sort(key=lambda r: -r["distance"])
    top = rows[:k]
    factors = sorted({name_to_base.get(r["name"], "") for r in top} - {""})
    return top, factors


def write_frontier_json(dist: pd.DataFrame, status_map: dict, *, k: int = 8,
                        out_path=None) -> Path:
    """落机器可读空白区(metasearch/frontier.json,月度刷新)。"""
    from datetime import datetime

    from metasearch.factor_mi_audit import _pool_factor_base_map

    top, factors = frontier_from_distances(dist, status_map, _pool_factor_base_map(), k=k)
    out = Path(out_path or Path(__file__).resolve().parent / "frontier.json")
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "min MI-distance to LIVE anchors (无锚退回平均两两距离), top-k",
        "k": k,
        "signals": top,
        "factors": factors,
        "consumer": "knowledge.directions.frontier_factors(生成端算力倾斜,fail-open)",
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-live", action="store_true", default=True,
                    help="包含 LIVE 策略 (默认 True)")
    ap.add_argument("--max-hyps", type=int, default=50)
    ap.add_argument("--output", help="HTML/PNG 输出路径 (可选)")
    ap.add_argument("--json", action="store_true",
                    help="落机器可读空白区到 metasearch/frontier.json")
    ap.add_argument("--json-path", default=None, help="自定义 JSON 输出路径")
    args = ap.parse_args()

    print("Loading data lake...")
    t0 = time.time()
    px = load_prices(start="2018-01-01", fields=("close", "volume"))
    raw = load_raw_close(start="2018-01-01")
    close, volume = px["close"], px["volume"]
    from lake.units import implied_amount

    amount = implied_amount(volume, raw)
    print(f"  {close.shape}, {time.time()-t0:.1f}s")

    # ── Candidate ICs ──
    ics = audit_hypothesis_pool(close, volume, amount, max_hyps=args.max_hyps)
    print(f"  {len(ics)} candidate ICs loaded")

    # ── LIVE strategies → IC of returns vs market ──
    status_map = {}
    pool = HypothesisPool()
    for h in pool.all():
        if h.name in ics:
            status_map[h.name] = h.status.value

    if args.with_live:
        print("\nAdding LIVE strategies as anchors...")
        from portfolio.strategy_runners import LIVE_STRATEGIES, run_all_live
        live_returns = run_all_live(start="2018-01-01")
        # 用 LIVE returns 直接作为 "IC-like" 时序 (与 candidate IC 同长度对齐 by date)
        for name, ret in live_returns.items():
            spec = LIVE_STRATEGIES[name]
            status_key = "live_active" if spec.get("status", "ACTIVE") == "ACTIVE" else "live_shadow"
            # Align to candidate IC dates
            common = next(iter(ics.values())).index.intersection(ret.index)
            if len(common) < 100:
                continue
            ics[name] = ret.loc[common]
            status_map[name] = status_key

    # ── MI matrix ──
    print(f"\nComputing MI matrix on {len(ics)} signals...")
    t1 = time.time()
    mat = mi_matrix(ics)
    print(f"  {mat.shape}, {time.time()-t1:.1f}s")

    # ── Distance matrix → MDS 2D ──
    dist = build_distance_matrix(mat)
    coords = mds_2d_embed(dist)

    # ── Print summary ──
    _print_coverage_summary(coords, status_map)
    _print_legend()

    if args.json or args.json_path:
        out = write_frontier_json(dist, status_map, out_path=args.json_path)
        print(f"\n✓ 机器可读空白区已落盘: {out}")

    # ── 邻居分析: 重点对每个 LIVE 看最相似/最独立的候选 ──
    print(f"\n{'='*70}")
    print("  LIVE 策略的信息邻居 (用来选下一个互补候选)")
    print(f"{'='*70}")
    for name, status in status_map.items():
        if "live" in status:
            _print_neighbors(dist, name, k=3)

    # ── 完整距离表前 15 行 ──
    _print_distance_table(dist, max_rows=15)

    # ── HTML 输出 ──
    if args.output:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(14, 10))
            for name in coords.index:
                status = status_map.get(name, "drafted")
                icon, _ = STATUS_COLORS.get(status, ("?", "?"))
                color = {
                    "live_active": "green", "live_shadow": "orange",
                    "l3_passed": "blue", "l2_passed": "lightblue",
                    "l1_passed": "gray", "l0_passed": "lightgray",
                    "discarded": "red", "drafted": "purple",
                }.get(status, "black")
                marker_size = 200 if "live" in status else 80
                ax.scatter(coords.loc[name, "x"], coords.loc[name, "y"],
                           c=color, s=marker_size, alpha=0.7, edgecolors="black")
                ax.annotate(name[:20], (coords.loc[name, "x"], coords.loc[name, "y"]),
                            fontsize=7, alpha=0.8, xytext=(5, 5),
                            textcoords="offset points")
            ax.set_xlabel("Information Distance (x)")
            ax.set_ylabel("Information Distance (y)")
            ax.set_title("Information Map — MI Distance 2D Embedding\n"
                         "(distant = independent information sources)")
            ax.grid(True, alpha=0.3)
            ax.axhline(0, color="gray", linewidth=0.5)
            ax.axvline(0, color="gray", linewidth=0.5)
            out = Path(args.output)
            fig.tight_layout()
            fig.savefig(out, dpi=150, bbox_inches="tight")
            print(f"\n✓ HTML/PNG output: {out.resolve()}")
        except Exception as e:
            print(f"\n⚠ visualization failed: {e}")


if __name__ == "__main__":
    main()
