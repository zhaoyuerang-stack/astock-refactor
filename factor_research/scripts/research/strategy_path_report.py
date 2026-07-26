#!/usr/bin/env python3
"""策略路径分析报告 —— 年/月分解 + 规律摘要 + 自包含 HTML。

canonical 用法见 .claude/skills/strategy-path-analysis/SKILL.md。

只产路径事实与风险叙事,不裁决 alpha、不写台账(R-LLM-001 / R-WF-001)。
回测唯一权威 = core.engine.BacktestEngine;有 executable_spec 优先走
strategies.executable.build_executable_strategy(全包含 rotation)。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# =============================================================================
# 标准输出契约（冻结）—— 改字段必须 bump REPORT_SCHEMA_VERSION，并同步 SKILL.md
# =============================================================================
REPORT_SCHEMA_VERSION = "1.3.1"

# 总览 8 卡固定顺序（label 必须逐字一致，HTML/对话共用）
OVERVIEW_KPI_SPECS = (
    # key, label, value_path, hint_builder_name
    ("window", "样本窗", "full.start_end", "n_days"),
    ("annual", "年化", "full.annual", "annual_hint"),
    ("maxdd", "最大回撤", "full.maxdd", "maxdd_hint"),
    ("sharpe", "Sharpe", "full.sharpe", "vol_hint"),
    ("calmar", "Calmar", "full.calmar", "calmar_hint"),
    ("end_nav", "期末净值", "full.end_nav", "nav_hint"),
    ("hit", "hit 门槛", "full.hit", "hit_hint"),
    ("month_win_rate", "月胜率", "monthly.stats.win_rate", "month_win_hint"),
)

# HTML 章节固定顺序（h2 文案冻结）—— 1.2.0 起含经济逻辑章
HTML_SECTIONS = (
    "1. 总览 KPI",
    "2. 经济逻辑与假设",
    "3. 年度表现",
    "4. 月度收益矩阵",
    "5. 能找到的规律",
    "6. 如何规避",
    "7. 台账 9-Gate 快照对照（本页不重跑门禁）",
    "8. 口径与来源",
)

# 对话回复固定段（agent 必须按此顺序输出，不得省略标题）
CHAT_SECTIONS = (
    "1) 对象与口径",
    "2) 经济逻辑与假设",
    "3) 全样本 KPI + MaxDD 峰谷",
    "4) 年/月要点",
    "5) 规律（①–⑤）",
    "6) 规避（A–D）",
    "7) 产物路径",
    "8) 边界",
)

# JSON 顶层必填键
REQUIRED_TOP_KEYS = (
    "schema_version",
    "meta",
    "full",
    "overview",
    "economic_logic",
    "registry_audit",
    "yearly",
    "monthly",
    "drawdown",
    "losing_streaks",
    "timing",
    "patterns",
    "avoidance",
    "nine_gate_summary",
    "honesty",
)

# 台账 metrics vs 本次路径：超过阈值标 material_mismatch（披露，不覆盖路径数字）
REGISTRY_COMPARE_SPECS = (
    # key, abs_tol for material, kind: ratio|bool
    ("annual", 0.01, "ratio"),
    ("maxdd", 0.01, "ratio"),
    ("sharpe", 0.10, "level"),
    ("hit", 0.0, "bool"),
)

REGISTRY_AUDIT_DISCLAIMER = (
    "台账 metrics 可能来自不同样本窗/成本/数据版本/实现路径/旧次审计；"
    "本报告 KPI 只认本次收益序列重算（live re-run 优先）。"
    "偏差必须披露，禁止用台账数字覆盖或「对齐」路径数字。"
)

ALLOWED_RETURNS_PROVENANCE = frozenset({
    "live_rerun",       # 本会话引擎/canonical runner 重跑
    "stale_csv",        # 显式 --allow-stale-csv
    "caller_series",    # 测试/库调用方传入 Series
    "unknown",
})

REQUIRED_ECONOMIC_LOGIC_KEYS = (
    "disclaimer",
    "source",
    "family_name",
    "hypothesis",
    "regime",
    "decay_signal",
    "failure_boundaries",
    "style_betas",
    "capacity_m",
    "version_desc",
    "mechanism_map",
    "path_vs_thesis",
    "open_questions",
)

ECONOMIC_LOGIC_DISCLAIMER = (
    "本节整理台账声明的经济学假设，并与本路径事实做机械对照；"
    "不裁决假设真伪，不构成 alpha/入册证据（R-LLM-001 / R-WF-001）。"
)

REQUIRED_FULL_KEYS = (
    "n", "annual", "vol", "sharpe", "maxdd", "calmar", "end_nav",
    "start", "end", "hit",
)

REQUIRED_OVERVIEW_KEYS = ("kpis",)  # kpis: list[ {key,label,value,display,hint,css} ] len==8


# 禁词：出现在 one_liner / patterns / yearly.comment / HTML 叙事 → 整份报告作废
# （路径报告不得伪装成有效性/入册裁决）
# 禁词针对「肯定性宣称」。否定句/横幅里的「不得宣称…」不在扫描全文时用过宽子串。
FORBIDDEN_CLAIM_PATTERNS = (
    r"该策略有效",
    r"策略已有效",
    r"证明有效",
    r"alpha\s*有效",
    r"找到\s*alpha",
    r"可上线",
    r"可以上线",
    r"建议在册",
    r"应当在册",
    r"可以部署",
    r"建议部署",
    r"可生产",
    r"生产就绪",
    r"入册通过",
    r"通过入册",
    r"9-?\s*Gate\s*通过",
    r"九门通过",
    r"达标可投",
    r"神策略",
    r"保证收益",
    r"稳赚",
    r"can_claim_valid\s*=\s*true",
)

HONESTY_BANNER = (
    "本输出仅为路径描述（path description），不是有效性裁决；"
    "不得用于入册、部署，也不得作出任何有效性/可投结论（R-LLM-001 / R-WF-001）。"
)


def _scan_forbidden_claims(text: str, where: str) -> None:
    if not text:
        return
    for pat in FORBIDDEN_CLAIM_PATTERNS:
        if re.search(pat, text, flags=re.IGNORECASE):
            raise ValueError(
                f"反自欺禁词命中 where={where} pattern={pat!r} text={text[:120]!r}"
            )


def _returns_fingerprint(ret: "pd.Series") -> str:
    import hashlib
    r = ret.dropna().astype("float64")
    # 稳定指纹：长度 + 首尾日 + 校验和（防换收益序列却保留旧 metrics）
    payload = (
        f"{len(r)}|"
        f"{r.index[0] if len(r) else ''}|"
        f"{r.index[-1] if len(r) else ''}|"
        f"{float(r.sum()):.12g}|"
        f"{float(r.mean()):.12g}|"
        f"{float((1+r).prod()):.12g}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_metrics_match_returns(ret: "pd.Series", full: dict, *, tol: float = 1e-9) -> None:
    """full.* 必须可由同一收益序列重算得到——禁止手改年化等字段骗展示。"""
    recomputed = _metrics_from_returns(ret)
    keys = ("n", "annual", "vol", "sharpe", "maxdd", "end_nav")
    for k in keys:
        a, b = full.get(k), recomputed.get(k)
        if a is None or b is None:
            raise ValueError(f"metrics 完整性: full/{k} 缺失")
        if k == "n":
            if int(a) != int(b):
                raise ValueError(f"metrics 与收益序列不一致: n {a}!={b}")
            continue
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"metrics 非数值: {k}={a!r}/{b!r}") from exc
        if fa != fa or fb != fb:  # NaN
            if fa == fa or fb == fb:
                raise ValueError(f"metrics NaN 不一致: {k}")
            continue
        scale = max(1.0, abs(fb))
        if abs(fa - fb) > tol * scale and abs(fa - fb) > 1e-12:
            raise ValueError(
                f"metrics 与收益序列不一致: {k} full={fa!r} recompute={fb!r}"
            )
    # hit 必须与 annual/maxdd 公式一致
    try:
        from engine.metrics import compute_hit
        expect_hit = bool(compute_hit(float(full["annual"]), float(full["maxdd"])))
    except Exception:
        expect_hit = bool(float(full["annual"]) > 0.15 and abs(float(full["maxdd"])) < 0.20)
    if bool(full.get("hit")) != expect_hit:
        raise ValueError(
            f"hit 与 annual/maxdd 不一致: hit={full.get('hit')} expect={expect_hit}"
        )


def verify_kpi_display_matches_value(kpis: list) -> None:
    """display 必须是 value 的唯一标准格式化——禁止展示层美化数字。"""
    for i, row in enumerate(kpis):
        key = row.get("key")
        val, disp = row.get("value"), row.get("display")
        if key == "window":
            if not disp or "→" not in str(disp):
                raise ValueError(f"kpi[{i}] window display 非法: {disp!r}")
            continue
        if key == "annual":
            expect = format_pct(val, 2, signed=True)
        elif key == "maxdd":
            expect = format_pct(val, 2, signed=True)
        elif key == "sharpe":
            try:
                expect = f"{float(val):.2f}"
            except (TypeError, ValueError):
                expect = "·"
        elif key == "calmar":
            try:
                fv = float(val)
                expect = f"{fv:.2f}" if fv == fv else "·"
            except (TypeError, ValueError):
                expect = "·"
        elif key == "end_nav":
            try:
                expect = f"{float(val):.2f}×"
            except (TypeError, ValueError):
                expect = "·"
        elif key == "hit":
            expect = str(bool(val)) if val is not None else "·"
            # allow True/False from bool
            if str(disp) not in (expect, str(val)):
                # normalize
                if str(disp).lower() not in ("true", "false") or bool(val) != (str(disp) == "True"):
                    if str(disp) != expect:
                        raise ValueError(
                            f"kpi[{i}] hit display 与 value 不一致: {disp!r} vs {val!r}"
                        )
            continue
        elif key == "month_win_rate":
            try:
                expect = f"{float(val) * 100:.1f}%"
            except (TypeError, ValueError):
                expect = "·"
        else:
            raise ValueError(f"kpi[{i}] 未知 key={key!r}")
        if str(disp) != expect:
            raise ValueError(
                f"kpi[{i}] key={key} display 必须严格等于 format(value): "
                f"display={disp!r} expect={expect!r} value={val!r}"
            )


def detect_holdout_touch(ret: "pd.Series") -> dict:
    """收益序列是否触碰 holdout 金库（全样本路径默认会碰）。"""
    info = {
        "touches_holdout": False,
        "holdout_boundary": None,
        "n_holdout_days": 0,
        "holdout_note": "holdout 边界不可用",
    }
    try:
        from governance.holdout import boundary as holdout_boundary
        b = holdout_boundary()
        info["holdout_boundary"] = str(pd.Timestamp(b).date())
        if len(ret) == 0:
            return info
        n_h = int((ret.index >= pd.Timestamp(b)).sum())
        info["n_holdout_days"] = n_h
        info["touches_holdout"] = n_h > 0
        if n_h > 0:
            info["holdout_note"] = (
                f"收益序列含 holdout 段 date≥{info['holdout_boundary']} "
                f"共 {n_h} 日；本报告是全样本路径描述，不是 holdout 未偷看的晋级证据。"
            )
        else:
            info["holdout_note"] = (
                f"收益序列均 < holdout boundary {info['holdout_boundary']}。"
            )
    except Exception as exc:
        info["holdout_note"] = f"无法读取 holdout 边界: {exc}"
    return info


def _load_family_thesis(family_id: str | None) -> dict:
    """只读台账 family 层经济学字段；缺省返回空 dict，不编造。"""
    if not family_id:
        return {}
    try:
        import strategy_registry
        data = strategy_registry._load()
        fam = next((f for f in data.get("families") or [] if f.get("id") == family_id), None)
        if not fam:
            return {}
        return {
            "family_name": fam.get("name") or family_id,
            "hypothesis": fam.get("hypothesis") or "",
            "regime": fam.get("regime") or "",
            "decay_signal": fam.get("decay_signal") or "",
            "failure_boundaries": dict(fam.get("failure_boundaries") or {}),
            "style_betas": dict(fam.get("style_betas") or {}),
            "capacity_m": fam.get("capacity_m"),
            "family_status": fam.get("status"),
        }
    except Exception as exc:
        return {"_load_error": str(exc)}


def _load_version_desc(family_id: str | None, version: str | None) -> dict:
    if not family_id or not version:
        return {}
    try:
        import strategy_registry
        data = strategy_registry._load()
        fam = next((f for f in data.get("families") or [] if f.get("id") == family_id), None)
        if not fam:
            return {}
        ver = next((v for v in fam.get("versions") or [] if v.get("version") == version), None)
        if not ver:
            return {}
        return {
            "version_desc": ver.get("desc") or "",
            "version_notes": ver.get("notes") or "",
            "config": ver.get("config") or {},
            "version_status": ver.get("status"),
        }
    except Exception as exc:
        return {"_load_error": str(exc)}


def build_economic_logic(
    meta: dict,
    full: dict,
    drawdown: dict,
    yearly: list,
    timing: dict | None,
) -> dict:
    """台账经济学假设 + 实现映射 + 与路径事实的机械对照。

    禁止在此裁决「假设成立/策略有效」——只陈述声明与观测张力。
    """
    meta = meta or {}
    fam_id = meta.get("family")
    ver_id = meta.get("version")
    thesis = _load_family_thesis(fam_id)
    vdesc = _load_version_desc(fam_id, ver_id)

    # meta 可覆盖（调用方已注入时优先）
    hypothesis = meta.get("hypothesis") or thesis.get("hypothesis") or ""
    regime = meta.get("regime") or thesis.get("regime") or ""
    decay_signal = meta.get("decay_signal") or thesis.get("decay_signal") or ""
    failure_boundaries = dict(
        meta.get("failure_boundaries") or thesis.get("failure_boundaries") or {}
    )
    style_betas = dict(meta.get("style_betas") or thesis.get("style_betas") or {})
    capacity_m = meta.get("capacity_m")
    if capacity_m is None:
        capacity_m = thesis.get("capacity_m")
    family_name = meta.get("family_name") or thesis.get("family_name") or (fam_id or "·")
    version_desc = meta.get("version_desc") or vdesc.get("version_desc") or ""
    config = meta.get("config_snapshot") or meta.get("config") or vdesc.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    # —— 机制映射：声明 → 实现部件（模板，不编造未声明机制）——
    mechanism_map: list[dict] = []
    factor_impl = (
        config.get("factor")
        or meta.get("factor_type")
        or meta.get("factor")
        or "·"
    )
    mechanism_map.append({
        "layer": "alpha_source",
        "label": "alpha 来源（声明）",
        "stated": hypothesis or "（台账未填 hypothesis）",
        "implementation": (
            f"version={ver_id}; desc={version_desc or '·'}; "
            f"config.factor={factor_impl}"
        ),
    })
    mechanism_map.append({
        "layer": "regime_timing",
        "label": "适用市况 / 择时",
        "stated": regime or "（台账未填 regime）",
        "implementation": (
            f"timing_type={meta.get('timing_type') or config.get('timing') or '·'}; "
            f"rotation={meta.get('rotation') or config.get('rotation') or 'none'}"
        ),
    })
    mechanism_map.append({
        "layer": "risk_control",
        "label": "风险边界 / 仓位",
        "stated": (
            f"failure_boundaries={failure_boundaries or '·'}; "
            f"capacity_m={capacity_m if capacity_m is not None else '·'} 百万"
        ),
        "implementation": (
            f"leverage={meta.get('leverage') if meta.get('leverage') is not None else config.get('leverage', '·')}; "
            f"top_n={config.get('top_n') or config.get('top_n_stocks') or '·'}; "
            f"rebal={config.get('rebal_days') or config.get('rebalance_days') or '·'}"
        ),
    })
    mechanism_map.append({
        "layer": "decay_monitor",
        "label": "预期失效信号",
        "stated": decay_signal or "（台账未填 decay_signal）",
        "implementation": "路径报告只标监控线索，不替代 decay_monitor / 9-Gate",
    })
    if style_betas:
        mechanism_map.append({
            "layer": "style_exposure",
            "label": "声明风格暴露",
            "stated": ", ".join(f"{k}={v}" for k, v in style_betas.items()),
            "implementation": "本报告不做风格回归；仅披露台账 style_betas 供对照",
        })

    # —— 路径 vs 假设：机械对照 ——
    path_vs_thesis: list[dict] = []
    open_questions: list[str] = []

    if not hypothesis:
        open_questions.append(
            "台账 family.hypothesis 为空：无法对照「为什么该赚」；补登记后再复盘。"
        )
    else:
        path_vs_thesis.append({
            "id": "hypothesis_stated",
            "title": "经济学假设（声明原文）",
            "observation": hypothesis,
            "implication": (
                "路径 KPI 只能描述「发生了什么」，不能证明该机制是收益来源"
                "（混淆相关/因果 = 自欺）。"
            ),
        })

    if regime:
        path_vs_thesis.append({
            "id": "regime_stated",
            "title": "适用/不适用市况（声明）",
            "observation": regime,
            "implication": (
                "若弱年/深回撤发生在声明的适用市况内，是对机制的压力；"
                "若发生在声明不适用市况，优先检查 timing/空仓是否按设计执行，"
                "而非先改参数。"
            ),
        })

    # failure boundary maxdd
    fb_mdd = failure_boundaries.get("max_drawdown")
    obs_mdd = full.get("maxdd")
    if fb_mdd is not None and obs_mdd is not None:
        try:
            fb_f, obs_f = float(fb_mdd), float(obs_mdd)
            # both usually negative; breach if observed more severe (more negative)
            breached = obs_f < fb_f - 1e-12
            path_vs_thesis.append({
                "id": "failure_boundary_maxdd",
                "title": "失效边界 MaxDD",
                "observation": (
                    f"声明 max_drawdown={fb_f:.2%}；本路径 MaxDD={obs_f:.2%}"
                    f"（峰 {drawdown.get('peak')} → 谷 {drawdown.get('trough')}）"
                ),
                "implication": (
                    "已触及/跌破 family.failure_boundaries.max_drawdown；"
                    "按台账语义这是失效边界压力，不是调参借口。"
                    if breached else
                    "本路径 MaxDD 仍在声明失效边界之内（仅路径描述，不作入册依据）。"
                ),
                "status": "breached" if breached else "within",
            })
        except (TypeError, ValueError):
            open_questions.append("failure_boundaries.max_drawdown 无法解析为数值。")
    elif not failure_boundaries:
        open_questions.append("台账未填 failure_boundaries：缺少可机械对照的失效边界。")

    # yearly extremes vs "premium always on" naive reading
    if yearly:
        best = max(yearly, key=lambda r: float(r.get("ret") or float("-inf")))
        worst = min(yearly, key=lambda r: float(r.get("ret") or float("inf")))
        path_vs_thesis.append({
            "id": "year_skew",
            "title": "年度路径对「稳定溢价」叙事的压力",
            "observation": (
                f"最强年 {best.get('year')} {float(best.get('ret') or 0):+.1%}；"
                f"最弱年 {worst.get('year')} {float(worst.get('ret') or 0):+.1%}；"
                f"全样本年化 {float(full.get('annual') or 0):+.2%} / hit={full.get('hit')}"
            ),
            "implication": (
                "若经济学故事暗示「持续补偿」，而路径高度依赖少数爆发年、"
                "多年度接近打平或显著为负，则故事需要解释这些年份"
                "（regime 错配 / 因子拥挤 / 成本 / 运气），禁止用全样本均值掩盖。"
            ),
        })

    # timing decomp if available
    if timing and isinstance(timing, dict):
        on = timing.get("on") or {}
        off = timing.get("off") or {}
        if on or off:
            path_vs_thesis.append({
                "id": "timing_vs_factor",
                "title": "择时开/关段与「因子溢价」叙事",
                "observation": (
                    f"开启段: n={on.get('n')} ann≈{on.get('annual')}；"
                    f"关闭段: n={off.get('n')} ann≈{off.get('annual')}"
                ),
                "implication": (
                    "若收益与回撤几乎全集中在 timing 开启持股段，则路径证据更接近"
                    "「带择时的暴露」，不能单独当作纯截面因子溢价的证明。"
                ),
            })

    if decay_signal:
        path_vs_thesis.append({
            "id": "decay_signal_monitor",
            "title": "声明失效信号（监控清单）",
            "observation": decay_signal,
            "implication": (
                "路径 HTML 不计算滚动 IC；运营侧应按该清单接 decay_monitor，"
                "触发后标记研究/退役流程，而非在本报告内口头「仍有效」。"
            ),
        })

    if style_betas and float(style_betas.get("size") or 0) >= 0.5:
        path_vs_thesis.append({
            "id": "size_style_risk",
            "title": "高 size 暴露的经济含义",
            "observation": f"style_betas.size={style_betas.get('size')}",
            "implication": (
                "声明以小盘/流动性类暴露为主；路径深回撤若与小盘风格逆风同步，"
                "优先归因风格，其次才是「选股 alpha」——本报告不做风格回归，"
                "仅作假设层提示。"
            ),
        })

    # capacity
    if capacity_m is not None:
        try:
            cm = float(capacity_m)
            path_vs_thesis.append({
                "id": "capacity",
                "title": "容量声明",
                "observation": f"capacity_m≈{cm} 百万",
                "implication": (
                    "小盘/非流动性故事通常与容量硬约束绑定；"
                    "超容量后冲击会侵蚀声明的溢价机制。"
                ),
            })
        except (TypeError, ValueError):
            pass

    source = "strategy_registry.family+version"
    if thesis.get("_load_error") or vdesc.get("_load_error"):
        source = f"partial/error:{thesis.get('_load_error') or vdesc.get('_load_error')}"
    if not thesis and not meta.get("hypothesis"):
        source = "meta_only_or_missing"

    # 禁词扫一遍 narrative fields
    blob_parts = [hypothesis, regime, decay_signal, version_desc]
    for row in path_vs_thesis:
        blob_parts.append(str(row.get("observation") or ""))
        blob_parts.append(str(row.get("implication") or ""))
    for q in open_questions:
        blob_parts.append(q)
    for part in blob_parts:
        _scan_forbidden_claims(part, "economic_logic")

    return {
        "disclaimer": ECONOMIC_LOGIC_DISCLAIMER,
        "source": source,
        "family_name": family_name,
        "hypothesis": hypothesis,
        "regime": regime,
        "decay_signal": decay_signal,
        "failure_boundaries": failure_boundaries,
        "style_betas": style_betas,
        "capacity_m": capacity_m,
        "version_desc": version_desc,
        "mechanism_map": mechanism_map,
        "path_vs_thesis": path_vs_thesis,
        "open_questions": open_questions,
    }


def build_registry_audit(full: dict, meta: dict) -> dict:
    """本次路径 full.* vs 台账 metrics 机械 diff。

    不采用台账数字作 KPI；只披露偏差，避免「读台账当重跑」的静默误差。
    """
    rm = (meta or {}).get("registry_metrics") or {}
    if not isinstance(rm, dict):
        rm = {}
    rows = []
    n_mismatch = 0
    n_material = 0
    for key, tol, kind in REGISTRY_COMPARE_SPECS:
        pv, rv = full.get(key), rm.get(key)
        if rv is None and key not in rm:
            rows.append({
                "key": key,
                "path_value": pv,
                "registry_value": None,
                "delta": None,
                "status": "registry_missing",
                "material": False,
            })
            continue
        if kind == "bool":
            try:
                pb, rb = bool(pv), bool(rv)
            except (TypeError, ValueError):
                pb, rb = pv, rv
            match = pb == rb
            delta = None if match else f"{pb}!={rb}"
            material = not match
            status = "match" if match else "mismatch"
        else:
            try:
                pf, rf = float(pv), float(rv)
            except (TypeError, ValueError):
                rows.append({
                    "key": key,
                    "path_value": pv,
                    "registry_value": rv,
                    "delta": None,
                    "status": "unparseable",
                    "material": True,
                })
                n_mismatch += 1
                n_material += 1
                continue
            if pf != pf or rf != rf:  # NaN
                match = (pf != pf) and (rf != rf)
                delta = float("nan")
            else:
                delta = pf - rf
                match = abs(delta) <= 1e-12
            material = (not match) and (abs(delta) > float(tol) if delta == delta else True)
            status = "match" if match else "mismatch"
        if status == "mismatch":
            n_mismatch += 1
        if material:
            n_material += 1
        rows.append({
            "key": key,
            "path_value": pv,
            "registry_value": rv,
            "delta": delta,
            "status": status,
            "material": material,
        })

    # 额外：若台账有 annual 但窗口字段暗示不同口径，记一笔说明
    notes = []
    if not rm:
        notes.append("台账 metrics 为空：无法与首次审计数字对拍。")
    if n_material > 0:
        notes.append(
            f"与台账存在 {n_material} 项实质偏差（年化/回撤>|1pp| 或 Sharpe>|0.1| 或 hit 不一致）；"
            "优先怀疑：样本窗、成本、数据湖版本、实现路径(executable vs module)、"
            "或台账为旧次审计未刷新——不得把台账 KPI 抄进本报告。"
        )
    elif n_mismatch == 0 and rm:
        notes.append("与台账 canonical 键一致或偏差在阈值内（仍以本次路径为准）。")

    return {
        "disclaimer": REGISTRY_AUDIT_DISCLAIMER,
        "registry_source": "strategy_versions.json → version.metrics",
        "path_window": f"{full.get('start')} → {full.get('end')}",
        "path_n": full.get("n"),
        "rows": rows,
        "n_mismatch": n_mismatch,
        "n_material_mismatch": n_material,
        "has_material_mismatch": n_material > 0,
        "material_threshold": {
            "annual_abs": 0.01,
            "maxdd_abs": 0.01,
            "sharpe_abs": 0.10,
            "hit": "exact",
        },
        "notes": notes,
    }


def build_honesty_block(meta: dict, ret: "pd.Series", full: dict) -> dict:
    """反自欺元数据：强制写进 JSON/HTML，且 can_claim_valid 恒为 false。"""
    hold = detect_holdout_touch(ret)
    path = str((meta or {}).get("path") or "")
    path_kind = "unknown"
    if path.startswith("csv:"):
        path_kind = "csv_external"
    elif path == "executable_spec":
        path_kind = "executable_spec"
    elif "run_production" in path:
        path_kind = "production_helper"
    elif path.startswith("strategies."):
        path_kind = "module_runner"

    provenance = (meta or {}).get("returns_provenance")
    if provenance not in ALLOWED_RETURNS_PROVENANCE:
        if path_kind in ("executable_spec", "production_helper", "module_runner"):
            provenance = "live_rerun"
        elif path_kind == "csv_external":
            provenance = "stale_csv"
        else:
            provenance = "unknown"

    is_live = provenance == "live_rerun"
    csv_warning = ""
    if path_kind == "csv_external" or provenance == "stale_csv":
        csv_warning = (
            "收益来自外部/陈旧 CSV，非本会话引擎重跑；"
            "可能与首次审计、与当前 data_lake 不一致。"
            "默认禁用；仅 --allow-stale-csv 可生成，且不得当作可复现引擎证据。"
        )
    return {
        "can_claim_valid": False,  # 恒 false，不可改 true
        "not_alpha_evidence": True,
        "not_admission_evidence": True,
        "not_deployment_evidence": True,
        "path_kind": path_kind,
        "path": path,
        "returns_provenance": provenance,
        "is_live_rerun": is_live,
        "registry_metrics_not_used_as_kpi": True,  # 钉死：KPI 永不抄台账
        "leverage": (meta or {}).get("leverage"),
        "timing_type": (meta or {}).get("timing_type"),
        "rotation": (meta or {}).get("rotation"),
        "returns_sha256": _returns_fingerprint(ret),
        "metrics_verified_against_returns": True,
        "touches_holdout": hold["touches_holdout"],
        "holdout_boundary": hold["holdout_boundary"],
        "n_holdout_days": hold["n_holdout_days"],
        "holdout_note": hold["holdout_note"],
        "banner": HONESTY_BANNER,
        "csv_warning": csv_warning,
    }



def _is_number(x) -> bool:
    try:
        v = float(x)
        return v == v  # not NaN
    except (TypeError, ValueError):
        return False


def validate_analysis(analysis: dict) -> None:
    """fail-closed：缺键/KPI 数量不对/章节契约漂移 → 直接 raise，禁止写出残缺 HTML。"""
    missing = [k for k in REQUIRED_TOP_KEYS if k not in analysis]
    if missing:
        raise ValueError(f"path_analysis 缺顶层键: {missing}")
    if analysis.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version={analysis.get('schema_version')!r} "
            f"!= frozen {REPORT_SCHEMA_VERSION!r}"
        )
    full = analysis.get("full") or {}
    miss_f = [k for k in REQUIRED_FULL_KEYS if k not in full]
    if miss_f:
        raise ValueError(f"full 缺键: {miss_f}")
    ov = analysis.get("overview") or {}
    kpis = ov.get("kpis")
    if not isinstance(kpis, list) or len(kpis) != len(OVERVIEW_KPI_SPECS):
        raise ValueError(
            f"overview.kpis 必须长度 {len(OVERVIEW_KPI_SPECS)}，实际 "
            f"{None if kpis is None else len(kpis)}"
        )
    for i, (spec, row) in enumerate(zip(OVERVIEW_KPI_SPECS, kpis)):
        key, label = spec[0], spec[1]
        if row.get("key") != key or row.get("label") != label:
            raise ValueError(
                f"overview.kpis[{i}] 期望 key={key!r} label={label!r}，"
                f"得到 key={row.get('key')!r} label={row.get('label')!r}"
            )
        if "display" not in row or "hint" not in row:
            raise ValueError(f"overview.kpis[{i}] 缺 display/hint")
    if not isinstance(analysis.get("yearly"), list):
        raise ValueError("yearly 必须是 list")
    for y in analysis["yearly"]:
        for k in ("year", "ret", "ann", "vol", "sharpe", "maxdd", "n", "nav_ye", "comment"):
            if k not in y:
                raise ValueError(f"yearly 行缺 {k}: {y}")
    monthly = analysis.get("monthly") or {}
    for k in ("matrix", "year_total", "stats", "best10", "worst10"):
        if k not in monthly:
            raise ValueError(f"monthly 缺 {k}")
    for k in ("maxdd", "peak", "trough", "under_10pct_days", "under_20pct_days"):
        if k not in (analysis.get("drawdown") or {}):
            raise ValueError(f"drawdown 缺 {k}")
    av = analysis.get("avoidance") or []
    if len(av) != 4:
        raise ValueError(f"avoidance 必须 4 层(A–D)，实际 {len(av)}")
    # HTML 章节文案冻结（render 侧也引用 HTML_SECTIONS）
    if len(HTML_SECTIONS) != 8:
        raise ValueError("HTML_SECTIONS 必须 8 节（含经济逻辑）")

    # —— 经济逻辑块（1.2.0）——
    econ = analysis.get("economic_logic") or {}
    if not econ:
        raise ValueError("缺 economic_logic 块（经济学假设分析）")
    miss_e = [k for k in REQUIRED_ECONOMIC_LOGIC_KEYS if k not in econ]
    if miss_e:
        raise ValueError(f"economic_logic 缺键: {miss_e}")
    if not str(econ.get("disclaimer") or "").strip():
        raise ValueError("economic_logic.disclaimer 不可空")
    if not isinstance(econ.get("mechanism_map"), list) or len(econ["mechanism_map"]) < 1:
        raise ValueError("economic_logic.mechanism_map 至少 1 条")
    if not isinstance(econ.get("path_vs_thesis"), list):
        raise ValueError("economic_logic.path_vs_thesis 必须是 list")
    if not isinstance(econ.get("open_questions"), list):
        raise ValueError("economic_logic.open_questions 必须是 list")
    for i, row in enumerate(econ["mechanism_map"]):
        for k in ("layer", "label", "stated", "implementation"):
            if k not in row:
                raise ValueError(f"economic_logic.mechanism_map[{i}] 缺 {k}")
    for i, row in enumerate(econ["path_vs_thesis"]):
        for k in ("id", "title", "observation", "implication"):
            if k not in row:
                raise ValueError(f"economic_logic.path_vs_thesis[{i}] 缺 {k}")

    # —— 反自欺：display≡value、禁词、honesty 块 ——
    verify_kpi_display_matches_value(kpis)
    honesty = analysis.get("honesty") or {}
    if not honesty:
        raise ValueError("缺 honesty 块（反自欺元数据）")
    if honesty.get("can_claim_valid") is not False:
        raise ValueError("honesty.can_claim_valid 必须恒为 False（禁止宣称有效）")
    if honesty.get("not_alpha_evidence") is not True:
        raise ValueError("honesty.not_alpha_evidence 必须为 True")
    if honesty.get("not_admission_evidence") is not True:
        raise ValueError("honesty.not_admission_evidence 必须为 True")
    if honesty.get("metrics_verified_against_returns") is not True:
        raise ValueError("honesty.metrics_verified_against_returns 必须为 True")
    if not honesty.get("returns_sha256"):
        raise ValueError("honesty.returns_sha256 缺失")
    if not honesty.get("banner"):
        raise ValueError("honesty.banner 缺失")
    prov = honesty.get("returns_provenance")
    if prov not in ALLOWED_RETURNS_PROVENANCE:
        raise ValueError(f"honesty.returns_provenance 非法: {prov!r}")
    if honesty.get("registry_metrics_not_used_as_kpi") is not True:
        raise ValueError("honesty.registry_metrics_not_used_as_kpi 必须为 True")

    # —— registry_audit：必须披露与台账偏差，禁止静默 ——
    ra = analysis.get("registry_audit") or {}
    if not ra:
        raise ValueError("缺 registry_audit（与台账 metrics 对拍）")
    for k in (
        "disclaimer", "rows", "n_mismatch", "n_material_mismatch",
        "has_material_mismatch", "path_window",
    ):
        if k not in ra:
            raise ValueError(f"registry_audit 缺 {k}")
    if not isinstance(ra.get("rows"), list):
        raise ValueError("registry_audit.rows 必须是 list")
    # 一致性：has_material 与 rows 对齐
    material_n = sum(1 for r in ra["rows"] if r.get("material"))
    if int(ra.get("n_material_mismatch") or 0) != material_n:
        raise ValueError(
            f"registry_audit.n_material_mismatch={ra.get('n_material_mismatch')} "
            f"!= rows material count {material_n}"
        )
    if bool(ra.get("has_material_mismatch")) != (material_n > 0):
        raise ValueError("registry_audit.has_material_mismatch 与 rows 不一致")

    # 所有叙事字段扫禁词——禁止任何有效性/入册/部署肯定句
    _scan_forbidden_claims(str(ov.get("one_liner") or ""), "overview.one_liner")
    for p in analysis.get("patterns") or []:
        _scan_forbidden_claims(str(p.get("title") or ""), "patterns.title")
        _scan_forbidden_claims(str(p.get("body") or ""), "patterns.body")
    for y in analysis.get("yearly") or []:
        _scan_forbidden_claims(str(y.get("comment") or ""), "yearly.comment")
    for layer in analysis.get("avoidance") or []:
        if isinstance(layer, dict):
            for k, v in layer.items():
                if isinstance(v, str):
                    _scan_forbidden_claims(v, f"avoidance.{k}")
        else:
            _scan_forbidden_claims(str(layer), "avoidance")
    # economic_logic 叙事
    _scan_forbidden_claims(str(econ.get("hypothesis") or ""), "economic_logic.hypothesis")
    _scan_forbidden_claims(str(econ.get("disclaimer") or ""), "economic_logic.disclaimer")
    for row in econ.get("path_vs_thesis") or []:
        _scan_forbidden_claims(str(row.get("observation") or ""), "economic_logic.path_vs_thesis")
        _scan_forbidden_claims(str(row.get("implication") or ""), "economic_logic.path_vs_thesis")
    for q in econ.get("open_questions") or []:
        _scan_forbidden_claims(str(q), "economic_logic.open_questions")
    for sec_key in ("html_sections", "chat_sections"):
        for item in analysis.get(sec_key) or []:
            _scan_forbidden_claims(str(item), sec_key)

    # 若台账 nine_gate 未通过，禁止任何「通过」暗示出现在摘要
    ng = analysis.get("nine_gate_summary") or {}
    if ng.get("passed_all") is False:
        blob = json.dumps(analysis.get("overview"), ensure_ascii=False) + json.dumps(
            analysis.get("patterns"), ensure_ascii=False
        )
        if re.search(r"9-?\s*Gate\s*通过|九门通过|passed_all\s*=\s*true", blob, re.I):
            raise ValueError("nine_gate 未通过却出现通过暗示")


def format_pct(x, d=1, *, signed=True) -> str:
    """标准百分比展示：收益带符号且负号用 Unicode −；波动/占比 signed=False。"""
    if x is None:
        return "·"
    try:
        v = float(x)
        if v != v:
            return "·"
    except (TypeError, ValueError):
        return "·"
    body = f"{abs(v) * 100:.{d}f}%"
    if not signed:
        return body
    if v > 1e-12:
        return f"+{body}"
    if v < -1e-12:
        return f"−{body}"
    return f"0.{('0' * d)}%" if d else "0%"


def build_overview_kpis(full: dict, monthly_stats: dict, drawdown: dict) -> list[dict]:
    """唯一总览构造入口：顺序/label 钉死在 OVERVIEW_KPI_SPECS。"""
    try:
        calmar = abs(float(full["annual"]) / float(full["maxdd"])) if abs(float(full["maxdd"])) > 1e-12 else float("nan")
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        calmar = float("nan")
    full = dict(full)
    full["calmar"] = calmar

    n_m = monthly_stats.get("n")
    try:
        n_pos = int(round(float(monthly_stats.get("win_rate")) * float(n_m)))
    except (TypeError, ValueError):
        n_pos = None

    peak_trough = ""
    if drawdown.get("peak"):
        peak_trough = f"{drawdown.get('peak')} → {drawdown.get('trough')}"

    def _css_ret(x):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return "neu"
        if v > 1e-6:
            return "pos"
        if v < -1e-6:
            return "neg"
        return "neu"

    rows = []
    for key, label, _path, _hint in OVERVIEW_KPI_SPECS:
        if key == "window":
            display = f"{full.get('start') or '?'} → {full.get('end') or '?'}"
            hint = f"n = {full.get('n')} 交易日" if full.get("n") is not None else ""
            value = display
            css = "neu"
        elif key == "annual":
            value = full.get("annual")
            display = format_pct(value, 2)
            hint = "算术 · rf=0"
            css = _css_ret(value)
        elif key == "maxdd":
            value = full.get("maxdd")
            display = format_pct(value, 2)
            hint = peak_trough or "全样本峰值回撤"
            css = "neg"
        elif key == "sharpe":
            value = full.get("sharpe")
            try:
                display = f"{float(value):.2f}"
            except (TypeError, ValueError):
                display = "·"
            hint = f"vol ≈ {format_pct(full.get('vol'), 1, signed=False)}"
            css = "neu"
        elif key == "calmar":
            value = full.get("calmar")
            try:
                display = f"{float(value):.2f}" if value == value else "·"
            except (TypeError, ValueError):
                display = "·"
            hint = "年化 / |MaxDD|"
            css = "neu"
        elif key == "end_nav":
            value = full.get("end_nav")
            try:
                display = f"{float(value):.2f}×"
            except (TypeError, ValueError):
                display = "·"
            hint = "起点 = 1"
            css = "pos"
        elif key == "hit":
            value = full.get("hit")
            display = str(value)
            hint = "年化>15% 且 |MDD|<20%"
            css = "pos" if value else "neg"
        elif key == "month_win_rate":
            value = monthly_stats.get("win_rate")
            try:
                display = f"{float(value) * 100:.1f}%"
            except (TypeError, ValueError):
                display = "·"
            hint = f"{n_pos}/{n_m} 正月" if n_pos is not None and n_m else (f"{n_m} 个月" if n_m else "")
            css = "neu"
        else:
            raise ValueError(f"未知 KPI key={key}")
        rows.append({
            "key": key,
            "label": label,
            "value": value,
            "display": display,
            "hint": hint,
            "css": css,
        })
    return rows


def build_one_liner(yearly: list, monthly_stats: dict, drawdown: dict) -> str:
    """一句话机械模板（禁止 agent 自由改写核心句式）。"""
    parts = []
    if yearly:
        def _ret(r):
            try:
                return float(r.get("ret"))
            except (TypeError, ValueError):
                return None
        scored = [(r, _ret(r)) for r in yearly if _ret(r) is not None]
        if scored:
            best = max(scored, key=lambda t: t[1])[0]
            worst = min(scored, key=lambda t: t[1])[0]
            parts.append(
                f"最强年 {best.get('year')}（{format_pct(best.get('ret'), 1)}），"
                f"最弱年 {worst.get('year')}（{format_pct(worst.get('ret'), 1)}）。"
            )
    if drawdown.get("peak"):
        parts.append(
            f"真正伤净值的是 MaxDD {format_pct(drawdown.get('maxdd'), 2)} "
            f"（{drawdown.get('peak')} → {drawdown.get('trough')}）。"
        )
    try:
        wr = float(monthly_stats.get("win_rate"))
        parts.append(
            f"月胜率 {wr * 100:.1f}%，"
            f"{'靠右偏厚尾月份' if wr < 0.55 else '胜率尚可'}。"
        )
    except (TypeError, ValueError):
        pass
    parts.append("路径事实 ≠ alpha 裁决；入册以 9-Gate/workflow 为准。")
    return "".join(parts)


def year_comment(r: dict) -> str:
    """年度简评词表冻结（机械）。"""
    try:
        ret = float(r.get("ret") or 0.0)
        mdd = abs(float(r.get("maxdd") or 0.0))
    except (TypeError, ValueError):
        return "—"
    n = int(r.get("n") or 0)
    if n < 180:
        return "未满年 / YTD"
    if ret >= 0.35:
        return "爆发年" if mdd < 0.15 else "大年·深回撤"
    if ret >= 0.15:
        return "强年可控" if mdd < 0.12 else "好年·回撤仍深"
    if ret >= 0.05:
        return "中等 / 平庸"
    if ret >= -0.02:
        return "接近打平"
    if ret >= -0.10:
        return "弱年小亏"
    return "差年"



def _metrics_from_returns(ret: pd.Series) -> dict:
    r = ret.dropna().astype(float)
    if len(r) < 20:
        return {
            "n": int(len(r)), "annual": float("nan"), "vol": float("nan"),
            "sharpe": float("nan"), "maxdd": float("nan"), "calmar": float("nan"),
            "end_nav": float("nan"), "start": None, "end": None, "hit": False,
        }
    ann = float(r.mean() * 252)
    vol = float(r.std() * math.sqrt(252))
    sharpe = float(ann / vol) if vol > 0 else float("nan")
    cum = (1 + r).cumprod()
    maxdd = float((cum / cum.cummax() - 1).min())
    calmar = float(abs(ann / maxdd)) if abs(maxdd) > 1e-12 else float("nan")
    out = {
        "n": int(len(r)),
        "annual": ann,
        "vol": vol,
        "sharpe": sharpe,
        "maxdd": maxdd,
        "calmar": calmar,
        "end_nav": float(cum.iloc[-1]),
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
    }
    try:
        from engine.metrics import compute_hit
        out["hit"] = bool(compute_hit(ann, maxdd))
    except Exception:
        out["hit"] = bool(ann > 0.15 and abs(maxdd) < 0.20)
    return out


def _yearly_table(ret: pd.Series) -> list[dict]:
    rows = []
    cum_all = (1 + ret).cumprod()
    for y, g in ret.groupby(ret.index.year):
        g = g.astype(float)
        if len(g) < 5:
            continue
        ann = float(g.mean() * 252)
        vol = float(g.std() * math.sqrt(252))
        sh = float(ann / vol) if vol > 0 else float("nan")
        cg = (1 + g).cumprod()
        row = {
            "year": int(y),
            "ret": float(cg.iloc[-1] - 1),
            "ann": ann,
            "vol": vol,
            "sharpe": sh,
            "maxdd": float((cg / cg.cummax() - 1).min()),
            "n": int(len(g)),
            "nav_ye": float(cum_all[cum_all.index.year == y].iloc[-1]),
        }
        row["comment"] = year_comment(row)
        rows.append(row)
    return rows


def _monthly_matrix(ret: pd.Series) -> dict:
    m = ret.groupby([ret.index.year, ret.index.month]).apply(
        lambda x: float((1 + x.astype(float)).prod() - 1)
    )
    years = sorted({int(y) for y, _ in m.index})
    matrix = {}
    for y in years:
        row = []
        for mo in range(1, 13):
            row.append(float(m[(y, mo)]) if (y, mo) in m.index else None)
        matrix[str(y)] = row
    year_total = {}
    for y in years:
        sub = [m[(y, mo)] for mo in range(1, 13) if (y, mo) in m.index]
        year_total[str(y)] = float(np.prod([1 + x for x in sub]) - 1) if sub else None
    vals = m.astype(float)
    return {
        "matrix": matrix,
        "year_total": year_total,
        "stats": {
            "n": int(len(vals)),
            "mean": float(vals.mean()),
            "median": float(vals.median()),
            "std": float(vals.std()),
            "win_rate": float((vals > 0).mean()),
            "best": {"ym": f"{vals.idxmax()[0]}-{vals.idxmax()[1]:02d}", "ret": float(vals.max())},
            "worst": {"ym": f"{vals.idxmin()[0]}-{vals.idxmin()[1]:02d}", "ret": float(vals.min())},
            "n_gt_10pct": int((vals > 0.10).sum()),
            "n_lt_m5pct": int((vals < -0.05).sum()),
        },
        "best10": [
            {"ym": f"{y}-{mo:02d}", "ret": float(r)}
            for (y, mo), r in vals.nlargest(10).items()
        ],
        "worst10": [
            {"ym": f"{y}-{mo:02d}", "ret": float(r)}
            for (y, mo), r in vals.nsmallest(10).items()
        ],
    }


def _drawdown_path(ret: pd.Series) -> dict:
    cum = (1 + ret.astype(float)).cumprod()
    dd = cum / cum.cummax() - 1
    trough = dd.idxmin()
    peak = cum.loc[:trough].idxmax()
    return {
        "maxdd": float(dd.min()),
        "peak": str(peak.date()),
        "trough": str(trough.date()),
        "under_10pct_days": float((dd < -0.10).mean()),
        "under_20pct_days": float((dd < -0.20).mean()),
    }


def _longest_losing_months(ret: pd.Series) -> list[dict]:
    m = ret.groupby([ret.index.year, ret.index.month]).apply(
        lambda x: float((1 + x.astype(float)).prod() - 1)
    )
    keys = list(m.index)
    signs = (m < 0).astype(int).values
    streaks = []
    cur = 0
    start_i = 0
    for i, v in enumerate(signs):
        if v:
            if cur == 0:
                start_i = i
            cur += 1
        else:
            if cur >= 2:
                a, b = keys[start_i], keys[i - 1]
                streaks.append({
                    "n": cur,
                    "from": f"{a[0]}-{a[1]:02d}",
                    "to": f"{b[0]}-{b[1]:02d}",
                })
            cur = 0
    if cur >= 2:
        a, b = keys[start_i], keys[-1]
        streaks.append({
            "n": cur,
            "from": f"{a[0]}-{a[1]:02d}",
            "to": f"{b[0]}-{b[1]:02d}",
        })
    streaks.sort(key=lambda x: -x["n"])
    return streaks[:8]


def _timing_decomp(ret: pd.Series, exposure: pd.Series | None = None) -> dict | None:
    """用策略真实 timing 暴露拆持股/空仓日。

    优先传入 build_executable_strategy 的 signal.timing;
    若无则回退 PureTrend Band 代理(仅作对照,报告须标明)。
    """
    source = "signal.timing"
    if exposure is None:
        source = "band_proxy_fallback"
        try:
            from factors.small_cap import small_cap_timing
            from strategies.small_cap import load_price_panels
        except Exception:
            return None
        try:
            close, volume, amount = load_price_panels("2010-01-01")
            _, _, dist = small_cap_timing(close, amount, ma_window=16)
            dc = dist.shift(1).clip(-0.5, 0.5)
            exposure = ((1.0 + dc * 8.0).clip(0.0, 1.5) * (dc > 0)).fillna(0.0)
        except Exception as exc:
            return {"error": str(exc), "source": source}

    exposure = exposure.reindex(ret.index).ffill().fillna(0.0)
    on = exposure > 0
    off = ~on
    r_on, r_off = ret[on], ret[off]

    def _ann(x: pd.Series) -> dict:
        if len(x) < 20:
            return {"n": int(len(x))}
        ann = float(x.mean() * 252)
        vol = float(x.std() * math.sqrt(252))
        return {
            "n": int(len(x)),
            "share_days": float(len(x) / max(len(ret), 1)),
            "annual": ann,
            "vol": vol,
            "sharpe": float(ann / vol) if vol > 0 else float("nan"),
            "mean_day": float(x.mean()),
        }

    m_ret = ret.groupby(pd.Grouper(freq="ME")).apply(
        lambda x: float((1 + x.astype(float)).prod() - 1)
    )
    m_exp = exposure.groupby(pd.Grouper(freq="ME")).mean()
    common = m_ret.index.intersection(m_exp.index)
    corr = float(m_ret.loc[common].corr(m_exp.loc[common])) if len(common) > 3 else float("nan")

    by_year = []
    for y, g in ret.groupby(ret.index.year):
        ey = exposure.reindex(g.index)
        ony = ey > 0
        by_year.append({
            "year": int(y),
            "on_share": float(ony.mean()),
            "year_ret": float((1 + g.astype(float)).prod() - 1),
            "sum_on": float(g[ony].sum()),
            "sum_off": float(g[~ony].sum()),
            "avg_exp": float(ey.mean()),
        })

    return {
        "source": source,
        "on": _ann(r_on),
        "off": _ann(r_off),
        "month_ret_vs_exp_corr": corr,
        "by_year": by_year,
    }


def load_returns_from_csv(path: Path) -> pd.Series:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    s = df.iloc[:, 0].astype(float).sort_index()
    s.name = "ret"
    return s


def run_strategy_returns(
    family: str,
    version: str,
    start: str = "2018-01-01",
) -> tuple[pd.Series, dict]:
    """有 executable_spec 则全包 spec 路径;illiquidity/v3.1 无 spec 时回退生产路径。"""
    import strategy_registry
    from app_config.settings import get_settings
    from core.engine import BacktestConfig, BacktestEngine, CostModel, PricePanel
    from core.strategy_spec import ExecutableStrategySpec
    from strategies.executable import build_executable_strategy
    from strategies.small_cap import load_price_panels

    data = strategy_registry._load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"family not found: {family}")
    ver = next((v for v in fam.get("versions", []) if v.get("version") == version), None)
    if ver is None:
        raise ValueError(f"version not found: {family}/{version}")

    meta = {
        "family": family,
        "version": version,
        "status": ver.get("status"),
        "registry_metrics": ver.get("metrics"),
        "nine_gate": {
            k: (ver.get("nine_gate") or {}).get(k)
            for k in ("passed_all", "dsr_p", "run_date", "gate4_verdict", "cost_decay_rate")
        },
        "production_blocked": (ver.get("evidence") or {}).get("production_blocked"),
        # family 层经济学字段（只读台账，供 economic_logic）
        "family_name": fam.get("name") or family,
        "hypothesis": fam.get("hypothesis") or "",
        "regime": fam.get("regime") or "",
        "decay_signal": fam.get("decay_signal") or "",
        "failure_boundaries": dict(fam.get("failure_boundaries") or {}),
        "style_betas": dict(fam.get("style_betas") or {}),
        "capacity_m": fam.get("capacity_m"),
        "version_desc": ver.get("desc") or "",
        "config": ver.get("config") or {},
    }

    es = ver.get("executable_spec") or {}
    spec_dict = es.get("spec")
    timing_series = None
    used_executable = False
    if spec_dict:
        try:
            warmup = get_settings().data.warmup_start
            ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
            close, volume, amount = load_price_panels(ds)
            prices = PricePanel(close=close, volume=volume, amount=amount)
            spec = ExecutableStrategySpec.from_dict(spec_dict)
            spec.validate()
            strat = build_executable_strategy(spec, prices)
            # 台账 config.leverage 若存在则用(如 small-cap v2.0=1.25);否则 1.0
            lev = 1.0
            try:
                lev = float((ver.get("config") or {}).get("leverage") or 1.0)
            except (TypeError, ValueError):
                lev = 1.0
            cfg = BacktestConfig(
                start=start,
                cost=CostModel(),
                leverage=lev,
            )
            result = BacktestEngine(prices=prices, config=cfg).run(strat.signal)
            timing_series = strat.timing
            meta["path"] = "executable_spec"
            meta["returns_provenance"] = "live_rerun"
            meta["spec_hash"] = spec.spec_hash
            meta["rotation"] = dict(spec.rotation or {})
            meta["leverage"] = lev
            meta["timing_type"] = (spec.timing or {}).get("type")
            meta["universe"] = dict(spec.universe or {})
            used_executable = True
        except Exception as exc:
            # 部分 autoresearch 台账 executable_spec 引用未注册 factor type
            # → 回退 AST/config 路径（若该 family 支持）
            from strategies.ast_config_strategy import is_dsl_family
            if not is_dsl_family(family):
                raise
            meta["executable_spec_fallback"] = f"{type(exc).__name__}: {exc}"[:300]
    if used_executable:
        pass  # result already set
    elif family == "illiquidity":
        # 多版本：优先 executable_spec（上面已处理）；其余走 ILLIQ_SPECS + BacktestEngine
        from types import SimpleNamespace

        from app_config.settings import get_settings
        from factors.alpha import transforms  # noqa: F401
        from factors.alpha.base import FactorData
        from factors.alpha.builtins.illiq import AmihudIlliq
        from factors.small_cap import small_cap_factor, small_cap_timing
        from factors.veto import salience_covariance_veto
        from strategies.small_cap import build_rebalance_weights
        from workflow.nine_gate_runner import ILLIQ_SPECS

        def _band_exposure(timing_dist: pd.Series) -> pd.Series:
            """PureTrend Band 暴露 0~1.5x，滞后一日（与 run_backtest 同口径，避免 import 重链）。"""
            dist = timing_dist.shift(1).clip(lower=-0.5, upper=0.5)
            return ((1.0 + dist * 8.0).clip(lower=0.0, upper=1.5) * (dist > 0)).fillna(0.0)

        ispec = ILLIQ_SPECS.get(version)
        if ispec is None:
            raise ValueError(
                f"illiquidity/{version} 无 ILLIQ_SPECS 规格，无法 live re-run"
            )
        cfg0 = ver.get("config") or {}
        try:
            lev = float(cfg0.get("leverage") or 1.0)
        except (TypeError, ValueError):
            lev = 1.0
        try:
            top_n = int(cfg0.get("top_n") or ispec["top_n"])
        except (TypeError, ValueError):
            top_n = int(ispec["top_n"])
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20

        warmup = get_settings().data.warmup_start
        ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
        close, volume, amount = load_price_panels(ds)
        fdata = FactorData(close=close, volume=volume, amount=amount)
        amihud = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(fdata)
        if ispec["factor"] == "blend":
            factor = 0.5 * amihud + 0.5 * small_cap_factor(amount, window=60).shift(1)
        else:
            factor = amihud
        veto = salience_covariance_veto(close).shift(1) if ispec["veto"] else None
        traw, _, tdist = small_cap_timing(close, amount, ma_window=16)
        timing = _band_exposure(tdist) if ispec["timing"] == "band" else traw.astype(float)
        scheduled = build_rebalance_weights(
            factor, close, top_n=top_n, rebalance_days=rebal,
            veto_factor=veto, veto_q=0.30,
        )
        defensive = None
        rot = {"type": "none"}
        if ispec["timing"] == "band" or version in ("v3.0", "v3.1"):
            try:
                from strategies.industry_rotation import load_bond_returns
                defensive = load_bond_returns("511010").astype(float)
                rot = {"type": "regime_bond_etf", "code": "511010"}
            except Exception:
                defensive = None
        prices = PricePanel(close=close, volume=volume, amount=amount)
        from core.engine import Signal as EngSignal
        signal = EngSignal(
            weights=scheduled,
            timing=timing,
            family=family,
            version=version,
            exposure_cap=1.5 if ispec["timing"] == "band" else 1.0,
            defensive_returns=defensive,
        )
        result = BacktestEngine(
            prices=prices,
            config=BacktestConfig(start=start, cost=CostModel(), leverage=lev),
        ).run(signal)
        timing_series = timing
        meta["path"] = f"illiquidity_ILLIQ_SPECS:{version}"
        meta["returns_provenance"] = "live_rerun"
        meta["rotation"] = rot
        meta["leverage"] = lev
        meta["timing_type"] = ispec["timing"]
        meta["config_snapshot"] = {
            "version": version,
            "factor": ispec["factor"],
            "veto": ispec["veto"],
            "timing": ispec["timing"],
            "top_n": top_n,
            "leverage": lev,
        }
    elif family == "small-cap-size":
        # 无 executable_spec 的版本（v1.0/v2.1 等）走 strategies.small_cap
        from strategies.small_cap import StrategyConfig as SCConfig
        from strategies.small_cap import run_small_cap_strategy

        cfg0 = ver.get("config") or {}
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        # v2.0+ 默认排除科创；v1.0 台账未写，仍默认 True（与 StrategyConfig 一致）
        exclude_star = bool(cfg0.get("exclude_star", True))
        sc_cfg = SCConfig(
            family=family,
            version=version,
            start=start,
            top_n=top_n,
            rebalance_days=rebal,
            leverage=lev,
            exclude_star=exclude_star,
        )
        sc_out = run_small_cap_strategy(sc_cfg)
        result = sc_out["engine_result"]
        timing_series = sc_out.get("timing")
        meta["path"] = "strategies.small_cap.run_small_cap_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "small_cap_ma16"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "top_n": top_n,
            "rebal_days": rebal,
            "leverage": lev,
            "exclude_star": exclude_star,
        }
    elif family == "size-earnings":
        # canonical module runner（无 executable_spec 时仍强制重跑，禁止默默读台账 metrics）
        from strategies.size_earnings import StrategyConfig, run_strategy as se_run

        se_cfg = StrategyConfig(family=family, version=version, start=start)
        se_out = se_run(se_cfg)
        result = se_out["engine_result"]
        timing_series = se_out.get("timing")
        meta["path"] = "strategies.size_earnings.run_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = se_cfg.leverage
        meta["timing_type"] = (
            f"pure_trend_ma{se_cfg.timing_ma}*vol_target_{se_cfg.vol_target}"
        )
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "size_window": se_cfg.size_window,
            "blend_weight": se_cfg.blend_weight,
            "top_n": se_cfg.top_n,
            "rebalance_days": se_cfg.rebalance_days,
            "leverage": se_cfg.leverage,
            "vol_target": se_cfg.vol_target,
        }
    elif family == "industry-neglect-rotation":
        # 华西量价行业轮动（v1.0–v1.4）；canonical = strategies.industry_rotation
        from strategies.industry_rotation import (
            StrategyConfig as IRConfig,
            run_industry_rotation_strategy,
        )

        cfg0 = ver.get("config") or {}
        # v1.0/v1.3 = ETF 轮动（低费率）；其余个股选股走 stock 成本
        cost_mode = "etf" if version in ("v1.0", "v1.3") else "stock"
        if isinstance(cfg0.get("cost"), dict) and "etf_fee" in str(cfg0.get("cost")):
            cost_mode = "etf"
        try:
            top_k = int(cfg0.get("top_k_industries") or 10)
        except (TypeError, ValueError):
            top_k = 10
        try:
            top_n = int(cfg0.get("top_n_stocks") or 2)
        except (TypeError, ValueError):
            top_n = 2
        # CPV 惩罚：仅 v1.2/v1.4 默认 0.5；v1.0/v1.1 为 0
        try:
            if cfg0.get("w_cpv") is not None:
                w_cpv = float(cfg0.get("w_cpv"))
            elif version in ("v1.2", "v1.4"):
                w_cpv = 0.5
            else:
                w_cpv = 0.0
        except (TypeError, ValueError):
            w_cpv = 0.5 if version in ("v1.2", "v1.4") else 0.0
        # 调仓周期：v1.1 台账「慢速半年频 120d」
        timing_s = str(cfg0.get("timing") or "")
        try:
            if cfg0.get("rebalance_days") is not None:
                rebal = int(cfg0.get("rebalance_days"))
            elif version == "v1.1" or "120" in timing_s:
                rebal = 120
            else:
                rebal = 20
        except (TypeError, ValueError):
            rebal = 20
        ir_cfg = IRConfig(
            family=family,
            version=version,
            start=start,
            top_k_industries=top_k,
            top_n_stocks=top_n,
            w_cpv=w_cpv,
            cost_mode=cost_mode,
            rebalance_days=rebal,
        )
        ir_out = run_industry_rotation_strategy(ir_cfg)
        result = ir_out["engine_result"]
        timing_series = ir_out.get("timing")
        meta["path"] = "strategies.industry_rotation.run_industry_rotation_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.0
        meta["timing_type"] = "small_cap_ma16" if version in ("v1.3", "v1.4") else "none"
        meta["rotation"] = (
            {"type": "regime_bond_etf", "code": "511010"}
            if version in ("v1.3", "v1.4")
            else {"type": "none"}
        )
        meta["config_snapshot"] = {
            "version": version,
            "cost_mode": cost_mode,
            "top_k_industries": top_k,
            "top_n_stocks": top_n,
            "w_cpv": w_cpv,
            "rebalance_days": rebal,
        }
    elif family == "large-cap-growth-hedged":
        from strategies.large_cap import StrategyConfig as LCConfig
        from strategies.large_cap import run_large_cap_strategy

        cfg0 = ver.get("config") or {}
        cost0 = cfg0.get("cost") if isinstance(cfg0.get("cost"), dict) else {}
        # buf：v1.0-full 台账 timing 文案为 2%；默认 v1.0 = 1%
        buf = 0.02 if "full" in str(version) else 0.01
        timing_s = str(cfg0.get("timing") or "")
        if "2%" in timing_s or "buf 2" in timing_s.lower():
            buf = 0.02
        elif "1%" in timing_s or "buf 1" in timing_s.lower():
            buf = 0.01
        try:
            w_cpv = float(cfg0.get("w_cpv_max") if cfg0.get("w_cpv_max") is not None else 0.0)
        except (TypeError, ValueError):
            w_cpv = 0.0
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 40)
        except (TypeError, ValueError):
            rebal = 40
        try:
            hedge_c = float(cost0.get("hedge_cost_annual") or 0.015)
        except (TypeError, ValueError):
            hedge_c = 0.015
        try:
            switch_f = float(cost0.get("switch_friction") or 0.0025)
        except (TypeError, ValueError):
            switch_f = 0.0025
        # full 历史版默认更早 start（调用方可覆盖）
        path_start = start
        if "full" in str(version) and start >= "2018-01-01":
            path_start = "2012-01-01"
        lc_cfg = LCConfig(
            family=family,
            version=version,
            start=path_start,
            top_n=top_n,
            rebalance_days=rebal,
            buffer_size=buf,
            hedge_cost_annual=hedge_c,
            switch_friction=switch_f,
            w_cpv_max=w_cpv,
        )
        lc_out = run_large_cap_strategy(lc_cfg)
        # 对冲后收益在 returns，不是 engine_result（后者多为多头腿）
        ret_lc = lc_out["returns"].astype(float).sort_index()
        from types import SimpleNamespace
        result = SimpleNamespace(returns=ret_lc)
        timing_series = lc_out.get("timing")
        meta["path"] = "strategies.large_cap.run_large_cap_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.0
        meta["timing_type"] = f"hysteresis_ma120_buf{buf}"
        meta["rotation"] = {"type": "none"}
        meta["hedged"] = True
        meta["config_snapshot"] = {
            "version": version,
            "top_n": top_n,
            "rebal_days": rebal,
            "buffer_size": buf,
            "w_cpv_max": w_cpv,
            "hedge_cost_annual": hedge_c,
            "start": path_start,
        }
    elif family == "hq-momentum-hedged":
        from strategies.hq_momentum import StrategyConfig as HQConfig
        from strategies.hq_momentum import run_hq_momentum_strategy

        cfg0 = ver.get("config") or {}
        cost0 = cfg0.get("cost") if isinstance(cfg0.get("cost"), dict) else {}
        try:
            lookback = int(cfg0.get("lookback") or 60)
        except (TypeError, ValueError):
            lookback = 60
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.0)
        except (TypeError, ValueError):
            lev = 1.0
        try:
            hedge_c = float(cost0.get("hedge_cost_annual") or 0.015)
        except (TypeError, ValueError):
            hedge_c = 0.015
        path_start = start
        if "full" in str(version) and start >= "2018-01-01":
            path_start = "2012-01-01"
        hq_cfg = HQConfig(
            family=family,
            version=version,
            start=path_start,
            lookback=lookback,
            top_n=top_n,
            rebalance_days=rebal,
            leverage=lev,
            hedge_cost_annual=hedge_c,
        )
        hq_out = run_hq_momentum_strategy(hq_cfg)
        from types import SimpleNamespace
        result = SimpleNamespace(returns=hq_out["returns"].astype(float).sort_index())
        timing_series = None
        meta["path"] = "strategies.hq_momentum.run_hq_momentum_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "none"
        meta["rotation"] = {"type": "none"}
        meta["hedged"] = True
        meta["config_snapshot"] = {
            "version": version,
            "lookback": lookback,
            "top_n": top_n,
            "rebal_days": rebal,
            "leverage": lev,
            "hedge_cost_annual": hedge_c,
            "start": path_start,
        }
    elif family == "illiquidity-large-cap":
        # Top800 成交额宇宙 + Amihud + Salience Veto + PureTrend Band（台账 v1.0）
        from factors.alpha import transforms  # noqa: F401
        from factors.alpha.base import FactorData
        from factors.alpha.builtins.illiq import AmihudIlliq
        from factors.small_cap import small_cap_timing
        from factors.veto import salience_covariance_veto
        from strategies.small_cap import build_rebalance_weights

        cfg0 = ver.get("config") or {}
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            univ_n = int(cfg0.get("universe_size") or 800)
        except (TypeError, ValueError):
            univ_n = 800
        warmup = get_settings().data.warmup_start
        ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
        close, volume, amount = load_price_panels(ds)
        # 宇宙 = 近窗成交额排名 top univ_n（滞后一日防泄露）
        amt_rank = amount.rolling(20).mean().rank(axis=1, ascending=False, method="first")
        univ = (amt_rank <= univ_n).shift(1)
        fdata = FactorData(close=close, volume=volume, amount=amount)
        amihud = AmihudIlliq(window=20).mad_clip(5).zscore().shift(1).compute(fdata)
        factor = amihud.where(univ)
        veto = salience_covariance_veto(close).shift(1)
        traw, _, tdist = small_cap_timing(close, amount, ma_window=16)
        dist = tdist.shift(1).clip(lower=-0.5, upper=0.5)
        timing = ((1.0 + dist * 8.0).clip(lower=0.0, upper=1.5) * (dist > 0)).fillna(0.0)
        scheduled = build_rebalance_weights(
            factor, close, top_n=top_n, rebalance_days=rebal,
            veto_factor=veto, veto_q=0.30,
        )
        prices = PricePanel(close=close, volume=volume, amount=amount)
        from core.engine import Signal as EngSignal
        signal = EngSignal(
            weights=scheduled,
            timing=timing,
            family=family,
            version=version,
            exposure_cap=1.5,
        )
        result = BacktestEngine(
            prices=prices,
            config=BacktestConfig(start=start, cost=CostModel(), leverage=1.25),
        ).run(signal)
        timing_series = timing
        meta["path"] = "illiquidity-large-cap:amihud_top800+veto+band"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.25
        meta["timing_type"] = "pure_trend_ma16_band"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "universe_size": univ_n,
            "top_n": top_n,
            "rebal_days": rebal,
            "veto": True,
            "timing": "band",
        }
    elif family == "amount-timing":
        from strategies.amount_timing import StrategyConfig as ATConfig
        from strategies.amount_timing import run_amount_timing_strategy

        cfg0 = ver.get("config") or {}
        cost0 = cfg0.get("cost") if isinstance(cfg0.get("cost"), dict) else {}
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25
        from core.engine import CostModel as _CM
        try:
            cost = _CM(
                buy_cost=float(cost0.get("buy") or 0.00225),
                sell_cost=float(cost0.get("sell") or 0.00275),
                financing_rate=float(cost0.get("financing_rate") or 0.065),
            )
        except (TypeError, ValueError):
            cost = _CM()
        at_cfg = ATConfig(
            family=family,
            version=version,
            start=start,
            top_n=top_n,
            rebalance_days=rebal,
            leverage=lev,
            exclude_star=bool(cfg0.get("exclude_star", True)),
            cost=cost,
        )
        at_out = run_amount_timing_strategy(at_cfg)
        result = at_out["engine_result"]
        timing_series = at_out.get("timing")
        meta["path"] = "strategies.amount_timing.run_amount_timing_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "small_cap_ma16_binary"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "top_n": top_n,
            "rebal_days": rebal,
            "leverage": lev,
            "factor": "amount.rank direction=-1",
        }
    elif family == "size-low-vol":
        from strategies.size_low_vol import StrategyConfig as SLConfig
        from strategies.size_low_vol import run_size_low_vol_strategy

        cfg0 = ver.get("config") or {}
        # v1.0 → vol20；v1.1 → vol40
        vol_window = 40 if str(version).startswith("v1.1") else 20
        factor_s = str(cfg0.get("factor") or "")
        if "40" in factor_s:
            vol_window = 40
        elif "20" in factor_s:
            vol_window = 20
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebal_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25
        sl_cfg = SLConfig(
            family=family,
            version=version,
            start=start,
            vol_window=vol_window,
            top_n=top_n,
            rebalance_days=rebal,
            leverage=lev,
        )
        sl_out = run_size_low_vol_strategy(sl_cfg)
        result = sl_out["engine_result"]
        timing_series = sl_out.get("timing")
        meta["path"] = "strategies.size_low_vol.run_size_low_vol_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "pure_trend_ma16"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "vol_window": vol_window,
            "top_n": top_n,
            "rebal_days": rebal,
            "leverage": lev,
        }
    elif family == "d-le-sc-hedged":
        from strategies.d_le_sc import StrategyConfig as DLConfig
        from strategies.d_le_sc import run_d_le_sc_strategy

        cfg0 = ver.get("config") or {}
        cost0 = cfg0.get("cost") if isinstance(cfg0.get("cost"), dict) else {}
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebalance_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            direction = int(cfg0.get("direction") if cfg0.get("direction") is not None else 1)
        except (TypeError, ValueError):
            direction = 1
        try:
            buy_c = float(cost0.get("buy") if cost0.get("buy") is not None else 0.00225)
            sell_c = float(cost0.get("sell") if cost0.get("sell") is not None else 0.00275)
            hedge_c = float(
                cost0.get("hedge_cost_annual")
                if cost0.get("hedge_cost_annual") is not None
                else 0.015
            )
        except (TypeError, ValueError):
            buy_c, sell_c, hedge_c = 0.00225, 0.00275, 0.015
        dl_cfg = DLConfig(
            family=family,
            version=version,
            start=start,
            network_type=str(cfg0.get("network_type") or "overnight_lead_daytime"),
            correlation_method=str(cfg0.get("correlation_method") or "pearson"),
            rebalance_days=rebal,
            top_n=top_n,
            direction=direction,
            buy_cost=buy_c,
            sell_cost=sell_c,
            hedge_cost_annual=hedge_c,
        )
        dl_out = run_d_le_sc_strategy(dl_cfg)
        from types import SimpleNamespace
        result = SimpleNamespace(returns=dl_out["returns"].astype(float).sort_index())
        timing_series = None
        meta["path"] = "strategies.d_le_sc.run_d_le_sc_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.0
        meta["timing_type"] = "none"
        meta["rotation"] = {"type": "none"}
        meta["hedged"] = True
        meta["config_snapshot"] = {
            "version": version,
            "network_type": dl_cfg.network_type,
            "rebalance_days": rebal,
            "direction": direction,
            "top_n": top_n,
        }
    elif family == "small-cap-staleness":
        # 台账 config: zscore(small_cap)+λ·zscore(zero_ret_days); 与 promote_smallcap_staleness 同口径
        import numpy as np

        from app_config.settings import get_settings
        from core.engine import Signal as EngSignal
        from factors.microstructure import zero_ret_days
        from factors.small_cap import small_cap_factor, small_cap_timing
        from factors.utils import mad_clip, safe_zscore
        from strategies.small_cap import build_rebalance_weights

        cfg0 = ver.get("config") or {}
        try:
            lam = float(cfg0.get("lambda") if cfg0.get("lambda") is not None else 0.5)
        except (TypeError, ValueError):
            lam = 0.5
        try:
            sc_win = int(cfg0.get("small_cap_window") or 60)
        except (TypeError, ValueError):
            sc_win = 60
        try:
            zr_win = int(cfg0.get("zero_ret_window") or sc_win)
        except (TypeError, ValueError):
            zr_win = sc_win
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebalance_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25

        def _z(df):
            return safe_zscore(mad_clip(df.replace([np.inf, -np.inf], np.nan)))

        warmup = get_settings().data.warmup_start
        ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
        close, volume, amount = load_price_panels(ds)
        core = _z(small_cap_factor(amount, window=sc_win))
        zr = zero_ret_days(close, n=zr_win)
        factor = _z(core + lam * zr)
        timing = small_cap_timing(close, amount, ma_window=16)[0].astype(float)
        scheduled = build_rebalance_weights(
            factor, close, top_n=top_n, rebalance_days=rebal
        )
        prices = PricePanel(close=close, volume=volume, amount=amount)
        signal = EngSignal(
            weights=scheduled,
            timing=timing,
            family=family,
            version=version,
        )
        result = BacktestEngine(
            prices=prices,
            config=BacktestConfig(start=start, cost=CostModel(), leverage=lev),
        ).run(signal)
        timing_series = timing
        meta["path"] = "small-cap-staleness:size+zero_ret_days"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "small_cap_ma16_binary"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "lambda": lam,
            "small_cap_window": sc_win,
            "zero_ret_window": zr_win,
            "top_n": top_n,
            "rebalance_days": rebal,
            "leverage": lev,
        }
    elif family.startswith("small_cap_factor__window"):
        # 窗口扫描家族：台账 config 驱动 factor_fn_name/window；与 nine_gate_runner 同口径
        import importlib

        from app_config.settings import get_settings
        from core.engine import Signal as EngSignal
        from strategies.small_cap import build_rebalance_weights

        cfg0 = ver.get("config") or {}
        if not cfg0:
            raise ValueError(
                f"{family}/{version} 台账无 config；拒绝默认参数假重跑"
            )
        factor_fn_name = cfg0.get("factor_fn_name")
        if not factor_fn_name or "." not in str(factor_fn_name):
            raise ValueError(f"{family}/{version} config 缺 factor_fn_name")
        factor_params = dict(cfg0.get("factor_params") or {})
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebalance_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25
        warmup = get_settings().data.warmup_start
        ds = str(min(pd.Timestamp(start), pd.Timestamp(warmup)).date())
        close, volume, amount = load_price_panels(ds)
        mod_name, fn_name = str(factor_fn_name).rsplit(".", 1)
        factor_fn = getattr(importlib.import_module(mod_name), fn_name)
        # 与 nine_gate_runner window 分支一致：factor.shift(1) 再进权重
        factor = factor_fn(amount, **factor_params).shift(1)
        scheduled = build_rebalance_weights(
            factor, close, top_n=top_n, rebalance_days=rebal
        )
        prices = PricePanel(close=close, volume=volume, amount=amount)
        signal = EngSignal(
            weights=scheduled,
            timing=None,
            family=family,
            version=version,
        )
        result = BacktestEngine(
            prices=prices,
            config=BacktestConfig(start=start, cost=CostModel(), leverage=lev),
        ).run(signal)
        timing_series = None
        meta["path"] = f"window_scan:{factor_fn_name}"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "none"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "factor_fn_name": factor_fn_name,
            "factor_params": factor_params,
            "top_n": top_n,
            "rebalance_days": rebal,
            "leverage": lev,
            "window": cfg0.get("window"),
        }
    elif family == "composite-portfolio":
        from strategies.composite_portfolio import run_composite_portfolio_strategy

        cfg0 = ver.get("config") or {}
        alloc = dict(cfg0.get("allocation") or {
            "illiq_sc": 0.4, "lc_mom": 0.4, "reversal": 0.2,
        })
        # 默认用 CLI --start（通常 2018）；v1.0 台账原注册窗约 2023，全样本 live 可对拍
        cp_out = run_composite_portfolio_strategy(
            family=family,
            version=version,
            start=start,
            allocation=alloc,
            use_lagged=True,
        )
        from types import SimpleNamespace
        result = SimpleNamespace(returns=cp_out["returns"].astype(float).sort_index())
        timing_series = None
        meta["path"] = "strategies.composite_portfolio.run_composite_portfolio_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.0
        meta["timing_type"] = "leg_embedded_T1_lag"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "allocation": alloc,
            "start": start,
            "use_lagged": True,
        }
    elif family == "multi-factor-composite":
        from strategies.composite_portfolio import run_multi_factor_composite_strategy

        cfg0 = ver.get("config") or {}
        alloc = dict(cfg0.get("allocation") or {
            "illiq_v3.1": 0.6,
            "small_cap_v2.0": 0.2,
            "lc_hedge_v1.0": 0.2,
        })
        mf_out = run_multi_factor_composite_strategy(
            family=family,
            version=version,
            start=start,
            allocation=alloc,
        )
        from types import SimpleNamespace
        result = SimpleNamespace(returns=mf_out["returns"].astype(float).sort_index())
        timing_series = None
        meta["path"] = "strategies.composite_portfolio.run_multi_factor_composite_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = 1.0
        meta["timing_type"] = "monthly_fixed_weight_blend"
        meta["rotation"] = {"type": "none"}
        meta["config_snapshot"] = {
            "version": version,
            "allocation": alloc,
            "rebalance": cfg0.get("rebalance") or "monthly",
            "leg_meta": mf_out.get("leg_meta"),
        }
    elif family == "roc-yc":
        from strategies.roc_yc import StrategyConfig as RYConfig
        from strategies.roc_yc import run_roc_yc_strategy

        cfg0 = ver.get("config") or {}
        try:
            blend = float(cfg0.get("blend_weight") if cfg0.get("blend_weight") is not None else 0.5)
        except (TypeError, ValueError):
            blend = 0.5
        try:
            top_n = int(cfg0.get("top_n") or 25)
        except (TypeError, ValueError):
            top_n = 25
        try:
            rebal = int(cfg0.get("rebalance_days") or 20)
        except (TypeError, ValueError):
            rebal = 20
        try:
            lev = float(cfg0.get("leverage") or 1.25)
        except (TypeError, ValueError):
            lev = 1.25
        ry_cfg = RYConfig(
            family=family,
            version=version,
            start=start,
            blend_weight=blend,
            neutralize=bool(cfg0.get("neutralize", True)),
            hedged=bool(cfg0.get("hedged", True)),
            top_n=top_n,
            rebalance_days=rebal,
            leverage=lev,
            exclude_star=bool(cfg0.get("exclude_star", True)),
        )
        ry_out = run_roc_yc_strategy(ry_cfg)
        from types import SimpleNamespace
        result = SimpleNamespace(returns=ry_out["returns"].astype(float).sort_index())
        timing_series = ry_out.get("timing")
        meta["path"] = "strategies.roc_yc.run_roc_yc_strategy"
        meta["returns_provenance"] = "live_rerun"
        meta["leverage"] = lev
        meta["timing_type"] = "none"
        meta["rotation"] = {"type": "none"}
        meta["hedged"] = bool(cfg0.get("hedged", True))
        meta["config_snapshot"] = ry_cfg.to_dict()
    else:
        # AutoResearch / AST 配置驱动（fundamental-momentum / autoresearch_* / alternative-flow）
        from strategies.ast_config_strategy import is_dsl_family, run_ast_config_strategy

        if is_dsl_family(family):
            cfg0 = ver.get("config") or {}
            ast_out = run_ast_config_strategy(
                family=family,
                version=version,
                start=start,
                config=cfg0,
            )
            result = ast_out["engine_result"]
            timing_series = ast_out.get("timing")
            snap = ast_out.get("config_snapshot") or {}
            meta["path"] = "strategies.ast_config_strategy.run_ast_config_strategy"
            meta["returns_provenance"] = "live_rerun"
            meta["leverage"] = snap.get("leverage", 1.25)
            meta["timing_type"] = f"small_cap_ma{snap.get('timing_ma', 16)}_binary"
            meta["rotation"] = {"type": "none"}
            meta["config_snapshot"] = {"version": version, **snap}
        else:
            raise ValueError(
                f"{family}/{version} 无 live re-run 路径"
                f"（executable_spec / illiquidity / small-cap-size / size-earnings / "
                f"industry-neglect-rotation / large-cap-growth-hedged / "
                f"amount-timing / size-low-vol / d-le-sc-hedged / "
                f"small-cap-staleness / small_cap_factor__window* / "
                f"composite-portfolio / multi-factor-composite / roc-yc / "
                f"AST-config(autoresearch/fundamental-momentum/alternative-flow) / "
                f"已登记 module runner）；"
                f"禁止用台账 metrics 冒充路径 KPI。"
                f"若仅有陈旧收益 CSV，须显式 --returns-csv + --allow-stale-csv（会强制偏差披露）。"
            )

    ret = result.returns.astype(float).sort_index()
    ret.name = "ret"
    return ret, meta, timing_series


def load_registry_meta(family: str, version: str) -> dict:
    """只读台账 meta（假设/metrics/nine_gate），不含收益。"""
    import strategy_registry

    data = strategy_registry._load()
    fam = next((f for f in data["families"] if f["id"] == family), None)
    if fam is None:
        raise ValueError(f"family not found: {family}")
    ver = next((v for v in fam.get("versions", []) if v.get("version") == version), None)
    if ver is None:
        raise ValueError(f"version not found: {family}/{version}")
    return {
        "family": family,
        "version": version,
        "status": ver.get("status"),
        "registry_metrics": ver.get("metrics"),
        "nine_gate": {
            k: (ver.get("nine_gate") or {}).get(k)
            for k in ("passed_all", "dsr_p", "run_date", "gate4_verdict", "cost_decay_rate")
        },
        "production_blocked": (ver.get("evidence") or {}).get("production_blocked"),
        "family_name": fam.get("name") or family,
        "hypothesis": fam.get("hypothesis") or "",
        "regime": fam.get("regime") or "",
        "decay_signal": fam.get("decay_signal") or "",
        "failure_boundaries": dict(fam.get("failure_boundaries") or {}),
        "style_betas": dict(fam.get("style_betas") or {}),
        "capacity_m": fam.get("capacity_m"),
        "version_desc": ver.get("desc") or "",
        "config": ver.get("config") or {},
    }


def build_analysis(
    ret: pd.Series,
    meta: dict,
    exposure: pd.Series | None = None,
    *,
    skip_timing: bool = False,
) -> dict:
    full = _metrics_from_returns(ret)
    # 完整性：full 必须可由 ret 重算（杜绝事后改年化）
    verify_metrics_match_returns(ret, full)

    timing = None if skip_timing else _timing_decomp(ret, exposure)
    yearly = _yearly_table(ret)
    monthly = _monthly_matrix(ret)
    drawdown = _drawdown_path(ret)
    overview_kpis = build_overview_kpis(full, monthly.get("stats") or {}, drawdown)
    for row in overview_kpis:
        if row["key"] == "calmar" and _is_number(row.get("value")):
            full["calmar"] = float(row["value"])
    verify_kpi_display_matches_value(overview_kpis)

    honesty = build_honesty_block(meta or {}, ret, full)
    # 再次钉死：任何调用方不得把 can_claim_valid 拧成 true
    honesty["can_claim_valid"] = False
    honesty["not_alpha_evidence"] = True
    honesty["not_admission_evidence"] = True
    honesty["not_deployment_evidence"] = True
    honesty["metrics_verified_against_returns"] = True
    honesty["registry_metrics_not_used_as_kpi"] = True
    if honesty.get("returns_provenance") not in ALLOWED_RETURNS_PROVENANCE:
        honesty["returns_provenance"] = "caller_series"

    ng = (meta or {}).get("nine_gate") or {}
    economic_logic = build_economic_logic(meta or {}, full, drawdown, yearly, timing)
    registry_audit = build_registry_audit(full, meta or {})
    analysis = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "meta": meta,
        "full": full,
        "overview": {
            "kpis": overview_kpis,
            "one_liner": build_one_liner(yearly, monthly.get("stats") or {}, drawdown),
        },
        "economic_logic": economic_logic,
        "registry_audit": registry_audit,
        "yearly": yearly,
        "monthly": monthly,
        "drawdown": drawdown,
        "losing_streaks": _longest_losing_months(ret),
        "timing": timing,
        "patterns": _pattern_notes(ret, meta),
        "avoidance": _avoidance_notes(),
        "nine_gate_summary": {
            "passed_all": ng.get("passed_all"),
            "dsr_p": ng.get("dsr_p"),
            "run_date": ng.get("run_date"),
            "gate4_verdict": ng.get("gate4_verdict"),
            "cost_decay_rate": ng.get("cost_decay_rate"),
        },
        "honesty": honesty,
        "html_sections": list(HTML_SECTIONS),
        "chat_sections": list(CHAT_SECTIONS),
    }
    validate_analysis(analysis)
    return analysis


def _pattern_notes(ret: pd.Series, meta: dict) -> list[dict]:
    """结构化规律条目(叙事模板,非统计裁决)。"""
    m = ret.groupby([ret.index.year, ret.index.month]).apply(
        lambda x: float((1 + x.astype(float)).prod() - 1)
    )
    win = float((m > 0).mean())
    notes = [
        {
            "id": "right_skew",
            "title": "右偏厚尾 / 低月胜率",
            "body": (
                f"月胜率约 {win:.0%}，月均收益显著高于中位数时，说明少数脉冲月决定大部分财富路径；"
                "不是稳定每个月赚一点的策略。"
            ),
        },
        {
            "id": "drawdown_cluster",
            "title": "回撤成段而非均匀噪声",
            "body": (
                "最长连亏月与跨年 MaxDD 往往重合；风险以季度～半年 regime 段出现，"
                "风险控制应写事前熔断/降仓，而非事后删坏月份。"
            ),
        },
        {
            "id": "timing_split",
            "title": "收益与风险主要来自 timing 开启的持股段",
            "body": (
                "若 timing 分解可用：开启日贡献主要收益与回撤；关闭日不是「免费安全」"
                "（可能含降仓成本/滞后）。有债轮动时债仅作空仓缓冲，不能证明因子在关闭日仍有效。"
            ),
        },
        {
            "id": "no_fake_rules",
            "title": "禁止把弱季节/小样本次月惯性写成交易规则",
            "body": (
                "1 月偏冷、大涨后次月续强等线索样本短，只可作监控提示；"
                "禁止为躲历史段再搜参数(R-OBJECTIVE-001 / Goodhart)。"
            ),
        },
    ]
    status = meta.get("status")
    if status and status != "在册":
        notes.append({
            "id": "registry_status",
            "title": f"台账 status={status}",
            "body": "路径好看不等于可部署；入册/生产以 9-Gate + workflow 为准。",
        })
    return notes


def _avoidance_notes() -> list[dict]:
    return [
        {
            "tier": "A 仓位",
            "items": [
                "降名义暴露 / 波动目标(如年化 vol 目标 12%)——用规模买睡眠",
                "硬性回撤熔断(−15%/−20% 降半仓或转防守资产)，事前写死",
                "单策略资金上限(小盘非流动冲击)",
                "禁止仅凭 headline 满仓",
            ],
        },
        {
            "tier": "B 组合",
            "items": [
                "不要单腿：与低相关腿并权",
                "风险预算制，而非谁年化高谁多给",
                "接受满意线下调，用组合换平滑",
            ],
        },
        {
            "tier": "C 研究(须过门禁)",
            "items": [
                "改进 risk-off / 双确认，而非再搜神 MA",
                "新规则必须 trial 记账 + OOS/holdout + 9-Gate",
                "保留债轮动作空仓缓冲，不指望债救持股段",
            ],
        },
        {
            "tier": "D 运营",
            "items": [
                "预期月胜率~50%，连亏 3–6 月是常态",
                "大涨后勿加杠杆回本；大跌后勿报复性加仓",
                "监控滚动夏普/IC/暴露/回撤阈值",
            ],
        },
    ]


def render_html(analysis: dict, out_path: Path) -> None:
    """生成自包含深色 HTML（服务端渲染）。

    版式对齐首版 illiquidity 手工报告：8 卡总览 + 年度(含简评/条形图/净值路径)
    + 月度热力/分布/连亏/极端月 + 规律四档 + 规避 A–D + 9-Gate 对照 + 口径。
    """
    import html as html_lib
    import math

    def esc(x) -> str:
        return html_lib.escape("" if x is None else str(x))

    def _f(x):
        try:
            v = float(x)
            if v != v:
                return None
            return v
        except (TypeError, ValueError):
            return None

    def pct(x, d=1, *, signed=True) -> str:
        """百分比。signed=False 用于波动/占比（无正号）。负号统一用 Unicode −。"""
        v = _f(x)
        if v is None:
            return "·"
        av = abs(v) * 100
        body = f"{av:.{d}f}%"
        if not signed:
            return body
        if v > 1e-12:
            return f"+{body}"
        if v < -1e-12:
            return f"−{body}"  # U+2212
        return f"0.{('0'*d)}%" if d else "0%"

    def cls_ret(x) -> str:
        v = _f(x)
        if v is None:
            return ""
        if v > 1e-6:
            return "pos"
        if v < -1e-6:
            return "neg"
        return ""

    def heat_bg(x) -> str:
        v = _f(x)
        if v is None:
            return "transparent"
        t = max(-0.08, min(0.20, v))
        if t >= 0:
            a = 0.12 + 0.55 * (t / 0.20)
            return f"rgba(62, 207, 142, {a:.3f})"
        a = 0.12 + 0.55 * (abs(t) / 0.08)
        return f"rgba(240, 113, 120, {a:.3f})"

    def bar_rows(items, value_key="ret", label_key="ym", d=2) -> str:
        vals = []
        rows = []
        for it in items:
            if isinstance(it, dict):
                lab, val = it.get(label_key, ""), _f(it.get(value_key)) or 0.0
            else:
                lab, val = it[0], float(it[1])
            rows.append((lab, val))
            vals.append(abs(val))
        max_abs = max(vals) if vals else 1e-6
        max_abs = max(max_abs, 1e-6)
        parts = []
        for lab, val in rows:
            w = abs(val) / max_abs * 100
            c = cls_ret(val)
            parts.append(
                f'<div class="bar-row"><span class="mono">{esc(lab)}</span>'
                f'<div class="bar-track"><div class="bar-fill {c}" style="width:{w:.1f}%"></div></div>'
                f'<span class="mono {c}">{pct(val, d)}</span></div>'
            )
        return "\n".join(parts)

    def year_comment(r: dict) -> str:
        """年度简评（机械、可复现）。"""
        ret = _f(r.get("ret")) or 0.0
        mdd = abs(_f(r.get("maxdd")) or 0.0)
        sh = _f(r.get("sharpe")) or 0.0
        y = r.get("year")
        if y == 2026 or (r.get("n") or 0) < 180:
            return "未满年 / YTD"
        if ret >= 0.35:
            return "爆发年" if mdd < 0.15 else "大年·深回撤"
        if ret >= 0.15:
            return "强年可控" if mdd < 0.12 else "好年·回撤仍深"
        if ret >= 0.05:
            return "中等 / 平庸"
        if ret >= -0.02:
            return "接近打平"
        if ret >= -0.10:
            return "弱年小亏"
        return "差年"

    meta = analysis.get("meta") or {}
    full = analysis.get("full") or {}
    dd = analysis.get("drawdown") or {}
    yearly = analysis.get("yearly") or []
    monthly = analysis.get("monthly") or {}
    matrix = monthly.get("matrix") or {}
    year_total = monthly.get("year_total") or {}
    st = monthly.get("stats") or {}
    streaks = analysis.get("losing_streaks") or []
    patterns = analysis.get("patterns") or []
    avoidance = analysis.get("avoidance") or []
    timing = analysis.get("timing") or {}
    # 只用 schema 内 nine_gate_summary（不读 meta 旁路），无数据也要出第 6 章
    ng = analysis.get("nine_gate_summary") or {}

    fam = meta.get("family") or "?"
    ver = meta.get("version") or "?"
    title = f"{fam}/{ver} 路径分析"

    # —— header identity line ——
    ttype = meta.get("timing_type") or "?"
    rot = meta.get("rotation") or {}
    rot_s = "无债轮动"
    if isinstance(rot, dict) and rot.get("type") not in (None, "", "none"):
        rot_s = f"{rot.get('type')}/{rot.get('code', '?')}"
    identity = (
        f"path={meta.get('path') or '?'} · timing={ttype} · lev={meta.get('leverage', '?')} · "
        f"rotation={rot_s} · CostModel 审慎费率"
    )

    # tags
    tags = []
    if meta.get("status"):
        tags.append(f'<span class="tag">{esc(meta["status"])}</span>')
    if meta.get("production_blocked"):
        tags.append('<span class="tag fail">production_blocked</span>')
    if ng.get("passed_all") is False:
        tags.append('<span class="tag fail">9-Gate REJECTED</span>')
    elif ng.get("passed_all") is True:
        tags.append('<span class="tag pass">9-Gate PASS</span>')
    if full.get("hit") is False:
        tags.append('<span class="tag fail">hit=False</span>')
    elif full.get("hit") is True:
        tags.append('<span class="tag pass">hit=True</span>')
    tags_html = " ".join(tags)

    # —— 8 KPIs：只读 overview.kpis（schema 冻结，禁止 render 侧另算）——
    validate_analysis(analysis)  # fail-closed before write
    honesty = analysis.get("honesty") or {}
    if honesty.get("can_claim_valid") is not False:
        raise ValueError("render 拒绝 can_claim_valid!=False")
    overview = analysis.get("overview") or {}
    kpis_rows = overview.get("kpis") or []
    kpi_html = "".join(
        f'<div class="kpi"><div class="l">{esc(row.get("label"))}</div>'
        f'<div class="v {esc(row.get("css") or "neu")}">{esc(row.get("display"))}</div>'
        + (f'<div class="hint">{esc(row.get("hint") or "")}</div>' if row.get("hint") else "")
        + "</div>"
        for row in kpis_rows
    )
    one_liner = overview.get("one_liner") or ""

    # yearly table + 简评
    y_rows = []
    for r in yearly:
        comment = r.get("comment") or year_comment(r)
        y_rows.append(
            "<tr>"
            f"<td>{esc(r.get('year'))}</td>"
            f'<td class="{cls_ret(r.get("ret"))}">{pct(r.get("ret"), 1)}</td>'
            f'<td class="{cls_ret(r.get("ann"))}">{pct(r.get("ann"), 1)}</td>'
            f"<td>{pct(r.get('vol'), 1, signed=False)}</td>"
            f"<td>{(_f(r.get('sharpe')) or 0):.2f}</td>"
            f'<td class="neg">{pct(r.get("maxdd"), 1)}</td>'
            f"<td>{esc(r.get('n'))}</td>"
            f"<td>{(_f(r.get('nav_ye')) or 0):.2f}</td>"
            f'<td class="note">{esc(comment)}</td>'
            "</tr>"
        )
    yearly_table = (
        "<thead><tr><th>年</th><th>日历收益</th><th>年化速率*</th><th>波动</th>"
        "<th>Sharpe</th><th>年内 MaxDD</th><th>交易日</th><th>年末净值</th><th>简评</th></tr></thead>"
        f"<tbody>{''.join(y_rows)}</tbody>"
    )
    year_bars = bar_rows([{"ym": str(r.get("year")), "ret": r.get("ret")} for r in yearly], d=1)

    # 净值路径 list
    nav_rows = "".join(
        f"<tr><td>{esc(r.get('year'))}</td><td>{(_f(r.get('nav_ye')) or 0):.2f}</td>"
        f'<td class="{cls_ret(r.get("ret"))}">{pct(r.get("ret"), 1)}</td></tr>'
        for r in yearly
    )
    nav_table = (
        "<thead><tr><th>年末</th><th>净值(起点=1)</th><th>当年收益</th></tr></thead>"
        f"<tbody>{nav_rows}</tbody>"
    )

    # monthly heat
    month_headers = "".join(f"<th>{i}</th>" for i in range(1, 13))
    heat_rows = []
    for y in sorted(matrix.keys(), key=lambda z: int(z)):
        cells = []
        for v in matrix[y]:
            if v is None:
                cells.append('<td class="muted">·</td>')
            else:
                cells.append(
                    f'<td class="{cls_ret(v)}" style="background:{heat_bg(v)}">{pct(v, 1)}</td>'
                )
        yt = year_total.get(y)
        heat_rows.append(
            f'<tr><td class="y">{esc(y)}</td>{"".join(cells)}'
            f'<td class="{cls_ret(yt)}" style="font-weight:600">{pct(yt, 1)}</td></tr>'
        )
    heat_table = (
        f"<thead><tr><th>年</th>{month_headers}<th>年合计</th></tr></thead>"
        f"<tbody>{''.join(heat_rows)}</tbody>"
    )

    try:
        win_pct = f"{float(st.get('win_rate')) * 100:.1f}%"
    except (TypeError, ValueError):
        win_pct = "·"

    month_stats_html = f"""
    <h3 style="margin-top:0">月度分布</h3>
    <ul>
      <li>月数 <strong>{esc(st.get('n'))}</strong>
          · 均 <span class="pos">{pct(st.get('mean'), 2)}</span>
          · 中位 {pct(st.get('median'), 2)}</li>
      <li>月波动 {pct(st.get('std'), 1, signed=False)} · 胜率 <strong>{win_pct}</strong></li>
      <li>最好 {(st.get('best') or {}).get('ym', '')}
          <span class="pos">{pct((st.get('best') or {}).get('ret'), 2)}</span></li>
      <li>最差 {(st.get('worst') or {}).get('ym', '')}
          <span class="neg">{pct((st.get('worst') or {}).get('ret'), 2)}</span></li>
      <li>单月 &gt;+10%：<strong>{esc(st.get('n_gt_10pct'))}</strong>
          · &lt;−5%：<strong>{esc(st.get('n_lt_m5pct'))}</strong></li>
    </ul>"""

    if streaks:
        streak_lis = "".join(
            f"<li><strong>{esc(s.get('n'))}</strong> 个月：{esc(s.get('from'))} → {esc(s.get('to'))}</li>"
            for s in streaks
        )
        longest = streaks[0].get("n")
    else:
        streak_lis = "<li>无 ≥2 月连亏</li>"
        longest = "·"
    streaks_html = f"""
    <h3 style="margin-top:0">连亏与水下</h3>
    <ul>
      <li>最长连亏：<strong class="neg">{esc(longest)} 个月</strong></li>
      {streak_lis}
      <li>回撤&gt;10% 交易日占比 {pct(dd.get('under_10pct_days'), 1, signed=False)}</li>
      <li>回撤&gt;20% 交易日占比 {pct(dd.get('under_20pct_days'), 1, signed=False)}</li>
    </ul>"""

    # patterns: rewrite with numbered structure matching handcrafted
    has_bond = isinstance(rot, dict) and rot.get("type") not in (None, "", "none")
    timing_src = (timing or {}).get("source") or ""
    on = (timing or {}).get("on") or {}
    off = (timing or {}).get("off") or {}

    pattern_blocks = []
    # ① right skew from stats
    pattern_blocks.append(f"""
    <div class="panel">
      <h3 style="margin-top:0">① 右偏厚尾 / 月胜率</h3>
      <ul>
        <li>月胜率 <strong>{win_pct}</strong>，月均 {pct(st.get('mean'), 2)} vs 中位 {pct(st.get('median'), 2)}</li>
        <li>单月 &gt;+10% 共 {esc(st.get('n_gt_10pct'))} 次 →
            {'少数脉冲月决定财富路径' if (_f(st.get('win_rate')) or 0) < 0.55 else '胜率与弹性并存'}</li>
      </ul>
    </div>""")

    # ② drawdown cluster
    pattern_blocks.append(f"""
    <div class="panel">
      <h3 style="margin-top:0">② 回撤成段而非均匀噪声</h3>
      <ul>
        <li>全样本 MaxDD {pct(dd.get('maxdd'), 2)}：
            <code>{esc(dd.get('peak'))}</code> → <code>{esc(dd.get('trough'))}</code></li>
        <li>最长连亏月 <strong>{esc(longest)}</strong>；风险以季度～半年段出现</li>
        <li>水下&gt;10% 日占比 {pct(dd.get('under_10pct_days'), 1, signed=False)}</li>
      </ul>
    </div>""")

    # ③ timing
    if timing and not timing.get("error") and on:
        bond_note = (
            "债轮动在空仓期减损，救不了持股段漏放行。"
            if has_bond else
            "二值/空仓日并非干净现金 0（含降仓成本与成交滞后）。"
        )
        pattern_blocks.append(f"""
    <div class="panel">
      <h3 style="margin-top:0">③ 择时暴露分解
        <span class="tag">source={esc(timing_src)}</span></h3>
      <div class="scroll"><table>
        <thead><tr><th>状态</th><th>日占比</th><th>年化粗算</th><th>波动</th><th>Sharpe</th><th>日均</th></tr></thead>
        <tbody>
          <tr><td>持股 on</td><td>{pct(on.get('share_days'), 1, signed=False)}</td>
            <td class="pos">{pct(on.get('annual'), 1)}</td>
            <td>{pct(on.get('vol'), 1, signed=False)}</td>
            <td>{(_f(on.get('sharpe')) or 0):.2f}</td>
            <td>{pct(on.get('mean_day'), 3)}</td></tr>
          <tr><td>off</td><td>{pct(off.get('share_days'), 1, signed=False)}</td>
            <td class="{cls_ret(off.get('annual'))}">{pct(off.get('annual'), 1)}</td>
            <td>{pct(off.get('vol'), 1, signed=False)}</td>
            <td>{(_f(off.get('sharpe')) or 0):.2f}</td>
            <td>{pct(off.get('mean_day'), 3)}</td></tr>
        </tbody>
      </table></div>
      <p class="sub">{esc(bond_note)}
        月收益 vs 月均暴露 corr =
        {f"{_f(timing.get('month_ret_vs_exp_corr')):.3f}" if _f(timing.get('month_ret_vs_exp_corr')) is not None else '·'}
      </p>
    </div>""")
    else:
        pattern_blocks.append("""
    <div class="panel">
      <h3 style="margin-top:0">③ 择时暴露</h3>
      <p class="sub">本次未计算 timing 分解（--skip-timing 或数据不可用）。</p>
    </div>""")

    # ④ weak / fake / registry
    pattern_blocks.append("""
    <div class="panel">
      <h3 style="margin-top:0">④ 弱线索与假规律（禁止写死成交易规则）</h3>
      <ul>
        <li><strong>弱线索</strong>：季节性、大涨/大跌后次月惯性——样本短，只可作监控提示</li>
        <li><strong>假规律</strong>：为躲某一段历史再搜 MA/参数；用单年爆发证明永远有效；
            为达标改成本/截样本</li>
      </ul>
    </div>""")

    if meta.get("status"):
        pattern_blocks.append(f"""
    <div class="panel">
      <h3 style="margin-top:0">⑤ 身份与门禁快照（非本次重算）</h3>
      <ul>
        <li>status=<strong>{esc(meta.get('status'))}</strong>
            · production_blocked={esc(meta.get('production_blocked'))}
            （台账身份字段）</li>
        <li>台账 nine_gate 快照：passed_all={esc(ng.get('passed_all'))}
            · dsr_p={esc(ng.get('dsr_p'))}
            · run_date={esc(ng.get('run_date'))}
            —— 本页不重跑 9-Gate</li>
        <li>路径 KPI 见总览（live re-run）；好看 ≠ 可部署；入册只走 workflow</li>
      </ul>
    </div>""")
    patterns_html = "\n".join(pattern_blocks)

    # avoidance A-D as two-column pairs
    avoid_map = {a.get("tier"): a.get("items") or [] for a in avoidance}
    def avoid_panel(title, items):
        lis = "".join(f"<li>{esc(i)}</li>" for i in items)
        return f'<div class="panel"><h3 style="margin-top:0">{esc(title)}</h3><ul>{lis}</ul></div>'

    avoid_html = f"""
    <div class="two">
      {avoid_panel('A. 仓位与风险预算', avoid_map.get('A 仓位') or [])}
      {avoid_panel('B. 组合层', avoid_map.get('B 组合') or [])}
    </div>
    <div class="two">
      {avoid_panel('C. 研究（须过门禁）', avoid_map.get('C 研究(须过门禁)') or avoid_map.get('C 研究') or [])}
      {avoid_panel('D. 运营预期', avoid_map.get('D 运营') or [])}
    </div>"""

    # 9-Gate section：七章契约强制始终渲染（无台账数据也明示「未提供」）
    gate_rows = []
    has_any = any(v is not None for v in ng.values()) if isinstance(ng, dict) else bool(ng)
    if has_any:
        gate_rows.append(
            f"<tr><td>passed_all</td><td>{'FAIL' if ng.get('passed_all') is False else esc(ng.get('passed_all'))}</td>"
            f"<td class=\"note\">台账已存总闸快照（非本次重算）</td></tr>"
        )
        if ng.get("dsr_p") is not None:
            gate_rows.append(
                f"<tr><td>G4 DSR_p</td><td class=\"neg\">{esc(ng.get('dsr_p'))}</td>"
                f"<td class=\"note\">{esc(ng.get('gate4_verdict') or '')}</td></tr>"
            )
        if ng.get("cost_decay_rate") is not None:
            gate_rows.append(
                f"<tr><td>G6 cost_decay</td><td>{pct(ng.get('cost_decay_rate'), 1, signed=False)}</td>"
                f"<td class=\"note\">成本敏感（台账快照）</td></tr>"
            )
        if ng.get("run_date"):
            gate_rows.append(
                f"<tr><td>run_date</td><td>{esc(ng.get('run_date'))}</td>"
                f"<td class=\"note\">台账 nine_gate 写入日（非本路径报告日）</td></tr>"
            )
    if not gate_rows:
        gate_rows.append(
            "<tr><td colspan=\"3\" class=\"note\">"
            "台账无 nine_gate 快照字段；本页<strong>不</strong>重跑 9-Gate、不作入册裁决。"
            "</td></tr>"
        )
    nine_html = f"""
<section id="gates">
  <h2>{esc(HTML_SECTIONS[6])}</h2>
  <div class="callout">
    <strong>数据源分工（防误解）</strong><br/>
    · <strong>路径 KPI / 年/月表</strong> = 本次 <code>live_rerun</code> 收益重算，<strong>不是</strong>台账 metrics<br/>
    · <strong>下表 9-Gate</strong> = 台账里<strong>上次门禁跑的历史快照</strong>，本页<strong>故意不重跑</strong> 9-Gate
      （重跑门禁属 workflow / run_nine_gates，不在路径复盘职责内）<br/>
    · 快照与本次路径数字不一致时，以路径 KPI 描述路径、以快照描述「台账当时门禁结论」——
      <strong>两者都不等于可入册</strong>
  </div>
  <div class="panel scroll"><table>
    <thead><tr><th>项</th><th>台账快照值</th><th>备注</th></tr></thead>
    <tbody>{''.join(gate_rows)}</tbody>
  </table></div>
</section>"""
    # —— 经济逻辑章 ——
    econ = analysis.get("economic_logic") or {}
    mech_rows = []
    for row in econ.get("mechanism_map") or []:
        mech_rows.append(
            f"<tr><td class=\"note\">{esc(row.get('label') or row.get('layer'))}</td>"
            f"<td class=\"note\">{esc(row.get('stated'))}</td>"
            f"<td class=\"note\">{esc(row.get('implementation'))}</td></tr>"
        )
    if not mech_rows:
        mech_rows.append(
            "<tr><td colspan=\"3\" class=\"note\">无机制映射（台账字段缺失）</td></tr>"
        )
    tension_blocks = []
    for row in econ.get("path_vs_thesis") or []:
        st = row.get("status")
        st_tag = ""
        if st == "breached":
            st_tag = ' <span class="tag fail">边界压力</span>'
        elif st == "within":
            st_tag = ' <span class="tag">边界内</span>'
        tension_blocks.append(
            f"<div class=\"panel\" style=\"margin:10px 0\">"
            f"<h3 style=\"margin-top:0\">{esc(row.get('title'))}{st_tag}</h3>"
            f"<p><strong>观测：</strong>{esc(row.get('observation'))}</p>"
            f"<p class=\"sub\"><strong>含义（非裁决）：</strong>{esc(row.get('implication'))}</p>"
            f"</div>"
        )
    if not tension_blocks:
        tension_blocks.append(
            "<div class=\"panel\"><p class=\"sub\">无路径对照条目"
            "（常见于台账 hypothesis/regime 全空）。</p></div>"
        )
    oq = econ.get("open_questions") or []
    oq_html = (
        "<ul>" + "".join(f"<li>{esc(q)}</li>" for q in oq) + "</ul>"
        if oq else "<p class=\"sub\">（无）</p>"
    )
    style_s = econ.get("style_betas") or {}
    style_txt = (
        ", ".join(f"{k}={v}" for k, v in style_s.items()) if style_s else "·"
    )
    fb = econ.get("failure_boundaries") or {}
    fb_txt = (
        ", ".join(f"{k}={v}" for k, v in fb.items()) if fb else "·"
    )
    econ_html = f"""
  <div class="callout">{esc(econ.get('disclaimer') or ECONOMIC_LOGIC_DISCLAIMER)}</div>
  <div class="panel">
    <p><strong>family</strong>：<code>{esc(econ.get('family_name'))}</code>
       · source=<code>{esc(econ.get('source'))}</code>
       · capacity_m=<code>{esc(econ.get('capacity_m'))}</code></p>
    <p><strong>hypothesis（声明）</strong>：{esc(econ.get('hypothesis') or '（空）')}</p>
    <p><strong>regime（声明）</strong>：{esc(econ.get('regime') or '（空）')}</p>
    <p><strong>decay_signal（声明）</strong>：{esc(econ.get('decay_signal') or '（空）')}</p>
    <p><strong>failure_boundaries</strong>：{esc(fb_txt)}</p>
    <p><strong>style_betas</strong>：{esc(style_txt)}</p>
    <p><strong>version desc</strong>：{esc(econ.get('version_desc') or '·')}</p>
  </div>
  <h3>机制映射：声明 → 实现</h3>
  <div class="panel scroll"><table>
    <thead><tr><th>层</th><th>台账声明</th><th>本版实现 / 路径入口</th></tr></thead>
    <tbody>{''.join(mech_rows)}</tbody>
  </table></div>
  <h3>路径事实 vs 假设（机械对照，非证伪裁决）</h3>
  {''.join(tension_blocks)}
  <h3>开放问题（本报告回答不了的）</h3>
  <div class="panel">{oq_html}</div>
"""
    rot_show = rot if isinstance(rot, dict) else {}
    # registry audit block（总览下强制展示，防静默读台账）
    ra = analysis.get("registry_audit") or {}
    ra_rows_html = []
    for row in ra.get("rows") or []:
        st_r = row.get("status")
        mat = row.get("material")
        cls = "neg" if mat else ("" if st_r == "match" else "muted")
        delta = row.get("delta")
        if isinstance(delta, float):
            if row.get("key") in ("annual", "maxdd"):
                d_s = f"{delta:+.2%}"
            else:
                d_s = f"{delta:+.4g}"
        else:
            d_s = esc(delta)
        pv, rv = row.get("path_value"), row.get("registry_value")
        if row.get("key") in ("annual", "maxdd") and _is_number(pv):
            pv_s = pct(pv, 2)
        elif row.get("key") == "sharpe" and _is_number(pv):
            pv_s = f"{float(pv):.2f}"
        else:
            pv_s = esc(pv)
        if row.get("key") in ("annual", "maxdd") and _is_number(rv):
            rv_s = pct(rv, 2)
        elif row.get("key") == "sharpe" and _is_number(rv):
            rv_s = f"{float(rv):.2f}"
        else:
            rv_s = esc(rv) if rv is not None else "·"
        tag = ""
        if mat:
            tag = ' <span class="tag fail">实质偏差</span>'
        elif st_r == "match":
            tag = ' <span class="tag pass">一致</span>'
        elif st_r == "registry_missing":
            tag = ' <span class="tag">台账缺键</span>'
        ra_rows_html.append(
            f"<tr><td class=\"note\">{esc(row.get('key'))}{tag}</td>"
            f"<td class=\"{cls}\">{pv_s}</td><td>{rv_s}</td><td class=\"{cls}\">{d_s}</td></tr>"
        )
    if not ra_rows_html:
        ra_rows_html.append(
            "<tr><td colspan=\"4\" class=\"note\">无 registry_audit 行</td></tr>"
        )
    ra_notes = "".join(f"<li>{esc(n)}</li>" for n in (ra.get("notes") or []))
    prov = honesty.get("returns_provenance")
    live_tag = (
        '<span class="tag pass">live_rerun</span>'
        if honesty.get("is_live_rerun")
        else '<span class="tag fail">非 live re-run</span>'
    )
    mismatch_tag = (
        '<span class="tag fail">与台账有实质偏差</span>'
        if ra.get("has_material_mismatch")
        else '<span class="tag">与台账无实质偏差/缺键</span>'
    )
    registry_audit_html = f"""
  <div class="callout {' ' if not ra.get('has_material_mismatch') else ''}">
    <strong>收益来源与台账对拍（防静默误差）</strong>
    · provenance=<code>{esc(prov)}</code> {live_tag} {mismatch_tag}<br/>
    KPI <strong>只认本次路径重算</strong>；台账 metrics <strong>永不覆盖</strong> KPI。
    <div class="scroll" style="margin-top:10px"><table>
      <thead><tr><th>指标</th><th>本次路径</th><th>台账 metrics</th><th>Δ(路径−台账)</th></tr></thead>
      <tbody>{''.join(ra_rows_html)}</tbody>
    </table></div>
    <ul class="clean">{ra_notes or '<li class="sub">（无附加说明）</li>'}</ul>
    <p class="sub">{esc(ra.get('disclaimer') or REGISTRY_AUDIT_DISCLAIMER)}</p>
  </div>
"""

    meta_html = f"""
    <ul>
      <li>策略：<code>{esc(fam)}/{esc(ver)}</code></li>
      <li>引擎：<code>core.engine.BacktestEngine</code> + CostModel（买 0.225% / 卖 0.275% / 融资 6.5%）</li>
      <li>path：<code>{esc(meta.get('path'))}</code>
          · provenance=<code>{esc(honesty.get('returns_provenance'))}</code>
          · live_rerun=<code>{esc(honesty.get('is_live_rerun'))}</code>
          · leverage=<code>{esc(meta.get('leverage'))}</code>
          · timing=<code>{esc(meta.get('timing_type'))}</code></li>
      <li>spec_hash：<code>{esc(str(meta.get('spec_hash') or '')[:24])}…</code></li>
      <li>rotation：<code>{esc(rot_show)}</code></li>
      <li>台账 status={esc(meta.get('status'))}
          · production_blocked={esc(meta.get('production_blocked'))}</li>
      <li>本报告服务端渲染；有效性判断归门禁（R-LLM-001），入册归 workflow（R-WF-001）</li>
    </ul>"""

    sec_notes_num = "7" if gate_rows else "6"
    sec_avoid_num = "5"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(title)}</title>
<style>
:root {{
  --bg:#0f1419; --panel:#1a222d; --panel2:#232d3b; --border:#2e3a4a;
  --text:#e7ecf3; --muted:#8b9bb0; --green:#3ecf8e; --red:#f07178;
  --amber:#e6b450; --blue:#6cb6ff; --purple:#c792ea;
  --mono:"SF Mono",Menlo,Consolas,ui-monospace,monospace;
  --sans:"PingFang SC","Hiragino Sans GB","Noto Sans SC","Segoe UI",system-ui,sans-serif;
}}
*{{box-sizing:border-box}}
body{{margin:0;font-family:var(--sans);background:var(--bg);color:var(--text);line-height:1.65;font-size:15px}}
.wrap{{max-width:1100px;margin:0 auto;padding:32px 20px 80px}}
header{{border-bottom:1px solid var(--border);padding-bottom:28px;margin-bottom:32px}}
.badge{{display:inline-block;font-size:12px;letter-spacing:.04em;color:var(--amber);
  background:rgba(230,180,80,.12);border:1px solid rgba(230,180,80,.35);
  padding:3px 10px;border-radius:999px;margin-bottom:12px}}
h1{{font-size:1.75rem;font-weight:650;margin:0 0 10px;letter-spacing:-.02em}}
h2{{font-size:1.2rem;margin:40px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--border);color:#fff}}
h3{{font-size:1rem;margin:24px 0 10px;color:#d0d8e4}}
.sub{{color:var(--muted);font-size:.92rem;max-width:800px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px}}
.kpi .l{{font-size:12px;color:var(--muted);margin-bottom:4px}}
.kpi .v{{font-family:var(--mono);font-size:1.15rem;font-weight:600;word-break:break-all}}
.kpi .hint{{font-size:11px;color:var(--muted);margin-top:4px;line-height:1.35}}
.pos{{color:var(--green)}}.neg{{color:var(--red)}}.neu{{color:var(--blue)}}.muted{{color:var(--muted)}}
.panel{{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px 20px;margin:16px 0}}
.callout{{border-left:3px solid var(--amber);background:rgba(230,180,80,.08);padding:12px 16px;
  border-radius:0 8px 8px 0;margin:16px 0;color:#e8d9b0;font-size:.93rem}}
.callout.ok{{border-left-color:var(--green);background:rgba(62,207,142,.1);color:#b8e8d0}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 6px}}
th,td{{border:1px solid var(--border);padding:6px 8px;text-align:right;white-space:nowrap}}
th{{background:var(--panel2);color:var(--muted);font-weight:550;text-align:center}}
td:first-child,th:first-child{{text-align:center;font-weight:550;color:#c5d0de}}
td.y{{font-weight:600;background:var(--panel2)}}
td.note{{text-align:left;color:var(--muted);font-weight:400;white-space:normal}}
.scroll{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.tag{{display:inline-block;font-size:11px;padding:1px 7px;border-radius:4px;background:var(--panel2);
  color:var(--muted);border:1px solid var(--border);margin-right:4px}}
.tag.fail{{color:var(--red);border-color:rgba(240,113,120,.4);background:rgba(240,113,120,.12)}}
.tag.pass{{color:var(--green);border-color:rgba(62,207,142,.4);background:rgba(62,207,142,.12)}}
.bar-row{{display:grid;grid-template-columns:56px 1fr 72px;gap:8px;align-items:center;margin:5px 0;font-size:12px}}
.bar-track{{height:10px;background:var(--panel2);border-radius:5px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:5px;min-width:2px}}
.bar-fill.pos{{background:linear-gradient(90deg,#2a9d6a,var(--green))}}
.bar-fill.neg{{background:linear-gradient(90deg,#b84a52,var(--red))}}
ul.clean,ul{{margin:8px 0;padding-left:1.2em}} li{{margin:6px 0}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:780px){{.two{{grid-template-columns:1fr}}}}
nav.toc{{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 0}}
nav.toc a{{color:var(--blue);text-decoration:none;font-size:13px;background:var(--panel);
  border:1px solid var(--border);padding:4px 10px;border-radius:6px}}
nav.toc a:hover{{border-color:var(--blue)}}
footer{{margin-top:48px;padding-top:16px;border-top:1px solid var(--border);color:var(--muted);font-size:12px}}
code{{font-family:var(--mono);font-size:.88em;color:var(--purple)}}
.mono{{font-family:var(--mono)}}
.heat td{{font-family:var(--mono);font-size:11.5px;padding:5px 4px;text-align:center}}
</style>
</head>
<body>
<div class="wrap">

<header>
  <div class="badge">研究路径分析 · 非正式入册证据 · schema {esc(analysis.get("schema_version"))}</div>
  <h1>{esc(title)}</h1>
  <div class="honesty-banner">
    <strong>反自欺声明（不可关闭）</strong><br/>
    {esc(honesty.get("banner") or HONESTY_BANNER)}<br/>
    can_claim_valid=<code>false</code>
    · provenance=<code>{esc(honesty.get("returns_provenance"))}</code>
    · live_rerun=<code>{esc(honesty.get("is_live_rerun"))}</code>
    · path_kind=<code>{esc(honesty.get("path_kind"))}</code>
    · touches_holdout=<code>{esc(honesty.get("touches_holdout"))}</code>
    · holdout=<code>{esc(honesty.get("holdout_boundary"))}</code>
    · returns_sha256=<code>{esc(str(honesty.get("returns_sha256") or "")[:16])}…</code>
    · metrics_verified=<code>true</code>
    · registry_metrics_as_kpi=<code>false</code>
    {("<br/><span class=neg>"+esc(honesty.get("csv_warning"))+"</span>") if honesty.get("csv_warning") else ""}
    {("<br/>"+esc(honesty.get("holdout_note"))) if honesty.get("holdout_note") else ""}
  </div>
  <p class="sub">{esc(identity)}</p>
  <div style="margin-top:10px">{tags_html}</div>
  <nav class="toc">
    <a href="#summary">总览</a>
    <a href="#econ">经济逻辑</a>
    <a href="#yearly">年度</a>
    <a href="#monthly">月度</a>
    <a href="#patterns">规律</a>
    <a href="#avoid">规避</a>
    <a href="#gates">9-Gate</a>
    <a href="#notes">口径</a>
  </nav>
</header>

<section id="summary">
  <h2>{esc(HTML_SECTIONS[0])}</h2>
  <div class="grid">{kpi_html}</div>
  <div class="callout"><strong>一句话：</strong>{esc(one_liner)}</div>
  {registry_audit_html}
</section>

<section id="econ">
  <h2>{esc(HTML_SECTIONS[1])}</h2>
  {econ_html}
</section>

<section id="yearly">
  <h2>{esc(HTML_SECTIONS[2])}</h2>
  <div class="panel">
    <div class="scroll"><table>{yearly_table}</table></div>
    <p class="sub" style="margin-top:8px">*年化速率 = 日均 ×252；非整年日历收益。2026 若未满年见简评。</p>
  </div>
  <h3>年度收益条形图</h3>
  <div class="panel">{year_bars}</div>
  <h3>净值路径（起点 = 1）</h3>
  <div class="panel scroll"><table>{nav_table}</table></div>
</section>

<section id="monthly">
  <h2>{esc(HTML_SECTIONS[3])}</h2>
  <div class="panel"><div class="scroll"><table class="heat">{heat_table}</table></div></div>
  <div class="two">
    <div class="panel">{month_stats_html}</div>
    <div class="panel">{streaks_html}</div>
  </div>
  <div class="two">
    <div class="panel"><h3 style="margin-top:0">最差 10 个月</h3>
      {bar_rows(monthly.get('worst10') or [])}</div>
    <div class="panel"><h3 style="margin-top:0">最好 10 个月</h3>
      {bar_rows(monthly.get('best10') or [])}</div>
  </div>
</section>

<section id="patterns">
  <h2>{esc(HTML_SECTIONS[4])}</h2>
  {patterns_html}
</section>

<section id="avoid">
  <h2>{esc(HTML_SECTIONS[5])}</h2>
  <p class="sub">「避免」= 降低不可接受的路径风险，不是消灭回撤或保证每年正。</p>
  {avoid_html}
</section>

{nine_html}

<section id="notes">
  <h2>{esc(HTML_SECTIONS[7])}</h2>
  <div class="panel">{meta_html}</div>
</section>

<footer>
  生成：scripts/research/strategy_path_report.py（服务端渲染，与首版 illiquidity 报告同构）<br />
  不构成投资建议 · R-LLM-001 · 不写台账
</footer>

</div>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")


def _assert_html_contract(html: str, analysis: dict) -> None:
    """生成后二次校验：HTML 不得缺章、不得丢反自欺横幅、不得出现禁词。"""
    for sec in HTML_SECTIONS:
        if sec not in html:
            raise ValueError(f"HTML 缺冻结章节 h2: {sec!r}")
    if "can_claim_valid" not in html or "false" not in html.lower():
        raise ValueError("HTML 必须展示 can_claim_valid=false")
    if "honesty-banner" not in html and "反自欺声明" not in html:
        raise ValueError("HTML 缺反自欺横幅")
    # 去掉反自欺横幅再扫禁词，避免横幅自身措辞误伤
    scrub = re.sub(
        r'<div class="honesty-banner">[\s\S]*?</div>',
        "",
        html,
        count=1,
    )
    _scan_forbidden_claims(scrub, "html_document")
    for _key, label, *_rest in OVERVIEW_KPI_SPECS:
        if label not in html:
            raise ValueError(f"HTML 缺 KPI label: {label!r}")
    if str(analysis.get("schema_version")) not in html:
        raise ValueError("HTML 未展示 schema_version")
    if "registry_audit" not in html and "台账 metrics" not in html and "台账对拍" not in html:
        raise ValueError("HTML 必须展示与台账 metrics 对拍块")
    if "returns_provenance" not in html and "provenance=" not in html:
        raise ValueError("HTML 必须展示 returns_provenance")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="策略路径分析: 默认引擎重跑 + 年/月 HTML（禁止用台账 metrics 冒充 KPI）"
    )
    p.add_argument("--family", default=None, help="台账 family id")
    p.add_argument("--version", default=None, help="台账 version")
    p.add_argument("--start", default="2018-01-01")
    p.add_argument(
        "--returns-csv",
        default=None,
        help="陈旧日收益 CSV（默认禁用；须同时 --allow-stale-csv）",
    )
    p.add_argument(
        "--allow-stale-csv",
        action="store_true",
        help="显式允许用 --returns-csv（强制 stale 披露 + registry_audit）",
    )
    p.add_argument(
        "--persist-path-metrics",
        action="store_true",
        help=(
            "将本次 live re-run 的 full metrics 经 strategy_registry.attach_path_metrics "
            "写回台账（纠偏误导数字；要求 provenance=live_rerun；不改 status/不伪造 9-Gate）"
        ),
    )
    p.add_argument("--out-dir", default=None, help="默认 scratch/")
    p.add_argument("--skip-timing", action="store_true", help="跳过择时分解(更快)")
    p.add_argument("--no-html", action="store_true")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "scratch"
    out_dir.mkdir(parents=True, exist_ok=True)

    timing_series = None
    if args.returns_csv:
        if not args.allow_stale_csv:
            p.error(
                "--returns-csv 默认禁用：路径分析必须 live re-run 引擎/canonical runner，"
                "避免与首次审计/当前 data_lake 静默偏差。"
                "若确认接受陈旧序列，请同时传 --allow-stale-csv（报告将强制披露）。"
            )
        ret = load_returns_from_csv(Path(args.returns_csv))
        if args.family and args.version:
            meta = load_registry_meta(args.family, args.version)
        else:
            meta = {
                "family": args.family or "from_csv",
                "version": args.version or "custom",
                "registry_metrics": {},
            }
        meta["path"] = f"csv:{args.returns_csv}"
        meta["returns_provenance"] = "stale_csv"
    else:
        if not args.family or not args.version:
            p.error("需要 --family/--version（默认重跑）；或 --returns-csv + --allow-stale-csv")
        ret, meta, timing_series = run_strategy_returns(
            args.family, args.version, start=args.start
        )
        meta.setdefault("returns_provenance", "live_rerun")

    # save returns（本次序列落盘；下次分析默认仍应重跑，勿把此 CSV 当权威）
    stem = f"{meta.get('family', 'strat')}_{meta.get('version', 'v')}_path"
    ret_path = out_dir / f"{stem}_returns.csv"
    ret.to_csv(ret_path, header=["ret"])

    analysis = build_analysis(
        ret, meta, exposure=timing_series, skip_timing=args.skip_timing
    )

    json_path = out_dir / f"{stem}_analysis.json"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    html_path = None
    if not args.no_html:
        html_path = out_dir / f"{stem}_analysis.html"
        render_html(analysis, html_path)
        _assert_html_contract(html_path.read_text(encoding="utf-8"), analysis)

    # console summary（固定字段，禁止临时增删）
    full = analysis["full"]
    print("=" * 60)
    print(f"  schema={analysis.get('schema_version')}")
    print(f"  {meta.get('family')}/{meta.get('version')}  path={meta.get('path')}")
    print("=" * 60)
    print(f"  window: {full.get('start')} → {full.get('end')}  n={full.get('n')}")
    print(f"  annual={full.get('annual'):.2%}  maxdd={full.get('maxdd'):.2%}  "
          f"sharpe={full.get('sharpe'):.2f}  hit={full.get('hit')}")
    print(f"  MaxDD path: {analysis['drawdown'].get('peak')} → {analysis['drawdown'].get('trough')}")
    print("  yearly:")
    for r in analysis["yearly"]:
        print(f"    {r['year']}: {r['ret']:+.1%}  sharpe={r['sharpe']:.2f}  mdd={r['maxdd']:.1%}")
    st = analysis["monthly"]["stats"]
    print(f"  monthly win_rate={st['win_rate']:.1%}  best={st['best']}  worst={st['worst']}")
    ra = analysis.get("registry_audit") or {}
    print(f"  REGISTRY_AUDIT: material_mismatch={ra.get('has_material_mismatch')} "
          f"n_material={ra.get('n_material_mismatch')} n_mismatch={ra.get('n_mismatch')}")
    for row in ra.get("rows") or []:
        if row.get("status") != "match":
            print(f"    {row.get('key')}: path={row.get('path_value')} "
                  f"registry={row.get('registry_value')} Δ={row.get('delta')} "
                  f"[{row.get('status')}] material={row.get('material')}")
    print(f"  wrote {ret_path}")
    print(f"  wrote {json_path}")
    if html_path:
        print(f"  wrote {html_path}")
    h = analysis.get("honesty") or {}
    print("  HONESTY: can_claim_valid=false"
          f" provenance={h.get('returns_provenance')}"
          f" live_rerun={h.get('is_live_rerun')}"
          f" touches_holdout={h.get('touches_holdout')}"
          f" path_kind={h.get('path_kind')}"
          f" returns_sha256={(h.get('returns_sha256') or '')[:12]}…")
    print("  NOTE: KPI=本次重算; 默认不写台账; 路径事实 ≠ alpha 裁决")

    if args.persist_path_metrics:
        if h.get("returns_provenance") != "live_rerun" or not h.get("is_live_rerun"):
            raise SystemExit(
                "拒绝 --persist-path-metrics：仅 live_rerun 可写回台账 metrics "
                f"(provenance={h.get('returns_provenance')!r})"
            )
        fam = meta.get("family")
        ver = meta.get("version")
        if not fam or not ver or fam == "from_csv":
            raise SystemExit("拒绝 --persist-path-metrics：需要有效 --family/--version")
        import strategy_registry as SR

        full = analysis["full"]
        mat_keys = [
            r["key"] for r in (ra.get("rows") or [])
            if r.get("material")
        ]
        result = SR.attach_path_metrics(
            fam,
            ver,
            {
                "annual": full.get("annual"),
                "maxdd": full.get("maxdd"),
                "sharpe": full.get("sharpe"),
                "vol": full.get("vol"),
                "calmar": full.get("calmar"),
                "end_nav": full.get("end_nav"),
            },
            provenance="live_rerun",
            window=f"{full.get('start')} → {full.get('end')}",
            n=full.get("n"),
            returns_sha256=h.get("returns_sha256"),
            path=meta.get("path"),
            material_delta_keys=mat_keys,
            note=(
                "path_analysis live re-run 纠偏；旧 metrics 见 evidence.metrics_history；"
                "nine_gate 若标 stale 须另跑 run_nine_gates_all --persist"
            ),
        )
        print(f"  PERSIST metrics → registry {result['id']} "
              f"hit={result['hit']} material_keys={result['material_delta_keys']} "
              f"nine_gate_stale={result['nine_gate_marked_stale']}")
        print("  NOTE: 台账 metrics 已更新为事实；9-Gate 未重跑"
              + ("（已标 stale_vs_path_metrics）" if result["nine_gate_marked_stale"] else ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
