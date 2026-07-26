"""Stable fingerprints for candidate JSON ASTs.

语义级指纹:同一线性组合的不同写法必须得到相同指纹,避免岛屿搜索
对等价候选重复跑 L0 回测。归一化规则:
- 剔除 thesis(解释性元数据,不参与身份)。
- 剔除 direction:方向在 L0 由 IC 符号经验定向(long if ic_ir>0),AST 的
  direction 字段不参与任何评估(L0 用 |IC|、novelty 用 |spearman|,皆符号无关),
  故 F 与 -F 是同一假设。
- terms 按内容排序(加法交换律)。
- 同 (factor, params, transforms) 项合并权重(0.42*X + 0.28*X ≡ 0.7*X),
  权重舍入吸收浮点求和噪声;合并后权重为 0 的项剔除。
- 整体符号归一:合并后若首项(按 key 排序)权重为负,则整组权重取反——
  这样 F 与 -F(无论经 direction 字段还是经权重取负编码)折叠到同一指纹,
  消除 |ICIR| 适应度下镜像候选霸占冠军席位的浪费。
- transforms 是有序管线(如 mad_clip → zscore → rank),顺序参与身份,
  绝不排序。
DSL 限定为 linear_combo,代数等价仅剩 排序+合并+舍入+整体符号归一,无需引入 CAS。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# 远细于生成/变异端 round(w, 2) 的精度,只用于吸收浮点求和误差。
_WEIGHT_DECIMALS = 9


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_terms(terms: list) -> list:
    merged: dict[str, dict] = {}
    for term in terms:
        params = term.get("params", {})
        identity = {
            "factor": term.get("factor"),
            "params": {k: params[k] for k in sorted(params)},
            "transforms": list(term.get("transforms", [])),
        }
        key = _canonical(identity)
        if key in merged:
            merged[key]["weight"] += float(term.get("weight", 1.0))
        else:
            merged[key] = {**identity, "weight": float(term.get("weight", 1.0))}
    out = []
    for key in sorted(merged):
        entry = merged[key]
        weight = round(entry["weight"], _WEIGHT_DECIMALS)
        if weight == 0:
            continue  # 同类项正负抵消,等价于该项不存在
        out.append({**entry, "weight": weight})
    # 整体符号归一:首项权重为负则整组取反(F 与 -F 折叠为同一假设)
    if out and out[0]["weight"] < 0:
        out = [{**e, "weight": round(-e["weight"], _WEIGHT_DECIMALS)} for e in out]
    return out


def fingerprint_ast(ast: dict) -> str:
    """Return a stable content hash for a candidate AST's semantic identity."""
    normalized = {k: v for k, v in ast.items() if k not in ("thesis", "direction")}
    if isinstance(normalized.get("terms"), list):
        normalized["terms"] = _canonical_terms(normalized["terms"])
    return hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()[:16]
