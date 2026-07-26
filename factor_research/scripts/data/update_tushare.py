"""Tushare 数据回填/增量 → 数据湖(registry 驱动,扎实可扩展)。

token 从环境变量 TUSHARE_TOKEN 读(不入库)。新增一个维度 = 往 INTERFACES 加一条声明,
不写新逻辑。三种抓取模式:
  by_date   按交易日批量(一次全市场一天):行情/估值类,~4000 次/全史
  by_period 按报告期批量(一次全市场一季):财务三表/指标,~64 次/全史(极省)
  by_stock  逐股(每股一次拿全史):分红等无批量接口的,~5500 次

增量:by_date 读现有最新 date 只补新日;by_period/by_stock 读已有 key 跳过。resumable:周期 flush。
限速铁律:单线程顺序(见 lake.sources.tushare),绝不并发(同 token 共享限速)。

用法:
    cd factor_research
    TUSHARE_TOKEN=xxx python3 scripts/data/update_tushare.py --interface daily_basic [--start 20100101]
    TUSHARE_TOKEN=xxx python3 scripts/data/update_tushare.py --interface fina_indicator
    TUSHARE_TOKEN=xxx python3 scripts/data/update_tushare.py --all
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import json  # noqa: E402

import pandas as pd  # noqa: E402

from lake.sources.tushare import call  # noqa: E402

LAKE = Path("data_lake")
TU_MANIFEST = LAKE / "tushare_manifest.json"


def _stamp_manifest(name, final, spec):
    """记录数据集 vintage(行数/末日/股数/落库时间),可追溯/查漂移。"""
    m = json.loads(TU_MANIFEST.read_text()) if TU_MANIFEST.exists() else {}
    date_col = next((c for c in ("trade_date", "end_date", "ann_date", "float_date")
                     if c in final.columns), None)
    m[name] = {
        "store": spec["store"], "mode": spec["mode"], "rows": int(len(final)),
        "stocks": int(final["ts_code"].nunique()) if "ts_code" in final.columns else None,
        "last": str(final[date_col].max()) if date_col else None,
        "stamped_at": pd.Timestamp.now().isoformat(timespec="seconds"),
    }
    TU_MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2))

# ── 接口注册表:维度声明,加一条即新增一个数据维度 ──
INTERFACES = {
    # 每日指标:市值/股本/换手/估值/股息 —— Barra Size/turnover/估值/Dividend
    "daily_basic": {
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "daily_basic/daily_basic_all.parquet",
        "fields": ("ts_code,trade_date,total_share,float_share,total_mv,circ_mv,"
                   "turnover_rate,turnover_rate_f,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm"),
    },
    # 复权因子:干净后复权(替代我们 volume×raw 的近似)
    "adj_factor": {
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "adj_factor/adj_factor_all.parquet",
        "fields": "ts_code,trade_date,adj_factor",
    },
    # 财务指标 200+:ROE/ROA/资产负债率/毛利净利率/周转/增长 —— Leverage/Quality/Growth
    "fina_indicator": {
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "financials/fina_indicator_all.parquet",
        "fields": ("ts_code,ann_date,end_date,roe,roe_waa,roa,debt_to_assets,assets_to_eqt,"
                   "netprofit_margin,grossprofit_margin,current_ratio,quick_ratio,"
                   "or_yoy,netprofit_yoy,assets_turn,ar_turn,ocfps,fcff,eps,bps,cfps"),
    },
    # 利润表
    "income": {
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "financials/income_all.parquet",
        "fields": ("ts_code,ann_date,end_date,revenue,oper_cost,operate_profit,total_profit,"
                   "n_income,n_income_attr_p,ebit,ebitda"),
    },
    # 资产负债表:债务/权益 → 真杠杆;应收/应付/存货 → 议价权 BPI / 现金循环周期 CCC
    "balancesheet": {
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "financials/balancesheet_all.parquet",
        "fields": ("ts_code,ann_date,end_date,total_assets,total_liab,total_hldr_eqy_exc_min_int,"
                   "total_cur_assets,total_cur_liab,money_cap,lt_borr,st_borr,"
                   "accounts_receiv,notes_receiv,inventories,acct_payable,notes_payable"),
    },
    # 现金流量表:FCF/现金质量
    "cashflow": {
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "financials/cashflow_all.parquet",
        "fields": ("ts_code,ann_date,end_date,n_cashflow_act,n_cashflow_inv_act,"
                   "n_cash_flows_fnc_act,c_pay_acq_const_fiolta,free_cashflow"),
    },
    # 分红送股:股息/除权
    "dividend": {
        "mode": "by_stock", "keys": ["ts_code", "end_date", "div_proc"],
        "store": "corp_action/dividend_all.parquet",
        "fields": "ts_code,end_date,ann_date,div_proc,stk_div,cash_div,cash_div_tax,record_date,ex_date",
    },
    # ── 事件/盈利:盈利惊喜/快报(by_stock)──
    "forecast": {  # 业绩预告
        "mode": "by_stock", "keys": ["ts_code", "ann_date", "end_date"],
        "store": "event/forecast_all.parquet",
        "fields": "ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max",
    },
    "express": {  # 业绩快报
        "mode": "by_stock", "keys": ["ts_code", "ann_date", "end_date"],
        "store": "event/express_all.parquet",
        "fields": "ts_code,ann_date,end_date,revenue,n_income,diluted_eps,diluted_roe,yoy_net_profit",
    },
    # ── 筹码/股东(by_stock)──
    "cyq_perf": {  # 筹码胜率/成本分布 → 支撑/获利盘
        "mode": "by_stock", "keys": ["ts_code", "trade_date"],
        "store": "cyq/cyq_perf_all.parquet",
        "fields": ("ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,"
                   "cost_85pct,cost_95pct,weight_avg,winner_rate"),
    },
    "stk_holdertrade": {  # 股东增减持 → 内部人信号
        "mode": "by_stock", "keys": ["ts_code", "ann_date", "holder_name"],
        "store": "holder/holdertrade_all.parquet",
        "fields": "ts_code,ann_date,holder_name,holder_type,in_de,change_vol,change_ratio,after_ratio,avg_price",
    },
    "stk_holdernumber": {  # 股东户数 → 筹码集中度
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "holder/holdernumber_all.parquet",
        "fields": "ts_code,ann_date,end_date,holder_num",
    },
    "share_float": {  # 限售解禁 → 供给压力
        "mode": "by_stock", "keys": ["ts_code", "float_date", "holder_name"],
        "store": "holder/share_float_all.parquet",
        "fields": "ts_code,ann_date,float_date,float_share,float_ratio,holder_name,share_type",
    },
    # ── 资金/市场结构(by_date,一次全市场一天)──
    "moneyflow": {  # 主力/大中小单资金流
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "moneyflow/moneyflow_all.parquet",
        "fields": ("ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,"
                   "buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"),
    },
    "stk_limit": {  # 每日涨跌停价(精确,替代推断)
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "market/stk_limit_all.parquet",
        "fields": "trade_date,ts_code,up_limit,down_limit",
    },
    "suspend_d": {  # 停复牌
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "market/suspend_all.parquet",
        "fields": "ts_code,trade_date,suspend_type",
    },
    "limit_list_d": {  # 涨跌停/连板统计 → 情绪
        "mode": "by_date", "date_param": "trade_date", "keys": ["ts_code", "trade_date"],
        "store": "market/limit_list_all.parquet",
        "fields": "trade_date,ts_code,limit,pct_chg,amount,float_mv,total_mv,turnover_ratio,fd_amount",
    },
    # ── 指数/行业 ──
    "index_classify": {  # 申万行业分类(一次)
        "mode": "once", "keys": ["index_code"],
        "store": "index/sw_classify.parquet",
        "fields": "index_code,industry_name,level,industry_code,parent_code,src",
        "params": {"src": "SW2021"},
    },
    "index_daily": {  # 基准指数日线(沪深300/中证500/1000/2000/上证50/创业板)
        "mode": "by_index", "keys": ["ts_code", "trade_date"],
        "store": "index/index_daily_all.parquet",
        "fields": "ts_code,trade_date,close,open,high,low,pct_chg,vol,amount",
        "index_codes": ["000300.SH", "000905.SH", "000852.SH", "932000.CSI",
                        "000016.SH", "399006.SZ", "000001.SH", "399001.SZ"],
    },
    # ── 机构/游资交易公示(by_date,一次全市场一天)── 零抓取空白区,2026-07-06 接入
    "top_list": {  # 龙虎榜:异常波动/换手触发的强制公示名单 → 游资/关注度代理
        "mode": "by_date", "date_param": "trade_date",
        # 同股同日可因触发多条不同上榜理由分别列示,reason 入键防止误删非重复行
        "keys": ["ts_code", "trade_date", "reason"],
        "store": "institutional/top_list_all.parquet",
        "fields": ("trade_date,ts_code,name,close,pct_change,turnover_rate,amount,"
                   "l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason"),
    },
    "top_inst": {  # 龙虎榜机构成交明细:同股同日可有多个营业部各买/卖各一行
        "mode": "by_date", "date_param": "trade_date",
        "keys": ["ts_code", "trade_date", "exalter", "side"],
        "store": "institutional/top_inst_all.parquet",
        "fields": "trade_date,ts_code,exalter,side,buy,buy_rate,sell,sell_rate,net_buy,reason",
    },
    "block_trade": {  # 大宗交易:无自然行 ID,同股同日同价可有多笔不同买卖方,全字段去重
        "mode": "by_date", "date_param": "trade_date",
        "keys": ["ts_code", "trade_date", "price", "vol", "buyer", "seller"],
        "store": "institutional/block_trade_all.parquet",
        "fields": "ts_code,trade_date,price,vol,amount,buyer,seller",
    },
    # 回购:ann_date 事件流,非按交易日全市场快照——用 start/end 窗口(补 by_date 没有的窗口模式)。
    # 实测:不带日期一次默认回2000条最新在前(20260706~20260423,~2.5月);带 start/end 窗口
    # 仍固定 2000 条硬顶——必须切小窗口(月度)防止早期历史被静默截断(同 report_rc 5000 行截断教训)。
    "repurchase": {
        "mode": "by_window", "date_param": "ann_date", "keys": ["ts_code", "ann_date"],
        "store": "institutional/repurchase_all.parquet",
        "fields": "ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit",
    },
    # 质押率:实测 end_date 单独查询返回空(非全市场快照接口),ts_code 单独查询返回该股全历史多期——
    # 走 by_stock(同 balancesheet 等 2000 积分档 vip 限制模式一致,逐股一次拿全史)。
    "pledge_stat": {
        "mode": "by_stock", "keys": ["ts_code", "end_date"],
        "store": "institutional/pledge_stat_all.parquet",
        "fields": "ts_code,end_date,pledge_count,unrest_pledge,rest_pledge,total_share,pledge_ratio",
    },
    # 注:cyq_chips(筹码分布,数十亿行)+ report_rc(研报海量)体量过大,需专门按日窗口/降采样,暂缓。
    # 注:cyq_perf 实测账号无该接口权限(40203),不接。
}


def _trade_dates(start, end):
    cal = pd.read_parquet(LAKE / "meta/trade_calendar.parquet")
    d = pd.to_datetime(cal["date"])
    d = d[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]
    return [x.strftime("%Y%m%d") for x in sorted(d)]


def _quarter_ends(start, end):
    return [d.strftime("%Y%m%d") for d in pd.date_range(start, end, freq="QE")]


def _month_windows(start, end):
    """按自然月切窗口(含首尾不完整月),用于 ann_date 事件流接口——覆盖全部日历日
    (公告可能落在非交易日),不用 _trade_dates(会漏周末/节假日公告)。"""
    months = pd.date_range(pd.Timestamp(start).to_period("M").to_timestamp(),
                            pd.Timestamp(end), freq="MS")
    out = []
    for ms in months:
        me = min(ms + pd.offsets.MonthEnd(0), pd.Timestamp(end))
        ws = max(ms, pd.Timestamp(start))
        out.append((ws.strftime("%Y%m%d"), me.strftime("%Y%m%d")))
    return out


def _all_codes():
    return list(pd.read_parquet(LAKE / "meta/codes.parquet")["code"].astype(str))


def _to_ts(code):
    return f"{code}.SH" if code.startswith(("6", "9")) else (
        f"{code}.BJ" if code.startswith(("4", "8")) else f"{code}.SZ")


def _load_existing(fp):
    return pd.read_parquet(fp) if fp.exists() else pd.DataFrame()


def _flush(fp, frames, keys):
    merged = pd.concat([f for f in frames if len(f)], ignore_index=True)
    merged = merged.drop_duplicates(keys, keep="last")
    merged.to_parquet(fp, index=False)
    return merged


def backfill(name, start="20100101", end=None, flush_every=200):
    spec = INTERFACES[name]
    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    fp = LAKE / spec["store"]
    fp.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing(fp)
    mode, fields, keys = spec["mode"], spec["fields"], spec["keys"]

    if mode == "by_date":
        done = set(existing[spec["date_param"]].astype(str)) if len(existing) else set()
        units = [d for d in _trade_dates(start, end) if d not in done]
        fetch = lambda u: call(name, {spec["date_param"]: u}, fields)
    elif mode == "by_period":
        done = set(existing["end_date"].astype(str)) if len(existing) else set()
        units = [p for p in _quarter_ends(start, end) if p not in done]
        fetch = lambda u: call(name, {"period": u}, fields)
    elif mode == "by_stock":
        done = set(existing["ts_code"]) if len(existing) else set()
        units = [_to_ts(c) for c in _all_codes() if _to_ts(c) not in done]
        fetch = lambda u: call(name, {"ts_code": u}, fields)
    elif mode == "by_index":
        done = set(existing["ts_code"]) if len(existing) else set()
        units = [c for c in spec["index_codes"] if c not in done]
        fetch = lambda u: call(name, {"ts_code": u}, fields)
    elif mode == "by_window":
        # 增量:已有数据的 date_param 最大值之前的月份窗口视为已补齐(该月内不会再有新公告落进旧月份)。
        done_max = str(existing[spec["date_param"]].astype(str).max()) if len(existing) else "00000000"
        all_windows = _month_windows(start, end)
        units = [(ws, we) for ws, we in all_windows if we >= done_max]
        done = set(all_windows) - set(units)
        fetch = lambda u: call(name, {"start_date": u[0], "end_date": u[1]}, fields)
    elif mode == "once":
        df = call(name, spec.get("params", {}), fields)
        df.to_parquet(fp, index=False)
        print(f"{name} [once] 完成: {len(df)} 行", flush=True)
        return {name: {"rows": int(len(df))}}
    else:
        raise ValueError(mode)

    print(f"{name} [{mode}]: 已有 {len(done)} 单元,待补 {len(units)} ({start}~{end})", flush=True)
    frames, buf = [existing] if len(existing) else [], []
    for i, u in enumerate(units):
        df = fetch(u)
        if len(df):
            buf.append(df)
        if (i + 1) % flush_every == 0 or i == len(units) - 1:
            if buf:
                merged = _flush(fp, frames + buf, keys)
                frames, buf = [merged], []
            n = len(frames[0]) if frames else 0
            print(f"  ...{i+1}/{len(units)} ({u}, 累计 {n} 行)", flush=True)

    final = frames[0] if frames else existing
    print(f"{name} 完成: {len(final)} 行, {final['ts_code'].nunique()} 股", flush=True)
    _stamp_manifest(name, final, spec)
    return {name: {"rows": int(len(final)), "stocks": int(final["ts_code"].nunique())}}


# ── 日频维度：每日增量更新（供 scheduled_daily_update 调用）──
# 排除季频财务维度（另由 update_fundamental 处理）和积分墙维度（cyq_perf/limit_list_d）。
_DAILY_INCREMENTAL = [
    "daily_basic",   # 市值/PE/PB/换手/估值 —— Barra Size/Value/Turnover
    "moneyflow",     # 主力/大中小单资金流 —— 情绪/资金面因子
    "stk_limit",     # 每日涨跌停价（精确）—— 回测/模拟盘约束
    "suspend_d",     # 停复牌状态 —— 停牌过滤
    "index_daily",   # 基准指数（沪深300/中证500等）—— Regime/基准计算
    "adj_factor",    # 复权因子 —— 后复权计算
]


def incremental_update(names=None, lookback_days=5, end=None):
    """日频维度快速增量更新（供 scheduled_daily_update 每日调用）。

    只补最近 lookback_days 自然日窗口内缺失的交易日，单次运行通常 <1 分钟。
    by_stock/by_period 维度跳过（不适合每日增量）。
    失败的维度记录错误但不中断其他维度的更新。

    Args:
        names: 维度列表，None 则用 _DAILY_INCREMENTAL 默认列表。
        lookback_days: 向前回溯的自然日数，默认 5（确保覆盖节假日）。
        end: 结束日期 YYYYMMDD，None 则为今天。

    Returns:
        dict: {name: {"ok": bool, "rows": int, "latest": str} | {"ok": False, "error": str}}
    """
    import traceback
    from datetime import datetime, timedelta

    names = names or _DAILY_INCREMENTAL
    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=lookback_days)).strftime("%Y%m%d")

    stats = {}
    for name in names:
        if name not in INTERFACES:
            stats[name] = {"ok": False, "error": f"未知维度: {name}"}
            continue
        spec = INTERFACES[name]
        mode = spec["mode"]
        if mode not in ("by_date", "by_index"):
            # by_stock/by_period/once 不适合每日增量
            stats[name] = {"ok": True, "skipped": True, "reason": f"mode={mode} 不做日增量"}
            continue
        try:
            fp = LAKE / spec["store"]
            fp.parent.mkdir(parents=True, exist_ok=True)
            existing = _load_existing(fp)
            keys = spec["keys"]
            fields = spec["fields"]

            if mode == "by_date":
                date_param = spec["date_param"]
                done = set(existing[date_param].astype(str)) if len(existing) else set()
                units = [d for d in _trade_dates(start, end) if d not in done]
                fetch = lambda u, name=name, date_param=date_param, spec=spec, fields=fields: \
                    call(name, {date_param: u, **spec.get("params", {})}, fields)
            elif mode == "by_index":
                # 指数日线：按 ts_code+trade_date 检查缺口
                done_pairs = set(
                    zip(existing["ts_code"].astype(str), existing["trade_date"].astype(str), strict=True)
                ) if len(existing) else set()
                new_dates = _trade_dates(start, end)
                units = [
                    (idx, d) for idx in spec["index_codes"] for d in new_dates
                    if (idx, d) not in done_pairs
                ]
                fetch = lambda u, name=name, fields=fields: call(
                    name, {"ts_code": u[0], "start_date": u[1], "end_date": u[1]}, fields
                )

            if not units:
                latest = str(existing[date_param if mode == "by_date" else "trade_date"].max()) \
                    if len(existing) else ""
                print(f"[tushare_inc] {name}: 已最新({latest})", flush=True)
                stats[name] = {"ok": True, "rows": len(existing), "latest": latest[:8], "new": 0}
                continue

            print(f"[tushare_inc] {name}: 补 {len(units)} 个单元 ({start}~{end})", flush=True)
            new_frames = []
            for u in units:
                df = fetch(u)
                if len(df):
                    new_frames.append(df)

            if new_frames:
                all_data = pd.concat([existing] + new_frames, ignore_index=True)
                all_data = all_data.drop_duplicates(keys, keep="last")
                all_data.to_parquet(fp, index=False)
                _stamp_manifest(name, all_data, spec)
                date_col = date_param if mode == "by_date" else "trade_date"
                latest = str(all_data[date_col].max())[:8]
                print(f"[tushare_inc] {name}: +{sum(len(f) for f in new_frames)} 行 → {latest}",
                      flush=True)
                stats[name] = {"ok": True, "rows": len(all_data), "latest": latest,
                               "new": sum(len(f) for f in new_frames)}
            else:
                latest = str(existing[date_param if mode == "by_date" else "trade_date"].max())[:8] \
                    if len(existing) else ""
                print(f"[tushare_inc] {name}: API 返回空（非交易日或数据源延迟），保留至 {latest}",
                      flush=True)
                stats[name] = {"ok": True, "rows": len(existing), "latest": latest, "new": 0}
        except Exception as exc:
            stats[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
            print(f"[tushare_inc] {name} ⚠ {stats[name]['error']}", flush=True)
            traceback.print_exc()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interface", choices=list(INTERFACES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--start", default="20100101")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    names = list(INTERFACES) if args.all else ([args.interface] if args.interface else [])
    if not names:
        ap.error("需 --interface <name> 或 --all")
    for n in names:  # 顺序执行(共享限速,绝不并发)
        backfill(n, args.start, args.end)


if __name__ == "__main__":
    main()
