"""Centralised schema definitions for the data lake.

All field names, rename mappings, and type specs live here so that no other
module hard-codes raw source column names.
"""

# ---------------------------------------------------------------------------
# Price / volume
# ---------------------------------------------------------------------------
PRICE_FIELDS = ["date", "open", "high", "low", "close", "volume", "amount"]
RAW_PRICE_FIELDS = ["date", "raw_open", "raw_high", "raw_low", "raw_close"]

# ---------------------------------------------------------------------------
# Fundamental (from yjbb_em batch source)
# ---------------------------------------------------------------------------
FUNDAMENTAL_FIELDS = [
    "roe",
    "eps",
    "eps_ttm",
    "bps",
    "revenue",
    "net_profit",
    "gross_margin",
    "cfo_ps",
    "revenue_yoy",
    "net_profit_yoy",
    "industry",
]

# Eastmoney yjbb_em 批量接口: 原始列名 → 标准列名
YJBB_RENAME = {
    "股票代码": "code",
    "每股收益": "eps",
    "营业总收入-营业总收入": "revenue",
    "营业总收入-同比增长": "revenue_yoy",
    "净利润-净利润": "net_profit",
    "净利润-同比增长": "net_profit_yoy",
    "每股净资产": "bps",
    "净资产收益率": "roe",
    "每股经营现金流量": "cfo_ps",
    "销售毛利率": "gross_margin",
    "所处行业": "industry",
    # DQ-2026-001:"最新公告日期"是公司级、随最近一次披露滚动刷新的字段,不是本报告期
    # 专属公告日(现场复现见 scripts/data/repair_fundamental_batch_ann.py 文档)。
    # 绝不可再直接当 ann_date 消费;真实 ann_date/avail_date 由该脚本按
    # income/balancesheet/cashflow 三表 ann_date 交叉核对后重铺,此列仅留作审计溯源。
    "最新公告日期": "em_last_touch_date",
}

# 逐只财务源 (Eastmoney stock_financial_abstract) — 已废弃,保留映射供兼容
EM_FIN_RENAME = {
    "归母净利润": "net_profit_parent",
    "营业总收入": "revenue",
    "净利润": "net_profit",
    "扣非净利润": "net_profit_deduct",
    "经营现金流量净额": "cfo",
    "基本每股收益": "eps",
    "每股净资产": "bps",
    "净资产收益率(ROE)": "roe",
    "毛利率": "gross_margin",
    "资产负债率": "debt_ratio",
    "每股经营现金流": "cfo_ps",
}

# ---------------------------------------------------------------------------
# Capital (margin financing + northbound)
# ---------------------------------------------------------------------------
CAPITAL_FIELDS = [
    "margin_balance",
    "margin_buy",
    "short_balance",
    "short_vol",
    "northbound_hold_shares",
    "northbound_hold_value",
    "northbound_hold_pct",
    "northbound_value_chg_1d",
    "northbound_value_chg_5d",
    "northbound_value_chg_10d",
    "northbound_hold_shares_chg_1d",
    "northbound_buy_value_1d",
]

# ---------------------------------------------------------------------------
# Daily basic (tushare 每日指标:市值/股本/换手/估值)
# ---------------------------------------------------------------------------
DAILY_BASIC_FIELDS = [
    "total_share",      # 总股本(万股)
    "float_share",      # 流通股本(万股)
    "total_mv",         # 总市值(万元)
    "circ_mv",          # 流通市值(万元)
    "turnover_rate",    # 换手率(%)
    "turnover_rate_f",  # 换手率(自由流通股)
    "pe",               # 市盈率(总市值/净利润)
    "pe_ttm",           # 市盈率(TTM)
    "pb",               # 市净率
    "ps",               # 市销率
    "ps_ttm",           # 市销率(TTM)
    "dv_ratio",         # 股息率(%)
    "dv_ttm",           # 股息率(TTM)
]

# ---------------------------------------------------------------------------
# Fina indicator (tushare 财务指标:杠杆/质量/成长 → Barra 基本面风格)
# ---------------------------------------------------------------------------
FINA_INDICATOR_FIELDS = [
    "roe",                # 净资产收益率
    "roe_waa",            # 加权 ROE
    "roa",                # 总资产收益率
    "debt_to_assets",     # 资产负债率 → Leverage
    "assets_to_eqt",      # 权益乘数
    "netprofit_margin",   # 净利率
    "grossprofit_margin", # 毛利率
    "current_ratio",      # 流动比率
    "quick_ratio",        # 速动比率
    "or_yoy",             # 营收同比 → Growth
    "netprofit_yoy",      # 净利同比 → Growth
    "assets_turn",        # 总资产周转率 → Quality
    "ar_turn",            # 应收账款周转率
    "ocfps",              # 每股经营现金流
    "fcff",               # 企业自由现金流
    "eps", "bps", "cfps",
]

# ---------------------------------------------------------------------------
# Tushare 扩展维度字段(by_date 当日对齐 / anndate 公告日 ffill)
# ---------------------------------------------------------------------------
MONEYFLOW_FIELDS = [  # 资金流(by_date):大中小单买卖额 + 净流入(万元)
    "buy_sm_amount", "sell_sm_amount", "buy_md_amount", "sell_md_amount",
    "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount", "net_mf_amount",
]
STK_LIMIT_FIELDS = ["up_limit", "down_limit"]            # 涨跌停价(by_date)
SUSPEND_FIELDS = ["suspend_type"]                        # 停复牌(by_date)
FORECAST_FIELDS = [                                      # 业绩预告(anndate)
    "type", "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
]
EXPRESS_FIELDS = [                                       # 业绩快报(anndate)
    "revenue", "n_income", "diluted_eps", "diluted_roe", "yoy_net_profit",
]
HOLDERNUMBER_FIELDS = ["holder_num"]                     # 股东户数(anndate)
INDEX_DAILY_FIELDS = ["close", "open", "high", "low", "pct_chg", "vol", "amount"]  # 基准指数(by_date)
CYQ_FIELDS = ["cost_5pct", "cost_50pct", "cost_95pct", "weight_avg", "winner_rate"]  # 筹码(by_date)
HOLDERTRADE_FIELDS = ["in_de", "change_vol", "change_ratio", "after_ratio", "avg_price"]  # 增减持(anndate)
ADJ_FACTOR_FIELDS = ["adj_factor"]                                                    # 复权因子(by_date)
INCOME_FIELDS = ["revenue", "operate_profit", "total_profit", "n_income",            # 利润表(anndate)
                 "n_income_attr_p", "ebit", "ebitda"]
BALANCESHEET_FIELDS = ["total_assets", "total_liab", "total_hldr_eqy_exc_min_int",    # 资产负债表(anndate)
                       "total_cur_assets", "total_cur_liab", "money_cap", "lt_borr", "st_borr",
                       "accounts_receiv", "notes_receiv", "inventories", "acct_payable", "notes_payable"]  # 议价权/CCC
CASHFLOW_FIELDS = ["n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act",    # 现金流量表(anndate)
                   "c_pay_acq_const_fiolta", "free_cashflow"]
DIVIDEND_FIELDS = ["stk_div", "cash_div", "cash_div_tax"]                             # 分红(anndate)

# ---------------------------------------------------------------------------
# 新增 tushare 维度字段(契约补齐 2026-07;存量 16 条字段表见上,本段只加新表)
# ---------------------------------------------------------------------------
SHARE_FLOAT_FIELDS = [  # 限售解禁(anndate):供给压力
    "float_share", "float_ratio", "holder_name", "share_type",
]
INDEX_CLASSIFY_FIELDS = [  # 申万行业分类(once 静态表)
    "industry_name", "level", "industry_code", "parent_code", "src",
]
BLOCK_TRADE_FIELDS = [  # 大宗交易
    "price", "vol", "amount", "buyer", "seller",
]
# 契约默认研究字段(原始源列);状态衍生列由 load_pledge_stat_panel 生成
PLEDGE_STAT_RAW_FIELDS = [
    "pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio",
]
TOP10_HOLDERS_FIELDS = [  # 十大股东
    "holder_name", "hold_amount", "hold_ratio", "holder_type", "hold_float_ratio",
]
TOP_LIST_FIELDS = [  # 龙虎榜名单
    "close", "pct_change", "turnover_rate", "amount",
    "l_sell", "l_buy", "l_amount", "net_amount", "net_rate", "amount_rate",
    "float_values", "reason",
]
TOP_INST_FIELDS = [  # 龙虎榜机构席位
    "exalter", "side", "buy", "buy_rate", "sell", "sell_rate", "net_buy", "reason",
]
REPURCHASE_FIELDS = [  # 回购公告
    "end_date", "proc", "exp_date", "vol", "amount", "high_limit", "low_limit",
]

# dataset → (store 相对路径, 口径 by_date|by_date_shift1|anndate, 默认字段)
# 时间轴口径三选一(data_source_onboarding.md §S2 唯一真相):
#   by_date         T 日盘后可知(价格衍生) → 不 shift
#   by_date_shift1  T 日盘后发布、次日才可用 → shift(1)
#   anndate         财务/公告/事件 → ann_date 公告日 ffill
# 拿不准 = 最晚可见口径 + # UNCERTAIN-REVIEW 注释。
# ⚠️ 存量 16 条的 (store, mode, fields) 冻结,禁止"顺手改口径"(见 test_dataset_contracts)。
TUSHARE_DATASETS = {
    "daily_basic":   ("daily_basic/daily_basic_all.parquet", "by_date", DAILY_BASIC_FIELDS),
    "moneyflow":     ("moneyflow/moneyflow_all.parquet", "by_date", MONEYFLOW_FIELDS),
    "stk_limit":     ("market/stk_limit_all.parquet", "by_date", STK_LIMIT_FIELDS),
    "suspend":       ("market/suspend_all.parquet", "by_date", SUSPEND_FIELDS),
    "fina_indicator": ("financials/fina_indicator_all.parquet", "anndate", FINA_INDICATOR_FIELDS),
    "forecast":      ("event/forecast_all.parquet", "anndate", FORECAST_FIELDS),
    "express":       ("event/express_all.parquet", "anndate", EXPRESS_FIELDS),
    "holdernumber":  ("holder/holdernumber_all.parquet", "anndate", HOLDERNUMBER_FIELDS),
    "index_daily":   ("index/index_daily_all.parquet", "by_date", INDEX_DAILY_FIELDS),
    "cyq_perf":      ("cyq/cyq_perf_all.parquet", "by_date", CYQ_FIELDS),
    "holdertrade":   ("holder/holdertrade_all.parquet", "anndate", HOLDERTRADE_FIELDS),
    "adj_factor":    ("adj_factor/adj_factor_all.parquet", "by_date", ADJ_FACTOR_FIELDS),
    "income":        ("financials/income_all.parquet", "anndate", INCOME_FIELDS),
    "balancesheet":  ("financials/balancesheet_all.parquet", "anndate", BALANCESHEET_FIELDS),
    "cashflow":      ("financials/cashflow_all.parquet", "anndate", CASHFLOW_FIELDS),
    "dividend":      ("corp_action/dividend_all.parquet", "anndate", DIVIDEND_FIELDS),
    # ── 以下为契约补齐新增(不得改上面 16 条)──
    # evidence: INTERFACES share_float by_stock + ann_date 字段;
    # docs/data_infrastructure.md L117 口径 anndate
    "share_float":   ("holder/share_float_all.parquet", "anndate", SHARE_FLOAT_FIELDS),
    # evidence: INTERFACES index_classify mode=once; data_infrastructure.md L118 once
    # 静态申万分类参考表,非交易日面板;声明 by_date=入库即可用(无盘后滞后语义)
    # UNCERTAIN-REVIEW: 无申万历史重分类 PIT 序列,静态快照可能覆盖历史成分变化
    "index_classify": ("index/sw_classify.parquet", "by_date", INDEX_CLASSIFY_FIELDS),
    # evidence: INTERFACES block_trade mode=by_date(下载按 trade_date);
    # 大宗交易交易所盘后披露 → 研究侧按次日可用(宁晚不泄,R-DATA-003)
    # UNCERTAIN-REVIEW: 公开库是否 T 日 EOD 即可用于 T+0 信号无权威文档,取 shift1
    "block_trade":   ("institutional/block_trade_all.parquet", "by_date_shift1", BLOCK_TRADE_FIELDS),
    # evidence: load_lake.align_pledge_stat ~314-315: end_date < T 才可见;
    # INTERFACES pledge_stat by_stock keys end_date(无 ann_date);
    # 正式加载走 load_pledge_stat_panel(稀疏状态+stale),非本表统一路由
    # UNCERTAIN-REVIEW: 专用 loader 用 end_date+1ns 与 stale 窗,非标准 shift1/anndate
    "pledge_stat":   ("institutional/pledge_stat_all.parquet", "by_date_shift1", PLEDGE_STAT_RAW_FIELDS),
    # evidence: manifest mode=by_stock + store holder/top10_holders_all.parquet;
    # tushare top10_holders 含 ann_date(定期报告披露) → anndate ffill
    # UNCERTAIN-REVIEW: INTERFACES 未注册该接口,字段表按 tushare 公开文档默认研究列
    "top10_holders": ("holder/top10_holders_all.parquet", "anndate", TOP10_HOLDERS_FIELDS),
    # evidence: 任务书/公开语义 龙虎榜盘后公布→次日可用; INTERFACES top_list by_date 仅为下载键
    "top_list":      ("institutional/top_list_all.parquet", "by_date_shift1", TOP_LIST_FIELDS),
    # evidence: 同 top_list(龙虎榜机构明细,同一披露节奏)
    "top_inst":      ("institutional/top_inst_all.parquet", "by_date_shift1", TOP_INST_FIELDS),
    # evidence: INTERFACES repurchase mode=by_window date_param=ann_date → anndate
    "repurchase":    ("institutional/repurchase_all.parquet", "anndate", REPURCHASE_FIELDS),
}

# manifest 顶层键 → TUSHARE_DATASETS 声明表名(命名差异别名,不做重复声明)
MANIFEST_ALIASES: dict[str, str] = {
    "stk_holdernumber": "holdernumber",
    "suspend_d": "suspend",
    "stk_holdertrade": "holdertrade",
}

# core 数据集声明表: (store 或说明, 口径 mode, 字段列表, kind)
# kind ∈ {"panel", "metadata"}; metadata 的 mode 用 "n/a"
# 口径证据均指向 load_lake.py 实现(禁止凭感觉)
CORE_DATASETS = {
    # evidence: load_lake.load_prices ~17-54; close/amount 同 daily_basic 当日口径不 shift
    # (对照 load_daily_basic_panel ~193-194 "与 close/amount 同口径,不 shift")
    "price_daily": (
        "price/daily_all.parquet|price/daily/*.parquet",
        "by_date",
        ["open", "high", "low", "close", "volume", "amount"],
        "panel",
    ),
    # evidence: load_lake.load_raw_close ~95-131; core _manifest fields/path;
    # 不复权原始价,估值专用,当日价无 shift
    "price_daily_raw": (
        "price/daily_raw_all.parquet|price/daily_raw/*.parquet",
        "by_date",
        list(RAW_PRICE_FIELDS),
        "panel",
    ),
    # evidence: load_lake.load_fundamental_panel ~60-83: avail_date(公告日) ffill
    # (同 anndate 语义; ~80-81 "公告日生效,ffill 到交易日")
    "fundamental": (
        "fundamental_batch.parquet",
        "anndate",
        list(FUNDAMENTAL_FIELDS),
        "panel",
    ),
    # evidence: load_lake.load_capital_panel ~157-183: pivot 后统一 shift(1)
    # (~161-162 "T 日盘后发布,只允许从 T+1 起被策略看到")
    "capital_margin": (
        "capital/margin_all.parquet",
        "by_date_shift1",
        ["margin_balance", "margin_buy", "short_balance", "short_vol"],
        "panel",
    ),
    # evidence: 同上 load_capital_panel, northbound 与 margin 同一 shift(1) 路径
    "capital_northbound": (
        "capital/northbound_all.parquet",
        "by_date_shift1",
        [
            "northbound_hold_shares", "northbound_hold_value", "northbound_hold_pct",
            "northbound_value_chg_1d", "northbound_value_chg_5d", "northbound_value_chg_10d",
            "northbound_hold_shares_chg_1d", "northbound_buy_value_1d",
        ],
        "panel",
    ),
    # 元数据记录,不是可加载 date×code 面板
    "meta": ("meta/*", "n/a", [], "metadata"),
    "data_vintage": ("_manifest / vintage stamps", "n/a", [], "metadata"),
}

# 合法时间轴口径词表(core metadata 另允许 n/a)
TIMELINE_MODES = frozenset({"by_date", "by_date_shift1", "anndate"})
TIMELINE_MODES_WITH_NA = TIMELINE_MODES | frozenset({"n/a"})


def resolve_dataset_decl(name: str):
    """经 MANIFEST_ALIASES 归一后查 TUSHARE_DATASETS ∪ CORE_DATASETS。

    返回 (canonical_name, source, store, mode, fields, kind) 或 None。
    source ∈ {"tushare", "core"}; kind 对 tushare 恒为 "panel"。
    """
    canon = MANIFEST_ALIASES.get(name, name)
    if canon in TUSHARE_DATASETS:
        store, mode, fields = TUSHARE_DATASETS[canon]
        return (canon, "tushare", store, mode, list(fields), "panel")
    if canon in CORE_DATASETS:
        store, mode, fields, kind = CORE_DATASETS[canon]
        return (canon, "core", store, mode, list(fields), kind)
    return None


def dataset_contract(name: str) -> dict | None:
    """机器可读契约 dict;查无返回 None(供 semantics contract_missing)。"""
    hit = resolve_dataset_decl(name)
    if hit is None:
        return None
    _canon, _src, store, mode, fields, kind = hit
    return {
        "timeline": mode,
        "store": store,
        "fields": fields,
        "kind": kind,
        "declared_in": "lake/schema.py",
    }

# 沪深交易所两融明细字段统一
MARGIN_RENAME = {
    "标的证券代码": "code",
    "证券代码": "code",
    "融资余额": "margin_balance",
    "融资买入额": "margin_buy",
    "融券余量": "short_vol",
    "融券余额": "short_balance",
    "融券卖出量": "short_sell",
}

# 北向持股每日个股统计字段统一
NORTHBOUND_RENAME = {
    "持股日期": "date",
    "股票代码": "code",
    "股票简称": "name",
    "当日收盘价": "close",
    "当日涨跌幅": "pct_chg",
    "持股数量": "northbound_hold_shares",
    "持股市值": "northbound_hold_value",
    "持股数量占发行股百分比": "northbound_hold_pct",
    "持股数量占A股百分比": "northbound_hold_pct",
    "持股市值变化-1日": "northbound_value_chg_1d",
    "持股市值变化-5日": "northbound_value_chg_5d",
    "持股市值变化-10日": "northbound_value_chg_10d",
    "今日增持股数": "northbound_hold_shares_chg_1d",
    "今日增持资金": "northbound_buy_value_1d",
    "今日持股市值变化": "northbound_value_chg_1d",
}

# 北向单股历史字段统一 (stock_hsgt_individual_em)
NORTHBOUND_INDIVIDUAL_RENAME = {
    "持股日期": "date",
    "股票代码": "code",
    "股票简称": "name",
    "当日收盘价": "close",
    "当日涨跌幅": "pct_chg",
    "持股数量": "northbound_hold_shares",
    "持股市值": "northbound_hold_value",
    "持股数量占发行股百分比": "northbound_hold_pct",
    "持股数量占A股百分比": "northbound_hold_pct",
    "今日增持股数": "northbound_hold_shares_chg_1d",
    "今日增持资金": "northbound_buy_value_1d",
    "今日持股市值变化": "northbound_value_chg_1d",
}

# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------
META_SCHEMA = {
    "trade_calendar": ["date"],
    "codes": ["code", "name"],
    "st_history": ["code", "date", "st_flag"],
    "list_date": ["code", "list_date"],
}

# ---------------------------------------------------------------------------
# Data lake directory layout
# ---------------------------------------------------------------------------
LAKE_DIRS = {
    "price_daily": "data_lake/price/daily",
    "price_daily_raw": "data_lake/price/daily_raw",
    "price_weekly": "data_lake/price/weekly",
    "price_monthly": "data_lake/price/monthly",
    "fundamental": "data_lake/fundamental",
    "fundamental_batch": "data_lake/fundamental_batch.parquet",
    "capital_margin": "data_lake/capital/margin",
    "capital_northbound": "data_lake/capital/northbound",
    "capital_northbound_stock": "data_lake/capital/northbound_stock",
    "capital_margin_all": "data_lake/capital/margin_all.parquet",
    "capital_northbound_all": "data_lake/capital/northbound_all.parquet",
    "meta": "data_lake/meta",
}
