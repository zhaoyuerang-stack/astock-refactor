# MODULE_STATUS: core
Status: ONLINE_CRITICAL
Role: Canonical BacktestEngine + CostModel backtest authority (R-BT-001, R-COST-001).
Keep because: Sole official backtest权威; all formal绩效 must be reproducible here.

Boundary:
- No dependency on concrete strategies; cost values are the single truth.
- 命名消歧(勿被目录名误导):`core/` 在 `engine/` 之**上**——core.engine(BacktestEngine)
  import engine.metrics 等底层无状态工具;绩效标量公式唯一权威在 engine/metrics.py,
  BacktestResult 只做委托,禁止在本模块内联重写(见 tests/test_metrics_single_source.py)。
