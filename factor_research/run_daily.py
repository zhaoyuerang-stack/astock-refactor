"""
每日生产入口 run_daily.py

流程：①增量更新数据 → ②质量校验 → ③生成择时信号 → ④持仓清单
      → ⑤调仓判断(距上次≥20交易日) → ⑥保存 signals/YYYY-MM-DD.json

策略: 由 deployments/production.json 声明的已验证 deployment legs 决定。
      国债 ETF 轮动只有在部署清单存在独立 role=defensive 腿时才会生成可执行指令;
      MA16 / regime 信号不得从已降级 alpha 主腿隐式继承为 defensive overlay。


用法：python3 run_daily.py            # 完整流程(含联网更新数据)
      python3 run_daily.py --no-update # 跳过数据更新，仅用现有数据出信号
"""
import warnings; warnings.filterwarnings("ignore")
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.chdir(Path(__file__).parent)
CHINA_TZ = ZoneInfo("Asia/Shanghai")
import pandas as pd

from app_config.settings import get_settings
from core.engine import PricePanel
from governance.holdout import current_data_fingerprint
from lake.load_lake import load_raw_close
from lake.validator import DataValidator
from runtime.deployment import (
    DeploymentNotReady,
    defensive_authorization,
    load_active_deployment,
    load_deployed_strategy_spec,
)
from runtime.production_readiness import get_production_readiness
from strategies.executable import build_executable_strategy, select_holdings
from strategies.small_cap import load_price_panels

_cfg = get_settings().strategy

SIGNALS = Path("signals"); SIGNALS.mkdir(exist_ok=True)
LAST_REBAL = SIGNALS / "_last_rebalance.txt"
STATE_FILE = SIGNALS / "state.json"

# illiquidity v1.0 参数
TOP_N = _cfg.top_n
TIMING_MA = _cfg.timing_ma
REBAL_DAYS = _cfg.rebalance_days
LEVERAGE = _cfg.leverage
START = get_settings().data.warmup_start


def load_state():
    default = {
        "current_position": "cash",
        "last_rebalance_date": None,
        "last_signal_date": None,
        "last_holdings": [],
    }
    if STATE_FILE.exists():
        return {**default, **json.loads(STATE_FILE.read_text())}
    if LAST_REBAL.exists():
        default["last_rebalance_date"] = LAST_REBAL.read_text().strip()
    return default


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    if state.get("last_rebalance_date"):
        LAST_REBAL.write_text(state["last_rebalance_date"])
    elif LAST_REBAL.exists():
        LAST_REBAL.unlink()


def _model_dict(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict()


def persist_signal_with_readiness(signal, new_state, readiness):
    readiness_payload = _model_dict(readiness)
    payload = dict(signal)
    payload["production_readiness"] = readiness_payload
    signal_date = signal["date"]

    if readiness_payload.get("allowed"):
        out = SIGNALS / f"{signal_date}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        save_state(new_state)
        return {
            "published": True,
            "signal_path": str(out),
            "readiness": readiness_payload,
        }

    draft_dir = SIGNALS / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    out = draft_dir / f"{signal_date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return {
        "published": False,
        "draft_path": str(out),
        "readiness": readiness_payload,
    }


def decide_action(trade_dates, last_date, in_market, state, *, rebalance_days=REBAL_DAYS):
    position = state.get("current_position", "cash")
    if not in_market:
        if position == "invested":
            return True, "择时转空，清仓", "清仓"
        return False, "当前空仓且择时为空，继续观望", "空仓观望"
    if position != "invested":
        return True, "择时转多，当前无持仓，建仓", "建仓买入"
    last_rebal_raw = state.get("last_rebalance_date")
    if not last_rebal_raw:
        return True, "持仓状态缺少上次调仓日，重新调仓", "调仓买入"
    last_rebal = pd.Timestamp(last_rebal_raw)
    gap = int(((trade_dates > last_rebal) & (trade_dates <= last_date)).sum())
    if gap >= rebalance_days:
        return True, f"距上次调仓 {gap} 交易日 (≥{rebalance_days})", "调仓买入"
    return False, f"距上次调仓 {gap} 交易日 (<{rebalance_days})，维持原仓位", "维持原仓位"


def build_rotation_payload(regime: str, defensive_auth: dict | None) -> dict:
    has_defensive = defensive_auth is not None
    payload = {
        "current_regime": regime,
        "recommend_stocks": regime == "bull",
        "recommend_bond": regime == "bear" and has_defensive,
        "bond_code": "511010" if has_defensive else "",
        "bond_name": "国债ETF" if has_defensive else "",
        "bond_allocation": "全部闲置资金" if has_defensive else "",
        "note": (
            "BEAR时全部现金买511010; BULL时卖光511010并按Band exposure买回股票"
            if has_defensive
            else "未授权独立 defensive overlay;债券轮动非现行可执行"
        ),
    }
    if defensive_auth is not None:
        payload["defensive_authorization"] = defensive_auth
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true")
    args = ap.parse_args()

    try:
        deployment = load_active_deployment()
        equity_leg = next(
            leg for leg in deployment.legs if leg.role == "equity_alpha"
        )
        spec = load_deployed_strategy_spec(equity_leg)
    except (DeploymentNotReady, StopIteration) as exc:
        print(f"❌ 生产部署未就绪，拒绝生成正式信号: {exc}")
        return 2

    print("=" * 60)
    print(
        f"  每日运行  {datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M')} CST"
        f"  —  {spec.family} {spec.version}"
    )
    print("=" * 60)

    # ① 增量更新数据
    if not args.no_update:
        print("\n[1/6] 增量更新价量数据...")
        try:
            from lake import update as update_lake
            update_lake.update_prices()
        except Exception as e:
            print(f"  ⚠️ 更新失败({str(e)[:50]})，用现有数据继续")
    else:
        print("\n[1/6] 跳过数据更新(--no-update)")

    # ② 加载 + 质量校验
    print("\n[2/6] 加载数据 + 质量校验...")
    warmup_start = spec.data.get("warmup_start", START)
    close, volume, amount = load_price_panels(warmup_start)
    raw_close = load_raw_close(start=warmup_start).reindex(
        index=close.index,
        columns=close.columns,
    )
    executable = build_executable_strategy(
        spec,
        PricePanel(
            close=close,
            volume=volume,
            amount=amount,
            raw_close=raw_close,
        ),
    )
    last = close.index[-1]
    cal = pd.read_parquet("data_lake/meta/trade_calendar.parquet")["date"]
    v = DataValidator(calendar=cal)
    sample = ["600519", "000001", "300750", "600036", "601398"]
    bad = [c for c in sample if c in close.columns
           and not v.validate(c, pd.read_parquet(f"data_lake/price/daily/{c}.parquet"))["ok"]]
    print(f"  最新交易日: {last.date()} | 抽样校验: {'✅全部通过' if not bad else f'⚠️{bad}异常'}")

    # ③ canonical spec 驱动的 factor / timing / policy
    print(f"\n[3/6] 生成策略信号 ({spec.family}/{spec.version})...")
    factor = executable.factor
    timing_dist = executable.diagnostics["timing"]["distance"]
    timing_raw = executable.diagnostics["timing"]["binary"]
    faded_st_cov = executable.diagnostics["veto_factor"]
    dist = float(timing_dist.loc[last]) if last in timing_dist.index else 0.0
    base_in_market = bool(timing_raw.loc[last])
    band_exposure = float(executable.timing.loc[last])
    band_in_market = band_exposure > 0

    # LIVE 决策 = Band timing (主), Binary 保留作 SHADOW 对比
    binary_in_market = bool(base_in_market)   # SHADOW (旧 LIVE)
    in_market = bool(band_in_market)          # 新 LIVE 主决策 = Band

    # Regime 检测: dist.shift(1) > 0 → bull, ≤ 0 → bear
    # (T日只用T-1日dist, 防未来函数)
    regime_dist = float(timing_dist.shift(1).loc[last]) if last in timing_dist.index else 0.0
    regime = "bull" if regime_dist > 0 else "bear"

    timing_ma = int(spec.timing.get("ma", TIMING_MA))
    print(f"  小盘指数 vs MA{timing_ma}: {dist:+.2%}")
    print(f"  Regime (shifted):         {'🟢 BULL' if regime == 'bull' else '🔴 BEAR'}")
    print(f"  Binary timing (SHADOW):    {'🟢持仓' if binary_in_market else '🔴空仓'}")
    print(f"  Band timing   (LIVE 主决策): exposure={band_exposure:.2f}x → "
          f"{'🟢持仓' if in_market else '🔴空仓观望'}")

    # 轮动信号
    defensive_auth = defensive_authorization(deployment)
    if regime == "bear" and defensive_auth is not None:
        print("  ⚠️ BEAR regime → 建议: 空仓资金配置 511010 国债ETF")
    elif regime == "bear":
        print("  ⛔ BEAR regime → 未授权独立 defensive overlay,不生成国债轮动指令")
    else:
        print("  ℹ️ BULL regime → 按 Band exposure 配置 illiq 股票")

    # ④ 持仓清单 —— 经 canonical select_holdings(与回测 apply_veto_filter 同源,禁手写复制)
    print("\n[4/6] 持仓清单...")
    active = close.loc[last].dropna().index
    f = factor.loc[last].reindex(active).dropna()

    if faded_st_cov is not None and last in faded_st_cov.index:
        veto_factor = faded_st_cov.loc[last].reindex(active).dropna()
    else:
        veto_factor = pd.Series(dtype=float)

    top_n = int(spec.selection["top_n"])
    rebalance_days = int(spec.selection["rebalance_days"])
    veto_q = float(executable.diagnostics.get("veto_q") or 0.0)
    holdings = select_holdings(f, veto_factor, top_n=top_n, veto_q=veto_q)
    if len(veto_factor) and veto_q > 0:
        print(f"  [Veto] apply_veto_filter(veto_q={veto_q:.2f}): 候选池 {len(active)} 只 → 选出 {len(holdings)} 只")

    # ⑤ 调仓判断
    print("\n[5/6] 调仓判断...")
    state = load_state()
    is_rebal, reason, action = decide_action(
        close.index,
        last,
        in_market,
        state,
        rebalance_days=rebalance_days,
    )
    print(f"  {'🔄 今日执行' if is_rebal else '⏸ 不执行'} — {reason}")

    # ⑥ 保存 signals
    print("\n[6/6] 保存信号...")
    # effective leverage = band_exposure (动态), 空仓时 0
    effective_leverage = round(band_exposure, 4) if in_market else 0.0
    signal = {
        "date": str(last.date()),
        "timing": "持仓" if in_market else "空仓",
        # ── LIVE 决策字段 (主) ──
        "in_market": in_market,                            # = band_in_market
        "band_exposure": round(band_exposure, 4),          # dynamic leverage 0~1.5
        "band_in_market": band_in_market,
        "timing_mode_live": "band",                        # 标识主决策来源
        "leverage": effective_leverage,                    # effective leverage = band_exposure
        # ── Regime + 轮动 (2026-06-08) ──
        "regime": regime,                                  # "bull" | "bear"
        "regime_dist": round(float(regime_dist), 4),       # shifted dist (防未来函数)
        "rotation": build_rotation_payload(regime, defensive_auth),
        # ── SHADOW: Binary timing (2026-06-07 Band 接替后保留对比) ──
        "binary_in_market_shadow": binary_in_market,
        "base_in_market": base_in_market,                  # binary 原始
        # ── 现有诊断字段 ──
        "small_index_vs_ma16": round(float(dist), 4),
        "is_execution_day": is_rebal,
        "is_rebalance_day": bool(is_rebal and in_market),
        "rebalance_reason": reason,
        "action": action,
        "holdings": holdings if in_market else [],
        "top_n": top_n,
        "strategy": spec.family,
        "strategy_version": spec.version,
        "family": spec.family,
        "version": spec.version,
        "spec_hash": spec.spec_hash,
        "deployment_id": deployment.deployment_id,
        "data_fingerprint": current_data_fingerprint(),
        # ── 向后兼容: 旧 shadow_band_* 字段保留 ──
        "shadow_band_exposure": round(band_exposure, 4),
        "shadow_band_in_market": band_in_market,
        "shadow_band_holdings": holdings if band_in_market else [],
        "candidates": holdings,
    }
    new_state = {
        "current_position": "invested" if in_market else "cash",
        "last_rebalance_date": (
            str(last.date()) if (is_rebal and in_market)
            else state.get("last_rebalance_date") if in_market
            else None
        ),
        "last_signal_date": str(last.date()),
        "last_holdings": holdings if in_market else [],
        "last_action": action,
    }
    readiness = get_production_readiness(data_date=str(last.date()))
    persist_result = persist_signal_with_readiness(signal, new_state, readiness)
    out = Path(persist_result.get("signal_path") or persist_result.get("draft_path"))

    # ── 终端总结 ──
    print("\n" + "=" * 60)
    print(f"  策略      : {spec.family} {spec.version} ({spec.spec_hash[:12]})")
    print(f"  日期      : {last.date()}")
    print(f"  Regime    : {'🟢 BULL' if regime == 'bull' else '🔴 BEAR'} (shifted)")
    print(f"  择时      : {signal['timing']}  (小盘指数{dist:+.2%} vs MA{TIMING_MA})")
    print(f"  执行      : {'是' if is_rebal else '否'} — {reason}")
    print(f"  操作      : {signal['action']}")
    if in_market:
        print(f"  持仓({len(holdings)}只): {', '.join(holdings[:12])}{' ...' if len(holdings)>12 else ''}")
    if regime == "bear" and defensive_auth is not None:
        print("  💡 建议   : 空仓资金配置 511010 国债ETF")
    if persist_result["published"]:
        print("  Readiness : 已放行")
        print(f"  已保存    : {out}")
    else:
        reasons = ", ".join(readiness.blocking_reasons) or "unknown"
        print(f"  Readiness : 未放行: {reasons}")
        print(f"  草稿      : {out}")
    print("=" * 60)

    # ⑦ 自动结算模拟盘 (T+1成交与净值更新)
    if not persist_result["published"]:
        print("\n[7/6] 跳过自动结算模拟盘...")
        print("  生产 readiness 未放行; draft 不触发模拟盘,正式信号保持旧版本。")
        return 2

    print("\n[7/6] 自动结算模拟盘...")
    try:
        import subprocess
        import sys
        proc = subprocess.run([sys.executable, "-m", "scripts.ops.paper_trade"], capture_output=True, text=True)
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️ 自动结算失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
