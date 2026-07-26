"""从现有因子做参数扰动，产生 Hypothesis。

每个 factor 的参数范围在 FACTOR_MUTATION_SPECS 显式声明。
扩展新 factor 只需加一条 spec。
"""
import itertools
from collections.abc import Iterator
from datetime import date

from factory.ontology import EconomicThesis, Hypothesis, HypothesisStatus

# ────────────────────────── Mutation 规格 ──────────────────────────

FACTOR_MUTATION_SPECS: dict[str, dict] = {
    # 已 LIVE 的小盘量价因子 — 从 strategy_versions.json 提取
    "factors.small_cap.small_cap_factor": {
        "thesis": EconomicThesis(
            mechanism="小盘流动性溢价：低成交额=低流动性=被低估，超额收益补偿流动性风险",
            citation="Amihud 2002; 用户已 LIVE illiquidity v1.0 +20% 实证",
            falsifiability="size 因子 IC 持续 < 0 一年；小盘指数滚动跑输沪深 300",
        ),
        "param_grid": {
            # 短/中/长三档,MI 可区分。原 [20,30,45,60,90,120,252] 经
            # metasearch/factor_mi_audit 实测 6 窗口互相 MI>2.0(同一信息算 6 遍),
            # 收敛去重(metasearch_findings_20260623)。
            "window": [20, 60, 252],
        },
        "data_dependencies": ("price/amount",),
    },

    # 动量因子（基础动量）
    "factors.momentum.mom_n": {
        "thesis": EconomicThesis(
            mechanism="A股动量短期反转长期延续：1月反转 + 12月动量",
            citation="Jegadeesh-Titman 1993；A股截面动量观察",
            falsifiability="动量因子在 OOS 全样本期 IR < 0",
        ),
        "param_grid": {
            "n": [5, 10, 20, 60, 120, 252],
            "skip": [0, 5],
        },
        "data_dependencies": ("price/close",),
    },

    # 低波因子
    "factors.momentum.volatility": {
        "thesis": EconomicThesis(
            mechanism="低波动率溢价：高波股票被散户过度追逐，低波长期占优",
            citation="Frazzini-Pedersen 2014 'Betting Against Beta'",
            falsifiability="VIX 持续低位时低波溢价消失",
        ),
        "param_grid": {
            "n": [10, 20, 60, 120],
        },
        "data_dependencies": ("price/close",),
    },

    # Amihud illiquidity（参数可调；分母=amount，与 OO AmihudIlliq 对齐）
    "factors.momentum.illiquidity": {
        "thesis": EconomicThesis(
            mechanism="Amihud 2002 非流动性 = mean(|ret|/amount)；DSL 用 volume×close 代理 amount",
            citation="Amihud 2002 + factors.alpha.builtins.illiq.AmihudIlliq",
            falsifiability="市场成交额持续放大；因子 IC 连续 4 季 < 0",
        ),
        "param_grid": {
            # 去 n60:metasearch/factor_mi_audit 实测 illiquidity n40↔n60 距离 0.52
            # (近同一信息源),保 n40(生产相邻口径)(metasearch_findings_20260623)。
            "n": [5, 10, 20, 40],
        },
        "data_dependencies": ("price/close", "price/volume", "price/amount"),
    },

    # 价格偏离均线
    "factors.momentum.price_to_ma": {
        "thesis": EconomicThesis(
            mechanism="价格相对均线偏离反映短期超买超卖",
            citation="技术分析经典；A股散户回归均值偏好",
            falsifiability="OOS Sharpe < 0",
        ),
        "param_grid": {
            "n": [10, 20, 60, 120, 252],
        },
        "data_dependencies": ("price/close",),
    },

    # ── 多元化 alpha 源（与 small_cap 正交）─────────────────────────

    # 短期反转：行为金融，与小盘流动性溢价不同
    "factors.microstructure.short_reversal": {
        "thesis": EconomicThesis(
            mechanism="短期过度反应 → 价格回归。A 股 1-3 周反转效应，与 small_cap 正交",
            citation="DeBondt-Thaler 1985；A 股短期反转实证",
            falsifiability="OOS Sharpe < 0；或反转周期延长到 1 个月以上",
        ),
        "param_grid": {
            "n": [3, 5, 10, 20],
        },
        "data_dependencies": ("price/close",),
    },

    # N 日价格位置反转
    "factors.microstructure.price_position": {
        "thesis": EconomicThesis(
            mechanism="N 日低位 = 已超卖 → 反弹概率高。比 short_reversal 更稳健",
            citation="Conrad-Kaul 1998 价格水平反转",
            falsifiability="OOS Sharpe < 0",
        ),
        "param_grid": {
            "n": [20, 60, 120],
        },
        "data_dependencies": ("price/close",),
    },

    # 量比突破：关注度跳变
    "factors.microstructure.vol_breakout": {
        "thesis": EconomicThesis(
            mechanism="突然放量 → 关注度跳变 + 信息冲击 → 短期 alpha（A 股散户驱动）",
            citation="Lee-Swaminathan 2000；A 股量价共振",
            falsifiability="高换手股长期跑输 → 关注度 alpha 衰减",
        ),
        "param_grid": {
            "short": [3, 5, 10],
            "long": [10, 20, 60],
        },
        "data_dependencies": ("price/volume",),
    },

    # 振幅：风险溢价 (低波的对立面)
    "factors.microstructure.amplitude_mean": {
        "thesis": EconomicThesis(
            mechanism="高振幅股 → 风险溢价 + 注意力溢价（A 股 lottery 偏好的正面）",
            citation="Bali-Cakici-Whitelaw 2011 lottery preference",
            falsifiability="低波溢价 dominate → 反方向更优",
        ),
        "param_grid": {
            "n": [10, 20, 60],
        },
        "data_dependencies": ("price/close",),
    },

    # ── 基本面 alpha 源 (Information Map 驱动,2026-06-07) ─────────────────
    # 这些应该与 LIVE (价量类) 在 MI 空间距离 >> 2,验证信息地图预测力
    # 单参数(没有 window),每个 factor 只一个 hypothesis

    "factors.fundamental.net_profit_yoy": {
        "thesis": EconomicThesis(
            mechanism="盈利增长 = 基本面动量,散户对成长性溢价。size_earnings v1.0 已 LIVE 实证",
            citation="Fama-French 1992 + size_earnings v1.0 OOS Sharpe 1.16",
            falsifiability="NPY IC 持续 < 0 一年 → 价值/反转 regime",
        ),
        "param_grid": {"_": [0]},   # 占位,本因子无参数
        "data_dependencies": ("price/close", "fundamental/net_profit_yoy"),
    },
    "factors.fundamental.revenue_yoy": {
        "thesis": EconomicThesis(
            mechanism="营收同比 = 顶层成长信号,绕过净利润操纵",
            citation="Lakonishok-Shleifer-Vishny 1994",
            falsifiability="IC 持续 < 0",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/revenue_yoy"),
    },
    "factors.fundamental.roe": {
        "thesis": EconomicThesis(
            mechanism="ROE 经典质量因子,高 ROE 长期跑赢",
            citation="Asness-Frazzini-Pedersen 2019 'Quality minus Junk'",
            falsifiability="IC 持续 < 0 / Junk 因子主导",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/roe"),
    },
    "factors.fundamental.gross_margin": {
        "thesis": EconomicThesis(
            mechanism="毛利率 = 经营护城河,高毛利持续",
            citation="Novy-Marx 2013 'Other Side of Value'",
            falsifiability="IC 持续 < 0",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/gross_margin"),
    },

    # ── 价值因子 BP/EP (与 LIVE long-only 选股池 overlap 最小,2026-06-07) ─────
    # 价值股 (高 BP/EP) 多大盘金融/周期/蓝筹,与小盘 fundamental-growth 反向
    "factors.fundamental.bp_proxy": {
        "thesis": EconomicThesis(
            mechanism="高 BP (净资产/价 高) = 被低估,经典 Graham 价值。选股多大盘金融/周期 → 与小盘 overlap 小",
            citation="Fama-French 1992 + Lakonishok 1994",
            falsifiability="价值 IC 持续 < 0 一年,或选股仍偏小盘",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/bps"),
    },
    "factors.fundamental.ep_proxy": {
        "thesis": EconomicThesis(
            mechanism="高 EP (EPS/价 高) = 低 PE = 经典价值因子",
            citation="Fama-French 1992; Basu 1977",
            falsifiability="价值 IC 持续 < 0",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/eps_ttm"),
    },
    "factors.fundamental.cfo_quality": {
        "thesis": EconomicThesis(
            mechanism="经营现金流/股 = 真实盈利质量,绕过应收账款操纵",
            citation="Sloan 1996 'accrual anomaly'",
            falsifiability="cfo_ps IC 持续 < 0",
        ),
        "param_grid": {"_": [0]},
        "data_dependencies": ("price/close", "fundamental/cfo_ps"),
    },

    # ── OHLC 微观结构 (raw_high/low/open 之前完全没用,2026-06-07) ─────────
    "factors.ohlc.amplitude_mean": {
        "thesis": EconomicThesis(
            mechanism="日内振幅 = 不确定性溢价 + lottery preference。日内 ≠ 收盘 vol",
            citation="Bali-Cakici-Whitelaw 2011 lottery preference",
            falsifiability="低波 dominate → 反方向更优",
        ),
        "param_grid": {"n": [10, 20, 60]},
        "data_dependencies": ("price/close",),    # OHLC 内部 lru_cache
    },
    "factors.ohlc.overnight_gap": {
        "thesis": EconomicThesis(
            mechanism="隔夜跳空 = 海外信息冲击 + 散户隔夜情绪,A 股 T+1 制度下隔夜 alpha 独特",
            citation="Lou-Polk-Skouras 2019 'overnight return'",
            falsifiability="隔夜 mean ≈ 0 in OOS",
        ),
        "param_grid": {"n": [5, 10, 20]},
        "data_dependencies": ("price/close",),
    },
    "factors.ohlc.close_position": {
        "thesis": EconomicThesis(
            mechanism="收盘相对日内位置 = 买盘/抛压强弱,收高位 → 多头持续",
            citation="技术分析经典 + 散户尾盘行为",
            falsifiability="OOS Sharpe < 0",
        ),
        "param_grid": {"n": [5, 10, 20]},
        "data_dependencies": ("price/close",),
    },
    "factors.ohlc.high_low_breakout": {
        "thesis": EconomicThesis(
            mechanism="突破 N 日新高 = 趋势确认,Donchian-style. A 股散户追涨杀跌增强",
            citation="Donchian Channel; Faber 2007",
            falsifiability="OOS Sharpe < 0",
        ),
        "param_grid": {"n": [10, 20, 60, 120]},
        "data_dependencies": ("price/close",),
    },

    # 截面动量 z-score
    "factors.microstructure.ret_zscore_cross": {
        "thesis": EconomicThesis(
            mechanism="N 日累计收益的截面排名；纯动量信号去除幅度噪声",
            citation="Jegadeesh-Titman 1993 截面动量",
            falsifiability="动量周期与 mom_n 完全一致 → 信息冗余",
        ),
        "param_grid": {
            "n": [20, 60, 120],
        },
        "data_dependencies": ("price/close",),
    },
}


# ────────────────────────── 生成 ──────────────────────────

def mutate_factor(
    factor_fn_name: str,
    parent_id: str | None = None,
    extra_thesis_note: str = "",
) -> Iterator[Hypothesis]:
    """对一个 factor 枚举所有参数组合，yield Hypothesis。"""
    spec = FACTOR_MUTATION_SPECS.get(factor_fn_name)
    if spec is None:
        return

    thesis = spec["thesis"]
    if extra_thesis_note:
        thesis = EconomicThesis(
            mechanism=f"{thesis.mechanism} | {extra_thesis_note}",
            citation=thesis.citation,
            falsifiability=thesis.falsifiability,
        )

    today = date.today().isoformat()
    param_names = list(spec["param_grid"].keys())
    param_values = [spec["param_grid"][n] for n in param_names]
    fn_short = factor_fn_name.rsplit(".", 1)[-1]

    for combo in itertools.product(*param_values):
        params = dict(zip(param_names, combo, strict=True))
        name_suffix = "_".join(f"{k}{v}" for k, v in params.items())

        yield Hypothesis(
            name=f"{fn_short}__{name_suffix}",
            description=f"{fn_short} with params={params}",
            factor_fn_name=factor_fn_name,
            factor_params=params,
            data_dependencies=spec["data_dependencies"],
            thesis=thesis,
            source="mutation",
            source_ref=parent_id,
            parent_hypothesis_id=parent_id,
            status=HypothesisStatus.DRAFTED,
            created_at=today,
        )


def generate_all_mutations() -> list[Hypothesis]:
    """对所有 FACTOR_MUTATION_SPECS 中的 factor 生成全部参数组合 hypothesis。"""
    out: list[Hypothesis] = []
    for fname in FACTOR_MUTATION_SPECS:
        out.extend(mutate_factor(fname))
    return out
