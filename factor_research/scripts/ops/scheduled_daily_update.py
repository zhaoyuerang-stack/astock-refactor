"""
Scheduled daily data update wrapper.

This script is the production entrypoint for launchd. It updates data first,
checks freshness, then calls run_daily.py --no-update only when data is fresh
enough for the expected latest A-share trading day.
"""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs/daily_update"
REPORT_DIR = ROOT / "reports/ops/daily_update"
DATA_TRIAGE_PATH = ROOT / "reports/data/data_issue_triage.json"
LOCK_PATH = LOG_DIR / ".scheduled_daily_update.lock"
PYTHON = "/opt/homebrew/bin/python3"
SAMPLE_CODES = ["600519", "000001", "300750", "600036", "601398"]
CALENDAR_ANCHORS = ["600519", "601398", "000001", "600036", "600000", "601988"]
CHINA_TZ = ZoneInfo("Asia/Shanghai")


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


@contextlib.contextmanager
def tee_log(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", buffering=1) as log:
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = Tee(old_out, log)
        sys.stderr = Tee(old_err, log)
        try:
            yield
        finally:
            sys.stdout = old_out
            sys.stderr = old_err


@contextlib.contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def china_now():
    return datetime.now(CHINA_TZ)


def should_run_for_china_time(not_before):
    hour, minute = [int(part) for part in not_before.split(":", 1)]
    now = china_now()
    threshold = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= threshold, now, threshold


def prior_success(report_path):
    if not report_path.exists():
        return False
    try:
        report = json.loads(report_path.read_text())
    except Exception:
        return False
    # partial_ok: 信号已生成，只是辅助数据(ETF/raw)有失败，无需重跑
    return report.get("status") in ("ok", "partial_ok")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def model_dump(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def _alert_body(report: dict) -> str:
    """从日更 report 提取告警正文(失败步骤 + 信号状态 + error 摘要)。"""
    parts = [f"status={report.get('status')}"]
    failed_steps = [
        k for k in (
            "calendar_update", "price_update", "raw_update", "etf_update",
            "fundamental_update", "tushare_incremental",
        )
        if isinstance(report.get(k), dict) and report[k].get("ok") is False
    ]
    if failed_steps:
        parts.append("失败步骤:" + ",".join(failed_steps))
        categories = sorted({
            str(report[step].get("error_category"))
            for step in failed_steps
            if report[step].get("error_category")
        })
        if categories:
            parts.append("错误类别:" + ",".join(categories))
    sig = report.get("signal")
    if isinstance(sig, dict) and not sig.get("generated"):
        reason = sig.get("reason") or (sig.get("error") or "")[:80]
        parts.append(f"信号未生成:{reason}")
    if report.get("error"):
        parts.append(f"error:{str(report['error'])[:120]}")
    return " | ".join(parts)


def maybe_alert(report: dict, report_path: Path) -> None:
    """日更结束后按 status 推送告警:去重(launchd 一天重试多次)+ 失败恢复报平安。

    告警是旁路:任何异常都吞掉,绝不影响日更返回码 / 主流程。
    """
    try:
        from app_config.settings import get_settings
        from scripts.ops import notify

        cfg = get_settings().notify
        status = report.get("status", "unknown")
        date = report_path.stem  # YYYY-MM-DD
        sentinel = report_path.parent / f".alert_{date}.json"

        if status in cfg.alert_on:
            # 去重:同一天同一 status 只推一次(launchd 盘后会重试 4 次)
            if sentinel.exists():
                try:
                    if json.loads(sentinel.read_text()).get("status") == status:
                        return
                except Exception:
                    pass
            label = "失败" if status == "failed" else "部分失败"
            notify.send_alert(
                f"🔴 A股日更{label} {date}",
                _alert_body(report),
                desktop=cfg.desktop,
                obsidian=cfg.obsidian,
            )
            write_json(sentinel, {"status": status, "alerted_at": now_iso()})
        elif status == "ok" and cfg.recovery and sentinel.exists():
            # 今天先前推过失败,现已恢复 → 报平安并清哨兵(避免持续焦虑)
            notify.send_alert(
                f"✅ A股日更已恢复 {date}",
                "日更恢复正常(status=ok)。",
                desktop=cfg.desktop,
                obsidian=cfg.obsidian,
            )
            sentinel.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[alert] 告警旁路异常(不影响日更): {exc}")


def rebuild_trade_calendar_from_prices():
    from lake.meta import rebuild_trade_calendar_from_prices as rebuild_calendar

    return rebuild_calendar(root=ROOT, anchors=CALENDAR_ANCHORS, min_anchor_count=5)


def expected_trade_date(today=None):
    if today is not None:
        today = pd.Timestamp(today)
    else:
        # Before ~09:00 China time the trading day hasn't started yet, so the
        # most recent *closed* trading day is still "yesterday". Shift the
        # clock back so early-morning runs (e.g. 00:30/01:30 launchd jobs)
        # target the prior day's close instead of the not-yet-traded today.
        today = pd.Timestamp((china_now() - pd.Timedelta(hours=9)).date())
    cal = pd.read_parquet(ROOT / "data_lake/meta/trade_calendar.parquet")["date"]
    cal = pd.to_datetime(cal)
    eligible = cal[cal <= today]
    local_expected = eligible.max() if len(eligible) else None
    # If the local calendar is stale, be conservative on weekdays. This may
    # skip holiday signals, but it prevents overwriting state with old data.
    if today.weekday() < 5 and (local_expected is None or local_expected < today):
        return today, "weekday_heuristic"
    return local_expected, "local_calendar"


def actual_latest_price_date():
    dates = []
    for fp in sorted((ROOT / "data_lake/price/daily").glob("*.parquet")):
        try:
            df = pd.read_parquet(fp, columns=["date"])
        except Exception:
            continue
        if len(df):
            dates.append(pd.to_datetime(df["date"]).max())
    return max(dates) if dates else None


def sample_quality_check():
    from lake.validator import DataValidator

    cal = pd.read_parquet(ROOT / "data_lake/meta/trade_calendar.parquet")["date"]
    validator = DataValidator(calendar=cal)
    bad = []
    checked = []
    for code in SAMPLE_CODES:
        fp = ROOT / f"data_lake/price/daily/{code}.parquet"
        if not fp.exists():
            continue
        result = validator.validate(code, pd.read_parquet(fp))
        checked.append(code)
        if not result["ok"]:
            bad.append({"code": code, "issues": result["issues"]})
    return {"checked": checked, "bad": bad, "ok": not bad}


def attach_production_readiness(report):
    from runtime.production_readiness import get_production_readiness

    readiness = get_production_readiness(
        data_date=report.get("latest_after_update") or None,
        expected_trade_date=report.get("expected_trade_date") or None,
    )
    report["production_readiness"] = model_dump(readiness)
    return readiness


def attach_data_issue_triage(report):
    from lake.data_issue_triage import build_scheduled_update_triage

    triage = build_scheduled_update_triage(report, save_path=DATA_TRIAGE_PATH)
    report["data_issue_triage"] = triage.get("summary", {})
    return triage


def run_updates(report, dry_run=False):
    if dry_run:
        print("[dry-run] skip update_prices/update_fundamental")
        return

    from lake.meta import update_trade_calendar
    from scripts.data import update_lake

    # ── 0. 交易日历（必须先于价量更新，否则 update_prices 不知道新交易日）──
    try:
        print("[update] trade_calendar")
        cal_result = update_trade_calendar(root=ROOT)
        report["calendar_update"] = cal_result
    except Exception as exc:
        report["calendar_update"] = {"ok": False, "error": str(exc)}
        print(f"[update] calendar failed: {exc}")
        traceback.print_exc()

    manifest = update_lake.load_manifest()
    try:
        print("[update] prices")
        result = update_lake.update_prices()
        manifest.update(result)
        report["price_update"] = {"ok": True, **result.get("price_daily", {})}
    except Exception as exc:
        report["price_update"] = {
            "ok": False,
            "error": str(exc),
            "error_category": getattr(exc, "category", type(exc).__name__),
        }
        print(f"[update] price failed: {exc}")
        traceback.print_exc()

    try:
        print("[update] raw OHLC (不复权,模拟盘 T+1 开盘成交用 + 消除 amount 滞后)")
        from scripts.data.fetch_raw_close import update_raw_prices
        stats = update_raw_prices()
        report["raw_update"] = {"ok": True, "ok_n": stats.get("ok", 0), "err_n": stats.get("error", 0)}
    except Exception as exc:
        report["raw_update"] = {"ok": False, "error": str(exc)}
        print(f"[update] raw failed: {exc}")
        traceback.print_exc()

    try:
        print("[update] cross-asset ETF (不复权 raw 列,模拟盘债券轮动成交/估值用)")
        from scripts.data.fetch_cross_asset_etf import update_etfs
        etf_stats = update_etfs()
        etf_ok = all(v.get("ok") for v in etf_stats.values())
        report["etf_update"] = {"ok": etf_ok, "detail": etf_stats}
    except Exception as exc:
        report["etf_update"] = {"ok": False, "error": str(exc)}
        print(f"[update] etf failed: {exc}")
        traceback.print_exc()

    try:
        print("[update] fundamental")
        result = update_lake.update_fundamental()
        manifest.update(result)
        report["fundamental_update"] = {"ok": True, **result.get("fundamental", {})}
    except Exception as exc:
        report["fundamental_update"] = {"ok": False, "error": str(exc)}
        print(f"[update] fundamental failed: {exc}")
        traceback.print_exc()

    try:
        print("[update] tushare 日频维度增量 (daily_basic/moneyflow/stk_limit/suspend_d/index_daily/adj_factor)")
        from scripts.data.update_tushare import incremental_update
        ts_stats = incremental_update()
        ts_ok = all(v.get("ok") for v in ts_stats.values())
        ts_new = sum(v.get("new", 0) for v in ts_stats.values() if v.get("ok"))
        report["tushare_incremental"] = {"ok": ts_ok, "new_rows": ts_new, "detail": ts_stats}
        for dim, s in ts_stats.items():
            if s.get("ok"):
                print(f"  [tushare_inc] {dim}: latest={s.get('latest','')} new={s.get('new',0)}")
            else:
                print(f"  [tushare_inc] {dim}: ⚠ {s.get('error','')}")
    except Exception as exc:
        report["tushare_incremental"] = {"ok": False, "error": str(exc)}
        print(f"[update] tushare incremental failed: {exc}")
        traceback.print_exc()

    try:
        update_lake.save_manifest(manifest)
    except Exception as exc:
        report["manifest_error"] = str(exc)
        print(f"[update] manifest save failed: {exc}")


def run_report_nlp(report, dry_run=False):
    if dry_run:
        print("[nlp] skip report nlp pipeline")
        report["report_nlp"] = {"ran": False, "dry_run": True}
        return

    print("[nlp] running auto_download_reports.py")
    try:
        # 1. 自动下载高价值方向研报
        proc_dl = subprocess.run(
            [PYTHON, "scripts/ops/auto_download_reports.py", "--days", "15"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        print(proc_dl.stdout)
        if proc_dl.stderr:
            print(proc_dl.stderr, file=sys.stderr)
        report["report_download"] = {
            "ran": proc_dl.returncode == 0,
            "returncode": proc_dl.returncode,
        }
    except Exception as exc:
        report["report_download"] = {"ok": False, "error": str(exc)}
        print(f"[nlp] auto_download_reports.py 运行异常: {exc}")

    print("[nlp] running report_nlp_pipeline.py")
    try:
        # 2. 运行 NLP 解析与图谱合并管线
        proc = subprocess.run(
            [PYTHON, "scripts/research/report_nlp_pipeline.py", "--delete"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        report["report_nlp"] = {
            "ran": proc.returncode == 0,
            "returncode": proc.returncode,
        }
        if proc.returncode != 0:
            print(f"[nlp] 警告: 研报 NLP 管线退出码非零: {proc.returncode}")
        else:
            print("[nlp] running run_ontology_shadow_pipeline.py")
            proc_ont = subprocess.run(
                [PYTHON, "scripts/research/run_ontology_shadow_pipeline.py"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            print(proc_ont.stdout)
            if proc_ont.stderr:
                print(proc_ont.stderr, file=sys.stderr)
    except Exception as exc:
        report["report_nlp"] = {"ran": False, "error": str(exc)}
        print(f"[nlp] 错误: 执行研报 NLP 管线出现异常: {exc}")


def run_signal(report, dry_run=False):
    if dry_run:
        print("[dry-run] skip run_daily.py --no-update")
        report["signal"] = {"generated": False, "dry_run": True}
        return

    print("[signal] run_daily.py --no-update")
    proc = subprocess.run(
        [PYTHON, "run_daily.py", "--no-update"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report["signal"] = {
        "generated": proc.returncode == 0,
        "returncode": proc.returncode,
        "blocked_readiness": proc.returncode == 2,
    }
    if proc.returncode != 0:
        report["signal"]["error"] = proc.stderr[-1000:]
    else:
        print("[signal] running validate_amount_timing.py")
        proc_val = subprocess.run(
            [PYTHON, "scripts/research/validate_amount_timing.py"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        print(proc_val.stdout)
        if proc_val.stderr:
            print(proc_val.stderr, file=sys.stderr)


def run_factor_health(report, dry_run=False):
    if dry_run:
        print("[health] skip factor health")
        report["factor_health"] = {"generated": False, "dry_run": True}
        return

    print("[health] generate_factor_health.py")
    proc = subprocess.run(
        [PYTHON, "scripts/ops/generate_factor_health.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report["factor_health"] = {
        "generated": proc.returncode == 0,
        "returncode": proc.returncode,
    }
    if proc.returncode != 0:
        report["factor_health"]["error"] = proc.stderr[-1000:]


def run_paper_forward_smallcap(report, dry_run=False):
    """旁路:small-cap-size/v2.0 纸面前向实验快照(ADR-024,人工 override,零真金)。
    非阻塞研究输出——失败不影响日更主流程,也不参与 readiness/部署。"""
    if dry_run:
        print("[smallcap-fwd] skip paper-forward tracker")
        report["smallcap_forward"] = {"ran": False, "dry_run": True}
        return

    print("[smallcap-fwd] paper_forward_smallcap.py (ADR-024 纸面前向快照)")
    proc = subprocess.run(
        [PYTHON, "scripts/research/paper_forward_smallcap.py"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report["smallcap_forward"] = {"ran": proc.returncode == 0, "returncode": proc.returncode}
    if proc.returncode != 0:
        report["smallcap_forward"]["error"] = proc.stderr[-1000:]


def run_paper_trade(report, dry_run=False):
    if dry_run:
        print("[paper] skip paper_trade")
        report["paper_trade"] = {"ran": False, "dry_run": True}
        return

    print("[paper] paper_trade → Obsidian 模拟盘卡片")
    proc = subprocess.run(
        [PYTHON, "-m", "scripts.ops.paper_trade"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report["paper_trade"] = {
        "ran": proc.returncode == 0,
        "returncode": proc.returncode,
    }
    if proc.returncode != 0:
        report["paper_trade"]["error"] = proc.stderr[-1000:]


def run_paper_accounts_update(report, dry_run=False):
    """旁路:paper 多账户并行实测日更(WS-D 执行侧,R-PROD-001「不下单 ≠ 不实测」)。

    给 reports/research/portfolio_recompose.json::paper_candidates 里的每个
    候选策略版本更新独立模拟盘账本(portfolio/paper_accounts.py)。与既有单账户
    paper_trade 完全解耦(不同账户目录、不同状态文件),失败不影响日更主流程,
    不参与 readiness/部署——与 run_paper_forward_smallcap 同款旁路纪律。
    """
    if dry_run:
        print("[paper-accounts] skip paper_accounts_update")
        report["paper_accounts_update"] = {"ran": False, "dry_run": True}
        return

    print("[paper-accounts] paper_accounts_update.py (多账户并行实测,WS-D 执行侧)")
    proc = subprocess.run(
        [PYTHON, "-m", "scripts.ops.paper_accounts_update"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report["paper_accounts_update"] = {"ran": proc.returncode == 0, "returncode": proc.returncode}
    if proc.returncode != 0:
        report["paper_accounts_update"]["error"] = proc.stderr[-1000:]


def run_daily_update(args):
    expected, expected_source = expected_trade_date(args.today)
    run_date = expected.strftime("%Y-%m-%d") if expected is not None else china_now().date().isoformat()
    log_path = LOG_DIR / f"{run_date}.log"
    report_path = REPORT_DIR / f"{run_date}.json"
    report = {
        "run_date": run_date,
        "run_date_timezone": "Asia/Shanghai",
        "started_at": now_iso(),
        "started_at_china": china_now().isoformat(timespec="seconds"),
        "finished_at": None,
        "status": "running",
        "dry_run": args.dry_run,
        "china_not_before": args.china_not_before,
    }

    with tee_log(log_path):
        print("=" * 72)
        print(f"scheduled_daily_update started_at={report['started_at']} dry_run={args.dry_run}")
        with file_lock(LOCK_PATH) as acquired:
            if not acquired:
                report.update({
                    "status": "skipped_locked",
                    "finished_at": now_iso(),
                    "log_path": str(log_path),
                })
                write_json(report_path, report)
                print("[lock] another scheduled update is running; skip")
                return 2

            try:
                time_ok, now_cn, threshold_cn = should_run_for_china_time(args.china_not_before)
                report["china_now"] = now_cn.isoformat(timespec="seconds")
                report["china_threshold"] = threshold_cn.isoformat(timespec="seconds")
                if not args.force and not time_ok:
                    report["status"] = "skipped_before_china_time"
                    print(f"[time] skip: china_now={report['china_now']} threshold={report['china_threshold']}")
                    return 0
                if not args.force and prior_success(report_path):
                    report["status"] = "skipped_already_ok"
                    print(f"[dedupe] skip: {report_path} already has status=ok")
                    return 0

                before_latest = actual_latest_price_date()
                calendar_max = rebuild_trade_calendar_from_prices()
                expected, expected_source = expected_trade_date(args.today)
                report["calendar_max_after_rebuild"] = str(calendar_max.date()) if calendar_max is not None else None
                report["expected_trade_date"] = str(expected.date()) if expected is not None else None
                report["expected_trade_date_source"] = expected_source
                report["latest_before_update"] = str(before_latest.date()) if before_latest is not None else None
                print(f"[freshness] before={report['latest_before_update']} expected={report['expected_trade_date']}")

                run_updates(report, dry_run=args.dry_run)

                calendar_max = rebuild_trade_calendar_from_prices()
                expected, expected_source = expected_trade_date(args.today)
                report["calendar_max_after_update"] = str(calendar_max.date()) if calendar_max is not None else None
                report["expected_trade_date"] = str(expected.date()) if expected is not None else None
                report["expected_trade_date_source"] = expected_source
                after_latest = actual_latest_price_date()
                report["latest_after_update"] = str(after_latest.date()) if after_latest is not None else None
                # freshness 仅基于核心价量数据(price/daily)，ETF/raw 是辅助数据，失败不阻断信号
                fresh = expected is not None and after_latest is not None and after_latest >= expected
                report["data_fresh"] = bool(fresh)

                # 区分核心更新失败 vs 辅助更新失败
                price_ok = report.get("price_update", {}).get("ok", True)
                fundamental_ok = report.get("fundamental_update", {}).get("ok", True)
                etf_ok = report.get("etf_update", {}).get("ok", True)
                raw_ok = report.get("raw_update", {}).get("ok", True)
                tushare_inc_ok = report.get("tushare_incremental", {}).get("ok", True)
                core_update_ok = price_ok and fundamental_ok
                aux_update_ok = etf_ok and raw_ok and tushare_inc_ok

                report["update_failed_but_data_fresh"] = bool(fresh and not core_update_ok)
                report["aux_update_partial"] = bool(not aux_update_ok)
                if not etf_ok:
                    etf_detail = report.get("etf_update", {}).get("detail", {})
                    failed_etfs = [c for c, v in etf_detail.items() if not v.get("ok")]
                    print(f"[freshness] ETF 更新部分失败({failed_etfs}),不影响价量 freshness")
                if not tushare_inc_ok:
                    ts_detail = report.get("tushare_incremental", {}).get("detail", {})
                    failed_dims = [d for d, v in ts_detail.items() if not v.get("ok")]
                    print(f"[freshness] tushare 日频维度部分失败({failed_dims}),不影响价量 freshness")
                print(f"[freshness] after={report['latest_after_update']} fresh={fresh} "
                      f"price_ok={price_ok} etf_ok={etf_ok} raw_ok={raw_ok} tushare_inc_ok={tushare_inc_ok}")

                report["sample_quality"] = sample_quality_check()
                print(f"[quality] sample_ok={report['sample_quality']['ok']} bad={report['sample_quality']['bad']}")
                triage = attach_data_issue_triage(report)
                print(f"[triage] production_blocked={triage['summary']['production_blocked']} "
                      f"categories={triage['summary']['counts_by_category']}")
                readiness = attach_production_readiness(report)
                print(f"[readiness] allowed={readiness.allowed} "
                      f"blocking={readiness.blocking_reasons} warnings={readiness.warnings}")

                if fresh or args.force:
                    run_report_nlp(report, dry_run=args.dry_run)
                    run_signal(report, dry_run=args.dry_run)
                    run_factor_health(report, dry_run=args.dry_run)
                    run_paper_forward_smallcap(report, dry_run=args.dry_run)  # ADR-024 旁路
                    if args.dry_run or report.get("signal", {}).get("generated") or args.force:
                        run_paper_trade(report, dry_run=args.dry_run)
                    run_paper_accounts_update(report, dry_run=args.dry_run)  # WS-D 执行侧旁路
                else:
                    report["signal"] = {
                        "generated": False,
                        "reason": "stale_data",
                    }
                    print("[signal] skip because data is stale")

                signal_ok = bool(report.get("signal", {}).get("generated") or args.dry_run)
                # ok = 核心数据新鲜 + 信号生成；partial_ok = 信号生成但辅助更新有失败
                if (fresh or args.force) and signal_ok:
                    report["status"] = "ok" if aux_update_ok else "partial_ok"
                else:
                    report["status"] = "failed"
                # partial_ok 仍返回 0(不触发 launchd 报警),failed 返回 1
                return 0 if report["status"] in ("ok", "partial_ok") else 1
            except Exception as exc:
                report["status"] = "failed"
                report["error"] = str(exc)
                traceback.print_exc()
                return 1
            finally:
                report["finished_at"] = now_iso()
                report["log_path"] = str(log_path)
                write_json(report_path, report)
                print(f"[report] {report_path}")
                print(f"scheduled_daily_update finished status={report['status']}")
                maybe_alert(report, report_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Do not update data or generate signals.")
    parser.add_argument("--today", help="Override local date for freshness tests, YYYY-MM-DD.")
    parser.add_argument("--china-not-before", default="16:30", help="Do not run before this Asia/Shanghai wall time.")
    parser.add_argument("--force", action="store_true", help="Ignore China-time gate and prior successful report.")
    args = parser.parse_args()
    raise SystemExit(run_daily_update(args))


if __name__ == "__main__":
    main()
