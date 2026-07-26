"""
增量更新 CLI 壳——canonical 逻辑唯一权威已迁至 lake/update.py。

架构评审发现 run_daily.py(生产层)原先经本模块调 update_prices(),是一条
canonical(生产层)→scripts 的反向依赖边(违反 R-ARCH-002)。可复用函数
(update_prices/update_fundamental/update_capital_margin/update_weekly_monthly
及其依赖的 manifest/vintage 辅助函数)已搬到 lake/update.py；本文件只保留
CLI 入口(argparse + __main__)与 run_validate()(纯 CLI 用,非生产层依赖)。

用法：python3 scripts/data/update_lake.py            # 全部增量更新
      python3 scripts/data/update_lake.py --prices  # 仅价量
"""
import warnings; warnings.filterwarnings("ignore")
import argparse
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT))

from lake.update import (  # noqa: E402
    LAKE,
    MANIFEST,
    _china_today,
    _is_drift,
    _require_price_unit_report,
    load_manifest,
    save_manifest,
    stamp_data_vintage,
    update_capital_margin,
    update_fundamental,
    update_prices,
    update_weekly_monthly,
)

__all__ = [
    "LAKE",
    "MANIFEST",
    "_china_today",
    "_is_drift",
    "_require_price_unit_report",
    "load_manifest",
    "save_manifest",
    "stamp_data_vintage",
    "update_capital_margin",
    "update_fundamental",
    "update_prices",
    "update_weekly_monthly",
    "run_validate",
]


def run_validate():
    """运行数据质量校验。"""
    import subprocess
    result = subprocess.run([sys.executable, "validate_final.py"], cwd=ROOT, capture_output=True, text=True)
    ok = result.returncode == 0 and "干净" in result.stdout
    return {"validate": {"last_check": str(date.today()), "ok": ok}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", action="store_true", help="Update price data only.")
    ap.add_argument("--fundamental", action="store_true", help="Update fundamental data only.")
    ap.add_argument("--capital", action="store_true", help="Update margin financing data only.")
    ap.add_argument("--weekly-monthly", action="store_true", help="Rebuild weekly/monthly aggregates.")
    ap.add_argument("--validate", action="store_true", help="Run data quality validation.")
    ap.add_argument("--all", action="store_true", help="Run all updates.")
    args = ap.parse_args()
    do_all = args.all or not (args.prices or args.fundamental or args.capital or args.weekly_monthly or args.validate)

    m = load_manifest()
    if do_all or args.prices:
        m.update(update_prices())
    if do_all or args.fundamental:
        m.update(update_fundamental())
    if args.capital:
        m.update(update_capital_margin())
    if do_all or args.weekly_monthly:
        m.update(update_weekly_monthly())
    if do_all or args.validate:
        m.update(run_validate())
    if do_all or args.prices:
        m["data_vintage"] = stamp_data_vintage(m.get("data_vintage"))
    save_manifest(m)
    vintage = m.get("data_vintage", {})
    print(f"\n增量更新完成，manifest: {MANIFEST}", flush=True)
    if vintage:
        print(f"数据指纹: {vintage.get('fingerprint')} (末日 {vintage.get('last_date')})", flush=True)
