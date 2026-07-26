"""隐含预期差因子族(市场预期层的因子化,probe 前候选)。

Disposition: dormant — 已 probe 全阴性(2026-07-12:implied_growth_gap/peg_inverse 真正交但 OOS 塌缩翻负,guidance_gap 落「真正交但太弱」量级带;登记簿两条 DEPRIORITIZE 180d,报告 reports/research/probe_expectation_gap_20260712.md);复活条件=湖内添真前瞻一致预期/预期修正数据源后整族重测(R-ARCH-005 精神)。

为什么:估值(pe_ttm)与增长(netprofit_yoy)各自已在因子层(value/fundamental),
但「价格隐含的增长要求 vs PIT 已知的增长兑现/指引」的**差**从未进入因子层——
市场交易的是预期差,不是利润本身;方向登记簿 frontier-fundamental-family(BOOST)
正指基本面族为现有池最独立的空白区(MI 距价格族 2.8-2.9)。

机制锚:永续增长恒等式 P/E=(1+g)/(r−g) 反解 g_implied=(PE·r−1)/(PE+1)。
g_implied 是 PE 的单调变换(截面排序上≡价值因子)——**族的信号在差上,不在水平上**:
  gap = 已兑现(netprofit_yoy)或已指引(forecast/express)的增速 − 价格隐含要求的增速。
高 gap = 交付超过价格的要求(预期差为正)。IC 符号由 probe 检验,本文件不含有效性声明
(R-LLM-001:只算,不裁决)。

状态:**probe 前候选**——未接 DSL 白名单/种子(probe-signal-source 步骤 3 正交性
+IS/OOS 体检通过后才走步骤 4 接工厂;证据先行,勿倒置)。probe 命令(须在有数据湖
的机器上跑):
  python scripts/research/signal_source_probe.py \
    --factor factors.expectation_gap:implied_growth_gap \
    --start 2018-01-01 --cutoff 2022-12-31 --end 2024-12-31

PIT 纪律(R-DATA-003/R-DATA-004):
  pe_ttm 经 load_tushare_panel("daily_basic")(by_date 当日对齐,交易所按不复权价
  计算的口径,T 日收盘已知);netprofit_yoy 经 load_tushare_panel("fina_indicator")
  (anndate 公告日 ffill);业绩指引经 forecast/express(anndate,口径同
  factors/earnings.py:快报实际优先,缺则预告中点)。loader 已防未来,本模块不再 shift。

覆盖诚实:pe_ttm≤0(亏损股)必须 NaN——本族只对盈利宇宙有定义,不给亏损股编分。
discount_rate 是截面等常数:不改 g_implied 的截面排序,只改 gap 中价值/成长两腿的
相对权重——它是真实搜索自由度,若网格化必须计入 n_trials(R-EVIDENCE-001④)。
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from factors.utils import mad_clip, safe_zscore


@lru_cache(maxsize=1)
def _load_db_cache():
    """加载 pe_ttm 面板(by_date 当日对齐),缓存避免重复 I/O(同 fundamental.py 模式)。"""
    from lake.load_lake import load_tushare_panel

    dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_tushare_panel("daily_basic", dates, fields=["pe_ttm"])


@lru_cache(maxsize=1)
def _load_fina_cache():
    """加载 netprofit_yoy 面板(anndate 公告日 ffill 防未来)。"""
    from lake.load_lake import load_tushare_panel

    dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    return load_tushare_panel("fina_indicator", dates, fields=["netprofit_yoy"])


@lru_cache(maxsize=1)
def _load_guidance_cache():
    """加载业绩预告/快报面板(anndate ffill;字段口径同 factors/earnings.py)。"""
    from lake.load_lake import load_tushare_panel

    dates = pd.date_range("2010-01-01", "2030-01-01", freq="B")
    fc = load_tushare_panel("forecast", dates, fields=["p_change_min", "p_change_max"])
    ex = load_tushare_panel("express", dates, fields=["yoy_net_profit"])
    return fc, ex


def _require(panels: dict, fields: list[str], src: str) -> None:
    """缺字段/空面板必须显式失败——静默给零分 = 半截口径混进候选池(R-DATA 系)。"""
    missing = [f for f in fields if f not in panels or getattr(panels[f], "empty", True)]
    if missing:
        raise ValueError(f"{src} 面板缺字段 {missing};先跑 update_tushare --interface {src}")


def _align(panel: pd.DataFrame, close: pd.DataFrame, ffill: bool = False) -> pd.DataFrame:
    """面板 → 对齐 close 交易日与股票池(基本面独有票丢弃;close 独有票 NaN 不编造)。

    anndate 面板传 ffill=True(公告值在下次公告前持续有效);by_date 面板(估值)
    不 ffill——停牌日无估值就是无估值,不用陈旧值冒充。
    """
    out = panel.reindex(close.index)
    if ffill:
        out = out.ffill()
    return out.reindex(columns=close.columns)


def implied_growth(pe_ttm: pd.DataFrame, discount_rate: float = 0.09) -> pd.DataFrame:
    """永续口径隐含增速(小数):g=(PE·r−1)/(PE+1);仅 PE>0 有定义(亏损股 NaN)。

    这是 PE 的单调变换——单独作因子≡价值因子,只作族内的"价格要求"一腿。
    """
    pe = pe_ttm.where(pe_ttm > 0)
    return (pe * discount_rate - 1.0) / (pe + 1.0)


# ── 纯核心(注入面板,可无数据单测) ───────────────────────────────────────────

def implied_growth_gap_core(db: dict, fina: dict, close: pd.DataFrame,
                            discount_rate: float = 0.09) -> pd.DataFrame:
    """预期差(兑现口径)= netprofit_yoy − 100·g_implied。

    机制:同等估值下兑现增速更高、或同等兑现下价格要求更低 → gap 更高;
    是估值与增长的差,不是任一单腿的单调变换(测试锁死两个方向)。
    """
    _require(db, ["pe_ttm"], "daily_basic")
    _require(fina, ["netprofit_yoy"], "fina_indicator")
    pe = _align(db["pe_ttm"], close)
    yoy = _align(fina["netprofit_yoy"], close, ffill=True)
    gap = yoy - 100.0 * implied_growth(pe, discount_rate)
    return safe_zscore(mad_clip(gap.replace([np.inf, -np.inf], np.nan)))


def guidance_gap_core(db: dict, fc: dict, ex: dict, close: pd.DataFrame,
                      discount_rate: float = 0.09) -> pd.DataFrame:
    """预期差(指引口径)= 业绩指引增速 − 100·g_implied。

    指引 = 快报实际(yoy_net_profit)优先,缺则预告中点(p_change 中点)——同
    factors/earnings.py 的 SUE 口径,但除以价格要求后含义不同:同样的指引,
    在价格要求低的票上预期差更大。覆盖有偏(预告为条件强制披露,偏大幅变动股),
    无指引的票诚实 NaN。
    """
    _require(db, ["pe_ttm"], "daily_basic")
    _require(fc, ["p_change_min", "p_change_max"], "forecast")
    _require(ex, ["yoy_net_profit"], "express")
    pe = _align(db["pe_ttm"], close)
    yoy_ex = _align(ex["yoy_net_profit"], close, ffill=True)
    mid = (_align(fc["p_change_min"], close, ffill=True)
           + _align(fc["p_change_max"], close, ffill=True)) / 2
    guided = yoy_ex.where(yoy_ex.notna(), mid)  # 快报实际优先,缺则预告中点
    gap = guided - 100.0 * implied_growth(pe, discount_rate)
    return safe_zscore(mad_clip(gap.replace([np.inf, -np.inf], np.nan)))


def peg_inverse_core(db: dict, fina: dict, close: pd.DataFrame) -> pd.DataFrame:
    """PEG 倒数 = netprofit_yoy / pe_ttm(仅 PE>0)。

    同一机制的乘性参数化(增长相对价格要求的性价比),无 discount_rate 自由度;
    负增速高估值自然垫底。
    """
    _require(db, ["pe_ttm"], "daily_basic")
    _require(fina, ["netprofit_yoy"], "fina_indicator")
    pe = _align(db["pe_ttm"], close).where(lambda x: x > 0)
    yoy = _align(fina["netprofit_yoy"], close, ffill=True)
    raw = yoy / pe
    return safe_zscore(mad_clip(raw.replace([np.inf, -np.inf], np.nan)))


# ── probe 入口(签名对齐 signal_source_probe:(close, **params)) ─────────────

def implied_growth_gap(close, discount_rate: float = 0.09, **_):
    return implied_growth_gap_core(_load_db_cache(), _load_fina_cache(), close,
                                   discount_rate=float(discount_rate))


def guidance_gap(close, discount_rate: float = 0.09, **_):
    fc, ex = _load_guidance_cache()
    return guidance_gap_core(_load_db_cache(), fc, ex, close,
                             discount_rate=float(discount_rate))


def peg_inverse(close, **_):
    return peg_inverse_core(_load_db_cache(), _load_fina_cache(), close)


# 工厂/autoresearch 可发现的族成员(name → callable;probe 通过前不接白名单)
EXPECTATION_GAP_FACTORS = {
    "implied_growth_gap": implied_growth_gap,
    "guidance_gap": guidance_gap,
    "peg_inverse": peg_inverse,
}
