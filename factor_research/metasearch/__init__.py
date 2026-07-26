"""MetaSearch — 自动化质疑工厂自身假设的元搜索引擎。

设计哲学:
  Line 1-3 是"在预设搜索空间内枚举"
  Line 0 (MetaSearch) 是"质疑预设搜索空间本身"

来自 Band 发现的反思 (2026-06-07):
  · dist 在 small_cap_timing 输出 6 个月没人用 → 被 _ 默默丢弃
  · engine 硬约束 timing ∈ [0,1] 6 周没人质疑 → 阻断 boost 实验
  · "PT 通用最优,无例外" 强结论封顶 → 关闭进一步探索
  · 工厂 6 周 55 hypothesis 产 0 个 LIVE_X,Band 30 分钟人想出 → 工厂搜索空间错位

MetaSearch 不产 hypothesis,产"扩展搜索空间的建议"。
"""
from .signal_flow_tracer import (
    UnusedSignal,
    audit_unused_signals,
    scan_module,
)

__all__ = ["UnusedSignal", "audit_unused_signals", "scan_module"]
