"""执行现实核对清单 → 机械化测试（对齐根目录 `回测执行现实核对清单.md`）。

设计原则（防自欺，对齐 CLAUDE.md §12 / R-BT-001 / R-DATA-003 / R-COST-001）：

* 测试只对准 **canonical 回测权威 `core.engine.BacktestEngine`**（清单 H 组锚定的 R-BT-001），
  不对孤儿脚手架刷绿。`execution/OrderSimulator` 仅被 `execution/__init__.py` 再导出、
  回测路径无人调用，对它做"回测尊重涨跌停/停牌"的断言 = 绿但什么也没验证。
* 引擎**真正编码**的执行现实只有：
  - C 组（T+1 / 信号→成交时序 / 无未来函数）—— `_map_decisions_to_fill_dates`；
  - E 组（成本扣除）—— `_run_weight_backtest` 的 buy/sell_cost。
  这两组用合成数据做**确定性**断言（下方 `TestExecutionRealityCanonical`）。
* 清单 A/B 组（涨跌停封板"顺延"+ 累计顺延损失、停牌冻结、退市归零清算、一字板识别）
  **未接入 canonical 引擎**。诚实做法：把 gap 显式钉死/skip（下方 `TestExecutionRealityGaps`），
  而非伪造通过。OrderSimulator 只"拒单"不"顺延"，断言不得暗示顺延已覆盖。

合成数据无任何 data_lake 依赖，bit 可复现。
运行：``cd factor_research && python -m unittest tests.test_execution_reality -v``
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import (  # noqa: E402
    BacktestConfig,
    BacktestEngine,
    CostModel,
    PricePanel,
    Signal,
    _map_decisions_to_fill_dates,  # noqa: E402
)

# ---------------------------------------------------------------------------
# 合成价格面板：6 个交易日，A/B 两只股票
# ---------------------------------------------------------------------------

def _make_panel() -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    dates = pd.bdate_range("2020-01-02", periods=6)  # d0..d5
    # A: 在 d2→d3 跳 +10%（成交当日 bar），d3→d4 再跳 +10%（持有期 bar）。
    #    用于区分"成交 bar 的涨幅是否被偷看"与"持有 bar 的涨幅是否被赚到"。
    a = [100.0, 100.0, 100.0, 110.0, 121.0, 121.0]
    b = [100.0] * 6  # 提供截面，不下单
    close = pd.DataFrame({"A": a, "B": b}, index=dates)
    return close, dates


def _run_full_weight_on_A(decision_date, *, leverage=1.0, buy=0.001, sell=0.001):
    """决策日 decision_date 满仓买入 A，返回回测结果与交易日轴。"""
    close, dates = _make_panel()
    prices = PricePanel(close=close, volume=pd.DataFrame(), amount=pd.DataFrame())
    config = BacktestConfig(
        start=str(dates[0].date()),
        cost=CostModel(buy_cost=buy, sell_cost=sell, financing_rate=0.0),
        leverage=leverage,
    )
    engine = BacktestEngine(prices=prices, config=config)
    decision = pd.DataFrame({"A": [1.0], "B": [0.0]}, index=[decision_date])
    result = engine.run(Signal(decision_weights=decision))
    return result, dates


class TestExecutionRealityCanonical(unittest.TestCase):
    """C 组 + E 组：canonical 引擎真正保证的执行现实。"""

    # --- 清单 C：信号→成交时序，T+1，无未来函数（R-DATA-003）-----------------

    def test_decision_maps_to_next_trading_day(self):
        """决策日 T 的权重只能在 T+1（下一交易日）落地，绝不当日成交。"""
        close, dates = _make_panel()
        decision = pd.DataFrame({"A": [1.0]}, index=[dates[2]])
        filled = _map_decisions_to_fill_dates(decision, dates, "T_PLUS_1_CLOSE")
        # 决策落在 d2 → 成交映射到 d3，而不是 d2。
        self.assertEqual(list(filled.index), [dates[3]])
        self.assertNotIn(dates[2], filled.index)

    def test_no_lookahead_into_fill_bar(self):
        """成交 bar(d3) 自身的 +10% 涨幅不得被偷看；只能从持有期 bar(d4) 起赚。"""
        result, dates = _run_full_weight_on_A(_make_panel()[1][2])
        ret = result.returns
        # 成交日 d3：仓位在 d3 收盘才建立，gross 用建仓前持仓(空)=0，
        # 故 d2→d3 的 +10% 不被赚到，当日净值≈ -买入成本。
        self.assertAlmostEqual(ret.loc[dates[3]], -0.001, places=6)
        self.assertLess(ret.loc[dates[3]], 0.0)
        # 持有日 d4：已持 A，吃到 d3→d4 的 +10%。
        self.assertAlmostEqual(ret.loc[dates[4]], 0.10, places=6)

    def test_decision_on_last_bar_is_dropped(self):
        """末日(d5)决策没有未来 bar 可成交 → 静默丢弃，不得凭空伪造成交。"""
        result, dates = _run_full_weight_on_A(_make_panel()[1][5])
        # 全程从未建仓：换手与成本恒为 0，收益恒为 0（仅 d0 的 NaN 被 dropna）。
        self.assertEqual(result.turnover.abs().sum(), 0.0)
        self.assertEqual(result.cost.abs().sum(), 0.0)
        self.assertTrue(np.allclose(result.returns.values, 0.0))

    def test_same_day_execution_is_structurally_rejected(self):
        """无法把 execution_timing 配成 T 日收盘成交 T 日信号（R-DATA-003 结构性守卫）。"""
        close, dates = _make_panel()
        decision = pd.DataFrame({"A": [1.0]}, index=[dates[2]])
        with self.assertRaises(ValueError):
            _map_decisions_to_fill_dates(decision, dates, "T_CLOSE")

    # --- 清单 E：成本扣除（R-COST-001）---------------------------------------
    #   只断言"成本确被扣、并压低净收益"；具体费率数值唯一权威 = CostModel/cost_model.md，
    #   本测试不写死 bps，避免与成本口径权威重复。

    def test_cost_is_deducted_on_rebalance(self):
        """调仓必产生 >0 成本，且净收益 = 毛收益 − 成本（成交 bar 上净 < 毛）。"""
        result, dates = _run_full_weight_on_A(_make_panel()[1][2], buy=0.002, sell=0.002)
        # 成交日 d3 发生买入：成本 = 买入换手(1.0) × buy_cost(0.002)。
        self.assertGreater(result.cost.loc[dates[3]], 0.0)
        self.assertAlmostEqual(result.cost.loc[dates[3]], 0.002, places=6)
        # 净收益被成本压到负（毛收益此日为 0）。
        self.assertAlmostEqual(result.returns.loc[dates[3]], -0.002, places=6)
        # 持有日 d4 无新交易 → 成本≈0。
        self.assertAlmostEqual(result.cost.loc[dates[4]], 0.0, places=6)


class TestExecutionRealityGaps(unittest.TestCase):
    """A/B 组执行现实：当前**未接入** canonical 引擎，显式钉死 gap（不伪造通过）。"""

    def test_order_simulator_is_not_on_canonical_path(self):
        """钉死架构现实：core.engine 不引用 OrderSimulator → 涨跌停/停牌过滤
        不在 canonical 回测路径上。若将来接入，必须更新本断言（让 gap 可追踪）。"""
        src = (ROOT / "core" / "engine.py").read_text(encoding="utf-8")
        self.assertNotIn("OrderSimulator", src)
        self.assertNotIn("simulate_execution", src)

    def test_execution_stack_archived_not_importable(self):
        """execution 执行栈已归档至 docs/archive/execution(2026-07-18,R-ARCH-005)。
        原"OrderSimulator 只拒单不顺延"的契约钉死随模块进档案(见归档文件内测试历史)；
        活体树断言收紧为:该栈不可 import——防止孤儿模拟器被误当执行现实接回。
        复活需 DECISIONS.md 新增 ADR 并恢复本组契约断言。"""
        import importlib

        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("execution._deprecated_order_simulator")

    @unittest.skip(
        "GAP 清单A/B/G：退市归零清算 / 一字板全天封板识别 / 封板顺延累计损失 / "
        "停牌持仓冻结 —— 均未接入 canonical BacktestEngine（OrderSimulator 孤儿，"
        "且其 docstring 宣称的 T+1 结算在 simulate_execution 里并未实现）。"
        "接入前，含此类标的的微盘回测执行 gap 视为未覆盖（须人工记录，见清单'怎么用'第3条）。"
    )
    def test_canonical_engine_models_limit_board_and_delisting(self):
        raise AssertionError("未实现：占位以让 gap 在测试输出中可见、可追踪。")


if __name__ == "__main__":
    unittest.main(verbosity=2)
