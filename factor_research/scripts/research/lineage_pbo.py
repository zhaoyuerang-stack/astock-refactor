"""Phase 2B/2C —— 因子血缘相关性 + 正交增量 alpha + 家族 PBO(CSCV)。

数据来源:``data_lake/version_returns/<family>__<version>.csv``
(由 ``run_nine_gates_all.py --persist`` 在跑 9-Gate 时顺带留存的 gate5 日收益序列,
 不二次回测)。

计算(全为确定性代码,符合「防自欺判断恒为代码」铁律):
  2B PBO  —— 把同一家族的多个版本当作 CSCV 的策略池,``pbo_cscv`` 评估
             「样本内最优版本是否在样本外塌陷」=版本选择过拟合概率。
  2C 血缘 —— 每个版本对其 lineage 父版本(同家族、登记序更靠前且有收益序列者):
             corr_to_parent  = 日收益 Pearson 相关
             incremental_alpha = 对父版本正交化后的残差年化均值(正交增量 alpha)

结果**合并**写回台账各版本 ``nine_gate``(先读后并,避免覆盖 2A 摘要)。
台账写入仍走唯一入口 ``strategy_registry.attach_nine_gate``。

用法:
  python3 scripts/research/lineage_pbo.py            # 全部家族
  python3 scripts/research/lineage_pbo.py --family illiquidity
  python3 scripts/research/lineage_pbo.py --dry-run  # 只算不写
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.analysis.walk_forward import pbo_cscv  # noqa: E402

STORE = ROOT / "data_lake" / "version_returns"


def _load_returns() -> dict[str, dict[str, pd.Series]]:
    """读 version_returns/*.csv → {family: {version: ret_series}}。"""
    out: dict[str, dict[str, pd.Series]] = {}
    if not STORE.exists():
        return out
    for csv in sorted(STORE.glob("*.csv")):
        stem = csv.stem
        if "__" not in stem:
            continue
        family, version = stem.split("__", 1)
        s = pd.read_csv(csv, index_col=0)["ret"]
        s.index = pd.to_datetime(s.index)
        out.setdefault(family, {})[version] = s.dropna()
    return out


def _registry_version_order(family: str) -> list[str]:
    """台账登记序(用于确定 lineage 父子)。"""
    import strategy_registry
    data = strategy_registry._load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        return []
    return [v["version"] for v in fam.get("versions", [])]


def _incremental_alpha(child: pd.Series, parent: pd.Series) -> tuple[float, float]:
    """对父版本正交化:child ~ a + b*parent;返回 (corr, 正交增量 alpha 年化)。

    增量 alpha = 回归截距 a 的年化(=父版本无法解释的那部分日均收益)。
    注意:不能用残差均值——OLS 残差均值恒为 0(最小二乘数学性质)。
    """
    df = pd.concat([child.rename("c"), parent.rename("p")], axis=1, sort=False).dropna()
    if len(df) < 50:
        return float("nan"), float("nan")
    corr = float(df["c"].corr(df["p"]))
    b, a = np.polyfit(df["p"].values, df["c"].values, 1)  # 1阶:[斜率 b, 截距 a]
    inc_alpha = float(a * 252)
    return corr, inc_alpha


def compute_family(family: str, vers: dict[str, pd.Series]) -> dict[str, dict]:
    """返回 {version: {pbo, pbo_risk, corr_to_parent, corr_parent_version, incremental_alpha}}。"""
    result: dict[str, dict] = {}

    # 2B —— 家族 PBO(版本池 CSCV)。M<2 时 pbo_cscv 自报 insufficient。
    pbo_out = pbo_cscv({v: s for v, s in vers.items()}, n_splits=100)
    pbo_val = pbo_out.get("pbo")
    pbo_risk = pbo_out.get("risk_level")
    family_pbo = {"pbo": pbo_val, "pbo_risk": pbo_risk} if pbo_out.get("n_strategies", 0) >= 2 else {}

    # 2C —— 按登记序定 lineage 父版本(更靠前且有收益序列者中最近的一个)
    order = _registry_version_order(family)
    ordered_present = [v for v in order if v in vers] or list(vers.keys())

    for i, ver in enumerate(ordered_present):
        rec = dict(family_pbo)  # 家族级 PBO 落到每个版本,便于前端按行展示
        parent = ordered_present[i - 1] if i > 0 else None
        if parent is not None:
            corr, inc = _incremental_alpha(vers[ver], vers[parent])
            if not np.isnan(corr):
                rec["corr_to_parent"] = round(corr, 4)
                rec["corr_parent_version"] = parent
            if not np.isnan(inc):
                rec["incremental_alpha"] = round(inc, 4)
        result[ver] = rec
    return result


def _merge_write(family: str, version: str, lineage_fields: dict) -> None:
    """读现有 nine_gate → 并入 lineage 字段 → 经唯一写入口写回(不覆盖 2A 摘要)。"""
    import strategy_registry
    data = strategy_registry._load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        return
    v = next((x for x in fam.get("versions", []) if x["version"] == version), None)
    if v is None:
        return
    merged = dict(v.get("nine_gate") or {})
    merged.update(lineage_fields)
    strategy_registry.attach_nine_gate(family, version, merged)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 2B/2C lineage 相关性 + PBO")
    ap.add_argument("--family", default=None, help="只算指定家族(缺省=全部)")
    ap.add_argument("--dry-run", action="store_true", help="只算不写台账")
    args = ap.parse_args()

    store = _load_returns()
    if not store:
        print(f"[lineage] 收益序列库为空:{STORE}\n  先跑 run_nine_gates_all.py --persist 留存收益序列。")
        return

    families = [args.family] if args.family else sorted(store.keys())
    for fam in families:
        vers = store.get(fam, {})
        if not vers:
            print(f"[lineage] {fam}: 无收益序列,跳过")
            continue
        res = compute_family(fam, vers)
        print(f"\n=== {fam}({len(vers)} 个版本有收益序列)===")
        for ver, rec in res.items():
            pbo = rec.get("pbo")
            corr = rec.get("corr_to_parent")
            inc = rec.get("incremental_alpha")
            par = rec.get("corr_parent_version")
            print(f"  {ver:10} PBO={pbo if pbo is not None else '—'}"
                  f"  corr→{par or '—'}={corr if corr is not None else '—'}"
                  f"  incΑ={inc if inc is not None else '—'}")
            if not args.dry_run and rec:
                _merge_write(fam, ver, rec)
        if not args.dry_run:
            print(f"  [写回] {fam} {len(res)} 个版本的 lineage/PBO 已并入台账 nine_gate")


if __name__ == "__main__":
    main()
