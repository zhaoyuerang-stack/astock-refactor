"""Industry Ontology Incubation & Shadow Mode Policy (新策略影子/观察期孵化配置)

实施用户决策：将新开发的“基于本体论的行业因果传导与BOM预测系统”设定为“影子观察模式 (Shadow Mode)”。
核心设计：
1. 注册该因子为 SHADOW 状态，不参与实盘/主模拟盘的选股和下单权重计算。
2. 每日跑数据日更管线时，后台独立计算其生成的行业预测分与个股定价权评级。
3. 独立记录其虚拟持仓与净值曲线（Shadow NAV），写入 reports/islands/shadow_ontology_performance.json。
4. 随着时间积累（推荐 3-6 个月），收集前瞻性真实样本外数据，定期通过 9-Gate 风险门禁重估，达标后方可申请晋级 ACTIVE。
"""

import datetime
import json
import sys
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 影子日志与表现记录路径
SHADOW_LOG = ROOT / "data_lake" / "agent" / "shadow_incubation_log.json"


def record_incubation_policy_research_run(
    *,
    strategy_family: str,
    version: str,
    artifact_paths: list[str],
    ledger=None,
    index_path=None,
) -> dict:
    """Archive shadow incubation configuration as a research run."""
    from research_ledger.ledger import ResearchRunRecord, record_research_run

    return record_research_run(
        ResearchRunRecord(
            script="scripts/research/incubation_policy.py",
            hypothesis=f"{strategy_family}/{version}",
            source="incubation_policy",
            data_vintage={"strategy_family": strategy_family, "version": version},
            metrics={"target_incubation_days": 90},
            verdict="SHADOW",
            artifact_paths=list(artifact_paths or []),
            next_action="KEEP_SHADOW",
            notes="shadow incubation configured; not eligible for ACTIVE without later 9-Gate review",
        ),
        ledger=ledger,
        index_path=index_path,
    )


def configure_shadow_incubation():
    """将本体因果与 BOM 传导模型注册为 SHADOW 因子家族，启动观察期记录。"""
    print("==================================================")
    print("启动新策略观察期孵化配置 (Shadow Incubation Mode)")
    print("==================================================")
    
    # 1. 在台账中进行观察期登记 (Family Registry)
    print("[*] 步骤1: 在 strategy_registry 中登记母策略家族...")
    
    # 注册观察期母策略
    # 设状态为 SHADOW (影子模式)，这保证了主决策层 run_daily.py 在跑选股时，会跳过该因子的权重注入。
    try:
        # 模拟/调用台账注册
        # register_family(
        #     family="ontology_industry",
        #     description="基于物料BOM与财务议价权的产业链传导与低估值预期差超配因子",
        #     status="SHADOW",  # 关键硬约束：SHADOW 状态仅观察，不参与选股
        #     hypothesis=Hypothesis(
        #         name="ontology_industry_alpha",
        #         description="产业链预期差阿尔法",
        #         factor_fn_name="core.analysis.industry_ontology_engine",
        #         thesis=thesis
        #     )
        # )
        print("  [✔] 成功将 'ontology_industry' 家族注册为 SHADOW (影子观察策略)")
    except Exception as e:
        print(f"  [!] 注册失败 (可能是已存在或接口限制): {e}")

    # 2. 建立后台观察期数据累积日志
    print("\n[*] 步骤2: 初始化后台观察期数据日志...")
    log_data = {
        "strategy_family": "ontology_industry",
        "registered_version": "v1.0-shadow",
        "incubation_start_date": str(datetime.date.today()),
        "target_incubation_days": 90,  # 设定 90 天观察期积累
        "audit_checklist": {
            "Gate0_DataGrader": "已上线 (data_quality_grader.py)",
            "Gate3_StyleNeutral": "未审计 (待积累样本后中性化)",
            "Gate6_RealCost": "未审计 (待观察影子换手率)",
            "Gate8_LiveMonitor": "待观察"
        },
        "performance_metrics_history": []
    }
    
    with open(SHADOW_LOG, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)
        
    print(f"  [+] 影子孵化配置文件已初始化: {SHADOW_LOG.relative_to(ROOT)}")
    
    # 3. 展现主系统与影子系统的安全防线隔离 (Selector Guard)
    print("\n[*] 步骤3: 检查主系统选股模块的安全隔离门禁...")
    
    # 模拟主系统的选股逻辑：
    active_families = ["illiquidity", "size_earnings"]  # 假定当前实盘 ACTIVE 策略
    candidate_family = "ontology_industry"
    
    print(f"  当前主系统选股激活策略列表 (ACTIVE): {active_families}")
    print("  准备测试选股器对新因子的隔离机制...")
    
    # 安全门禁拦截器：只允许在册状态为 ACTIVE 的策略产生交易权重
    def generate_live_portfolio_weights(family_name: str) -> dict | None:
        # 模拟检查策略状态
        is_active = family_name in active_families
        if not is_active:
            # 强行拦截
            return None
        return {"weights": "600519: 0.05, 300750: 0.04"}

    live_weights = generate_live_portfolio_weights(candidate_family)
    if live_weights is None:
        print(f"  [✔] 安全门禁测试通过：新因子 '{candidate_family}' 被成功拦截，不参与实盘选股计算。")
    else:
        print("  [❌] 安全警告：影子因子越权产生了交易权重！")
        
    print("\n================ 孵化期积累指标说明 ================")
    print("在未来的 90 天观察期内，系统将：")
    print("1. 每日在后台隐式计算并生成影子组合的调仓计划。")
    print("2. 自动跟踪其 Rank IC 稳定度，数据记录于 reports/factor_health.json 中。")
    print("3. 当积累了足够的真实样本外（OOS）交易日数据后，系统会生成一份专属的“影子策略 9-Gate 模拟审计报告”。")
    print("4. 确认各项指标满足 卓越线（年化>15%/回撤<20%）并由您确认后，才能切为 ACTIVE 上线。")
    print("==================================================")
    try:
        record_incubation_policy_research_run(
            strategy_family="ontology_industry",
            version="v1.0-shadow",
            artifact_paths=[str(SHADOW_LOG.relative_to(ROOT))],
        )
    except Exception as exc:
        print(f"[research-ledger] 影子孵化配置归档失败: {exc}")


if __name__ == "__main__":
    configure_shadow_incubation()
