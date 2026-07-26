"""WS4 对抗测试:审计 / holdout 权重必须用候选搜出的 portfolio_size,不是硬写 25。

护栏 C(每功能对抗性测试)四类里的三类落在这里:
① 修复真传播——旧代码硬写 top_n=25,本测试断言 portfolio_size=50 的候选建 50 只,
   旧行为(25 只)必然失败本断言(见 test_audit_weights_use_searched_size_not_hardcoded_25);
② 参数解析健壮——rebalance_freq "10D"→10、越界/缺失退默认;
③ 无 execution 块时退合法默认 (25,20),而非把缺省当成"忽略搜索"。

回归意义:任何把审计权重改回硬写 25 的改动都会打红本文件。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops.scheduled_factor_search import (  # noqa: E402
    _candidate_exec_params,
    build_weights_for_candidate,
)


def _synthetic_panel(n_days: int = 140, n_stocks: int = 150, seed: int = 7):
    """确定性合成面板:≥100 个因子日(build_rebalance_weights 的硬门槛)、
    股票数 > 100(可选出 top_n=100)、因子无 NaN(nlargest 稳定)。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    cols = [f"{i:06d}.SZ" for i in range(n_stocks)]
    rets = rng.normal(0, 0.02, size=(n_days, n_stocks))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=cols)
    factor = pd.DataFrame(rng.normal(size=(n_days, n_stocks)), index=dates, columns=cols)
    return factor, close


def test_exec_params_parses_searched_size_and_freq():
    assert _candidate_exec_params({"execution": {"portfolio_size": 50, "rebalance_freq": "10D"}}) == (50, 10)
    assert _candidate_exec_params({"execution": {"portfolio_size": 100, "rebalance_freq": "40D"}}) == (100, 40)


def test_exec_params_falls_back_when_no_execution_block():
    # 候选从未变异出 execution → 合法默认 (25, 20),不是"忽略搜索"
    assert _candidate_exec_params({"type": "linear_combo", "terms": []}) == (25, 20)
    assert _candidate_exec_params({}) == (25, 20)
    # execution 存在但缺字段 → 各自退默认
    assert _candidate_exec_params({"execution": {"portfolio_size": 35}}) == (35, 20)
    assert _candidate_exec_params({"execution": {"rebalance_freq": "5D"}}) == (25, 5)


def test_audit_weights_use_searched_size_not_hardcoded_25():
    """核心回归:portfolio_size=50 的候选,审计权重必须每期持 50 只,而非旧硬写 25。"""
    factor, close = _synthetic_panel()
    ast = {"execution": {"portfolio_size": 50, "rebalance_freq": "10D"}}
    w = build_weights_for_candidate(ast, factor, close, veto_factor=None, veto_q=0.0)

    assert len(w) > 0, "无调仓日(合成面板太短?)"
    assert all(len(s) == 50 for s in w.values()), "搜出 50 只但某调仓日持仓数≠50"

    # 对抗:旧行为(硬写 25)给出 25 只/期,与修复后 50 只不同 → 证明修复确实改变了审计口径
    from strategies.small_cap import build_rebalance_weights
    w_old25 = build_rebalance_weights(factor, close, top_n=25, rebalance_days=10, veto_factor=None, veto_q=0.0)
    assert all(len(s) == 25 for s in w_old25.values())
    assert len(next(iter(w.values()))) != len(next(iter(w_old25.values()))), "修复后与旧硬写25无差异=没生效"


def test_searched_sizes_10_and_100_both_honored():
    """网格边缘 10 / 100 都要如实建仓(owner:为什么不是 10/50/100)。"""
    factor, close = _synthetic_panel()
    for size in (10, 100):
        ast = {"execution": {"portfolio_size": size, "rebalance_freq": "20D"}}
        w = build_weights_for_candidate(ast, factor, close, veto_factor=None, veto_q=0.0)
        assert len(w) > 0
        assert all(len(s) == size for s in w.values()), f"搜出 {size} 只但持仓数不符"


# ─────────────────────────────────────────────────────────────────────────────
# WS4 item1(ADR-032):审计层 size 选择的对抗测试
#   核心红线:审计选 size **不是**选最大 N(否则就是我们特意规避的 L2 退化——
#   往适应度加容量项会漂向 top_n=100、抛弃集中的小盘 alpha)。
# ─────────────────────────────────────────────────────────────────────────────


def _synthetic_ohlcav(n_days: int = 140, n_stocks: int = 150, seed: int = 11):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    cols = [f"{i:06d}.SZ" for i in range(n_stocks)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (n_days, n_stocks)), axis=0)), index=dates, columns=cols)
    volume = pd.DataFrame(rng.uniform(1e5, 1e7, (n_days, n_stocks)), index=dates, columns=cols)
    amount = close * volume  # CNY 成交额,供容量估计
    factor = pd.DataFrame(rng.normal(size=(n_days, n_stocks)), index=dates, columns=cols)
    return close, volume, amount, factor


def test_pick_audit_size_is_not_just_max_size():
    """核心对抗:size=25 净夏普最高 → 必选 25,绝不是最大 size/最大容量的 100。"""
    from scripts.ops.scheduled_factor_search import _pick_audit_size
    sweep = {
        10: {"net_sharpe": 0.80, "net_annual": 0.15, "capacity_aum": 2e7},
        25: {"net_sharpe": 1.30, "net_annual": 0.22, "capacity_aum": 8e7},   # best sharpe
        50: {"net_sharpe": 0.95, "net_annual": 0.14, "capacity_aum": 3e8},
        100: {"net_sharpe": 0.60, "net_annual": 0.09, "capacity_aum": 9e8},  # 最大 size+容量、最差夏普
    }
    assert _pick_audit_size(sweep) == 25  # 不是 100 —— 防"退化成选最大 N"


def test_pick_audit_size_capacity_breaks_near_ties_only():
    """净夏普近似平手(5% 内)→ 取高容量;差距 >5% 时容量不得翻盘。"""
    from scripts.ops.scheduled_factor_search import _pick_audit_size
    near_tie = {
        25: {"net_sharpe": 1.00, "net_annual": 0.2, "capacity_aum": 5e7},
        50: {"net_sharpe": 0.97, "net_annual": 0.2, "capacity_aum": 4e8},  # 3% 内、容量大
    }
    assert _pick_audit_size(near_tie) == 50
    clear_win = {
        25: {"net_sharpe": 1.00, "net_annual": 0.2, "capacity_aum": 5e7},
        50: {"net_sharpe": 0.80, "net_annual": 0.2, "capacity_aum": 9e8},  # 夏普差 20%
    }
    assert _pick_audit_size(clear_win) == 25  # 容量再大也不翻盘


def test_sweep_audit_size_records_grid_width_to_ledger(tmp_path):
    """item3 对抗:扫 k 个 size 是多重检验,函数内必须把 len(grid) 记入 trial 账本。

    否则 best-of-k 选 size 而不计惩罚 = p-hacking。用注入的 tmp 账本,绝不碰真账本。
    """
    from governance.trial_ledger import honest_n_trials
    from scripts.ops.scheduled_factor_search import _AUDIT_SIZE_GRID, sweep_audit_size
    close, volume, amount, factor = _synthetic_ohlcav()
    ledger = tmp_path / "trial_ledger.jsonl"
    ast = {"execution": {"portfolio_size": 25, "rebalance_freq": "20D"}}
    chosen, sweep = sweep_audit_size(
        ast, factor, close, volume, amount, veto_factor=None, veto_q=0.0, ledger_path=ledger,
    )
    assert honest_n_trials("autoresearch", path=ledger) == len(_AUDIT_SIZE_GRID), "sweep 宽度未计入 n_trials = 隐性 p-hacking"
    assert chosen in _AUDIT_SIZE_GRID
    assert sweep and all({"net_sharpe", "net_annual", "capacity_aum"} <= set(v) for v in sweep.values())


def test_build_weights_top_n_override_wins_over_searched():
    """审计选定的 size(override)必须压过候选搜出的 size。"""
    from scripts.ops.scheduled_factor_search import build_weights_for_candidate
    factor, close = _synthetic_panel()
    ast = {"execution": {"portfolio_size": 25}}  # 候选搜出 25
    w = build_weights_for_candidate(ast, factor, close, veto_factor=None, veto_q=0.0, top_n_override=100)
    assert all(len(s) == 100 for s in w.values())  # override=100 胜出


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
