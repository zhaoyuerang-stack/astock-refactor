"""归档的一次性研究脚本(已退役的探索变体族)。

这些脚本是 hmm_*/state_transition_*/mkt_diffusion_*/breadth_dd20_*/abcd_* 等
中间迭代,不再维护,仅保留以备追溯。它们彼此互相 import,但不被任何活跃脚本
或生产代码依赖(见 scripts/ci/check_layer_deps.py 的分层约束)。
"""
