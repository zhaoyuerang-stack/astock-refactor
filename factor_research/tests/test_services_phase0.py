"""Phase 0 验证:services 接缝 + run_backtest 包壳。

Run:
    cd factor_research && python3 tests/test_services_phase0.py            # 轻量 smoke(test_all.sh 用)
    cd factor_research && PHASE0_FULL=1 python3 tests/test_services_phase0.py   # 铁证:service == strategy_lake

铁律对齐:run_backtest 与 strategy_lake.py 走完全相同的 core.engine 路径,
故同区间 metrics 必须逐项相等。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from contracts.views import BacktestResult
from services.actions.run_backtest import run_backtest
from services.read.factors import list_factors
from services.read.registry import list_strategies


def test_read_endpoints_callable():
    strategies = list_strategies()
    factors = list_factors()
    assert isinstance(strategies, list)
    assert isinstance(factors, list)
    print(f"✅ read endpoints callable (strategies={len(strategies)}, factors={len(factors)})")


def test_backtest_smoke():
    res = run_backtest(start="2024-01-01")
    assert isinstance(res, BacktestResult)
    assert res.n_stocks > 0 and res.n_days > 0
    assert res.annual == res.annual  # not NaN
    assert isinstance(res.hit, bool)
    assert res.family == "illiquidity", res.family
    assert res.version == "v3.1", res.version
    print(f"✅ backtest smoke: 年化={res.annual:+.2%} 夏普={res.sharpe:.2f} "
          f"回撤={res.maxdd:.2%} ({res.n_stocks}只×{res.n_days}日)")


def test_service_matches_strategy_lake():
    """铁证:同区间 service 结果与 strategy_lake.py 逐项相等。"""
    import strategy_lake  # 导入即 chdir 到 factor_research(模块级),不跑 main

    lake_result = strategy_lake.run_backtest("(test-2018)", "2018-01-01")
    m = lake_result.metrics
    svc = run_backtest(start="2018-01-01")

    assert abs(svc.annual - m["annual"]) < 1e-9, (svc.annual, m["annual"])
    assert abs(svc.sharpe - m["sharpe"]) < 1e-9, (svc.sharpe, m["sharpe"])
    assert abs(svc.maxdd - m["maxdd"]) < 1e-9, (svc.maxdd, m["maxdd"])
    assert abs(svc.calmar - m["calmar"]) < 1e-9, (svc.calmar, m["calmar"])
    assert svc.hit == m["hit"]
    assert svc.n == m["n"]
    print(f"✅ service == strategy_lake: 年化={svc.annual:+.4%} 夏普={svc.sharpe:.4f} "
          f"回撤={svc.maxdd:.4%}(逐项相等)")


if __name__ == "__main__":
    print("Running Phase 0 services tests...\n")
    test_read_endpoints_callable()
    test_backtest_smoke()
    if os.environ.get("PHASE0_FULL"):
        test_service_matches_strategy_lake()
    else:
        print("ℹ️  跳过 service==strategy_lake 全量比对(设 PHASE0_FULL=1 开启)")
    print("\n🎉 Phase 0 services tests passed!")
