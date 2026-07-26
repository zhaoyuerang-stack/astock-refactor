# MODULE_STATUS: engine
Status: ONLINE_CRITICAL
Role: Canonical metrics/neutralize/composer used in official backtests (R-BT-001 engine.metrics).
Keep because: Part of the backtest authority chain; formal绩效 depends on it.

Boundary:
- Deterministic computation only; no strategy-specific coupling.
- 命名消歧(勿被目录名误导):`engine/` 是位于 `core/` 之**下**的最底层无状态叶子
  (metrics/neutralize/portfolio/factor_analysis/signal_factory),历史遗留命名;
  "回测引擎"本体是 core/engine.py。分层守卫对本目录黑名单最严(禁依赖 factors/
  strategies/factory/workflow/scripts)。绩效标量公式(annual/vol/sharpe/maxdd/calmar)
  唯一权威 = 本目录 metrics.py;上层出口(BacktestResult 等)一律委托,不得内联重写。
