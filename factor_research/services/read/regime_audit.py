"""Regime Audit Read Service —— regime 从「特征」升级为「审计器」(WS6, ADR-033)。

决策:**当前处于什么 regime(置信度多少),各在册策略在各 regime 下真实表现如何,
谁是 regime 依赖的「晴天策略」?** 此前 regime 只被消费(择时/搜索适应度/Gate7 拆分),
从未被独立审计——「压力段反成最佳年」这类 §7 自欺信号(LOOP_ENGINEERING)靠人眼。

诚实护栏:
- **纯披露层,不是新门**:输出归因数值与 WARN 标注,绝不做准入/退役判定
  (那是 9-Gate / decay_monitor 的事);阈值仅为披露排序,不进任何 gate。
- **归因口径防同日虚假相关**:RegimeEngine 的 trend 列已内建 shift(1)
  (factory/regime.py 注释明确警告);volatility/liquidity/breadth 三列用 T 日
  数据,本层归因时统一 shift(1)——ret[T] 只能归到 T-1 已知的 regime,
  否则「高波日收益高」是同日信息重叠的假发现。
- **确定性**:同输入同输出;数据可注入(close/amount/labels/returns_dir),
  运行时默认读 canonical 面板与 data_lake/version_returns。
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
VERSION_RETURNS_DIR = ROOT / "data_lake" / "version_returns"

# regime 维度 → RegimeEngine.classify() 的离散列;trend 已内建 lag,其余归因时再 shift(1)
_DIMS = ("trend", "volatility", "liquidity", "breadth")
_PRELAGGED_DIMS = {"trend"}
# 连续值列(current_regime 置信度用),与 factory/regime.py 输出一一对应
_VALUE_COLS = {"trend": "trend_dist", "volatility": "vol_value",
               "liquidity": "liq_value", "breadth": "breadth_value"}
# 单桶最少交易日:少于此不产出年化/夏普(纯披露约束,防噪声结论;非准入门)
_MIN_BUCKET_DAYS = 20
# stress_outperforms 用统计口径而非固定夏普差:两桶年化夏普差的标准误
# ≈ √(252/n_down + 252/n_up)(130 天桶时随机差 SD≈2,任何拍脑袋常数都会
# 在长短样本间失衡)。z>2 = 标准两倍标准误惯例;连续 z 值同时披露,
# 人仍可审视未过线的差异。WARN 路标,非判定门。
_STRESS_Z = 2.0


def load_regime_labels(close=None, amount=None, start: str = "2018-01-01") -> pd.DataFrame:
    """RegimeEngine 薄封装;close/amount 缺省时从 canonical 面板加载。"""
    if close is None or amount is None:
        from strategies.small_cap import load_price_panels

        close, _, amount = load_price_panels(start)
    from factory.regime import RegimeEngine

    return RegimeEngine(close, amount).classify()


def _lagged_dim(labels: pd.DataFrame, dim: str) -> pd.Series:
    """归因用标签:非 trend 维 shift(1)(trend 在 RegimeEngine 内已 lag,再 shift 会双重滞后)。"""
    col = labels[dim]
    return col if dim in _PRELAGGED_DIMS else col.shift(1)


def _bucket_stats(ret: pd.Series) -> dict:
    n = int(ret.notna().sum())
    if n < _MIN_BUCKET_DAYS:
        return {"days": n, "annual": None, "sharpe": None, "insufficient": True}
    mean, std = float(ret.mean()), float(ret.std())
    sharpe = mean / std * math.sqrt(252) if std > 1e-12 else 0.0
    return {"days": n, "annual": round(mean * 252, 4), "sharpe": round(sharpe, 3),
            "insufficient": False}


def attribute_returns_by_regime(ret: pd.Series, labels: pd.DataFrame) -> dict:
    """把日收益按四维 regime 桶归因(lagged 口径),并给出 §7 自欺披露标注。

    返回 {"dims": {dim: {value: stats}}, "flags": {...}}。flags:
      - stress_outperforms: trend=down 段夏普 > trend=up 段夏普(「压力段反成最佳年」
        的机械化,LOOP_ENGINEERING §7)——WARN 级披露,非判定;
      - sharpe_gap: 每维两值段夏普差(连续披露值,供排序审视 regime 依赖度)。
    """
    ret = ret.dropna()
    idx = ret.index.intersection(labels.index)
    ret = ret.loc[idx]
    dims: dict = {}
    gaps: dict = {}
    for dim in _DIMS:
        lab = _lagged_dim(labels, dim).reindex(idx)
        buckets = {}
        for value in sorted(pd.unique(lab.dropna())):
            buckets[str(value)] = _bucket_stats(ret[lab == value])
        dims[dim] = buckets
        sharpes = [b["sharpe"] for b in buckets.values() if b["sharpe"] is not None]
        gaps[dim] = round(max(sharpes) - min(sharpes), 3) if len(sharpes) >= 2 else None

    trend = dims.get("trend", {})
    down = trend.get("down") or {}
    up = trend.get("up") or {}
    stress_outperforms, stress_z = False, None
    if down.get("sharpe") is not None and up.get("sharpe") is not None:
        se = math.sqrt(252.0 / down["days"] + 252.0 / up["days"])
        stress_z = round((down["sharpe"] - up["sharpe"]) / se, 2)
        stress_outperforms = stress_z > _STRESS_Z  # 纯噪声随机差过不了两倍标准误
    return {
        "dims": dims,
        "flags": {"stress_outperforms": bool(stress_outperforms),
                  "stress_z": stress_z, "sharpe_gap": gaps},
    }


def current_regime(close=None, amount=None, labels: pd.DataFrame | None = None) -> dict:
    """最新 regime 标签 + 连续值 + 置信度(历史分位偏离 |pct−0.5|×2 ∈ [0,1])。

    置信度是确定性描述量:连续值越偏离历史中位数,当前分类越「深」;
    ≈0 表示贴着分界线(分类易翻转),不是概率模型。
    """
    if labels is None:
        labels = load_regime_labels(close, amount)
    latest = labels.iloc[-1]
    out: dict = {"date": str(labels.index[-1].date()), "dims": {}}
    for dim in _DIMS:
        vcol = _VALUE_COLS[dim]
        hist = labels[vcol].dropna()
        cur = latest[vcol]
        if len(hist) < _MIN_BUCKET_DAYS or pd.isna(cur):
            conf = None
        else:
            pct = float((hist <= cur).mean())
            conf = round(abs(pct - 0.5) * 2, 3)
        out["dims"][dim] = {"label": str(latest[dim]), "value": None if pd.isna(cur) else float(cur),
                            "confidence": conf}
    return out


def audit_registered_strategies(
    close=None,
    amount=None,
    labels: pd.DataFrame | None = None,
    returns_dir: Path | None = None,
) -> dict:
    """对 data_lake/version_returns 全部在册版本收益做 regime 归因审计(只读)。

    输出按 stress_outperforms 优先、max sharpe_gap 降序排列——最像「晴天策略」的
    排最前供人审视。文件名口径与 decay_monitor 一致:<family>__<version>.csv。
    """
    if labels is None:
        labels = load_regime_labels(close, amount)
    rdir = Path(returns_dir) if returns_dir is not None else VERSION_RETURNS_DIR
    rows = []
    for fp in sorted(rdir.glob("*.csv")) if rdir.exists() else []:
        try:
            ret = pd.read_csv(fp, index_col=0)["ret"]
            ret.index = pd.to_datetime(ret.index)
        except Exception:
            rows.append({"version": fp.stem, "error": "unreadable"})
            continue
        att = attribute_returns_by_regime(ret, labels)
        gaps = [g for g in att["flags"]["sharpe_gap"].values() if g is not None]
        rows.append({
            "version": fp.stem.replace("__", "/"),
            "stress_outperforms": att["flags"]["stress_outperforms"],
            "max_sharpe_gap": max(gaps) if gaps else None,
            "attribution": att["dims"],
        })
    rows.sort(key=lambda r: (not r.get("stress_outperforms", False),
                             -(r.get("max_sharpe_gap") or 0.0)))
    return {"current": current_regime(labels=labels), "strategies": rows,
            "note": "披露层:WARN 标注非判定;归因用 lagged regime 标签(防同日虚假相关)"}
