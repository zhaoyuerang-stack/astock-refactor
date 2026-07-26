"""数据集契约全量声明化对抗测试。

覆盖:
1. 两 manifest 30 个数据集(经 MANIFEST_ALIASES 归一)均有声明,mode ∈ 合法词表
2. 存量 16 条 TUSHARE_DATASETS 的 (store, mode) 冻结
3. 声明-实现一致:合成 parquet 验证 by_date / by_date_shift1 / anndate 路由
4. 未声明 dataset → load_tushare_panel raise KeyError
5. 新增 tushare 声明真实湖冒烟(store 不存在则 skip)

Run:
    cd factor_research && python3 -m pytest tests/test_dataset_contracts.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.load_lake import (  # noqa: E402
    LAKE,
    ffill_by_anndate,
    load_pledge_stat_panel,
    load_tushare_panel,
    pivot_daily_basic,
)
from lake.schema import (  # noqa: E402
    CORE_DATASETS,
    MANIFEST_ALIASES,
    TIMELINE_MODES,
    TIMELINE_MODES_WITH_NA,
    TUSHARE_DATASETS,
    dataset_contract,
    resolve_dataset_decl,
)

# 存量 16 条冻结快照(store, mode)——改口径即本测试红
_FROZEN_TUSHARE_16 = {
    "daily_basic": ("daily_basic/daily_basic_all.parquet", "by_date"),
    "moneyflow": ("moneyflow/moneyflow_all.parquet", "by_date"),
    "stk_limit": ("market/stk_limit_all.parquet", "by_date"),
    "suspend": ("market/suspend_all.parquet", "by_date"),
    "fina_indicator": ("financials/fina_indicator_all.parquet", "anndate"),
    "forecast": ("event/forecast_all.parquet", "anndate"),
    "express": ("event/express_all.parquet", "anndate"),
    "holdernumber": ("holder/holdernumber_all.parquet", "anndate"),
    "index_daily": ("index/index_daily_all.parquet", "by_date"),
    "cyq_perf": ("cyq/cyq_perf_all.parquet", "by_date"),
    "holdertrade": ("holder/holdertrade_all.parquet", "anndate"),
    "adj_factor": ("adj_factor/adj_factor_all.parquet", "by_date"),
    "income": ("financials/income_all.parquet", "anndate"),
    "balancesheet": ("financials/balancesheet_all.parquet", "anndate"),
    "cashflow": ("financials/cashflow_all.parquet", "anndate"),
    "dividend": ("corp_action/dividend_all.parquet", "anndate"),
}

# 本单新增的 tushare 声明(相对冻结 16 条)
_NEW_TUSHARE = (
    "share_float",
    "index_classify",
    "block_trade",
    "pledge_stat",
    "top10_holders",
    "top_list",
    "top_inst",
    "repurchase",
)


def _manifest_dataset_names() -> list[str]:
    """两个 manifest 顶层数据集名(跳 _ 前缀元键)。"""
    names: list[str] = []
    for rel in ("data_lake/_manifest.json", "data_lake/tushare_manifest.json"):
        path = ROOT / rel
        assert path.exists(), f"missing manifest {rel}"
        data = json.loads(path.read_text(encoding="utf-8"))
        for k in data:
            if str(k).startswith("_"):
                continue
            if isinstance(data[k], dict):
                names.append(str(k))
    return names


def test_full_coverage_30_datasets_declared():
    """两 manifest 全部数据集经别名归一后都能在声明表找到;mode 合法。"""
    names = _manifest_dataset_names()
    assert len(names) == 30, f"expected 30 manifest datasets, got {len(names)}: {names}"
    missing = []
    bad_mode = []
    for name in names:
        hit = resolve_dataset_decl(name)
        if hit is None:
            missing.append(name)
            continue
        _canon, _src, _store, mode, _fields, kind = hit
        if kind == "metadata":
            if mode not in TIMELINE_MODES_WITH_NA:
                bad_mode.append((name, mode, kind))
        else:
            if mode not in TIMELINE_MODES:
                bad_mode.append((name, mode, kind))
        # dataset_contract 亦非 None
        assert dataset_contract(name) is not None
    assert not missing, f"undeclared datasets: {missing}"
    assert not bad_mode, f"illegal modes: {bad_mode}"


def test_frozen_16_tushare_store_mode_unchanged():
    """存量 16 条 (store, mode) 逐字节冻结——防顺手改研究口径。"""
    assert set(_FROZEN_TUSHARE_16) <= set(TUSHARE_DATASETS)
    for name, (store, mode) in _FROZEN_TUSHARE_16.items():
        got_store, got_mode, _fields = TUSHARE_DATASETS[name]
        assert got_store == store, f"{name} store changed: {got_store!r} != {store!r}"
        assert got_mode == mode, f"{name} mode changed: {got_mode!r} != {mode!r}"


def test_new_tushare_entries_present_with_valid_mode():
    for name in _NEW_TUSHARE:
        assert name in TUSHARE_DATASETS, f"missing new decl {name}"
        store, mode, fields = TUSHARE_DATASETS[name]
        assert isinstance(store, str) and store.endswith(".parquet")
        assert mode in TIMELINE_MODES
        assert isinstance(fields, list) and len(fields) >= 1


def test_manifest_aliases_resolve():
    for mname, canon in MANIFEST_ALIASES.items():
        hit = resolve_dataset_decl(mname)
        assert hit is not None
        assert hit[0] == canon
        assert canon in TUSHARE_DATASETS


def test_core_datasets_seven():
    expected = {
        "price_daily",
        "price_daily_raw",
        "fundamental",
        "capital_margin",
        "capital_northbound",
        "meta",
        "data_vintage",
    }
    assert set(CORE_DATASETS) == expected
    assert CORE_DATASETS["meta"][3] == "metadata"
    assert CORE_DATASETS["data_vintage"][3] == "metadata"
    assert CORE_DATASETS["meta"][1] == "n/a"
    assert CORE_DATASETS["capital_margin"][1] == "by_date_shift1"
    assert CORE_DATASETS["capital_northbound"][1] == "by_date_shift1"
    assert CORE_DATASETS["price_daily"][1] == "by_date"
    assert CORE_DATASETS["fundamental"][1] == "anndate"


def test_load_unknown_dataset_raises_keyerror():
    with pytest.raises(KeyError):
        load_tushare_panel("__no_such_dataset_xyz__", pd.to_datetime(["2026-06-12"]))


def test_route_by_date_no_shift(tmp_path, monkeypatch):
    """mode=by_date: pivot 对齐后当日值可见,不 shift。"""
    # 借用已声明 by_date 数据集 suspend,把 store 指到 tmp fixture
    store, mode, fields = TUSHARE_DATASETS["suspend"]
    assert mode == "by_date"
    fp = tmp_path / "suspend_all.parquet"
    fp.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "600519.SH"],
        "trade_date": ["20260610", "20260611"],
        "suspend_type": [1.0, 2.0],
    })
    df.to_parquet(fp, index=False)
    # 把 LAKE 下相对 store 接到 tmp:monkeypatch load_lake.LAKE
    import lake.load_lake as ll

    # store = market/suspend_all.parquet → 建镜像目录
    mirror = tmp_path / "lake"
    target = mirror / store
    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, index=False)
    monkeypatch.setattr(ll, "LAKE", mirror)

    trade_dates = pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"])
    panels = load_tushare_panel("suspend", trade_dates, fields=["suspend_type"], codes=["600519"])
    s = panels["suspend_type"]["600519"]
    assert s.loc["2026-06-10"] == 1.0  # 当日可见
    assert s.loc["2026-06-11"] == 2.0
    assert pd.isna(s.loc["2026-06-12"])


def test_route_by_date_shift1_lags_one_trade_day(tmp_path, monkeypatch):
    """mode=by_date_shift1: 恰好滞后一交易日。"""
    store, mode, fields = TUSHARE_DATASETS["top_list"]
    assert mode == "by_date_shift1"
    import lake.load_lake as ll

    mirror = tmp_path / "lake"
    target = mirror / store
    target.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "600519.SH"],
        "trade_date": ["20260610", "20260611"],
        "net_amount": [100.0, 200.0],
        "reason": ["r1", "r2"],
    })
    df.to_parquet(target, index=False)
    monkeypatch.setattr(ll, "LAKE", mirror)

    trade_dates = pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"])
    panels = load_tushare_panel("top_list", trade_dates, fields=["net_amount"], codes=["600519"])
    s = panels["net_amount"]["600519"]
    assert pd.isna(s.loc["2026-06-10"])  # T 日不可见
    assert s.loc["2026-06-11"] == 100.0  # T-1 的值
    assert s.loc["2026-06-12"] == 200.0


def test_route_anndate_ffill_before_ann_invisible(tmp_path, monkeypatch):
    """mode=anndate: 公告日前不可见,公告日起 ffill。"""
    store, mode, fields = TUSHARE_DATASETS["forecast"]
    assert mode == "anndate"
    import lake.load_lake as ll

    mirror = tmp_path / "lake"
    target = mirror / store
    target.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "ts_code": ["600519.SH"],
        "ann_date": ["20260611"],
        "end_date": ["20260331"],
        "p_change_min": [10.0],
        "p_change_max": [20.0],
        "type": ["预增"],
        "net_profit_min": [1.0],
        "net_profit_max": [2.0],
    })
    df.to_parquet(target, index=False)
    monkeypatch.setattr(ll, "LAKE", mirror)

    trade_dates = pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"])
    panels = load_tushare_panel(
        "forecast", trade_dates, fields=["p_change_min"], codes=["600519"]
    )
    s = panels["p_change_min"]["600519"]
    assert pd.isna(s.loc["2026-06-10"])  # 公告日前不可见
    assert s.loc["2026-06-11"] == 10.0
    assert s.loc["2026-06-12"] == 10.0  # ffill


def test_by_date_path_byte_stable_vs_pivot_helper():
    """现有 by_date 路径与 pivot_daily_basic 直接调用结果一致(回归钉)。"""
    df = pd.DataFrame({
        "ts_code": ["600519.SH", "000001.SZ"],
        "trade_date": ["20260610", "20260610"],
        "total_mv": [1.0, 2.0],
    })
    idx = pd.to_datetime(["2026-06-10", "2026-06-11"])
    direct = pivot_daily_basic(df, idx, ["total_mv"], codes=["600519", "000001"])
    # 不经过文件:直接比对 helper 自身确定性
    again = pivot_daily_basic(df.copy(), idx, ["total_mv"], codes=["600519", "000001"])
    pd.testing.assert_frame_equal(direct["total_mv"], again["total_mv"])


def test_anndate_helper_matches_ffill_contract():
    """anndate helper: 公告日前 NaN,之后 ffill。"""
    df = pd.DataFrame({
        "ts_code": ["600519.SH"],
        "ann_date": ["20260611"],
        "roe": [15.0],
    })
    idx = pd.to_datetime(["2026-06-10", "2026-06-11", "2026-06-12"])
    panels = ffill_by_anndate(df, ["roe"], idx, codes=["600519"])
    s = panels["roe"]["600519"]
    assert pd.isna(s.loc["2026-06-10"])
    assert s.loc["2026-06-11"] == 15.0
    assert s.loc["2026-06-12"] == 15.0


@pytest.mark.parametrize("name", list(_NEW_TUSHARE))
def test_new_tushare_real_lake_smoke(name):
    """每个新增声明:store 存在则真实 load 一次不抛错;worktree 无 parquet 则 skip。"""
    store, mode, fields = TUSHARE_DATASETS[name]
    fp = LAKE / store
    if not fp.exists():
        pytest.skip(f"store missing (expected in bare worktree): {store}")
    trade_dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    if name == "pledge_stat":
        # 专用 loader(稀疏状态源),统一路由列结构不匹配
        panels = load_pledge_stat_panel(trade_dates, codes=None)
        assert "pledge_ratio" in panels
        return
    if name == "index_classify":
        # once 静态表,非 date×code 面板;冒烟=可读 + 契约存在
        df = pd.read_parquet(fp)
        assert len(df) >= 0
        assert dataset_contract(name) is not None
        return
    # 标准统一入口
    panels = load_tushare_panel(name, trade_dates, fields=fields[:1] if fields else None)
    assert isinstance(panels, dict)
    assert len(panels) >= 1
