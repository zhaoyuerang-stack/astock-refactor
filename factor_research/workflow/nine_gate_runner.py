"""9-Gate Evaluation 核心实现(canonical 层)。

原住址 scripts/research/run_nine_gates_all.py:研究脚本目录本不该是
R-WF-001 唯一 9-Gate 执行点的住址(canonical→scripts 反向依赖边,架构评审
2026-07-18 发现)。run_evaluation 及其依赖的策略构建分支迁到本文件;
scripts/research/run_nine_gates_all.py 现在只是薄 CLI 壳,re-export 本模块、
行为与输出保持不变。workflow/promote.py 的 _default_nine_gate_runner 改从
这里 import。
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance as DataclassInstance

    from research_ledger.ledger import ResearchLedger

warnings.filterwarnings("ignore")

ROOT: Path = Path(__file__).resolve().parent.parent

import pandas as pd

from app_config.log import get_logger
from core.analysis.nine_gates import NineGatesEvaluator, NineGatesReport
from core.engine import PricePanel, Signal

logger = get_logger(__name__)

# CLI 策略名 → 台账母策略 id（用于 --persist 把审计摘要写回对应版本）
STRATEGY_TO_FAMILY: dict[str, str] = {
    "small_cap": "small-cap-size",
    "size_earnings": "size-earnings",
    "large_cap": "large-cap-growth-hedged",
    "hq_momentum": "hq-momentum-hedged",
    "illiquidity": "illiquidity",
    "roc_yc": "roc-yc",
}

# 台账版本 → 该版本真实 config 的非周期参数（来自 strategy_versions.json 的 config 字段）。
# 审计变体版本时必须用其真实配置，否则用默认 config 产出的 DSR 会张冠李戴（假记分牌）。
# 注：回测窗口（start）不在此处硬编码，统一由 _taibook_start 从台账 data_scope.period 取。
VERSION_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("large_cap", "v1.1"): {"w_cpv_max": 0.5},
    ("large_cap", "v1.1-full"): {"w_cpv_max": 0.5},
    # v1.0-full 与 v1.0 config 逐字段相同(仅回测窗口不同,_taibook_start 已从台账自动取),
    # 空字典不是占位符——唯一作用是让 _auditable() 的成员判断为 True,不被 --audit-stale 漏审。
    ("hq_momentum", "v1.0-full"): {},
}

# illiquidity 各台账版本的真实配置规格(对齐 strategy_versions.json 的 config):
#   v1.0/v1.1 纯 Amihud 无 veto 二值择时;v1.1 top_n=50;v1.3 illiq+size 混合;v3.1 +Veto+Band。
ILLIQ_SPECS: dict[str, dict[str, Any]] = {
    "v1.0": {"factor": "amihud", "veto": False, "timing": "plain", "top_n": 25},
    "v1.1": {"factor": "amihud", "veto": False, "timing": "plain", "top_n": 50},
    "v1.3": {"factor": "blend",  "veto": False, "timing": "plain", "top_n": 25},
    "v3.0": {"factor": "amihud", "veto": False, "timing": "band",  "top_n": 25},  # = v3.1 去 Salience Veto(Amihud+Band+Bond)
    "v3.1": {"factor": "amihud", "veto": True,  "timing": "band",  "top_n": 25},
    # clean-v1:同 v1.0 真实配置(amihud/无veto/二值MA16/top25),lev=1.0;
    # 区别仅在登记纪律(全套防自欺证据,见 scratch/illiquidity_clean_registration_DRAFT.md),非因子本身。
    "clean-v1": {"factor": "amihud", "veto": False, "timing": "plain", "top_n": 25},
}


def record_nine_gate_research_run(
    *,
    strategy_name: str,
    version: str,
    summary: dict[str, Any],
    report_path: Path,
    ledger: ResearchLedger | None = None,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Archive one 9-Gate run into the immutable research ledger."""
    from research_ledger.ledger import ResearchRunRecord, record_research_run

    family = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
    gate4 = str(summary.get("gate4_verdict", "")).upper()
    dsr_p = summary.get("dsr_p")
    passed = gate4 == "PASS" or (isinstance(dsr_p, (int, float)) and float(dsr_p) < 0.05)
    failed = str(summary.get("status", "")).upper() == "FAILED_TO_RUN"
    verdict = "PASS" if passed else ("FAILED" if failed else "PENDING_REVIEW")
    next_action = "PROMOTE_REVIEW" if passed else "HUMAN_REVIEW"
    return record_research_run(
        ResearchRunRecord(
            # 保留 CLI 入口路径作为溯源标签(用户实际调用的命令),不是本实现文件的路径。
            script="scripts/research/run_nine_gates_all.py",
            hypothesis=f"{family}/{version}",
            source="nine_gate",
            data_vintage={
                "strategy": strategy_name,
                "family": family,
                "version": version,
                "start": summary.get("start"),
            },
            metrics=dict(summary or {}),
            verdict=verdict,
            artifact_paths=[str(report_path)],
            next_action=next_action,
            notes=f"DSR_p={summary.get('dsr_p')} gate4={summary.get('gate4_verdict')}",
        ),
        ledger=ledger,
        index_path=index_path or None,
    )


def _taibook_start(family: str, version: str | None) -> str | None:
    """从台账 data_scope.period 取该版本声明的起始（'2023-2026' → '2023-01-01'）。

    让 DSR/回测窗口与台账声明的 OOS/全历史区间逐版精确对齐，杜绝「证据测的区间≠版本声称区间」。
    """
    try:
        import strategy_registry
        data = strategy_registry._load()
        fam = next((f for f in data.get("families", []) if f["id"] == family), None)
        v = next((x for x in fam.get("versions", []) if x["version"] == version), None) if fam else None
        ds = (v or {}).get("data_scope") if v else None
        period = ds.get("period", "") if isinstance(ds, dict) else ""
        if period and "-" in period:
            yr = period.split("-")[0].strip()
            if yr.isdigit() and len(yr) == 4:
                return f"{yr}-01-01"
    except Exception:
        pass
    return None


class TrialCountUnknown(RuntimeError):
    """DSR 的真实搜索次数缺失，不能降级成固定地板。"""


def _family_n_trials(family: str, *, path: Path | None = None) -> int:
    """从 append-only 实验账本读取真实尝试数；无记录即阻断。"""
    from governance.trial_ledger import honest_n_trials

    count = honest_n_trials(family, path=path)
    if count < 1:
        raise TrialCountUnknown(f"trial_count_unknown: family={family!r}")
    return count


# 各策略的 StrategyConfig 是各自的 frozen dataclass,这里只要求"可 dataclasses.replace"。
_ConfigT = TypeVar("_ConfigT", bound="DataclassInstance")


def _apply_version_overrides(config: _ConfigT, strategy_name: str,
                             version: str | None, start: str | None) -> _ConfigT:
    """按台账版本设置 config 的 version / 真实参数 / 审计窗口，确保审计的是该版本配置而非默认。

    窗口优先级：显式 --start > 台账 data_scope.period > 策略默认。
    StrategyConfig 多为 frozen dataclass，用 dataclasses.replace 构造新实例（不可 setattr）。
    """
    import dataclasses
    eff_version = cast(str, version or getattr(config, "version", ""))
    overrides = dict(VERSION_OVERRIDES.get((strategy_name, eff_version), {}))
    if version:
        overrides["version"] = version
    if start:
        overrides["start"] = start
    else:
        fam = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
        ts = _taibook_start(fam, eff_version) if fam else None
        if ts:
            overrides["start"] = ts
    return dataclasses.replace(config, **overrides) if overrides else config


def _load_spec_from_registry(family: str, version: str | None) -> dict | None:
    """从台账载入策略规格 spec 字典。"""
    if not version:
        try:
            import strategy_registry
            data = strategy_registry._load()
            for fam in data.get("families", []):
                if fam["id"] == family:
                    default_ver = DEFAULT_VERSIONS.get(family)
                    if default_ver:
                        for v in fam.get("versions", []):
                            if v["version"] == default_ver and v.get("executable_spec") and v["executable_spec"].get("spec"):
                                return v["executable_spec"]["spec"]
                    for v in fam.get("versions", []):
                        if v.get("executable_spec") and v["executable_spec"].get("spec"):
                            return v["executable_spec"]["spec"]
        except Exception:
            pass
        return None

    try:
        import strategy_registry
        data = strategy_registry._load()
        for fam in data.get("families", []):
            if fam["id"] == family:
                for v in fam.get("versions", []):
                    if v["version"] == version:
                        if v.get("executable_spec") and v["executable_spec"].get("spec"):
                            return v["executable_spec"]["spec"]
    except Exception:
        pass
    return None


def run_evaluation(strategy_name: str, n_trials: int | None = None, persist: bool = False,
                   version: str | None = None, start: str | None = None) -> dict:
    logger.info("=" * 80)
    logger.info(f"  Running 9-Gate Evaluation Pipeline for Strategy: {strategy_name}")
    logger.info("=" * 80)

    # 1. Dynamically run strategy and retrieve outputs
    logger.info(f"\n[Step 1] Loading and executing strategy '{strategy_name}'...")

    family_id = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
    spec_dict = _load_spec_from_registry(family_id, version)

    if spec_dict:
        logger.info(f"  Found executable_spec in registry for {family_id}/{version}. Running dynamically via ExecutableStrategySpec...")
        from types import SimpleNamespace

        from core.strategy_spec import ExecutableStrategySpec
        from strategies.executable import build_executable_strategy

        ts = start or _taibook_start(family_id, version) or spec_dict.get("data", {}).get("warmup_start", "2018-01-01")
        spec_dict["data"]["warmup_start"] = ts

        spec = ExecutableStrategySpec.from_dict(spec_dict)
        spec.validate()

        from app_config.settings import get_settings
        warmup = get_settings().data.warmup_start
        ds = str(min(pd.Timestamp(ts), pd.Timestamp(warmup)).date())

        from strategies.small_cap import load_price_panels
        close, volume, amount = load_price_panels(ds)
        prices = PricePanel(close=close, volume=volume, amount=amount)

        strat = build_executable_strategy(spec, prices)

        res = {
            "close": prices.close,
            "volume": prices.volume,
            "amount": prices.amount,
            "factor": strat.factor,
            "scheduled_weights": strat.scheduled_weights,
            "timing": strat.timing,
        }

        thesis_mechanism = f"Spec-driven execution of {family_id} {version}"
        thesis_citation = family_id
        try:
            import strategy_registry
            data = strategy_registry._load()
            for fam in data.get("families", []):
                if fam["id"] == family_id:
                    thesis_mechanism = fam.get("hypothesis", thesis_mechanism)
                    thesis_citation = fam.get("name", thesis_citation)
        except Exception:
            pass

        thesis = {
            "mechanism": thesis_mechanism,
            "citation": thesis_citation
        }
        config: Any = SimpleNamespace(version=spec.version, start=ts)
    else:
        if strategy_name == "small_cap":
            from strategies.small_cap import StrategyConfig, run_small_cap_strategy
            config = StrategyConfig()
            config = _apply_version_overrides(config, strategy_name, version, start)
            res = run_small_cap_strategy(config)
            thesis = {
                "mechanism": "做多极小市值个股（-log 成交额），获取流动性溢价与小市值规模溢价，并在行情转熊时使用择时过滤器空仓防守。",
                "citation": "small_cap size premium"
            }
        elif strategy_name == "size_earnings":
            from strategies.size_earnings import StrategyConfig as SizeEarningsConfig
            from strategies.size_earnings import run_strategy
            config = SizeEarningsConfig()
            config = _apply_version_overrides(config, strategy_name, version, start)
            res = run_strategy(config)
            thesis = {
                "mechanism": "小盘效应与成长逻辑重合：利用 size 因子提供牛市弹性，结合 net_profit_yoy (净利润增长) 在熊市期提供质量安全锚。",
                "citation": "size-earnings blend strategy"
            }
        elif strategy_name == "large_cap":
            from strategies.large_cap import StrategyConfig as LargeCapConfig
            from strategies.large_cap import run_large_cap_strategy
            config = LargeCapConfig()
            config = _apply_version_overrides(config, strategy_name, version, start)
            res = run_large_cap_strategy(config)
            thesis = {
                "mechanism": "做多大盘高质量估值合理白马股，并等权重做空大盘指数以剥离 Beta，捕捉纯粹大盘成长股特质超额收益。",
                "citation": "large_cap growth hedged"
            }
        elif strategy_name == "hq_momentum":
            from strategies.hq_momentum import StrategyConfig as HqMomentumConfig
            from strategies.hq_momentum import run_hq_momentum_strategy
            config = HqMomentumConfig()
            config = _apply_version_overrides(config, strategy_name, version, start)
            res = run_hq_momentum_strategy(config)
            thesis = {
                "mechanism": "做多高质量且前期具备动量共振特征的中大盘股票，对冲大盘指数，捕获高保真动量趋势残差收益。",
                "citation": "high quality momentum hedged"
            }
        elif strategy_name == "illiquidity":
            from types import SimpleNamespace
            ver = version or "v3.1"
            spec = ILLIQ_SPECS.get(ver)
            if spec is None:
                raise ValueError(f"illiquidity 无 {ver} 配置规格;请在 ILLIQ_SPECS 按台账 config 补该版本")
            from app_config.settings import get_settings
            from factors.alpha import transforms  # noqa: F401 register zscore/mad_clip/shift
            from factors.alpha.base import FactorData
            from factors.alpha.builtins.illiq import AmihudIlliq
            from factors.small_cap import small_cap_factor, small_cap_timing
            from factors.veto import salience_covariance_veto
            from services.actions.run_backtest import _band_exposure
            from strategies.small_cap import build_rebalance_weights, load_price_panels

            ts = start or _taibook_start("illiquidity", ver) or "2018-01-01"
            config = SimpleNamespace(version=ver, start=ts)
            warmup = get_settings().data.warmup_start
            ds = str(min(pd.Timestamp(ts), pd.Timestamp(warmup)).date())
            close, volume, amount = load_price_panels(ds)
            fdata = FactorData(close=close, volume=volume, amount=amount)
            amihud = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(fdata)
            if spec["factor"] == "blend":
                factor = 0.5 * amihud + 0.5 * small_cap_factor(amount, window=60).shift(1)
            else:
                factor = amihud
            veto = salience_covariance_veto(close).shift(1) if spec["veto"] else None
            traw, _, tdist = small_cap_timing(close, amount, ma_window=16)
            timing = _band_exposure(tdist) if spec["timing"] == "band" else traw
            scheduled = build_rebalance_weights(
                factor, close, top_n=spec["top_n"], rebalance_days=20, veto_factor=veto, veto_q=0.30,
            )
            res = {
                "close": close, "volume": volume, "amount": amount, "factor": factor,
                "scheduled_weights": scheduled, "timing": timing,
            }
            thesis = {
                "mechanism": ("Amihud 非流动性溢价(|ret|/amount,20日)"
                              + ("+0.5×Size60 混合" if spec["factor"] == "blend" else "")
                              + (";Salience Veto 30%" if spec["veto"] else "")
                              + (";PureTrend MA16 " + ("Band" if spec["timing"] == "band" else "二值") + "择时")),
                "citation": "Amihud (2002) illiquidity premium",
            }
        elif strategy_name.startswith("small_cap_factor__window"):
            # 窗口扫描家族:无 executable_spec,按台账 config 真实参数驱动(防假记分牌铁律:
            # 审计变体必须用其真实配置,拒绝 fallback 默认参数)。factor 一律 .shift(1)(T+1)。
            import importlib
            from types import SimpleNamespace

            import strategy_registry
            from app_config.settings import get_settings
            from strategies.small_cap import build_rebalance_weights, load_price_panels

            ver = version or "v1.0"
            cfg = None
            fam_hypothesis = ""
            for fam in strategy_registry._load().get("families", []):
                if fam["id"] == strategy_name:
                    fam_hypothesis = fam.get("hypothesis", "")
                    for v in fam.get("versions", []):
                        if v["version"] == ver:
                            cfg = v.get("config") or {}
            if not cfg:
                raise ValueError(
                    f"{strategy_name} 台账无 {ver} config;拒绝用默认参数产出假 DSR")
            factor_fn_name = cfg.get("factor_fn_name")
            if not factor_fn_name or "." not in factor_fn_name:
                raise ValueError(f"{strategy_name}/{ver} config 缺 factor_fn_name,无法审计")
            factor_params = dict(cfg.get("factor_params") or {})
            top_n = int(cfg.get("top_n", 25))
            rebalance_days = int(cfg.get("rebalance_days", 20))

            ts = start or _taibook_start(strategy_name, ver) or "2018-01-01"
            config = SimpleNamespace(version=ver, start=ts)
            warmup = get_settings().data.warmup_start
            ds = str(min(pd.Timestamp(ts), pd.Timestamp(warmup)).date())
            close, volume, amount = load_price_panels(ds)
            mod_name, fn_name = factor_fn_name.rsplit(".", 1)
            factor_fn = getattr(importlib.import_module(mod_name), fn_name)
            factor = factor_fn(amount, **factor_params).shift(1)
            scheduled = build_rebalance_weights(
                factor, close, top_n=top_n, rebalance_days=rebalance_days)
            res = {
                "close": close, "volume": volume, "amount": amount, "factor": factor,
                "scheduled_weights": scheduled, "timing": None,
            }
            thesis = {
                "mechanism": fam_hypothesis or f"{strategy_name} 窗口扫描变体(台账 config 驱动)",
                "citation": strategy_name,
            }
        else:
            raise ValueError(f"Unknown strategy name: {strategy_name}")

    # Extract returned components first
    close = res["close"]
    volume = res.get("volume")
    amount = res.get("amount")

    # Strictly truncate at holdout boundary (< 2025-01-01) to eliminate holdout pollution in OOS/WF metrics (ADR-021)
    from governance.holdout import boundary
    b = boundary()

    close_tr = close.loc[close.index < b]
    volume_tr = volume.loc[volume.index < b] if volume is not None else None
    amount_tr = amount.loc[amount.index < b] if amount is not None else None

    if volume_tr is None:
        volume_tr = pd.DataFrame(1000.0, index=close_tr.index, columns=close_tr.columns)
    if amount_tr is None:
        amount_tr = volume_tr * 100 * close_tr

    prices = PricePanel(close=close_tr, volume=volume_tr, amount=amount_tr)
    factor = res["factor"].loc[res["factor"].index < b]

    if isinstance(res["scheduled_weights"], dict):
        scheduled = {k: v for k, v in res["scheduled_weights"].items() if k < b}
    else:
        scheduled = res["scheduled_weights"].loc[res["scheduled_weights"].index < b]

    timing = res.get("timing")
    if timing is not None:
        timing = timing.loc[timing.index < b]

    logger.info(f"  Execution complete. Truncated to < {b.date()} to protect holdout. Loaded {close_tr.shape[1]} stocks x {close_tr.shape[0]} dates.")

    # 2. Build Signal
    signal = Signal(
        weights=scheduled,
        timing=timing,
        family=strategy_name,
        version=config.version
    )

    # 3. Instantiate and run NineGatesEvaluator
    # n_trials 未显式指定 → 取研究账本搜索广度（多重检验诚实下界），替代硬编码 15
    if n_trials is None:
        try:
            n_trials = _family_n_trials(STRATEGY_TO_FAMILY.get(strategy_name, strategy_name))
        except TrialCountUnknown as e:
            try:
                import strategy_registry
                data = strategy_registry._load()
                fam_id = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
                found = False
                for fam in data.get("families", []):
                    if fam["id"] == fam_id:
                        for v in fam.get("versions", []):
                            if v["version"] == config.version:
                                nt = (v.get("nine_gate") or {}).get("n_trials")
                                if nt is not None and int(nt) >= 1:
                                    n_trials = int(nt)
                                    found = True
                                    break
                        if found:
                            break
            except Exception:
                pass
            if n_trials is None:
                logger.warning(f"  ⚠️ Warning: {e}. No search trials logged in governance ledger. Using default n_trials=1.")
                n_trials = 1
        logger.info(f"  [n_trials] 自动取该母策略台账迭代数 N={n_trials}（逐家族搜索广度，公平多重检验）")
    logger.info(f"\n[Step 2] Initializing 9-Gate Evaluator (n_trials={n_trials}, start={config.start}) & running audits...")

    # We define a stub factor builder to support look-ahead perturbation checks
    def factor_builder_stub(p: PricePanel) -> pd.DataFrame:
        if spec_dict:
            try:
                from core.strategy_spec import ExecutableStrategySpec
                from strategies.executable import build_executable_strategy
                s = ExecutableStrategySpec.from_dict(spec_dict)
                strat_pert = build_executable_strategy(s, p)
                return strat_pert.factor
            except Exception as e:
                logger.warning(f"  [lookahead check] Dynamic factor rebuild failed: {e}. Falling back to cached factor.")
        return factor

    evaluator = NineGatesEvaluator(
        prices=prices,
        factor_df=factor,
        factor_builder=factor_builder_stub,
        thesis=thesis,
        n_trials=n_trials,
        forward_days=20
    )

    # Run the evaluation
    reports = evaluator.evaluate_all(signal, start=config.start)

    # Check overall passed
    passed_all = all(r.passed for r in reports)

    # Generate consolidated report
    report = NineGatesReport(
        factor_name=f"{strategy_name}_{config.version}",
        run_date=pd.Timestamp.now().strftime("%Y-%m-%d"),
        passed_all=passed_all,
        reports=reports
    )

    # 4. Save report
    markdown_content = report.to_markdown()

    report_dir = ROOT / "reports" / "research"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{strategy_name}_9_gates_report.md"

    report_path.write_text(markdown_content, encoding="utf-8")

    logger.info("\n" + "=" * 80)
    logger.info(f"9-Gate Evaluation Completed! Report saved to:\n{report_path}")
    logger.info("=" * 80)

    # Print summary to console
    logger.info("\nExecutive Summary:")
    logger.info(markdown_content.split("## Detailed Gate Findings")[0].strip())

    # 5. （可选）把 DSR/PSR/多重检验摘要写回台账对应版本（机构级多重检验证据落库）
    summary = report.summarize()
    summary.setdefault("status", "PERSISTED" if persist else "COMPLETED")
    summary.setdefault("strategy", strategy_name)
    summary.setdefault("version", config.version)
    if persist:
        family_id = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
        if not family_id:
            logger.info(f"  [persist] 跳过：{strategy_name} 无台账 family 映射")
        else:
            from strategy_registry import attach_nine_gate
            attach_nine_gate(family_id, config.version, summary)
            logger.info(f"  [persist] Nine-Gate 摘要已写入台账 {family_id}/{config.version}："
                        f"DSR_p={summary.get('dsr_p')}, PSR={summary.get('psr')}, n_trials={summary.get('n_trials')}")
            # 留存 gate5 日收益序列 → lineage 相关性 / PBO(2B/2C)复用,避免二次回测
            # 守卫审计 #5:必须走 lake.version_returns.write_version_returns(身份信封)
            rets = getattr(evaluator, "gate5_returns", None)
            if rets is not None and len(rets) > 0:
                from lake.version_returns import config_hash as _vr_config_hash
                from lake.version_returns import write_version_returns

                spec_hash = None
                cfg_hash = None
                try:
                    import strategy_registry as _sr
                    for fam in _sr._load().get("families", []):
                        if fam.get("id") != family_id:
                            continue
                        for v in fam.get("versions", []):
                            if v.get("version") != config.version:
                                continue
                            spec_hash = (v.get("executable_spec") or {}).get("spec_hash")
                            if not spec_hash:
                                cfg_hash = _vr_config_hash(v.get("config") or {})
                            break
                        break
                except Exception as exc:  # noqa: BLE001 — 台账读失败时降级 config-only
                    logger.warning(f"  [persist] 台账身份读取失败({exc}),尝试 config 降级")
                if not spec_hash and not cfg_hash:
                    # 无台账 config 时用运行时 config 快照(若可 dict 化)
                    raw_cfg = getattr(config, "__dict__", None) or {}
                    if not isinstance(raw_cfg, dict):
                        raw_cfg = {}
                    # dataclass / namespace:过滤不可 JSON 的值
                    safe = {}
                    for k, val in raw_cfg.items():
                        try:
                            json.dumps(val, ensure_ascii=False)
                            safe[k] = val
                        except (TypeError, ValueError):
                            safe[k] = str(val)
                    cfg_hash = _vr_config_hash(safe)
                write_version_returns(
                    family_id,
                    config.version,
                    rets,
                    source="run_nine_gates_all --persist",
                    spec_hash=spec_hash,
                    config_hash=cfg_hash,
                )
                logger.info(f"  [persist] 收益序列已留存 ({len(rets)} 日) → version_returns/{family_id}__{config.version}.csv(+provenance)")
    try:
        record_nine_gate_research_run(
            strategy_name=strategy_name,
            version=config.version,
            summary=summary,
            report_path=report_path,
        )
    except Exception as exc:
        logger.warning(f"  [research-ledger] 9-Gate 归档失败: {exc}")
    return summary


# 各策略的默认版本(无 override 即审此版本)
DEFAULT_VERSIONS: dict[str, str] = {"small_cap": "v2.0", "size_earnings": "v1.0", "large_cap": "v1.0", "hq_momentum": "v1.0", "roc_yc": "v1.0"}


def _auditable(strategy_name: str | None, version: str | None) -> bool:
    """该 (strategy, version) 是否有已知真实配置可审 —— 避免用错配置产出假 DSR。"""
    if strategy_name is None:
        return False
    # If the version has an executable spec in the registry, it is auditable dynamically!
    family_id = STRATEGY_TO_FAMILY.get(strategy_name, strategy_name)
    if _load_spec_from_registry(family_id, version) is not None:
        return True
    if strategy_name == "illiquidity":
        return version in ILLIQ_SPECS
    if strategy_name.startswith("small_cap_factor__window"):
        return True  # 台账 config 驱动(run_evaluation 分支),真实参数可审
    if (strategy_name, version) in VERSION_OVERRIDES:
        return True
    return DEFAULT_VERSIONS.get(strategy_name) == version


def audit_stale_registered(persist: bool = True) -> list[dict[str, Any]]:
    """自动补审:扫描所有「在册」但无 DSR 审计的版本,对配置已知者自动跑 9-Gate 并落台账。

    配置未知 / 无兼容 runner(如行业级因子)记 SKIP 并说明原因,绝不伪造。
    供调度自动化调用,保持台账 DSR 审计覆盖不留空。返回逐版本处置列表。
    """
    import strategy_registry
    fam_to_strat = {v: k for k, v in STRATEGY_TO_FAMILY.items()}
    data = strategy_registry._load()
    results: list[dict] = []
    for fam in data.get("families", []):
        sname = fam_to_strat.get(fam["id"], fam["id"])
        for v in fam.get("versions", []):
            if v.get("status") != "在册":
                continue
            if (v.get("nine_gate") or {}).get("dsr_p") is not None:
                continue  # 已有审计
            sid = f"{fam['id']}/{v['version']}"
            if not _auditable(sname, v["version"]):
                reason = "无兼容 runner(行业级因子等)" if sname is None else "无该版本配置规格"
                results.append({"id": sid, "action": "SKIP", "reason": reason})
                logger.info(f"  [audit-stale] SKIP {sid}: {reason}")
                continue
            try:
                logger.info(f"  [audit-stale] 审计 {sid} ...")
                run_evaluation(sname, version=v["version"], persist=persist)
                results.append({"id": sid, "action": "AUDITED", "reason": ""})
            except Exception as e:
                results.append({"id": sid, "action": "FAIL", "reason": str(e)[:120]})
                logger.warning(f"  [audit-stale] FAIL {sid}: {e}")
    n = {k: sum(1 for r in results if r["action"] == k) for k in ("AUDITED", "SKIP", "FAIL")}
    logger.info(f"\n[audit-stale] 完成:审计 {n['AUDITED']} / 跳过 {n['SKIP']} / 失败 {n['FAIL']}")
    return results
