# MODULE_STATUS: providers
Status: ONLINE_SUPPORT
Role: External service clients (LLM provider adapters). stdlib+yaml only, no business-layer imports.
Keep because: 全仓唯一 LLM 访问口——研报 NLP 提取 / Agent 大脑(route/synthesize) / AutoResearch(complete) 共用,
五层消费方(factory/services/api/scripts/tests)共享的底层叶子。

Boundary:
- Consumed downward by factory/services/api/scripts/tests; must never import any business layer
  (enforced by check_layer_deps FORBIDDEN_EDGES providers. entry).
- llm_adapter 自 services/agent 迁来(2026-07-21 P1-1②):切 factory/autoresearch/agent_loop.py
  模块级 import services.agent.llm_adapter 的倒灌边;api 禁表禁 factory. 使 factory/ 落点不可行,
  新顶层 providers/ 是唯一同时满足五层消费方向的落点。
