"""
数据湖增量更新——canonical 逻辑唯一权威(此前藏身 scripts/data/update_lake.py)。

架构评审发现 run_daily.py(生产层)原先经 scripts.data.update_lake 模块
调 update_prices() —— 生产层依赖 scripts 目录,是一条 canonical→scripts 反向边
(违反 R-ARCH-002)。本模块把可复用的增量更新函数迁到 lake/ 层(与其它 canonical
writer 同层);scripts/data/update_lake.py 保留为薄 CLI 壳(re-export + argparse
入口),行为不变。

设计原则：
- 价量：每只读现有最新date，腾讯从 last_date+1 增量下载新交易日，merge去重
- 财务：读现有最新报告期，yjbb 批量补新季度（按报告期，避封禁）
- 两融：读现有最新date，补新交易日
- manifest：记录每数据集的 last_date + 更新时间，可追溯

CLI 用法（薄壳入口）：python3 scripts/data/update_lake.py            # 全部增量更新
                      python3 scripts/data/update_lake.py --prices  # 仅价量
"""
from app_config.log import get_logger

logger = get_logger(__name__)

import warnings; warnings.filterwarnings("ignore")
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]  # factor_research/
_CHINA_TZ = ZoneInfo("Asia/Shanghai")
def _china_today():
    return datetime.now(_CHINA_TZ).date()
os.chdir(ROOT)
import sys

sys.path.insert(0, str(ROOT))
import akshare as ak
import pandas as pd

from lake.sources.exchange import merge_margin
from lake.sources.registry import resolve_source

LAKE = Path("data_lake")
MANIFEST = LAKE / "_manifest.json"


def load_manifest():
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}

def save_manifest(m):
    m["_updated"] = datetime.now().isoformat(timespec="seconds")
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2))


def _is_drift(prev, last_date, fp):
    """末日不变但指纹变 = 同日数据被改写(漂移),不是正常增量(增量会推进 last_date)。"""
    return bool(prev and prev.get("last_date") == last_date and prev.get("fingerprint") != fp)


def stamp_data_vintage(prev=None):
    """更新后给价量 close 面板盖内容指纹存 manifest(漂移检测,非快照副本)。

    走 load_prices(含 688 单位归一修复)——重跑指纹不符=数据漂移。
    若末日不变而指纹变(如 2026-06-12 同日三次重写事故)→ 立即告警。
    """
    from lake.fingerprint import panel_fingerprint
    from lake.load_lake import load_prices
    close = load_prices(fields=("close",))["close"]
    fp = panel_fingerprint(close)
    last = str(close.index[-1].date())
    if _is_drift(prev, last, fp):
        logger.warning(f"  ⚠️ 数据漂移!末日 {last} 不变但指纹变 ({prev.get('fingerprint')}→{fp})"
              f"——同日数据被改写,需核查")
    return {"stamped_at": datetime.now().isoformat(timespec="seconds"),
            "last_date": last, "shape": list(close.shape), "fingerprint": fp}


def _require_price_unit_report(report: dict) -> None:
    """Fail closed when the pre-write physical-unit sample is inconclusive."""
    if report.get("passed") is True:
        return
    from lake.invariants import PriceAmountInvariantError

    raise PriceAmountInvariantError(
        "价量单位预写校验未通过: "
        f"status={report.get('status', 'unknown')}, n={report.get('n', 0)}"
    )


# ── 价量增量 ──
def update_prices():
    from lake.compact import compact_prices
    from lake.invariants import (
        LakeInvariantError,
        PriceAmountInvariantError,
        validate_price_amount_units,
    )
    from lake.sources.tushare_price import fetch_new_day

    daily = LAKE / "price/daily"
    today = pd.Timestamp(_china_today())
    rebuild_lock = LAKE / ".price_unit_rebuild.lock"
    if rebuild_lock.exists():
        raise RuntimeError(f"历史价量重建进行中,日更拒绝启动: {rebuild_lock}")

    # ── 新上市/缺失代码：用 Tencent 下载完整历史（逐只，量少不触发 WAF）──
    f = resolve_source("price_hfq")
    codes_fp = LAKE / "meta/codes.parquet"
    added = 0
    if codes_fp.exists():
        all_codes = set(pd.read_parquet(codes_fp)["code"].tolist())
        existing = {fp.stem for fp in daily.glob("*.parquet")
                    if fp.stem.isdigit() and len(fp.stem) == 6}
        missing = {c for c in all_codes - existing if not c.startswith("92")}
        if missing:
            logger.info(f"[价量] 缺失代码 {len(missing)} 只(新上市/退市)，Tencent 下载历史...")
            f.start = "2010-01-01"
            for i, code in enumerate(sorted(missing)):
                try:
                    df = f.fetch_one(code)
                    if df is not None and len(df) > 20:
                        df.to_parquet(daily / f"{code}.parquet", index=False)
                        added += 1
                except Exception:
                    continue
                if (i + 1) % 50 == 0:
                    logger.info(f"  缺失下载 {i+1}/{len(missing)} (新增{added})")
            if added:
                logger.info(f"[价量] 新增 {added} 只")

    # ── 找出需要补的交易日（日历 > 当前最新日期）──
    daily_all_fp = LAKE / "price/daily_all.parquet"
    cal = pd.to_datetime(
        pd.read_parquet(LAKE / "meta/trade_calendar.parquet")["date"]
    ).sort_values()

    # 当前数据的最新交易日
    if daily_all_fp.exists():
        latest_ts = pd.to_datetime(
            pd.read_parquet(daily_all_fp, columns=["date"])["date"]
        ).max()
    else:
        latest_ts = pd.Timestamp("2010-01-01")

    new_dates = cal[(cal > latest_ts) & (cal <= today)].tolist()
    if not new_dates:
        logger.info(f"[价量] 已最新({latest_ts.date()}), 无需增量")
        compact_status = "skipped"
        return {"price_daily": {"last_check": str(date.today()),
                                "updated": 0, "added_delisted": added,
                                "compact": compact_status}}

    logger.info(f"[价量] 需补 {len(new_dates)} 个交易日: "
          f"{new_dates[0].date()} ~ {new_dates[-1].date()}")

    # ── 逐日从 tushare 批量拉取（2 次 API/日，全市场）──
    all_new_rows: list[pd.DataFrame] = []
    # daily_all 用来提供前日 hfq 收盘基准
    daily_all_df = (pd.read_parquet(daily_all_fp, columns=["date", "code", "close"])
                    if daily_all_fp.exists() else pd.DataFrame())

    for i, td in enumerate(new_dates):
        # 前一交易日（日历里 td 的前一条，或 latest_ts）
        earlier = cal[cal < td]
        prev_td = earlier.iloc[-1] if len(earlier) else td - pd.Timedelta(days=1)

        # 前日 hfq 收盘 Series（index=code）
        if not daily_all_df.empty:
            prev_slice = daily_all_df[
                pd.to_datetime(daily_all_df["date"]) == prev_td
            ]
        else:
            prev_slice = pd.DataFrame()

        prev_closes = (prev_slice.set_index("code")["close"]
                       if not prev_slice.empty else pd.Series(dtype=float))

        logger.info(f"  [价量 {i+1}/{len(new_dates)}] {td.date()} prev={prev_td.date()} "
              f"基准={len(prev_closes)}只")
        try:
            new_df = fetch_new_day(td, prev_td, prev_closes)
        except Exception as exc:
            logger.warning(f"  [价量] {td.date()} tushare 失败: {exc}")
            continue

        if new_df.empty:
            logger.info(f"  [价量] {td.date()} 返回空(非交易日?)")
            continue

        all_new_rows.append(new_df)
        # 更新 daily_all_df 缓存，让下一个新日期能用当日数据作基准
        daily_all_df = pd.concat(
            [daily_all_df, new_df[["date", "code", "close"]]],
            ignore_index=True
        )

    if not all_new_rows:
        logger.info("[价量] 无新数据写入")
        return {"price_daily": {"last_check": str(date.today()),
                                "updated": 0, "added_delisted": added,
                                "compact": "skipped"}}

    # ── 把新行写入 per-stock parquet ──
    combined = pd.concat(all_new_rows, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    try:
        unit_report = validate_price_amount_units(combined)
    except PriceAmountInvariantError as exc:
        logger.warning(f"  🚨 价量单位不变量拒绝写入: {exc}")
        raise
    _require_price_unit_report(unit_report)
    logger.info(
        "  [价量单位] canonical 股/元校验通过: "
        f"n={unit_report['n']}, median_ratio={unit_report['median_ratio']:.4f}",
        flush=True,
    )
    updated = 0
    for code, grp in combined.groupby("code"):
        fp = daily / f"{code}.parquet"
        new_rows = grp.drop(columns=["code", "raw_close"]).reset_index(drop=True)
        if fp.exists():
            old = pd.read_parquet(fp)
            old["date"] = pd.to_datetime(old["date"])
            merged = (pd.concat([old, new_rows])
                      .drop_duplicates("date")
                      .sort_values("date")
                      .reset_index(drop=True))
        else:
            merged = new_rows.sort_values("date").reset_index(drop=True)
        merged.to_parquet(fp, index=False)
        updated += 1

    logger.info(f"[价量] tushare 增量写入 {updated} 只 × {len(all_new_rows)} 日")

    # ── 重建大表 ──
    compact_status = "skipped"
    logger.info("重新合并 daily_all.parquet ...")
    try:
        compact_prices(daily, daily_all_fp)
        compact_status = "ok"
    except LakeInvariantError as e:
        compact_status = f"REJECTED: {e}"
        logger.warning(f"  🚨 大表合并被不变量拒绝(旧 daily_all 保留): {e}")

    return {"price_daily": {"last_check": str(date.today()),
                            "updated": updated, "added_delisted": added,
                            "compact": compact_status}}


# ── 财务增量（按报告期补新季度）──
def update_fundamental():
    fp = LAKE / "fundamental_batch.parquet"
    if not fp.exists():
        return {}
    df = pd.read_parquet(fp)
    have = set(df["report_date"].dt.strftime("%Y%m%d"))
    # 生成所有应有报告期，找缺失的（新季度）
    periods = [f"{y}{md}" for y in range(2010, date.today().year + 1)
               for md in ["0331", "0630", "0930", "1231"]]
    cutoff = date.today().strftime("%Y%m%d")
    missing = [p for p in periods if p <= cutoff and p not in have]
    if not missing:
        logger.info("[财务] 已最新，无新报告期")
        return {"fundamental": {"last_check": str(date.today())}}

    from lake.schema import YJBB_RENAME
    RENAME = YJBB_RENAME
    new_frames = []
    for p in missing:
        try:
            d = ak.stock_yjbb_em(date=p)
            if d is None or d.empty:
                continue
            d = d.rename(columns=RENAME)
            d["report_date"] = pd.to_datetime(p)
            new_frames.append(d[[c for c in RENAME.values() if c in d.columns] + ["report_date"]])
            logger.info(f"  财务新报告期 {p}: {len(d)}只")
        except Exception:
            continue
    if new_frames:
        # DQ-2026-001:东财"最新公告日期"(→em_last_touch_date,见 schema.py 注释)不是
        # 本报告期专属公告日,禁止直接当 ann_date 用。新增行同存量湖数据一样,走
        # lake.fundamental_repair 的三级真值级联重铺(不能只对存量跑修复脚本、日更
        # 增量继续走粗糙 +45d 兜底,那样会让守卫刚清零的例外集又重新长出新违规)。
        from lake.fundamental_repair import build_true_date_map, repair
        nf = pd.concat(new_frames, ignore_index=True)
        nf["code"] = nf["code"].astype(str).str.zfill(6)
        merged_raw = pd.concat([df, nf], ignore_index=True).drop_duplicates(["code","report_date"], keep="last")
        true_map = build_true_date_map()
        merged, _stat = repair(merged_raw, true_map)
        merged.to_parquet(fp, index=False)
        logger.info(f"[财务] 新增{len(missing)}个报告期")
    return {"fundamental": {"last_check": str(date.today()), "new_periods": missing}}


def update_capital_margin():
    """补两融到最新交易日；北向依赖 eastmoney 稳定性，走 build_capital.py 单独维护。"""
    cal_fp = LAKE / "meta/trade_calendar.parquet"
    if not cal_fp.exists():
        return {}
    cal = pd.read_parquet(cal_fp)
    trade_dates = pd.to_datetime(cal["date"])
    margin_dir = LAKE / "capital/margin"
    margin_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(margin_dir.glob("*.parquet"))
    if existing:
        last = pd.to_datetime(existing[-1].stem)
        keys = trade_dates[trade_dates > last].dt.strftime("%Y%m%d").tolist()
    else:
        keys = trade_dates.dt.strftime("%Y%m%d").tolist()
    if not keys:
        logger.info("[两融] 已最新，无新交易日")
        return {"capital_margin": {"last_check": str(date.today()), "updated_days": 0}}
    fetcher = resolve_source("margin", max_workers=3, timeout=30, retries=2)
    stats = fetcher.run(keys, skip_existing=True, progress_every=50)
    merge_margin()
    return {"capital_margin": {"last_check": str(date.today()), "updated_days": stats.get("ok", 0), "empty": stats.get("empty", 0), "error": stats.get("error", 0)}}


def update_weekly_monthly():
    """重新生成周/月线聚合。"""
    from lake.aggregate import build_periodic
    build_periodic("data_lake/price/daily")
    return {"periodic": {"last_check": str(date.today())}}
