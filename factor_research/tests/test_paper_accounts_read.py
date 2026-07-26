"""services/read/paper_accounts.py 多账户只读视图单元测试(T4,
PLAN_paper_multiaccount_loop.md)。

全部 hermetic:monkeypatch portfolio.paper_accounts 的 ACCOUNTS_ROOT/SUMMARY_FP/ROOT
指向临时目录,手写 nav.csv + version_returns csv 核对回测偏差算法,不依赖
data_lake/真实 paper 状态。

覆盖(对抗验收,承计划 T4):
  1. summary.json 缺失 → healthy=False + 可读 error(不是"名单健康但空"的静默空数组)
  2. summary.json 里 provision.status=rejected(stale/缺失名单)→ healthy=False
  3. 名单健康但账户列表为空 → healthy=True, accounts=[](非错误)
  4. 回测偏差手算核对:合成 nav.csv + 合成 version_returns.csv,手工验证
     cumulative_deviation/tracking_error 数值
  5. 缺 version_returns 文件 → backtest_deviation.available=False,可读原因
  6. blocked/frozen 账户:NAV 为空时诚实报告,不产生除零/异常
  7. 顺序 = list_account_metas 的既有目录字典序(前端展示顺序保证的后端来源)
用法(cwd=factor_research): python3 -m pytest tests/test_paper_accounts_read.py -q
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import portfolio.paper_accounts as pa  # noqa: E402
import services.read.paper_accounts as spa  # noqa: E402


def _write_meta(accounts_root: Path, family: str, version: str, status: str, **extra):
    d = accounts_root / f"{family}__{version}"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"family": family, "version": version, "status": status, "reason": extra.get("reason", ""),
            "opened_at": extra.get("opened_at", "2026-01-01T00:00:00+08:00"),
            "frozen_at": extra.get("frozen_at", ""), "last_update_date": extra.get("last_update_date", "")}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def _write_nav_csv(account_dir: Path, rows: list[tuple[str, float, float]]):
    """rows: [(date, nav, total_return), ...]"""
    fp = account_dir / "nav.csv"
    with fp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "nav", "cash", "position_value", "total_return"])
        for date, nav, ret in rows:
            w.writerow([date, f"{nav:.2f}", "0.00", f"{nav:.2f}", f"{ret:.6f}"])
    return fp


def _write_version_returns(root: Path, family: str, version: str, rows: list[tuple[str, float]]):
    d = root / "data_lake" / "version_returns"
    d.mkdir(parents=True, exist_ok=True)
    fp = d / f"{family}__{version}.csv"
    with fp.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["", "ret"])
        for date, ret in rows:
            w.writerow([date, ret])
    return fp


@pytest.fixture
def hermetic_root(tmp_path, monkeypatch):
    """把 portfolio.paper_accounts 的三个模块级路径常量指向临时目录。"""
    accounts_root = tmp_path / "accounts"
    summary_fp = accounts_root / "summary.json"
    monkeypatch.setattr(pa, "ACCOUNTS_ROOT", accounts_root)
    monkeypatch.setattr(pa, "SUMMARY_FP", summary_fp)
    monkeypatch.setattr(pa, "ROOT", tmp_path)
    return tmp_path, accounts_root, summary_fp


# ─────────────────────────── 1/2/3. 三态诚实 ───────────────────────────

def test_missing_summary_is_unhealthy_not_silent_empty(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    view = spa.list_paper_accounts()
    assert view.healthy is False
    assert "不存在" in view.error
    assert view.accounts == []
    print(f"✅ summary.json 缺失 → healthy=False,可读原因:{view.error}")


def test_rejected_provision_in_summary_is_unhealthy(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "rejected", "reason": "recompose 产物已过期", "accounts": []},
        "update": {"ran": False, "reason": "provision_rejected"},
    }), encoding="utf-8")
    view = spa.list_paper_accounts()
    assert view.healthy is False
    assert "过期" in view.error
    assert view.accounts == []
    print(f"✅ summary.json 里 provision=rejected → healthy=False,原因透传:{view.error}")


def test_healthy_empty_accounts_is_not_error(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "", "accounts": []},
        "update": {"ran": False, "reason": "no_candidates"},
    }), encoding="utf-8")
    view = spa.list_paper_accounts()
    assert view.healthy is True
    assert view.error == ""
    assert view.accounts == []
    print("✅ 名单健康但空 → healthy=True,accounts=[](非错误)")


# ─────────────────────────── 4/5. 回测偏差手算核对 ───────────────────────────

def test_backtest_deviation_matches_hand_calculation(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "", "accounts": [{"family": "f", "version": "v1.0", "status": "active"}]},
    }), encoding="utf-8")
    acc_dir = _write_meta(accounts_root, "f", "v1.0", "active")
    # paper NAV:6 个交易日,每日 +1%(total_return 由本函数手算写入,复现 upsert_nav
    # 口径 nav/init_capital-1,而非在这里重新发明另一套计算)。
    daily_ret = 0.01
    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    navs = [1_000_000.0]
    for _ in range(len(dates) - 1):
        navs.append(navs[-1] * (1 + daily_ret))
    nav_rows = [(d, nav, nav / 1_000_000.0 - 1.0) for d, nav in zip(dates, navs, strict=True)]
    _write_nav_csv(acc_dir, nav_rows)
    # 回测收益序列同窗:每日 +0.5%(与 paper 逐日 +1% 有稳定差,便于手算 tracking_error)
    bt_daily_ret = 0.005
    bt_rows = [(d, bt_daily_ret) for d in dates]
    _write_version_returns(tmp_path, "f", "v1.0", bt_rows)

    view = spa.list_paper_accounts()
    assert view.healthy is True
    assert len(view.accounts) == 1
    dev = view.accounts[0].backtest_deviation
    assert dev["available"] is True

    # 手算地面真值(容差按实现的 round(...,6) 口径 + nav.csv 落盘 .2f 截断放宽到 1e-5)
    paper_cum_expected = navs[-1] / 1_000_000.0 - 1.0                    # (1.01)^5 - 1
    bt_cum_expected = (1.0 + bt_daily_ret) ** len(dates) - 1.0           # (1.005)^6 - 1
    assert abs(dev["paper_cumulative_return"] - paper_cum_expected) < 1e-5
    assert abs(dev["backtest_cumulative_return"] - bt_cum_expected) < 1e-5
    assert abs(dev["cumulative_deviation"] - (paper_cum_expected - bt_cum_expected)) < 1e-5

    # tracking_error 手算:paper 逐日收益 = [0, 0.01, 0.01, 0.01, 0.01, 0.01]
    # (第一天 total_return 定义为 0,后续逐日 = daily_ret);bt 逐日收益全部 = bt_daily_ret。
    import numpy as np
    paper_daily = [0.0] + [daily_ret] * (len(dates) - 1)
    bt_daily = [bt_daily_ret] * len(dates)
    diff = np.array(paper_daily) - np.array(bt_daily)
    expected_te = float(np.std(diff, ddof=1) * (252 ** 0.5))
    assert dev["tracking_error"] is not None
    assert abs(dev["tracking_error"] - expected_te) < 1e-6
    print(f"✅ 回测偏差手算核对通过:cumulative_deviation={dev['cumulative_deviation']}, "
          f"tracking_error={dev['tracking_error']}(与手算地面真值一致)")


def test_missing_version_returns_reports_unavailable_reason(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "", "accounts": [{"family": "f", "version": "v9.9", "status": "active"}]},
    }), encoding="utf-8")
    acc_dir = _write_meta(accounts_root, "f", "v9.9", "active")
    _write_nav_csv(acc_dir, [("2026-01-02", 1_000_000.0, 0.0), ("2026-01-03", 1_005_000.0, 0.005)])
    # 不写 version_returns 文件

    view = spa.list_paper_accounts()
    dev = view.accounts[0].backtest_deviation
    assert dev["available"] is False
    assert "version_returns" in dev["reason"]
    print(f"✅ 缺 version_returns → available=False,可读原因:{dev['reason']}")


# ─────────────────────────── 6. blocked/frozen 账户诚实空态 ───────────────────────────

def test_blocked_account_has_no_nav_and_honest_deviation_reason(hermetic_root):
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "",
                     "accounts": [{"family": "f", "version": "v2.0", "status": "blocked"}]},
    }), encoding="utf-8")
    _write_meta(accounts_root, "f", "v2.0", "blocked", reason="no_executable_spec: registry 版本无 executable_spec.spec 字段")
    # 不写 nav.csv(blocked 账户从未产生估值)

    view = spa.list_paper_accounts()
    assert len(view.accounts) == 1
    acc = view.accounts[0]
    assert acc.status == "blocked"
    assert acc.nav_points == []
    assert acc.latest_nav == 0.0
    assert acc.max_drawdown == 0.0
    assert acc.backtest_deviation["available"] is False
    assert "无 NAV 记录" in acc.backtest_deviation["reason"]
    print(f"✅ blocked 账户诚实空态:无 NAV/无假回撤/回测偏差原因可读:{acc.backtest_deviation['reason']}")


# ─────────────────────────── 7. 顺序 = recompose 排名顺序(非目录字典序) ───────────────────────────

def test_accounts_order_matches_recompose_rank_not_alphabetical(hermetic_root):
    """「排名靠前策略并排实测」——展示顺序必须是 recompose 排名顺序,不是账户
    目录名的字典序。用 zzz-fam(排名靠前)vs aaa-fam(排名靠后)制造两者冲突的
    场景:若实现退化成按目录名字典序(aaa < zzz),本用例必须挂,专治"顺序来源
    看似正确、实则抄了错误的排序键"这类静默倒退。
    """
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "",
                     "accounts": [
                         {"family": "zzz-fam", "version": "v1.0", "status": "active"},  # 排名 #1
                         {"family": "aaa-fam", "version": "v1.0", "status": "active"},  # 排名 #2
                     ]},
    }), encoding="utf-8")
    # 故意反向创建目录(字典序 aaa < zzz),确保"顺序恰好和字典序一致"不会掩盖 bug。
    _write_meta(accounts_root, "aaa-fam", "v1.0", "active")
    _write_meta(accounts_root, "zzz-fam", "v1.0", "active")

    view = spa.list_paper_accounts()
    names = [a.name for a in view.accounts]
    assert names == ["zzz-fam.v1.0", "aaa-fam.v1.0"], \
        f"展示顺序必须是 recompose 排名顺序(zzz 排名靠前);实际 {names}" \
        "(若为 ['aaa-fam.v1.0', 'zzz-fam.v1.0'] 则说明退化成了目录字典序)"
    print(f"✅ 账户顺序 = recompose 排名顺序(非目录字典序):{names}")


def test_accounts_order_appends_unranked_accounts_without_dropping(hermetic_root):
    """summary.json 里没出现的账户(防御性场景,理论上不该发生)不得被丢弃——
    追加在已排名账户之后,保留可见性而非静默消失。
    """
    tmp_path, accounts_root, summary_fp = hermetic_root
    accounts_root.mkdir(parents=True, exist_ok=True)
    summary_fp.write_text(json.dumps({
        "generated_at": "2026-07-01T00:00:00+08:00",
        "provision": {"status": "ok", "reason": "",
                     "accounts": [{"family": "ranked-fam", "version": "v1.0", "status": "active"}]},
    }), encoding="utf-8")
    _write_meta(accounts_root, "ranked-fam", "v1.0", "active")
    _write_meta(accounts_root, "orphan-fam", "v1.0", "frozen")  # 不在 summary.provision.accounts 里

    view = spa.list_paper_accounts()
    names = [a.name for a in view.accounts]
    assert names == ["ranked-fam.v1.0", "orphan-fam.v1.0"], \
        f"未在 summary 排名列表里的账户应追加在末尾,不得丢失;实际 {names}"
    print(f"✅ 未排名账户追加末尾不丢失:{names}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
