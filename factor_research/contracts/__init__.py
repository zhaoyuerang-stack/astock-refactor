"""产品数据契约层(纯 DTO,只依赖 pydantic + stdlib)。

- ``models``:SPEC §7 产品 write-schema DTO(占位 + 活跃 DTO)。Hypothesis/Experiment/
  ExperimentResult 的运行时本体唯一来源是 ``factory.ontology``,不在此重复定义。
- ``views`` :Phase 0 API 的读/响应 DTO(端点实际返回的形状)。

分层铁律:``contracts`` 是叶子,不得 import 任何业务层(core/lake/factors/...
services/api)。守卫见 scripts/ci/check_layer_deps.py。
"""
from contracts import models, views  # noqa: F401
