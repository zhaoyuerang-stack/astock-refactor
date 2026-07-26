"""providers —— 外部服务客户端层(LLM provider 适配器等)。

只依赖 stdlib(+yaml),不得 import 任何业务层;各上层(factory/services/api/scripts)
统一经此访问外部 LLM 服务。llm_adapter 自 services/agent 迁来(2026-07-21 P1-1②,
切 factory→services 倒灌边)。
"""
