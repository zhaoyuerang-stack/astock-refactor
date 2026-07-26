"""scripts/ops/paper_accounts_update.py + scheduled_daily_update.py 旁路挂载单元测试
(T3,PLAN_paper_multiaccount_loop.md)。

全部 hermetic:monkeypatch portfolio.paper_accounts 的顶层函数(provision_from_recompose/
update_all)与 subprocess.run,不联网、不读数据湖、不生成真实价量面板。

覆盖(对抗验收,承计划 T3):
  1. provision 被拒(stale/缺失)→ paper_accounts_update 不加载价格面板/不调用
     update_all,summary.json 如实记录 rejected,不产生假账本
  2. 名单健康但空(status=ok, accounts=[])→ 不加载价格面板,summary 记 no_candidates
  3. 正常路径:provision ok 且有候选 → 加载面板 → update_all → summary 落盘且形状正确
  4. dry-run:只 provision,不加载面板/不 update_all
  5. 调度旁路(scheduled_daily_update.run_paper_accounts_update)——子进程失败被吞掉,
     不传播为日更 failed(与既有 run_paper_forward_smallcap 同款纪律)
用法(cwd=factor_research): python3 -m pytest tests/test_paper_accounts_update.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ops import paper_accounts_update as pau  # noqa: E402

# ─────────────────────────── 1/2/3/4. paper_accounts_update.run_paper_accounts_update ───────────────────────────

def test_rejected_provision_skips_price_loading_and_update(tmp_path, monkeypatch):
    """provision 被拒(stale/缺失)→ 不得加载价格面板、不得调用 update_all。"""
    summary_fp = tmp_path / "summary.json"
    with mock.patch("portfolio.paper_accounts.provision_from_recompose",
                    return_value={"status": "rejected", "reason": "过期", "accounts": []}) as m_provision, \
         mock.patch("portfolio.paper_accounts.update_all") as m_update, \
         mock.patch.object(pau, "_load_prices") as m_load_prices, \
         mock.patch("portfolio.paper_accounts.SUMMARY_FP", summary_fp):
        summary = pau.run_paper_accounts_update()

    assert summary["provision"]["status"] == "rejected"
    assert summary["update"]["ran"] is False
    assert summary["update"]["reason"] == "provision_rejected"
    m_provision.assert_called_once()
    m_load_prices.assert_not_called()
    m_update.assert_not_called()
    assert summary_fp.exists()
    on_disk = json.loads(summary_fp.read_text())
    assert on_disk == summary
    print("✅ provision 被拒 → 不加载价格面板/不调用 update_all,summary.json 如实记录 rejected")


def test_healthy_empty_candidates_skips_update(tmp_path, monkeypatch):
    """名单健康但空(status=ok, accounts=[])→ 不是错误,但也没有账户可更新。"""
    summary_fp = tmp_path / "summary.json"
    with mock.patch("portfolio.paper_accounts.provision_from_recompose",
                    return_value={"status": "ok", "reason": "", "accounts": []}), \
         mock.patch("portfolio.paper_accounts.update_all") as m_update, \
         mock.patch.object(pau, "_load_prices") as m_load_prices, \
         mock.patch("portfolio.paper_accounts.SUMMARY_FP", summary_fp):
        summary = pau.run_paper_accounts_update()

    assert summary["provision"]["status"] == "ok"
    assert summary["update"]["ran"] is False
    assert summary["update"]["reason"] == "no_candidates"
    m_load_prices.assert_not_called()
    m_update.assert_not_called()
    print("✅ 名单健康但空 → 不误加载价格面板/不误调用 update_all")


def test_dry_run_skips_price_loading_and_update(tmp_path):
    """--dry-run:只 provision,不加载面板/不 update_all(供人工核对候选而不产生成交)。"""
    summary_fp = tmp_path / "summary.json"
    with mock.patch("portfolio.paper_accounts.provision_from_recompose",
                    return_value={"status": "ok", "reason": "",
                                 "accounts": [{"family": "f", "version": "v1.0", "status": "active"}]}), \
         mock.patch("portfolio.paper_accounts.update_all") as m_update, \
         mock.patch.object(pau, "_load_prices") as m_load_prices, \
         mock.patch("portfolio.paper_accounts.SUMMARY_FP", summary_fp):
        summary = pau.run_paper_accounts_update(dry_run=True)

    assert summary["dry_run"] is True
    assert summary["update"]["ran"] is False
    assert summary["update"]["reason"] == "dry_run"
    m_load_prices.assert_not_called()
    m_update.assert_not_called()
    print("✅ dry-run → 只 provision,不加载价格面板/不 update_all")


def test_normal_path_loads_prices_and_calls_update_all(tmp_path):
    """provision ok 且有候选 → 加载价格面板 → update_all(唯一 canonical 路径)→ summary 形状正确。"""
    import pandas as pd

    summary_fp = tmp_path / "summary.json"
    fake_close = pd.DataFrame({"600000": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    fake_panel = mock.Mock(close=fake_close)
    fake_results = [{"family": "f", "version": "v1.0", "status": "active", "date": "2024-01-02",
                     "nav": 1_000_000.0, "trades": 1, "blocked": 0}]

    with mock.patch("portfolio.paper_accounts.provision_from_recompose",
                    return_value={"status": "ok", "reason": "",
                                 "accounts": [{"family": "f", "version": "v1.0", "status": "active"}]}), \
         mock.patch("portfolio.paper_accounts.update_all", return_value=fake_results) as m_update, \
         mock.patch.object(pau, "_load_prices", return_value=fake_panel) as m_load_prices, \
         mock.patch("portfolio.paper_accounts.SUMMARY_FP", summary_fp):
        summary = pau.run_paper_accounts_update()

    m_load_prices.assert_called_once()
    m_update.assert_called_once()
    called_args = m_update.call_args.args
    assert called_args[0] is fake_panel
    assert called_args[1] == "2024-01-02"  # as_of 取价格面板最新交易日
    assert summary["update"]["ran"] is True
    assert summary["update"]["accounts"] == fake_results
    print("✅ 正常路径:加载价格面板 → 唯一调用 portfolio.paper_accounts.update_all,summary 形状正确")


# ─────────────────────────── 5. 调度旁路:子进程失败不传播为日更 failed ───────────────────────────

def test_scheduled_daily_update_bypass_does_not_propagate_failure(monkeypatch):
    """先证明"若把 paper_accounts_update 的失败当成主流程一部分处理"会被抓红——
    再证明 scheduled_daily_update.run_paper_accounts_update 的真实实现不会犯这个错误
    (子进程非零退出码只记进 report['paper_accounts_update'],不 raise、不改
    report['status'] 的计算依据)。
    """
    from scripts.ops import scheduled_daily_update as sdu

    failing_proc = mock.Mock(returncode=1, stdout="", stderr="boom: no data lake\n")
    report: dict = {}
    with mock.patch("scripts.ops.scheduled_daily_update.subprocess.run", return_value=failing_proc):
        # 坏实现的对照组:若把子进程失败当异常抛出,run_daily_update 的主 try 块
        # 会被这个异常打断(既有代码用 try/except Exception 兜底,但那本该只保护
        # 「数据更新」阶段,不该保护到「旁路」阶段——旁路失败不该有机会changed
        # report['status'] 之外的语义)。这里直接验证正确实现:调用不 raise。
        sdu.run_paper_accounts_update(report, dry_run=False)

    assert report["paper_accounts_update"]["ran"] is False
    assert report["paper_accounts_update"]["returncode"] == 1
    assert "error" in report["paper_accounts_update"]
    # 关键断言:report 里除 paper_accounts_update 自己的字段外,不应该出现
    # status=failed 或任何被这次失败污染的顶层字段——report_status 的计算
    # (在 run_daily_update 里)只看 fresh/signal_ok/aux_update_ok,压根不读
    # paper_accounts_update,这里用"report 只多了 paper_accounts_update 一个
    # key"这个事实间接证明旁路没有越权改写其它字段。
    assert set(report.keys()) == {"paper_accounts_update"}
    print("✅ 调度旁路:子进程失败被吞进 report['paper_accounts_update'],不传播/不越权改写其它字段")


def test_scheduled_daily_update_bypass_success_recorded(monkeypatch):
    from scripts.ops import scheduled_daily_update as sdu

    ok_proc = mock.Mock(returncode=0, stdout="ok\n", stderr="")
    report: dict = {}
    with mock.patch("scripts.ops.scheduled_daily_update.subprocess.run", return_value=ok_proc):
        sdu.run_paper_accounts_update(report, dry_run=False)
    assert report["paper_accounts_update"]["ran"] is True
    assert report["paper_accounts_update"]["returncode"] == 0
    assert "error" not in report["paper_accounts_update"]
    print("✅ 调度旁路:子进程成功时如实记录 ran=True,不产生 error 字段")


def test_scheduled_daily_update_bypass_dry_run_skips_subprocess():
    from scripts.ops import scheduled_daily_update as sdu

    report: dict = {}
    with mock.patch("scripts.ops.scheduled_daily_update.subprocess.run") as m_run:
        sdu.run_paper_accounts_update(report, dry_run=True)
    m_run.assert_not_called()
    assert report["paper_accounts_update"] == {"ran": False, "dry_run": True}
    print("✅ 调度旁路 dry_run=True → 不实际起子进程")


def test_run_daily_update_status_computation_ignores_paper_accounts_update():
    """静态确认:run_daily_update 计算 report['status'] 的代码路径不读取
    report['paper_accounts_update']——防止未来有人"顺手"把它接进 status 判定,
    使旁路失去"失败不传播"的属性却没有测试挡住。
    """
    import ast

    src = (ROOT / "scripts" / "ops" / "scheduled_daily_update.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_daily_update")
    fn_src = ast.get_source_segment(src, fn)
    # run_daily_update 函数体内,除了调用 run_paper_accounts_update(...) 那一行外,
    # 不应该出现读取 report["paper_accounts_update"] 的代码(status 判定不依赖它)。
    lines_referencing_key = [
        line for line in fn_src.splitlines()
        if 'report["paper_accounts_update"]' in line or "report['paper_accounts_update']" in line
    ]
    assert lines_referencing_key == [], \
        f"run_daily_update 不应读取 report['paper_accounts_update'](旁路结果不得参与 status 判定):{lines_referencing_key}"
    print("✅ 静态确认:run_daily_update 的 status 判定代码不读取 paper_accounts_update 字段")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
