"""已退役：旧脚本直接读取 2025 holdout 并生成交易明细。

该路径绕过 ``governance.holdout.validate_on_holdout`` 的单次消费、身份绑定和
trial 记账，不能继续作为研究或展示证据。保留同名 tombstone，防旧命令静默复活。
"""

raise SystemExit(
    "scripts/research/simulate_2025.py 已退役：禁止直接读取 holdout；"
    "请通过 canonical holdout validation receipt 获取可审计结果。"
)
