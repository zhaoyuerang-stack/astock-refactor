"""api —— FastAPI 薄层(HTTP + 校验,业务全转 services)。

分层铁律:``api`` 只能 import ``services`` + ``contracts``(+ fastapi/pydantic/
uvicorn 外部库),**不得**直碰 core/factory/engine/lake 等引擎层。守卫见
scripts/ci/check_layer_deps.py。
"""
