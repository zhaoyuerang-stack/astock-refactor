"""推 fund_mom(基本面动量)走 workflow 唯一入册通道 phase1~4。

闭环冠军 momentum(60)+revenue_yoy:L1 31%/ICIR 0.48、注册册未有的机制,
但裸因子回撤 -33% 超注册 bar。本驱动走正式通道(自动叠加 PureTrend MA16
择时 → phase2 三段回测 + phase3 walk-forward),让闸门诚实裁决能否入册。

force=False:不强推,gate 不过就如实报失败(证伪优先)。phase4 是唯一台账写入口。

Run:
    cd factor_research && python3 scripts/research/promote_fundamental_momentum.py
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FUND_MOM_AST = {
    "type": "linear_combo", "direction": "negative",
    "terms": [
        {"factor": "momentum", "params": {"window": 60}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
        {"factor": "revenue_yoy", "params": {}, "transforms": ["mad_clip", "zscore"], "weight": 0.5},
    ],
    "thesis": {"mechanism": "营收同比增长确认基本面改善,与 60 日价格动量共振:"
                            "基本面支撑的趋势更可持续,过滤纯价格动量的假突破。",
               "citation": "autoresearch closed-loop champion 6cd21086"},
}


def main():
    from factory.autoresearch import ast_to_hypothesis, validate_candidate_ast
    from workflow.from_factory import hypothesis_to_spec
    from workflow.promote import promote_spec

    cand = validate_candidate_ast(FUND_MOM_AST)
    hyp = ast_to_hypothesis(cand)
    # 干净的母策略族名(原 autoresearch_<fp> 不适合做台账 id)
    hyp = replace(hyp, name="fundamental-momentum")

    spec = hypothesis_to_spec(hyp)
    print(f"候选: {spec.name}  指纹 {cand.fingerprint[:10]}")
    print("因子: momentum(60)+revenue_yoy  | 择时: 缺省 PureTrend MA16")
    print(f"data_deps: {list(hyp.data_dependencies)}")

    report = promote_spec(
        spec,
        version="v0.1",
        warmup_start="2010-01-01",
        force=False,  # 不强推,闸门诚实裁决
        run_marginal=True,  # 边际残差去冗余(守卫销账:禁跳过)
        regime="大盘/中盘成长有基本面主线时;月频(20d)选 top25,PureTrend MA16 截左尾。",
        decay_signal="与在册动量/成长母策略相关性转强正 / 营收增长因子系统性失效 / 长期无超额。",
        hyp=hyp,
    )

    print("\n==== 入册裁决 ====")
    if report is None:
        print("知识图谱 gate 跳过(未跑 phase);fund_mom 与在册策略疑似冗余,需查 reference。")
    else:
        print(f"registered={report.registered}  detail={getattr(report, 'detail', '')}")
    return report


if __name__ == "__main__":
    main()
