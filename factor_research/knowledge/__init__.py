"""knowledge — 机器可读知识图谱(空容器,findings 由本系统验证流水线现场生长)。

机制借鉴自 alpha_engine/knowledge/graph.py,但**只借机制不照搬结论**:
  - 零预置 seeds(findings.json 初始为 {})
  - 失败候选默认 DEPRIORITIZE(+保质期)而非永久 SKIP,避免搜索失明
  - 不引入 alpha_engine 的 Gate-3 边际律(它忽略相关性符号);KG 只记录,不裁决边际

对接 factory.ontology.Hypothesis(duck-typed:只读 factor_fn_name/factor_params/
timing_fn_name/id),不在本层硬依赖 factory。
"""
from knowledge.graph import Finding, KnowledgeGraph, SearchGate, load_graph

__all__ = ["Finding", "SearchGate", "KnowledgeGraph", "load_graph"]
