"""右尾尺子接入 canonical engine.metrics 的回归测试。

覆盖三个新增/延伸字段:
- institutional_metrics: cvar_right(组合层右尾 CVaR), capture_spread(需 bench)
- winner_concentration: 选股层赢家集中度
"""
import numpy as np
import pandas as pd

from engine.metrics import institutional_metrics, metrics, winner_concentration


def _series(vals):
    idx = pd.date_range("2020-01-01", periods=len(vals), freq="B")
    return pd.Series(vals, index=idx)


def test_cvar_right_present_and_positive_on_right_skew():
    rng = np.random.default_rng(0)
    # 右偏:多数小负 + 少数大正(模拟右尾暴利)
    base = rng.normal(-0.001, 0.01, 500)
    base[::50] += 0.15  # 注入右尾暴击
    out = institutional_metrics(_series(base))
    assert "cvar_right" in out
    # 右尾 CVaR(best 5% 均值)应为正,且量级大于左尾损失(cvar_95 存的是损失正数)
    assert out["cvar_right"] > 0
    assert out["cvar_right"] > out["cvar_95"]


def test_capture_spread_only_with_bench():
    r = _series(np.random.default_rng(1).normal(0.001, 0.01, 300))
    assert "capture_spread" not in institutional_metrics(r)  # 无基准不产出
    b = _series(np.random.default_rng(2).normal(0.0005, 0.01, 300))
    with_bench = institutional_metrics(r, bench=b)
    assert "capture_spread" in with_bench
    assert np.isclose(
        with_bench["capture_spread"],
        with_bench["up_capture"] - with_bench["down_capture"],
    )


def test_metrics_propagates_right_tail_fields():
    r = _series(np.random.default_rng(3).normal(0.0008, 0.012, 400))
    out = metrics(r)  # 顶层 metrics() 合并 institutional_metrics
    assert "cvar_right" in out and "skew" in out


def test_winner_concentration_catches_and_concentrates():
    # 两个调仓期,各持 4 票;其中一票暴涨 +120%,其余平庸
    dates = [pd.Timestamp("2020-01-06"), pd.Timestamp("2020-02-03")]
    idx = pd.date_range("2020-01-06", "2020-03-02", freq="B")
    codes = ["A", "B", "C", "D"]
    close = pd.DataFrame(1.0, index=idx, columns=codes)
    close.loc[dates[1]:, "A"] = 2.2  # A 在第一期 +120%
    weights = {d: pd.Series(0.25, index=codes) for d in dates}

    out = winner_concentration(weights, close)
    assert out["n_cells"] == 8
    assert out["name_period_ret_max"] > 1.0           # 抓到 >100% 暴击
    assert out["pct_ret_gt_100pct"] > 0
    assert 0.0 < out["winners_top1_share"] <= 1.0     # 集中度有定义
    # 单赢家应主导正贡献(A 一票几乎是全部正 PnL)
    assert out["winners_top1_share"] > 0.8


def test_winner_concentration_empty_safe():
    assert winner_concentration({}, pd.DataFrame())["n_cells"] == 0
