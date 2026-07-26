"""Deterministic seed candidate generation for AutoResearch Lite."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

from app_config.log import get_logger

from .models import Candidate
from .validator import validate_candidate_ast

logger = get_logger(__name__)

_SEEDS = [
    # 股东户数/北向正交族:probe 早期看好(原始 ICIR 0.57),但后续全市场 top25 long-only
    # 实测太弱(research_ledger e6e655401623899d,残差 ICIR~0.2)——现由方向登记簿
    # (knowledge/direction_registry.json)动态降权排尾,不再硬编码置顶;条目到期自动复活重测。
    ("holder_count_chg", {"window": 60}, "momentum", {"window": 20}),
    ("holder_count_chg", {"window": 60}, "roe", {}),
    ("northbound_accumulation", {"window": 20}, "momentum", {"window": 20}),
    ("northbound_accumulation", {"window": 20}, "roe", {}),
    ("momentum", {"window": 20}, "volume_ratio", {"window": 5}),
    ("alpha_003", {}, "bp_proxy", {}),
    ("momentum", {"window": 60}, "volatility", {"window": 20}),
    ("alpha_055", {}, "roe", {}),
    ("momentum", {"window": 120}, "illiquidity", {"window": 20}),
    ("alpha_013", {}, "volatility", {"window": 60}),
    ("volume_ratio", {"window": 10}, "volatility", {"window": 60}),
    ("alpha_050", {}, "ep_proxy", {}),
    ("illiquidity", {"window": 40}, "momentum", {"window": 20}),
    ("alpha_044", {}, "net_profit_yoy", {}),
    ("roe", {}, "momentum", {"window": 60}),
    ("net_profit_yoy", {}, "volume_ratio", {"window": 20}),
    ("revenue_yoy", {}, "momentum", {"window": 120}),
    ("bp_proxy", {}, "volatility", {"window": 60}),
    ("ep_proxy", {}, "illiquidity", {"window": 20}),
    ("roe", {}, "bp_proxy", {}),
    ("net_profit_yoy", {}, "revenue_yoy", {}),
]


def _ast_for(seed: tuple, weight: float) -> dict:
    left_factor, left_params, right_factor, right_params = seed
    return {
        "type": "linear_combo",
        "terms": [
            {
                "factor": left_factor,
                "params": left_params,
                "transforms": ["mad_clip", "zscore", "rank"],
                "weight": weight,
            },
            {
                "factor": right_factor,
                "params": right_params,
                "transforms": ["mad_clip", "zscore", "rank"],
                "weight": round(1.0 - weight, 2),
            },
        ],
        "direction": "positive",
        "thesis": {
            "mechanism": f"{left_factor} 与 {right_factor} 的互补信号组合,用于受控自动研究初筛。",
            "citation": "autoresearch seed generator",
        },
    }


def _steer_seed_order(all_seeds: list) -> list:
    """方向登记簿 + MI 冗余簇对种子过滤/排序(生成端 steering,LOOP §3 生成层)。

    SKIP 方向不生成(死路不复搜);DEPRIORITIZE / 两腿同 MI 簇(同信息算两遍)排尾;
    BOOST(策展空白区 ∪ metasearch frontier)排头,islands 小 limit 采种时优先吃到。
    fail-open + 自饿保护:登记簿读不出 / 过滤后为空 → 退回原顺序 + 诚实警告,
    方向层故障绝不阻断搜索;验真端(L0-L3/9-Gate/holdout)不受本函数影响。
    """
    try:
        from knowledge.directions import (
            boost_factors,
            redundancy_clusters,
            same_cluster,
            seed_action,
        )

        boosts = boost_factors()
        clusters = redundancy_clusters()
        front: list = []
        mid: list = []
        back: list = []
        skipped = 0
        for seed in all_seeds:
            left_f, right_f = seed[0], seed[2]
            action, _reason = seed_action((left_f, right_f))
            if action == "SKIP":
                skipped += 1
                continue
            if action == "DEPRIORITIZE" or same_cluster(left_f, right_f, clusters=clusters):
                back.append(seed)
            elif left_f in boosts or right_f in boosts:
                front.append(seed)
            else:
                mid.append(seed)
        steered = front + mid + back
        if not steered:
            logger.warning("[generator] 方向登记簿过滤后种子为空,退回未过滤顺序(自饿保护)")
            return all_seeds
        if skipped:
            logger.info(f"[generator] 方向登记簿 SKIP {skipped} 个种子(已证伪方向不再复搜)")
        return steered
    except Exception as e:
        logger.warning(f"[generator] 方向层 steering 失败(fail-open,保持原顺序): {e}")
        return all_seeds


def generate_seed_candidates(limit: int = 10) -> Iterator[Candidate]:
    """Yield unique, validated low-complexity seed candidates, covering all 47 whitelisted factors."""
    from .registry import ALLOWED_FACTORS

    # 1. Start with manually designed high-quality seeds
    all_seeds = list(_SEEDS)

    # 2. Track which factors are covered by manually designed seeds
    covered = set()
    for left_f, _, right_f, _ in _SEEDS:
        covered.add(left_f)
        covered.add(right_f)

    # 3. For any whitelisted factor not covered, pair it with a default companion
    for fname, spec in ALLOWED_FACTORS.items():
        if fname not in covered:
            params = {}
            if "window" in spec.params:
                lo, hi = spec.params["window"]
                params = {"window": int((lo + hi) // 2)}

            # If alternative or fundamental, pair with momentum; else pair with roe
            if "fundamental" in str(spec.data_dependencies) or "holder" in str(spec.data_dependencies):
                partner = "momentum"
                partner_params = {"window": 20}
            else:
                partner = "roe"
                partner_params = {}

            all_seeds.append((fname, params, partner, partner_params))

    # 4. 方向层教训回流:limit 截断前先过滤/重排(否则死路种子照旧占掉小 limit 的名额)
    all_seeds = _steer_seed_order(all_seeds)

    seen: set[str] = set()
    weights = [0.7, 0.6, 0.5]
    for seed in all_seeds:
        for weight in weights:
            try:
                candidate = validate_candidate_ast(_ast_for(seed, weight))
                if candidate.fingerprint in seen:
                    continue
                seen.add(candidate.fingerprint)
                yield replace(candidate, provenance={
                    "origin": "deterministic_seed",
                    "catalog": "factory.autoresearch.generator._SEEDS",
                    "pair": f"{seed[0]}×{seed[2]}",
                })
                if len(seen) >= limit:
                    return
            except Exception:
                continue
