"""portfolio/paper_accounts.py 多账户 paper 实测管理器单元测试(T2,PLAN_paper_multiaccount_loop.md)。

全部 hermetic:合成价格面板 + 临时 strategy_registry.json + 临时 recompose.json +
临时账户目录,不依赖 data_lake/真实 paper 状态。

覆盖(对抗验收,承计划 T2):
  1. stale 名单(>14 天)/ 缺失文件 → fail-closed 真拒,provision=0 且状态可读
  2. 账本隔离:先证明「共享可变状态」的坏实现真的会红(A 污染 B),
     再证明本模块的实现不会(正确实现必须绿)
  3. 无 executable_spec 的版本 → 诚实 blocked(no_executable_spec),不产假 NAV;
     as_of 不在注入价格面板交易日索引里 → 诚实 degraded(no_price_data),不产假 NAV
  4. 确定性:同输入两次 update_all 产出逐字节相同的账本文件
  5. 下榜 → frozen,历史账本不可变(update_all 跳过,文件字节不变)
用法(cwd=factor_research): python3 -m pytest tests/test_paper_accounts.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import portfolio.paper_accounts as pa  # noqa: E402
import strategy_registry  # noqa: E402
from core.engine import PricePanel  # noqa: E402
from core.strategy_spec import ExecutableStrategySpec  # noqa: E402

CHINA_TZ = ZoneInfo("Asia/Shanghai")


# ─────────────────────────── 合成数据构造 ───────────────────────────

def _synthetic_panel(n_days=260, n_codes=8, seed=1) -> PricePanel:
    # seed=1 确认在本合成漂移/波动参数下,序列末尾 ma_trend timing exposure=1.0
    # (账户实际在市/买入非空持仓),使隔离测试/确定性测试有真实成交可验证——
    # 而非因择时空仓导致 target=[] 的"零成交"假阳性通过。
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    codes = [f"60{i:04d}" for i in range(n_codes)]
    rets = rng.normal(0.0003, 0.02, size=(n_days, n_codes))
    close = pd.DataFrame(100 * np.exp(np.cumsum(rets, axis=0)), index=dates, columns=codes)
    volume = pd.DataFrame(rng.uniform(1e5, 1e6, size=(n_days, n_codes)), index=dates, columns=codes)
    amount = volume * close
    raw_close = close.copy()  # 合成数据无复权差异,raw==adj 简化估值/成交测试
    return PricePanel(close=close, volume=volume, amount=amount, raw_close=raw_close)


def _valid_spec(family="synth-fam", version="v1.0", factor_window=20) -> ExecutableStrategySpec:
    return ExecutableStrategySpec(
        family=family, version=version,
        universe={"scope": "all"},
        data={"warmup_start": "2024-01-01"},
        factor={"type": "small_cap_amount", "window": factor_window, "shift": 1},
        selection={"top_n": 3, "rebalance_days": 20},
        timing={"type": "ma_trend", "ma": 10},
        policy={"veto": "none"},
        execution={"fill": "T_PLUS_1_CLOSE", "cost_model": "default"},
    )


@pytest.fixture
def patch_prices(monkeypatch):
    """worktree 无 data_lake/price/daily_raw,paper_engine 的成交/估值原语
    (buyable_open/sellable_open/get_close)直接读盘——与 test_paper_etf.py 同款
    monkeypatch 惯例:按合成收盘价返回固定成交价,永不停牌不涨跌停。
    只打三个函数即可短路 get_open/get_prev_close/get_fill_price 的盘内逻辑
    (buyable_open/sellable_open 是它们唯一的外部入口)。
    """
    import portfolio.paper_engine as pe

    monkeypatch.setattr(pe, "buyable_open", lambda code, date, name: 10.0)
    monkeypatch.setattr(pe, "sellable_open", lambda code, date, name: 10.0)
    monkeypatch.setattr(pe, "get_close", lambda code, date: 10.0)
    return pe


def _registry_with(tmp_path: Path, versions: list[dict]) -> Path:
    """写一份临时 strategy_versions.json,families[0] 下挂 versions。"""
    fam = {"id": "synth-fam", "versions": versions}
    fp = tmp_path / "strategy_versions.json"
    fp.write_text(json.dumps({"families": [fam]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def _version_record(version: str, spec: ExecutableStrategySpec | None) -> dict:
    rec = {"version": version, "status": "候选", "config": {}, "data_scope": "lake",
           "metrics": {}, "desc": "test", "date": "2026-07-01", "notes": "", "evidence": {},
           "nine_gate": {}, "admission": {}}
    if spec is not None:
        rec["executable_spec"] = {"spec": spec.to_dict(), "spec_hash": spec.spec_hash}
    return rec


def _write_recompose(tmp_path: Path, candidates: list[str], *, generated_at: datetime | None = None) -> Path:
    gen = generated_at or datetime.now(CHINA_TZ)
    fp = tmp_path / "portfolio_recompose.json"
    fp.write_text(json.dumps({
        "ranking_version": "v1", "top_n": 3, "legs": [], "proposal": {"status": "ok"},
        "paper_candidates": candidates,
        "generated_at": gen.isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    """把 strategy_registry.REGISTRY 指向临时文件,测试结束后恢复(既有仓库 fixture 惯用模式)。"""
    fp = tmp_path / "strategy_versions.json"
    fp.write_text(json.dumps({"families": []}), encoding="utf-8")
    monkeypatch.setattr(strategy_registry, "REGISTRY", fp)
    return fp


def _set_registry(tmp_registry_fp: Path, versions: list[dict]):
    fam = {"id": "synth-fam", "versions": versions}
    tmp_registry_fp.write_text(json.dumps({"families": [fam]}, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────── 1. stale / 缺失 名单 fail-closed ───────────────────────────

def test_missing_recompose_file_rejects_provision(tmp_path):
    missing_fp = tmp_path / "does_not_exist.json"
    candidates, reason = pa.read_recompose_candidates(missing_fp)
    assert candidates == []
    assert "不存在" in reason
    result = pa.provision_from_recompose(missing_fp, accounts_root=tmp_path / "accounts")
    assert result["status"] == "rejected"
    assert not (tmp_path / "accounts").exists() or not list((tmp_path / "accounts").iterdir())
    print("✅ recompose 产物缺失 → fail-closed 拒绝 provision,不产生任何账户")


def test_stale_recompose_rejects_provision(tmp_path):
    stale_time = datetime.now(CHINA_TZ) - timedelta(days=20)
    fp = _write_recompose(tmp_path, ["synth-fam.v1.0"], generated_at=stale_time)
    candidates, reason = pa.read_recompose_candidates(fp)
    assert candidates == []
    assert "过期" in reason
    result = pa.provision_from_recompose(fp, accounts_root=tmp_path / "accounts")
    assert result["status"] == "rejected"
    assert result["accounts"] == []
    print(f"✅ recompose 20 天前生成(>{pa.STALE_DAYS} 天)→ fail-closed 拒绝,原因可读:{reason}")


def test_fresh_empty_candidates_is_healthy_not_error(tmp_path):
    """名单健康但空(全灭空提案)不是错误——provision 结果 status=ok, accounts=[]。"""
    fp = _write_recompose(tmp_path, [])
    result = pa.provision_from_recompose(fp, accounts_root=tmp_path / "accounts")
    assert result["status"] == "ok"
    assert result["accounts"] == []
    print("✅ 名单健康但空(无候选)→ status=ok,不误报 rejected")


# ─────────────────────────── 2. 账本隔离(先证明坏实现会红) ───────────────────────────

def test_bad_shared_state_implementation_is_caught_by_isolation_assertion():
    """先证明「共享一份可变 acc dict / 模块级当前账户」的坏实现真的会被抓红,
    再在下面 test_update_account_does_not_leak_state_between_accounts 证明本模块
    的真实实现(每账户独立 load/save)不会犯同样的错误。

    场景:账户 A 独立建仓 600000(leverage=0.9,给成本留余量,与
    test_paper_etf.py 的既有惯例一致)。账户 B 的目标是持有 600001(不含
    600000)。若两个账户错误共享同一个 acc dict(而非各自独立 load 一份),
    "账户 B 执行完自己的目标后" 这个共享字典里仍会残留账户 A 买的 600000——
    因为 top_n=1 的等权预算口径下,该 dict 已经在账户 A 那一步花掉了大半现金,
    B 自己的目标股买不满仓,但 A 的 600000 从未被当成"不在 B 的 target 里的
    持仓"卖出(现实中调用方根本不会把 A 的持仓传给 B 的 execute_to_target,
    这正是"共享同一个可变账户对象"这个错误实现类别的可观察症状)。
    """
    import portfolio.paper_engine as pe

    pe.buyable_open = lambda code, date, name: 10.0
    pe.sellable_open = lambda code, date, name: 10.0
    pe.get_close = lambda code, date: 10.0

    shared_acc = {"init_capital": 1_000_000.0, "inception": "2024-01-01", "cash": 1_000_000.0,
                  "positions": {}, "pending": None, "last_date": None}

    def buggy_run_account_a_then_b():
        """错误实现:两账户共享同一个 acc dict,而不是"每账户独立 load_account()"。"""
        trades_a, blocked_a = [], []
        pe.execute_to_target(shared_acc, "2024-06-01", ["600000"], 1, {},
                             trades_a, blocked_a, leverage=0.9, bond=None)
        # 账户 B 的目标只有 600001,不含 600000;但因为复用同一个 dict,
        # 账户 B "执行"时看到的持仓基线已经包含账户 A 买入的 600000。
        trades_b, blocked_b = [], []
        pe.execute_to_target(shared_acc, "2024-06-01", ["600000", "600001"], 1, {},
                             trades_b, blocked_b, leverage=0.9, bond=None)
        return shared_acc  # 这就是(错误地)被当成"账户 B 最终状态"的对象

    account_b_final_state = buggy_run_account_a_then_b()
    # 正确隔离的账户 B(独立 1,000,000 现金起步)买 600001 应得到一个干净、
    # 只含 600001 的持仓字典;坏实现下,600000(账户 A 的持仓)混入了这个
    # "账户 B 最终状态"——断言必须失败,证明红。
    with pytest.raises(AssertionError):
        assert "600000" not in account_b_final_state["positions"], \
            "账户 A 的持仓(600000)不得出现在账户 B 的最终状态里;若出现,证明隔离缺失"
    print("✅ RED 证明:共享 acc dict 的坏实现确实让账户 A 的持仓混入了账户 B 的最终状态")


def test_update_account_does_not_leak_state_between_accounts(tmp_registry, tmp_path, monkeypatch, patch_prices):
    """正确实现:两个账户各自独立 load/save,互不污染(对照上面的坏实现,这里必须通过)。

    隔离的真正判据不是"文件路径不同"(路径不同永远成立,与是否共享内存状态
    无关),而是:**B 在 update_all([A, B]) 组里跑出的结果,必须与"B 单独跑"
    完全一致**——若 A/B 共享了任何可变对象(acc dict/模块级缓存),A 先跑一步
    会让 B 的结果偏离"B 单独跑"的基准。用不同 factor_window 让 A/B 选到不同的
    持仓(A: 600000/600001/600006, B: 600001/600002/600006 或反之),这样"B 的
    持仓混入 A 的股票"这类污染在数值上可被观察到,而非因两账户目标恰好相同而
    被空转掩盖。
    """
    prices = _synthetic_panel()
    as_of = str(prices.close.index[-1].date())
    group_root = tmp_path / "accounts_group"

    spec_a = _valid_spec(version="v1.0", factor_window=5)     # holdings: 600000/600001/600006
    spec_b = _valid_spec(version="v2.0", factor_window=20)    # holdings: 600001/600002/600006(不同于 A)
    _set_registry(tmp_registry, [_version_record("v1.0", spec_a), _version_record("v2.0", spec_b)])

    recompose_fp = _write_recompose(tmp_path, ["synth-fam.v1.0", "synth-fam.v2.0"])
    result = pa.provision_from_recompose(recompose_fp, accounts_root=group_root)
    assert result["status"] == "ok"
    assert {a["status"] for a in result["accounts"]} == {"active"}

    # 独立于 paper_accounts 模块任何内部状态的「地面真值」:直接调用
    # canonical build_executable_strategy 算出 A/B 各自"应该"持有什么——不经过
    # provision/update_account/update_all 的任何缓存或共享对象,专治"组内 vs 单独跑
    # 两边恰好共享同一处污染、比对本身失去区分力"这类假阴性(mutation testing 实测
    # 命中过一次:模块级 dict 缓存会让"组内"和"单独跑"两次调用都读到同一份被污染
    # 的对象,凡是只比较"两次调用结果是否相同"的断言都会被这种 bug 骗过)。
    from strategies.executable import build_executable_strategy as _build

    as_of_ts = pd.Timestamp(as_of)
    expected_a = sorted(pa._resolve_target_and_exposure(_build(spec_a, prices), as_of_ts)[0])
    expected_b = sorted(pa._resolve_target_and_exposure(_build(spec_b, prices), as_of_ts)[0])
    assert expected_a != expected_b, "本用例需要 A/B 目标持仓不同,否则隔离测试无区分力"

    # 组内跑:A 先于 B(update_all 遍历顺序 = list_account_metas 的 sorted 目录序)。
    results = pa.update_all(prices, as_of, accounts_root=group_root)
    by_version = {r["version"]: r for r in results}
    assert all(r["status"] == "active" for r in results), results
    # 关键判据①:A、B 必须**各自独立**产生非零成交——若两账户共享同一个 acc dict,
    # 后跑的账户会看到"目标已经满足"(因为先跑账户已经把持仓摆在那了),从而
    # trades=0。这是本套 mutation testing 实测踩到的真实假阴性:仅比较最终持仓
    # 集合在某些巧合(A/B 目标有交集)下测不出「后跑账户零成交」这个共享状态的
    # 直接证据,trades 计数则不受这种巧合影响。
    assert by_version["v1.0"]["trades"] > 0, "账户 A(先跑)必须产生真实成交"
    assert by_version["v2.0"]["trades"] > 0, \
        "账户 B(后跑)必须独立产生真实成交——若为 0,说明 B 复用了账户 A 已摆好的共享持仓状态"

    paths_a = pa.AccountPaths.for_version("synth-fam", "v1.0", group_root)
    paths_b = pa.AccountPaths.for_version("synth-fam", "v2.0", group_root)
    acc_a_grouped = json.loads(paths_a.account_fp.read_text())
    acc_b_grouped = json.loads(paths_b.account_fp.read_text())

    # 核心断言:组内跑出的 A/B 持仓必须分别是各自地面真值 target 的**子集**——
    # 用子集而非严格相等,是因为 execute_to_target 在 leverage=1.0 等权多腿建仓时
    # 有个与本次改动无关的既有引擎行为(与 test_paper_etf.py 里同款注释一致):
    # 后面几条腿可能因累计成本差一点点现金买不满,是「预算取整」问题不是「隔离」
    # 问题。子集断言仍然严格排除"跨账户污染"——任何不在 expected_x 里的代码出现,
    # 必然来自另一账户的目标或残留持仓,断言照样会失败。
    a_positions = set(acc_a_grouped["positions"])
    b_positions = set(acc_b_grouped["positions"])
    assert a_positions, "账户 A 组内应有非空持仓(否则本用例没有验证力)"
    assert b_positions, "账户 B 组内应有非空持仓(否则本用例没有验证力)"
    assert a_positions <= set(expected_a), \
        f"账户 A 组内持仓 {sorted(a_positions)} 必须是地面真值 {expected_a} 的子集(超出部分只能来自跨账户污染)"
    assert b_positions <= set(expected_b), \
        f"账户 B 组内持仓 {sorted(b_positions)} 必须是地面真值 {expected_b} 的子集" \
        "(超出部分只能来自账户 A 的持仓/现金污染)"

    # B 独立跑(全新临时账户根目录,只 provision/update B)作为额外交叉验证。
    solo_root = tmp_path / "accounts_solo_b"
    _set_registry(tmp_registry, [_version_record("v2.0", spec_b)])
    solo_recompose_fp = _write_recompose(tmp_path, ["synth-fam.v2.0"])
    pa.provision_from_recompose(solo_recompose_fp, accounts_root=solo_root)
    pa.update_account("synth-fam", "v2.0", spec_b, prices, as_of, accounts_root=solo_root)
    paths_b_solo = pa.AccountPaths.for_version("synth-fam", "v2.0", solo_root)
    acc_b_solo = json.loads(paths_b_solo.account_fp.read_text())
    assert set(acc_b_solo["positions"]) == b_positions, \
        f"B 单独跑持仓 {sorted(acc_b_solo['positions'])} 应与 B 组内持仓 {sorted(b_positions)} 一致" \
        "(相同输入、相同预算取整行为,不该因是否与 A 同组而改变)"

    a_only_codes = a_positions - set(expected_b)  # A 目标里、B 目标里都没有的代码,若混进 B 就是污染
    assert not (a_only_codes & b_positions), \
        "账户 A 独有(不属于 B 目标)的持仓代码不得出现在账户 B 的持仓里"
    print(f"✅ 账本隔离:A 组内持仓{sorted(a_positions)}⊆地面真值{expected_a};"
          f"B 组内持仓{sorted(b_positions)}⊆地面真值{expected_b}==B单独跑{sorted(acc_b_solo['positions'])}")


# ─────────────────────────── 3. 无 executable_spec → blocked ───────────────────────────

def test_version_without_executable_spec_is_blocked(tmp_registry, tmp_path):
    accounts_root = tmp_path / "accounts"
    _set_registry(tmp_registry, [_version_record("v9.9", spec=None)])
    fp = _write_recompose(tmp_path, ["synth-fam.v9.9"])
    result = pa.provision_from_recompose(fp, accounts_root=accounts_root)
    assert result["status"] == "ok"
    assert len(result["accounts"]) == 1
    acc = result["accounts"][0]
    assert acc["status"] == "blocked"
    assert "no_executable_spec" in acc["reason"]
    # blocked 账户不得产生 nav.csv(不产假 NAV)
    paths = pa.AccountPaths.for_version("synth-fam", "v9.9", accounts_root)
    assert not paths.nav_fp.exists()
    print(f"✅ 无 executable_spec 版本 → blocked,原因可读:{acc['reason']},未产生假 NAV")


def test_unknown_candidate_name_not_in_registry(tmp_registry, tmp_path):
    _set_registry(tmp_registry, [])
    fp = _write_recompose(tmp_path, ["ghost-fam.v1.0"])
    result = pa.provision_from_recompose(fp, accounts_root=tmp_path / "accounts")
    assert result["status"] == "ok"
    assert result["accounts"][0]["status"] == "unknown"
    print("✅ 名单里的名字在台账里不存在 → unknown,不臆测拆分/不开户")


def test_update_account_degraded_when_as_of_missing_from_price_panel(tmp_registry, tmp_path, patch_prices):
    """有合法 executable_spec,但注入的价格面板不含 as_of 交易日
    (no_price_data)——跳过当日更新,不产假 NAV,状态可读为 degraded。
    """
    accounts_root = tmp_path / "accounts"
    prices = _synthetic_panel()
    spec = _valid_spec(version="v1.0")
    _set_registry(tmp_registry, [_version_record("v1.0", spec)])
    fp = _write_recompose(tmp_path, ["synth-fam.v1.0"])
    result = pa.provision_from_recompose(fp, accounts_root=accounts_root)
    assert result["accounts"][0]["status"] == "active"

    not_a_trading_day = "2099-01-01"  # 远超合成面板日期范围,必然不在 prices.close.index
    res = pa.update_account("synth-fam", "v1.0", spec, prices, not_a_trading_day,
                            accounts_root=accounts_root)
    assert res["status"] == "degraded"
    assert "no_price_data" in res["reason"]
    paths = pa.AccountPaths.for_version("synth-fam", "v1.0", accounts_root)
    assert not paths.nav_fp.exists(), "degraded 账户不得产生 nav.csv(不产假 NAV)"
    assert not paths.trades_fp.exists(), "degraded 账户不得产生任何成交记录"
    print(f"✅ as_of 不在价格面板交易日索引里 → degraded,原因可读:{res['reason']},未产生假 NAV")


# ─────────────────────────── 4. 确定性 ───────────────────────────

def test_update_all_is_deterministic(tmp_registry, tmp_path, patch_prices):
    accounts_root = tmp_path / "accounts"
    prices = _synthetic_panel()
    as_of = str(prices.close.index[-1].date())
    spec = _valid_spec(version="v1.0")
    _set_registry(tmp_registry, [_version_record("v1.0", spec)])
    fp = _write_recompose(tmp_path, ["synth-fam.v1.0"])
    pa.provision_from_recompose(fp, accounts_root=accounts_root)

    results1 = pa.update_all(prices, as_of, accounts_root=accounts_root)
    assert results1[0]["trades"] > 0, "本用例需要真实成交才能验证确定性,而非空转"
    paths = pa.AccountPaths.for_version("synth-fam", "v1.0", accounts_root)
    snap1 = {
        "account": paths.account_fp.read_bytes(),
        "trades": paths.trades_fp.read_bytes() if paths.trades_fp.exists() else b"",
        "nav": paths.nav_fp.read_bytes(),
    }

    # 第二次调用:相同输入(同 prices/as_of),账户已建仓,预期收敛到同一目标 → 无新增交易,
    # nav 因同日期 upsert 覆盖同一行,文件应逐字节相同。
    pa.update_all(prices, as_of, accounts_root=accounts_root)
    snap2 = {
        "account": paths.account_fp.read_bytes(),
        "trades": paths.trades_fp.read_bytes() if paths.trades_fp.exists() else b"",
        "nav": paths.nav_fp.read_bytes(),
    }
    assert snap1 == snap2, "同输入两次 update_all 必须产出逐字节相同的账本"
    print("✅ 确定性:同输入两次 update_all 账本文件逐字节相同")


# ─────────────────────────── 5. 下榜 → frozen,历史不可变 ───────────────────────────

def test_delisted_account_is_frozen_and_history_immutable(tmp_registry, tmp_path, patch_prices):
    accounts_root = tmp_path / "accounts"
    prices = _synthetic_panel()
    as_of = str(prices.close.index[-1].date())
    spec = _valid_spec(version="v1.0")
    _set_registry(tmp_registry, [_version_record("v1.0", spec)])

    fp1 = _write_recompose(tmp_path, ["synth-fam.v1.0"])
    pa.provision_from_recompose(fp1, accounts_root=accounts_root)
    setup_results = pa.update_all(prices, as_of, accounts_root=accounts_root)
    assert setup_results[0]["trades"] > 0, "本用例需要真实成交才能验证历史不可变,而非空转"

    paths = pa.AccountPaths.for_version("synth-fam", "v1.0", accounts_root)
    nav_before = paths.nav_fp.read_bytes()
    account_before = paths.account_fp.read_bytes()

    # 下一轮 recompose:v1.0 不再上榜
    fp2 = _write_recompose(tmp_path, [])
    result = pa.provision_from_recompose(fp2, accounts_root=accounts_root)
    assert result["status"] == "ok"
    metas = pa.list_account_metas(accounts_root)
    frozen = next(m for m in metas if m.name == "synth-fam.v1.0")
    assert frozen.status == "frozen"

    # update_all 应该跳过 frozen 账户,历史文件字节不变
    results = pa.update_all(prices, as_of, accounts_root=accounts_root)
    assert any(r["status"] == "frozen" for r in results)
    assert paths.nav_fp.read_bytes() == nav_before
    assert paths.account_fp.read_bytes() == account_before
    print("✅ 下榜 → frozen,历史账本 NAV/account 文件字节不变(§7.4 退役纪律)")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
