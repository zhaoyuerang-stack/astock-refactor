"""模拟盘债券 ETF 轮动(P5)单元测试——纯逻辑,monkeypatch 价格函数,不依赖 data_lake。

覆盖:
  1. BEAR 稳态:全现金 + bond.enabled → 闲置现金整手买入 511010(费率 etf_buy_cost)
  2. BULL 转换:持有债券 + bond 关闭 + target 股票 → 先卖光 ETF 资金回笼,再买股
  3. 估值:valuation 含债券市值,detail 行带 asset="etf"
  4. 背离日:bond.enabled + target 非空 → 先买股,余钱买债
  5. 向后兼容:bond=None → 不产生任何债券交易(行为与旧版一致)
  6. estimate_bond_order 整手/费率计算正确
用法(cwd=factor_research): python3 tests/test_paper_etf.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from contracts.views import BondInstructionView  # noqa: E402
from portfolio import paper_engine as pe  # noqa: E402
from scripts.ops import paper_trade as pt  # noqa: E402

BOND_PX = 141.0     # ETF 不复权参考价
STOCK_PX = 10.0


def _patch():
    """股票/ETF 价格全部打桩:股票 10 元、ETF 141 元,永不停牌不涨跌停。"""
    pe.get_etf_fill = lambda code, date, mode=None: BOND_PX
    pe.get_etf_close = lambda code, date: BOND_PX
    pe.buyable_open = lambda code, date, name: STOCK_PX
    pe.sellable_open = lambda code, date, name: STOCK_PX
    pe.get_close = lambda code, date: STOCK_PX


def setup_function():
    _patch()


def _acc(cash=1_000_000.0, positions=None, bond=None):
    return {"init_capital": 1_000_000.0, "inception": "2026-01-01", "cash": cash,
            "positions": positions or {}, "pending": None, "last_date": None, "bond": bond}


DEFENSIVE_AUTH = {
    "role": "defensive",
    "family": "defensive-ma16-bond",
    "version": "v1.0",
    "spec_hash": "d" * 64,
}
BOND = {"enabled": True, "code": "511010", "name": "国债ETF", "authorization": DEFENSIVE_AUTH}
NO_BOND = {"enabled": False, "code": "511010", "name": "国债ETF", "authorization": DEFENSIVE_AUTH}
LEGACY_BOND = {"enabled": True, "code": "511010", "name": "国债ETF"}


def test_bear_buys_bond():
    acc = _acc()
    trades, blocked = [], []
    pe.execute_to_target(acc, "2026-06-11", [], 25, {}, trades, blocked, leverage=0.0, bond=BOND)
    assert not blocked, blocked
    assert acc["bond"] and acc["bond"]["code"] == "511010"
    sh = acc["bond"]["shares"]
    assert sh % pe.LOT == 0 and sh > 0
    # 整手约束下买不超现金:notional + cost <= 100万
    notional = sh * BOND_PX
    assert notional + notional * pe.ETF_BUY_COST <= 1_000_000.0 + 1e-6
    # 再加一手就超:验证"全部闲置资金"语义
    n2 = (sh + pe.LOT) * BOND_PX
    assert n2 * (1 + pe.ETF_BUY_COST) > 1_000_000.0
    assert len(trades) == 1 and trades[0][3] == "BUY" and trades[0][1] == "511010"
    print(f"✅ BEAR 稳态:闲置现金整手买入 511010({sh} 份,费率 {pe.ETF_BUY_COST:.2%})")


def test_legacy_bond_signal_without_defensive_authorization_is_blocked():
    acc = _acc()
    trades, blocked = [], []
    pe.execute_to_target(acc, "2026-06-11", [], 25, {}, trades, blocked, leverage=0.0, bond=LEGACY_BOND)
    assert acc.get("bond") is None, "未授权债券轮动不得新建 ETF 仓位"
    assert trades == []
    assert blocked == [("买入", "511010", "国债ETF", "defensive overlay 未授权,拒绝债券轮动")]
    print("✅ legacy bond.enabled 无独立 defensive 授权时 fail-closed")


def test_legacy_bond_holding_is_not_sold_without_defensive_authorization():
    acc = _acc(cash=10_000.0, bond={"code": "511010", "name": "国债ETF", "shares": 7000, "avg_cost": 140.0})
    trades, blocked = [], []
    pe.execute_to_target(acc, "2026-06-11", ["600001"], 1, {}, trades, blocked,
                         leverage=1.0, bond={"enabled": False, "code": "511010", "name": "国债ETF"})
    assert acc["bond"]["shares"] == 7000, "未授权信号不得触发卖出遗留债券"
    assert all(t[1] != "511010" for t in trades)
    print("✅ legacy 债券持仓无独立 defensive 授权时不被当成可执行轮动")


def test_bull_sells_bond_then_buys_stocks():
    acc = _acc(cash=10_000.0, bond={"code": "511010", "name": "国债ETF", "shares": 7000, "avg_cost": 140.0})
    trades, blocked = [], []
    # leverage 0.9:等权预算留余量(leverage=1.0 时末腿会因成本差零头买不进——既有引擎行为,生产 25 腿靠整手取整余量覆盖)
    pe.execute_to_target(acc, "2026-06-11", ["600001", "600002"], 2, {}, trades, blocked,
                         leverage=0.9, bond=NO_BOND)
    assert acc["bond"] is None, "BULL 转换必须卖光债券"
    assert trades[0][1] == "511010" and trades[0][3] == "SELL", "必须先卖债再买股"
    stock_buys = [t for t in trades if t[3] == "BUY"]
    assert len(stock_buys) == 2, stock_buys
    # 买股预算用上了卖债回笼的资金(各腿 ≈ (1万 + 98.7万卖债净额)×0.9/2 ≫ 原现金 1万)
    assert all(t[6] > 100_000 for t in stock_buys)
    print(f"✅ BULL 转换:先卖光 ETF(回笼 {trades[0][6]:,.0f})→ 买入 {len(stock_buys)} 只股票")


def test_valuation_includes_bond():
    acc = _acc(cash=1_000.0, bond={"code": "511010", "name": "国债ETF", "shares": 7000, "avg_cost": 140.0})
    nav, pos_value, detail = pe.valuation(acc, "2026-06-11")
    assert abs(pos_value - 7000 * BOND_PX) < 1e-6
    assert abs(nav - (1_000.0 + 7000 * BOND_PX)) < 1e-6
    etf_rows = [d for d in detail if d.get("asset") == "etf"]
    assert len(etf_rows) == 1 and etf_rows[0]["name"] == "国债ETF"
    assert abs(etf_rows[0]["pnl"] - 7000 * (BOND_PX - 140.0)) < 1e-6
    print(f"✅ 估值:nav 含债券市值 {pos_value:,.0f},detail 带 asset=etf")


def test_divergence_day_stocks_first_then_bond():
    acc = _acc()
    trades, blocked = [], []
    pe.execute_to_target(acc, "2026-06-11", ["600001"], 2, {}, trades, blocked,
                         leverage=1.0, bond=BOND)
    # 顺序:先买股(预算 1/top_n),余钱买债
    assert trades[0][3] == "BUY" and trades[0][1] == "600001"
    assert trades[-1][1] == "511010" and trades[-1][3] == "BUY"
    assert acc["bond"]["shares"] > 0
    print("✅ 背离日:先满足股票腿,剩余闲置现金进债券")


def test_bond_none_backward_compat():
    acc = _acc()
    trades, blocked = [], []
    pe.execute_to_target(acc, "2026-06-11", [], 25, {}, trades, blocked, leverage=0.0, bond=None)
    assert not trades and not blocked and acc.get("bond") is None
    assert acc["cash"] == 1_000_000.0
    print("✅ 向后兼容:bond=None 行为与旧版一致(零交易)")


def test_estimate_bond_order():
    est = pe.estimate_bond_order("2026-06-11", 1_000_000.0, "511010")
    assert est is not None
    sh, ref, amt = est
    assert ref == BOND_PX and sh % pe.LOT == 0 and abs(amt - sh * BOND_PX) < 1e-6
    assert sh * BOND_PX * (1 + pe.ETF_BUY_COST) <= 1_000_000.0
    assert pe.estimate_bond_order("2026-06-11", 100.0) is None  # 不足一手
    print(f"✅ estimate_bond_order:{sh} 份 × {ref} = {amt:,.0f}(整手 + 费率约束)")


def test_pending_bond_disables_legacy_recommendation_without_authorization():
    signal = {
        "rotation": {
            "recommend_bond": True,
            "bond_code": "511010",
            "bond_name": "国债ETF",
        }
    }
    pend = pt.build_pending_bond(signal)
    assert pend["requested"] is True
    assert pend["enabled"] is False
    assert pend["blocked_reason"] == "defensive overlay 未授权,拒绝债券轮动"
    assert "authorization" not in pend


def test_pending_bond_preserves_defensive_authorization():
    signal = {
        "rotation": {
            "recommend_bond": True,
            "bond_code": "511010",
            "bond_name": "国债ETF",
            "defensive_authorization": DEFENSIVE_AUTH,
        }
    }
    pend = pt.build_pending_bond(signal)
    assert pend["requested"] is True
    assert pend["enabled"] is True
    assert pend["authorization"] == DEFENSIVE_AUTH


def test_render_card_does_not_recommend_bond_without_authorization():
    signal = {
        "date": "2026-06-11",
        "strategy_version": "v3.1",
        "in_market": False,
        "top_n": 25,
        "small_index_vs_ma16": -0.01,
        "regime_dist": -0.01,
        "regime": "bear",
        "rotation": {
            "current_regime": "bear",
            "recommend_bond": False,
            "bond_code": "",
            "bond_name": "",
            "note": "未授权独立 defensive overlay;债券轮动非现行可执行",
        },
    }
    acc = _acc()
    acc["pending"] = {"bond": pt.build_pending_bond(signal)}
    card = pt.render_card(
        "2026-06-11", signal, None, acc, 1_000_000.0, 0.0, [], [], [], {}, None
    )
    assert "**建议**: 空仓资金配置" not in card
    assert "债券轮动非现行可执行" in card


def test_bond_instruction_view_exposes_authorization_state():
    view = BondInstructionView(
        active=True,
        side="BLOCKED",
        code="511010",
        name="国债ETF",
        authorized=False,
        blocked_reason="defensive overlay 未授权,拒绝债券轮动",
    )
    assert view.authorized is False
    assert view.blocked_reason == "defensive overlay 未授权,拒绝债券轮动"


if __name__ == "__main__":
    _patch()
    test_bear_buys_bond()
    test_bull_sells_bond_then_buys_stocks()
    test_valuation_includes_bond()
    test_divergence_day_stocks_first_then_bond()
    test_bond_none_backward_compat()
    test_estimate_bond_order()
    print("\n🎉 Paper ETF rotation tests passed!")
