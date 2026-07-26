# astock-refactor

A 股全市场因子研究系统的隔离精简版。

本仓从 `zhaoyuerang-stack/astock@68c52d90ad43` 的已提交快照建立，不包含原工作树中的未提交研究、数据湖载荷、凭证或本机运行状态。目标不是重写，而是保留一条可信主链：

```text
PIT 数据 → 因子 → BacktestEngine → workflow/9-Gate
→ registry → readiness → signals/paper
```

## 不变量

- 正式回测只认 `core.engine.BacktestEngine` 和 canonical `CostModel`。
- 数据湖写入只走 `lake/` 或受控数据脚本。
- 候选只能经 `workflow` 晋级，registry 只有一个写入口。
- LLM 只生成和解释候选，不判断 Alpha 是否有效。
- 不含真实下单路径；模拟盘不使用真金。
- 数据、退市覆盖或正式证据缺失时 fail closed。

## 验证

```bash
cd factor_research
bash scripts/test_all.sh
```

数据湖 payload 不进入 Git。需要真实数据验证时，请按 `factor_research/docs/agent_skills/data_source_onboarding.md` 接入独立数据副本；不要把本仓指向正在运行的生产数据湖。
