"""services —— UI/Agent 唯一业务入口(受控接缝)。

分层铁律:
- ``api``/``web`` 只能依赖 ``services`` + ``contracts``,不得直碰引擎。
- ``services`` 是**受控接缝**:有意允许 import core.engine / strategy_registry /
  strategies / factors / factory / lake;但不得反向 import ``api``/``web``。
- 守卫见 scripts/ci/check_layer_deps.py。

铁律护栏:services 内**绝不**旁路重算因子/估值或调低成本——回测一律走
core.engine + 固化 CostModel(见 services/actions/run_backtest.py)。
"""
