"""数据清洗规则 —— 确定性、可复现、加载时生效(不手改源 parquet)。

真实性铁律(见对话/LESSONS):
- **可复现**:清洗是规则(quarantine.json + repair_ohlc),每次重建数据湖结果一致;
  绝不手改产物。
- **不删数据造幸存者偏差**:坏数据用 quarantine **隔离**(加载时排除),源 parquet 保留
  真实记录;隔离区间有 reason/审计。
- **无未来函数**:repair_ohlc 只用**本行自身** o/c 推 high/low;quarantine 用静态区间;
  绝不用后来的数据回填。

两类清洗各司其职:
- repair_ohlc:正但不自洽的 OHLC(tick 噪声)→ 夹紧到自洽。
- quarantine :不可用区间(负价/复权崩溃/已知坏数据)→ 加载时排除。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_QUARANTINE_FILE = Path(__file__).parent / "quarantine.json"


def load_quarantine() -> list[dict]:
    if not _QUARANTINE_FILE.exists():
        return []
    try:
        return json.loads(_QUARANTINE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


def apply_quarantine(df: pd.DataFrame, entries: list[dict] | None = None) -> pd.DataFrame:
    """从含 (code,date) 的长表中剔除 quarantine 区间。确定性、可复现。"""
    if entries is None:
        entries = load_quarantine()
    if not entries or df.empty or "code" not in df.columns or "date" not in df.columns:
        return df
    codes = df["code"].astype(str)
    drop = pd.Series(False, index=df.index)
    for e in entries:
        if e.get("action", "exclude") != "exclude":
            continue
        m = codes == str(e["code"])
        if e.get("date_from"):
            m &= df["date"] >= pd.Timestamp(e["date_from"])
        if e.get("date_to"):
            m &= df["date"] <= pd.Timestamp(e["date_to"])
        drop |= m
    return df[~drop] if drop.any() else df


def repair_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """夹紧 OHLC 自洽:仅对违规行,且只用本行 o/h/l/c(无未来函数)。

    对已自洽的行是 no-op(high 本就是 max、low 本就是 min)。负价不在此修复 ——
    负价属"不可用",由 quarantine 处理。
    """
    if "high" not in df.columns or "low" not in df.columns:
        return df
    cols = [c for c in ("open", "high", "low", "close") if c in df.columns]
    if len(cols) < 2:
        return df
    h, l = df["high"], df["low"]
    viol = pd.Series(False, index=df.index)
    if "open" in df.columns:
        viol |= (l > df["open"]) | (h < df["open"])
    if "close" in df.columns:
        viol |= (l > df["close"]) | (h < df["close"])
    if viol.any():
        sub = df[cols]
        df = df.copy()
        df.loc[viol, "high"] = sub.max(axis=1)[viol]
        df.loc[viol, "low"] = sub.min(axis=1)[viol]
    return df


def quarantine_sql_not_clause(entries: list[dict] | None = None) -> str:
    """构造 DuckDB WHERE 的 NOT 子句,用于即席扫描排除已隔离区间。

    返回形如  "NOT ((code='600608' AND date>=TIMESTAMP '2026-06-08'))",无条目返回 "TRUE"。
    """
    if entries is None:
        entries = load_quarantine()
    parts = []
    for e in entries:
        if e.get("action", "exclude") != "exclude":
            continue
        conds = [f"code='{e['code']}'"]
        if e.get("date_from"):
            conds.append(f"date>=TIMESTAMP '{e['date_from']}'")
        if e.get("date_to"):
            conds.append(f"date<=TIMESTAMP '{e['date_to']}'")
        parts.append("(" + " AND ".join(conds) + ")")
    if not parts:
        return "TRUE"
    return "NOT (" + " OR ".join(parts) + ")"
