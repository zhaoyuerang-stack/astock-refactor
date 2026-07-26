"""数据湖写路径不变量——落盘前的强制体检,拦截系统性坏数据。

背景(2026-06-12 事故):腾讯源 hfqday 缺失静默回退不复权价,后复权大表
末两日出现全市场中位 -59.6%、72% 个股 |r|>30% 的假崩盘;当日被修复两次、
日更又写坏一次,污染了当天全部 OOS 回测的 NAV 指标。

原则:质量校验必须在写路径上强制执行,不能是"事后可选命令"。
判定口径遵循铁律第 7 条——区分系统性坏数据与 A 股正常现象:
个别股票 |r|>30% 正常(涨跌停 30%/炸板/复牌);**截面占比**超阈值
(默认 5%,事故当天 72%)只可能是口径混入/复权断裂,不存在对应的真实行情。
"""
from __future__ import annotations

import pandas as pd


class LakeInvariantError(RuntimeError):
    """写路径不变量被破坏;调用方必须放弃本次落盘,保留旧文件。"""


class PriceAmountInvariantError(LakeInvariantError):
    """价量物理单位不一致;volume/amount 不得进入 canonical 数据湖。"""

    category = "price_unit_contract"


def _price_board(code: str) -> str:
    code = str(code)
    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    return "main"


def validate_price_amount_units(
    long_df: pd.DataFrame,
    *,
    min_rows: int = 100,
    median_bounds: tuple[float, float] = (0.90, 1.10),
    max_p95_relative_error: float = 0.20,
) -> dict:
    """验证 ``amount ≈ volume(shares) × raw_close(CNY/share)`` 的物理量纲。

    成交额使用成交均价而非收盘价，故不要求逐行相等；以板块截面的中位比率和
    P95 相对误差判定系统性 100 倍量纲错误。停牌、零成交和缺失行不参与统计。
    样本不足时明确返回 ``insufficient_sample``，不得伪报通过。
    """
    required = {"date", "code", "raw_close", "volume", "amount"}
    missing = sorted(required - set(long_df.columns))
    if missing:
        raise ValueError(f"价量单位校验缺字段: {missing}")

    clean = long_df[list(required)].copy()
    for col in ("raw_close", "volume", "amount"):
        clean[col] = pd.to_numeric(clean[col], errors="coerce")
    clean = clean.dropna(subset=["date", "code", "raw_close", "volume", "amount"])
    clean = clean[
        (clean["raw_close"] > 0)
        & (clean["volume"] > 0)
        & (clean["amount"] > 0)
    ].copy()
    clean["board"] = clean["code"].map(_price_board)
    clean["ratio"] = clean["amount"] / (clean["volume"] * clean["raw_close"])
    clean = clean[clean["ratio"].notna() & (clean["ratio"] > 0)]

    boards: dict[str, dict] = {}
    breaches: list[str] = []
    evaluated = 0
    for board in ("main", "chinext", "star"):
        ratio = clean.loc[clean["board"] == board, "ratio"]
        n = int(len(ratio))
        if n < min_rows:
            boards[board] = {
                "status": "insufficient_sample",
                "n": n,
                "median_ratio": None,
                "p95_relative_error": None,
            }
            continue
        evaluated += 1
        median_ratio = float(ratio.median())
        p95_error = float((ratio - 1.0).abs().quantile(0.95))
        passed = (
            median_bounds[0] <= median_ratio <= median_bounds[1]
            and p95_error <= max_p95_relative_error
        )
        boards[board] = {
            "status": "passed" if passed else "failed",
            "n": n,
            "median_ratio": median_ratio,
            "p95_relative_error": p95_error,
        }
        if not passed:
            breaches.append(
                f"{board}: median_ratio={median_ratio:.6g}, "
                f"p95_relative_error={p95_error:.2%}, n={n}"
            )

    n = int(len(clean))
    median_ratio = float(clean["ratio"].median()) if n else None
    if breaches:
        date_min = pd.to_datetime(clean["date"]).min()
        date_max = pd.to_datetime(clean["date"]).max()
        raise PriceAmountInvariantError(
            "价量单位不变量被破坏,拒绝落盘: "
            f"date={date_min.date()}~{date_max.date()}; "
            + "; ".join(breaches)
        )
    if evaluated == 0:
        return {
            "passed": False,
            "status": "insufficient_sample",
            "n": n,
            "median_ratio": median_ratio,
            "boards": boards,
        }
    return {
        "passed": True,
        "status": "passed",
        "n": n,
        "median_ratio": median_ratio,
        "boards": boards,
    }


def check_cross_section_sanity(
    long_df: pd.DataFrame,
    *,
    close_col: str = "close",
    last_n_days: int = 5,
    jump_threshold: float = 0.30,
    max_jump_fraction: float = 0.05,
    min_stocks: int = 500,
) -> dict:
    """检查长表(date/code/close)末 N 日的截面收益分布,返回体检报告。

    报告 {"ok": bool, "days": {date: {"n": 截面数, "jump_frac": |r|>阈值占比}}, "breaches": [...]}。
    截面不足 min_stocks 的日期跳过(新湖/小样本不误报)。
    """
    px = (long_df[["date", "code", close_col]]
          .dropna()
          .pivot(index="date", columns="code", values=close_col)
          .sort_index())
    tail = px.tail(last_n_days + 1)
    ret = tail.pct_change().iloc[1:]

    report: dict = {"ok": True, "days": {}, "breaches": []}
    for d, row in ret.iterrows():
        r = row.dropna()
        if len(r) < min_stocks:
            continue
        frac = float((r.abs() > jump_threshold).mean())
        day = str(pd.Timestamp(d).date())
        report["days"][day] = {"n": int(len(r)), "jump_frac": round(frac, 4)}
        if frac > max_jump_fraction:
            report["ok"] = False
            report["breaches"].append(
                f"{day}: {frac:.1%} 个股 |r|>{jump_threshold:.0%} (阈值 {max_jump_fraction:.0%}, n={len(r)})"
            )
    return report


def assert_price_panel_sane(long_df: pd.DataFrame, *, close_col: str = "close", **kw) -> dict:
    """通过返回报告;违规抛 LakeInvariantError(调用方不得落盘)。"""
    report = check_cross_section_sanity(long_df, close_col=close_col, **kw)
    if not report["ok"]:
        raise LakeInvariantError(
            "价格面板截面不变量被破坏,拒绝落盘: " + "; ".join(report["breaches"])
        )
    return report
