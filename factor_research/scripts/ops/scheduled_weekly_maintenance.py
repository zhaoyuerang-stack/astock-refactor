"""Weekly maintenance wrapper for periodic aggregation and quality checks."""
import argparse
import contextlib
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

LOG_DIR = ROOT / "logs/weekly_maintenance"
REPORT_DIR = ROOT / "reports/ops/weekly_maintenance"
PYTHON = "/opt/homebrew/bin/python3"


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


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def run_subprocess(label, cmd, dry_run=False):
    if dry_run:
        print(f"[dry-run] skip {label}: {' '.join(cmd)}")
        return {"ok": True, "dry_run": True, "returncode": None}
    print(f"[run] {label}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-1000:] if proc.stderr else "",
    }


def run_weekly(args):
    run_date = datetime.now().date().isoformat()
    log_path = LOG_DIR / f"{run_date}.log"
    report_path = REPORT_DIR / f"{run_date}.json"
    report = {
        "run_date": run_date,
        "started_at": now_iso(),
        "finished_at": None,
        "status": "running",
        "dry_run": args.dry_run,
    }
    with tee_log(log_path):
        print("=" * 72)
        print(f"scheduled_weekly_maintenance started_at={report['started_at']} dry_run={args.dry_run}")
        try:
            report["aggregate"] = run_subprocess(
                "weekly/monthly aggregate",
                [PYTHON, "-m", "lake.aggregate"],
                dry_run=args.dry_run,
            )
            report["raw_close"] = run_subprocess(
                "raw close refresh",
                [PYTHON, "scripts/data/fetch_raw_close.py"],
                dry_run=args.dry_run,
            )
            report["quality"] = run_subprocess(
                "full quality validation",
                [PYTHON, "validate_final.py"],
                dry_run=args.dry_run,
            )
            # v2.0 实盘监控三件套:失效监控 → 容量/可成交 → 就绪卡
            report["decay_monitor"] = run_subprocess(
                "v2.0 decay monitor (失效监控 → reports/decay_status.json)",
                [PYTHON, "-m", "scripts.ops.decay_monitor"],
                dry_run=args.dry_run,
            )
            report["tradability"] = run_subprocess(
                "v2.0 tradability (容量/可成交率)",
                [PYTHON, "-m", "scripts.research.tradability"],
                dry_run=args.dry_run,
            )
            report["live_readiness"] = run_subprocess(
                "v2.0 live readiness (实盘就绪卡)",
                [PYTHON, "-m", "scripts.research.live_readiness"],
                dry_run=args.dry_run,
            )
            # AutoResearch & 9-Gate 自动搜寻审计定时任务
            report["factor_search"] = run_subprocess(
                "automated factor search & 9-gate evaluation",
                [PYTHON, "scripts/ops/scheduled_factor_search.py"],
                dry_run=args.dry_run,
            )
            # 跨资产防御腿边际搜索:equity 搜索已证 ≈0 边际,本步把发现算力对准
            # 唯一无条件正边际的分散源(国债/黄金腿),按对在册 Δsharpe 排序标 SHADOW。
            report["cross_asset_leg_search"] = run_subprocess(
                "cross-asset defensive-leg search (边际透镜,标 SHADOW 推荐)",
                [PYTHON, "scripts/ops/scheduled_cross_asset_leg_search.py"],
                dry_run=args.dry_run,
            )
            # 组合再构成(WS-D,ADR-034 后续;研究旁路,失败不标 failed):在册腿多目标
            # 排名 + 非冗余组合权重提案 + top-N paper 名单,后端确定性产出并持久化
            # (R-PROD-001);advisory,生效由人经决策收件箱裁决。
            report["portfolio_recompose"] = run_subprocess(
                "weekly portfolio recompose (多策略组合重排提案,advisory)",
                [PYTHON, "scripts/ops/scheduled_portfolio_recompose.py"],
                dry_run=args.dry_run,
            )
            # 组合配置发现(WS2/ADR-034):在册腿的配权方法×腿子集搜索,按 Δsharpe 标
            # SHADOW;发现≠晋级(晋级唯一通道 promote_composite + 人工)。研究旁路,
            # 失败不标 weekly failed(同 factor_search / cross_asset)。
            report["composite_search"] = run_subprocess(
                "composite allocation search (组合配权发现,标 SHADOW 推荐)",
                [PYTHON, "scripts/research/scheduled_composite_search.py"],
                dry_run=args.dry_run,
            )
            # 自动补审:对任何「在册」但缺 DSR 审计的版本(配置已知者)自动跑 9-Gate 并落台账,
            # 保持台账多重检验覆盖不留空(行业级因子等不适配者会被记 SKIP)。
            report["audit_stale"] = run_subprocess(
                "auto-audit stale registered (补审未审计在册的 DSR)",
                [PYTHON, "scripts/research/run_nine_gates_all.py", "--audit-stale", "--persist"],
                dry_run=args.dry_run,
            )
            # metasearch 月度刷新(每月首周;研究旁路,失败不标 failed):
            # MI 冗余簇 + 信息地图空白区 → 机器可读 JSON,供生成端 steering
            # (knowledge/directions.py 消费:同簇排尾 / frontier 排头)。此前这两个
            # 元研究产物只有人工跑+人读报告,教训不回流(见 metasearch_findings_20260623)。
            if datetime.now().day <= 7:
                report["metasearch_clusters"] = run_subprocess(
                    "metasearch MI redundancy clusters (月度,机器可读)",
                    [PYTHON, "-m", "metasearch.factor_mi_audit", "--json"],
                    dry_run=args.dry_run,
                )
                report["metasearch_frontier"] = run_subprocess(
                    "metasearch information-map frontier (月度,机器可读)",
                    [PYTHON, "-m", "metasearch.information_map", "--json"],
                    dry_run=args.dry_run,
                )
            report["status"] = "ok" if all(
                report[name].get("ok")
                for name in ["aggregate", "raw_close", "quality", "decay_monitor"]
            ) else "failed"
            return 0 if report["status"] == "ok" else 1
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
            print(f"scheduled_weekly_maintenance finished status={report['status']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run_weekly(args))


if __name__ == "__main__":
    main()
