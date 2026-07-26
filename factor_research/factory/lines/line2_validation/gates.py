"""Cheap-First 各关阈值。

设计哲学：L0/L1 阈值偏宽松（多留候选，组合层做最终筛选）。
L2/L3/audit 才严。
"""

# L0: 5 秒级 IC IR 粗筛
GATES_L0 = {
    "ic_ir_min": 0.03,        # |ICIR| < 0.03 → DISCARD
    "min_ic_count": 60,       # IC 序列至少 60 个日期才有意义
}

# L1: 30 秒快速回测
GATES_L1 = {
    "annual_min": 0.05,       # 年化 < 5% → DISCARD
    "maxdd_max": -0.40,       # 回撤 > 40% → DISCARD
    "min_days": 252,          # 回测窗口至少 1 年
}

# L2: 5 分钟 multi-regime
GATES_L2 = {
    "regime_pass_min": 2,     # 4 regime 至少 2 个通过单 regime 标准
    "regime_annual_min": 0.0, # 单 regime 年化 ≥ 0（不亏即可，挑战 regime_dependent insight）
}

# L3: walk-forward 年度稳定性
# 对工厂 mutation 的固定因子，walk-forward 退化为"年度 OOS 稳定性测试"
GATES_L3 = {
    "wf_positive_ratio_min": 0.50,  # ≥ 一半年份 sharpe > 0
    "wf_avg_sharpe_min": 0.5,       # 平均年 sharpe ≥ 0.5
}
