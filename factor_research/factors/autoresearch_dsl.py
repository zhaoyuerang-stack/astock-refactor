"""Runtime factor for controlled AutoResearch JSON AST candidates.

This is the only execution surface for AutoResearch DSL. Agents never write
Python factor code; validated ASTs are interpreted here and then passed through
the existing L0/L1/L2/L3 validation lines.
"""
from __future__ import annotations

import hashlib as _hashlib
import importlib
import json

# ADR-040: trade-path default ON. Synthetic unit tests pass feature_mask="off".
# Env override: ASTOCK_FEATURE_MASK=off only for hermetic tests / emergency.
import os as _os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from factor_store.store import write_panel_cache
from factors.tradable_mask import (  # noqa: E402
    FEATURE_MASK_NONE,
    cleanse,
    normalize_feature_mask_version,
    resolve_feature_tradable_for_panel,
)
from factors.utils import mad_clip, safe_zscore

_FEATURE_MASK_DEFAULT = _os.environ.get("ASTOCK_FEATURE_MASK", "on").strip().lower()

_FACTOR_CALLS = {
    # momentum/illiquidity 已迁 @register_factor;volume_ratio/volatility 仍手工
    # (搜索白名单无 probe 证据指针,fail-closed 不迁)。
    "volume_ratio": ("factors.momentum", "vol_ratio", {"window": "short"}),
    "volatility": ("factors.momentum", "volatility", {"window": "n"}),
    # roe/net_profit_yoy/revenue_yoy/bp_proxy/ep_proxy 与隔离岛/北向已迁
    # @register_factor,经文件末自动接线进入,不再手工列。
    # 与 factory.autoresearch.registry.ALLOWED_FACTORS 同步;退化/近重复项不进 DSL
    # (alpha_005/020/022/024/033/049 已移出,实现仍保留在 alpha101.py 供对照)。
    "alpha_001": ("factors.alpha101", "alpha_001", {}),
    "alpha_002": ("factors.alpha101", "alpha_002", {}),
    "alpha_003": ("factors.alpha101", "alpha_003", {}),
    "alpha_006": ("factors.alpha101", "alpha_006", {}),
    "alpha_008": ("factors.alpha101", "alpha_008", {}),
    "alpha_009": ("factors.alpha101", "alpha_009", {}),
    "alpha_012": ("factors.alpha101", "alpha_012", {}),
    "alpha_013": ("factors.alpha101", "alpha_013", {}),
    "alpha_014": ("factors.alpha101", "alpha_014", {}),
    "alpha_015": ("factors.alpha101", "alpha_015", {}),
    "alpha_017": ("factors.alpha101", "alpha_017", {}),
    "alpha_018": ("factors.alpha101", "alpha_018", {}),
    "alpha_019": ("factors.alpha101", "alpha_019", {}),
    "alpha_021": ("factors.alpha101", "alpha_021", {}),
    "alpha_023": ("factors.alpha101", "alpha_023", {}),
    "alpha_025": ("factors.alpha101", "alpha_025", {}),
    "alpha_028": ("factors.alpha101", "alpha_028", {}),
    "alpha_030": ("factors.alpha101", "alpha_030", {}),
    "alpha_032": ("factors.alpha101", "alpha_032", {}),
    "alpha_034": ("factors.alpha101", "alpha_034", {}),
    "alpha_037": ("factors.alpha101", "alpha_037", {}),
    "alpha_038": ("factors.alpha101", "alpha_038", {}),
    "alpha_040": ("factors.alpha101", "alpha_040", {}),
    "alpha_044": ("factors.alpha101", "alpha_044", {}),
    "alpha_050": ("factors.alpha101", "alpha_050", {}),
    "alpha_055": ("factors.alpha101", "alpha_055", {}),
}

# ── @register_factor 自动接线: factors 层登记的因子自动补进 DSL 调用表(手工优先)──
# 同层 factors→factors;新因子 @register_factor 后这里自动出现,无需手改。
from factors.registry import discover as _discover_factors  # noqa: E402

for _name, _rec in _discover_factors().items():
    _FACTOR_CALLS.setdefault(_name, (_rec.fn.__module__, _rec.fn.__name__, dict(_rec.arg_map)))


_BASE_FACTOR_MEM_CACHE: dict = {}
_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_DATA_PATHS = (
    _ROOT / "data_lake" / "price" / "daily_all.parquet",
    _ROOT / "data_lake" / "daily_all.parquet",
)


def _frame_signature(frame: pd.DataFrame | None) -> tuple:
    """Return a compact content signature for a panel.

    We hash the actual values plus index/column labels so different sub-panels with
    the same shape cannot collide just because their source file mtime is equal.
    """
    if frame is None:
        return (None,)
    if not isinstance(frame, pd.DataFrame):
        return ("invalid",)
    if frame.empty:
        return (0, None, None, 0, None, None, "empty")

    hashed = pd.util.hash_pandas_object(frame, index=True).to_numpy(dtype="uint64", copy=False)
    digest = _hashlib.sha256(hashed.tobytes()).hexdigest()[:16]
    return (
        len(frame.index),
        frame.index[0],
        frame.index[-1],
        len(frame.columns),
        frame.columns[0],
        frame.columns[-1],
        digest,
    )


def _data_signature(close: pd.DataFrame, volume: pd.DataFrame | None = None) -> tuple:
    return (_frame_signature(close), _frame_signature(volume))


def _source_data_mtime() -> int:
    for source_path in _SOURCE_DATA_PATHS:
        try:
            if source_path.exists():
                return int(source_path.stat().st_mtime)
        except OSError:
            continue
    return 0


def _factor_source_hash(name: str) -> str:
    """摘要因子实现源码;改公式后缓存 key 失效,防静默复用旧面板。

    解析失败返回 ``nosrc``(仍带上 name,不阻断计算)。
    """
    try:
        import inspect

        if name not in _FACTOR_CALLS:
            return "unknown"
        module_name, fn_name, _ = _FACTOR_CALLS[name]
        fn = getattr(importlib.import_module(module_name), fn_name)
        src = inspect.getsource(fn)
        return _hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return "nosrc"


def _feature_mask_suffix(feature_mask_version: str | None) -> str:
    """Empty for dirty default (backward-compatible cache names); else ``_fm{version}``."""
    ver = normalize_feature_mask_version(feature_mask_version)
    if ver == FEATURE_MASK_NONE:
        return ""
    # Keep filenames path-safe (version tokens are already slug-like).
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in ver)
    return f"_fm{safe}"


def _get_cache_path(
    name: str,
    params: dict,
    data_signature: tuple | None = None,
    *,
    source_hash: str | None = None,
    feature_mask_version: str | None = None,
) -> Path:
    """磁盘缓存路径。panels/ 是 **cache 区**(非资产区).

    key = name+params+data+mtime+源码摘要[+feature_mask_version when clean path].
    Dirty default omits ``_fm*`` so existing caches keep hitting.
    资产区在 data_lake/factor_store/manifests|scores;本目录可 GC,不得当证据引用。
    """
    mtime = _source_data_mtime()
    src = source_hash if source_hash is not None else _factor_source_hash(name)
    sig_suffix = ""
    if data_signature is not None:
        sig_suffix = f"_sig{_hashlib.sha256(repr(data_signature).encode('utf-8')).hexdigest()[:16]}"
    src_suffix = f"_src{src}"
    fm_suffix = _feature_mask_suffix(feature_mask_version)
    if not params:
        filename = f"{name}{sig_suffix}{src_suffix}{fm_suffix}_mt{mtime}.parquet"
    else:
        param_str = "_".join(f"{k}_{v}" for k, v in sorted(params.items()))
        filename = f"{name}_{param_str}{sig_suffix}{src_suffix}{fm_suffix}_mt{mtime}.parquet"
    base_dir = _ROOT / "data_lake" / "factor_store" / "panels"
    return base_dir / filename


def _resolve_feature_mask(
    close: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    feature_mask_version: str | None = None,
    *,
    feature_mask: str | None = None,
) -> tuple[pd.DataFrame | None, str]:
    """Resolve feature-tradable for DSL (ADR-040 default-on for trade paths).

    ``feature_mask``: ``on`` | ``off`` | ``auto``(follow env ASTOCK_FEATURE_MASK).
    When on: context → lake stk_limit → volume floor (never silent dirty rolling).
    """
    mode = (feature_mask or _FEATURE_MASK_DEFAULT or "on").strip().lower()
    if mode in {"off", "0", "false", "no", "dirty"}:
        # Caller already prepared panels (or explicit dirty). Keep version for
        # cache labeling only — do not cleanse again.
        if feature_mask_version is not None:
            return None, normalize_feature_mask_version(feature_mask_version)
        return None, FEATURE_MASK_NONE
    tradable, ver = resolve_feature_tradable_for_panel(close, volume, prefer_lake=True)
    if feature_mask_version is not None:
        forced = normalize_feature_mask_version(feature_mask_version)
        if forced != FEATURE_MASK_NONE and tradable is not None:
            ver = forced
    return tradable, ver


def _call_factor(
    name: str,
    close: pd.DataFrame,
    volume: pd.DataFrame | None,
    params: dict,
    cache_mode: str = "disk",
    *,
    feature_mask_version: str | None = None,
    feature_mask: str | None = None,
) -> pd.DataFrame:
    mtime = _source_data_mtime()
    tradable, fm_ver = _resolve_feature_mask(
        close, volume, feature_mask_version, feature_mask=feature_mask
    )
    # When feature mask is active, NaN non-tradable cells *before* rolling factors.
    close_use = cleanse(close, tradable) if tradable is not None else close
    volume_use = cleanse(volume, tradable) if (tradable is not None and volume is not None) else volume

    data_sig = _data_signature(close_use, volume_use)
    src_hash = _factor_source_hash(name)

    # 1. Check in-memory cache first
    param_key = json.dumps(params, sort_keys=True)
    mem_key = (name, param_key, data_sig, mtime, src_hash, fm_ver)
    if mem_key in _BASE_FACTOR_MEM_CACHE:
        return _BASE_FACTOR_MEM_CACHE[mem_key]

    # 2. Check local parquet cache unless caller requested pure in-memory mode.
    # If cache_mode is 'memory', bypass disk reading completely (Task 1652 test requirement)
    if cache_mode != "memory":
        cache_path = _get_cache_path(
            name,
            params,
            data_signature=data_sig,
            source_hash=src_hash,
            feature_mask_version=fm_ver,
        )
        if cache_path.exists():
            try:
                cached = pd.read_parquet(cache_path)
                if cached.index.intersection(close_use.index).empty or cached.columns.intersection(close_use.columns).empty:
                    raise ValueError("cached factor panel does not overlap active panel")
                # Reindex to ensure strict compatibility with the active close index and columns
                df = cached.reindex(index=close_use.index, columns=close_use.columns)
                if not df.notna().to_numpy().any():
                    raise ValueError("cached factor panel has no valid values for active panel")
                _BASE_FACTOR_MEM_CACHE[mem_key] = df
                return df
            except Exception:
                pass

    # 3. Compute factor if not cached
    if name not in _FACTOR_CALLS:
        raise ValueError(f"unknown AutoResearch DSL factor: {name}")
    module_name, fn_name, param_map = _FACTOR_CALLS[name]
    fn = getattr(importlib.import_module(module_name), fn_name)
    mapped = {target: params[source] for source, target in param_map.items() if source in params}

    if name.startswith("alpha_"):
        out = fn(close_use, volume_use, **mapped)
    elif name in {"volume_ratio"}:
        if volume_use is None:
            raise ValueError(f"{name} requires volume")
        if "long" not in mapped:
            mapped["long"] = max(int(mapped.get("short", 5)) * 4, int(mapped.get("short", 5)) + 1)
        out = fn(volume_use, **mapped)
    elif name == "illiquidity":
        # Canonical Amihud uses amount; DSL surface proxies amount≈volume×close
        # inside factors.momentum.illiquidity (aligned with AmihudIlliq).
        if volume_use is None:
            raise ValueError("illiquidity requires volume (amount proxy = volume×close)")
        out = fn(close_use, volume_use, **mapped)
    else:
        out = fn(close_use, **mapped)

    # 4. Save to parquet cache via factor_store scoped writer (ADR-038 决策三)
    # cache 区;改源码后 source_hash 变,旧文件可 GC。路径/key 仍由 _get_cache_path 决定。
    if cache_mode != "memory":
        try:
            cache_path = _get_cache_path(
                name,
                params,
                data_signature=data_sig,
                source_hash=src_hash,
                feature_mask_version=fm_ver,
            )
            write_panel_cache(out, cache_path)
        except Exception:
            pass

    _BASE_FACTOR_MEM_CACHE[mem_key] = out
    return out

def _apply_transform(values: pd.DataFrame, op: str, close: pd.DataFrame | None = None) -> pd.DataFrame:
    if op == "mad_clip":
        return mad_clip(values)
    if op == "zscore":
        return safe_zscore(values)
    if op == "rank":
        return values.rank(axis=1, pct=True)
    if op == "neg":
        return -values
    if op == "log1p":
        return np.log1p(values.clip(lower=-0.999999))
    if op == "rolling_mean":
        return values.rolling(20).mean()
    if op == "rolling_std":
        return values.rolling(20).std()
    if op == "regime_gate":
        if close is None:
            raise ValueError("regime_gate requires close price panel")
        mkt_ret = close.pct_change(fill_method=None).fillna(0.0).mean(axis=1)
        mkt_idx = (1 + mkt_ret).cumprod()
        mkt_ma = mkt_idx.rolling(16).mean()
        bull_mask = mkt_idx > mkt_ma

        out = values.copy()
        common_idx = out.index.intersection(bull_mask.index)
        bear_dates = common_idx[~bull_mask.loc[common_idx]]
        out.loc[bear_dates] = 0.0
        return out
    if op == "fundamental_veto":
        if close is None:
            raise ValueError("fundamental_veto requires close price panel")
        from factors.fundamental import _align_to_close, _load_fundamental_cache
        fund = _load_fundamental_cache()
        roe_panel = _align_to_close(fund["roe"], close)
        npy_panel = _align_to_close(fund["net_profit_yoy"], close)
        # Veto stocks with negative/low ROE (<= 0.0%) or crashing earnings growth (< -30%)
        veto_mask = (roe_panel <= 0.0) | (npy_panel < -30.0)
        out = values.copy()
        out[veto_mask] = np.nan
        return out
    if op == "salience_veto":
        if close is None:
            raise ValueError("salience_veto requires close price panel")
        # Overheating proxy: 5-day return volatility divided by 60-day return volatility
        ret = close.pct_change(fill_method=None)
        vol_5d = ret.rolling(5).std()
        vol_60d = ret.rolling(60).std()
        vol_ratio = vol_5d / (vol_60d + 1e-10)
        # Veto top 5% most volatile stocks in the cross-section
        overheated_mask = vol_ratio.rank(axis=1, pct=True) > 0.95
        out = values.copy()
        out[overheated_mask] = np.nan
        return out
    if op == "error_feedback_correction":
        if close is None:
            raise ValueError("error_feedback_correction requires close price panel")
        # 1. Calculate stock returns
        ret = close.pct_change(fill_method=None)
        # 2. Identify factor's historical signals (we shift values by 1 day to align with holding period)
        # For simplicity, we assume values > 0 are buy signals
        signal_held = (values.shift(1) > 0).astype(float)
        # 3. Calculate realized losses: signal_held * min(0, return)
        realized_loss = signal_held * ret.clip(upper=0.0)
        # 4. Accumulate rolling losses over the past 20 days (the rebalance window)
        rolling_loss = realized_loss.rolling(20, min_periods=1).sum().fillna(0.0)
        # 5. Correct the factor values: subtract/penalize based on rolling realized loss (feedback gain = 2.0)
        corrected = values + 2.0 * rolling_loss
        return corrected
    raise ValueError(f"unknown AutoResearch DSL transform: {op}")


# 因子面板搜索内 memo:L0(算 IC)与岛屿适应度(novelty/corr/turnover)对同一
# 候选各算一次全市场面板(5207×2000),memo 让二者共享一次计算(~2× 加速)。
# key 含**带符号** ast 哈希(F 与 -F 面板相反,绝不可共享)+ id(close)
# (防数据湖同日重写的陈旧命中:重载 = 新对象 = 新 id)。搜索起点 clear。
_PANEL_CACHE: dict = {}
_PANEL_ORDER: list = []
_PANEL_CACHE_MAX = 6


def clear_factor_cache() -> None:
    """搜索起点清空面板 memo(隔离不同 run / 不同数据口径)。"""
    _PANEL_CACHE.clear()
    _PANEL_ORDER.clear()
    _BASE_FACTOR_MEM_CACHE.clear()


def _panel_key(ast: dict, close, volume, *, feature_mask_version: str = FEATURE_MASK_NONE):
    body = {k: v for k, v in ast.items() if k != "thesis"}  # direction 参与=带符号
    h = _hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    fm = normalize_feature_mask_version(feature_mask_version)
    return (h, _data_signature(close, volume), fm)


def compute_dsl_factor(
    close: pd.DataFrame,
    volume: pd.DataFrame | None = None,
    *,
    ast: dict[str, Any],
    cache_mode: str = "disk",
    feature_mask_version: str | None = None,
    feature_mask: str | None = None,
) -> pd.DataFrame:
    """Compute a validated AutoResearch linear_combo AST(搜索内 memo 化)。

    ADR-040: default feature_mask=on (env ASTOCK_FEATURE_MASK). Non-tradable
    closes are cleansed before rolling factors and close-based transforms so
    search rankings cannot silently use limit prices that will not fill live.
    Pass ``feature_mask=\"off\"`` only for hermetic synthetic tests.
    """
    if ast.get("type") != "linear_combo":
        raise ValueError("unsupported AutoResearch AST type: " + str(ast.get("type")))

    tradable, fm_ver = _resolve_feature_mask(
        close, volume, feature_mask_version, feature_mask=feature_mask
    )
    close_work = cleanse(close, tradable) if tradable is not None else close
    volume_work = (
        cleanse(volume, tradable) if (tradable is not None and volume is not None) else volume
    )

    key = _panel_key(ast, close_work, volume_work, feature_mask_version=fm_ver)
    cached = _PANEL_CACHE.get(key)
    if cached is not None:
        return cached

    out = pd.DataFrame(0.0, index=close_work.index, columns=close_work.columns)
    for term in ast.get("terms", []):
        values = _call_factor(
            term["factor"],
            close_work,
            volume_work,
            term.get("params", {}),
            cache_mode=cache_mode,
            feature_mask_version=fm_ver if tradable is not None else feature_mask_version,
            # already cleansed above; avoid second lake resolve inside term calls
            feature_mask="off" if tradable is not None else (feature_mask or "on"),
        )
        values = values.reindex(index=close_work.index, columns=close_work.columns)
        for op in term.get("transforms", []):
            # close_work already cleansed when mask active → regime/salience transforms
            # do not re-ingest limit closes.
            values = _apply_transform(values, op, close=close_work)
        out = out.add(float(term.get("weight", 1.0)) * values, fill_value=0.0)

    if ast.get("direction") == "negative":
        out = -out
    out = out.replace([np.inf, -np.inf], np.nan)

    # Apply root-level AST transforms on the combined out panel
    for op in ast.get("transforms", []):
        out = _apply_transform(out, op, close=close)

    # 1. Apply Size & Industry Style Neutralization (事前特征中性化)
    neutralize_opts = ast.get("neutralize", [])
    if neutralize_opts:
        from lake.load_lake import load_daily_basic_panel, load_fundamental_panel
        neut_size = "size" in neutralize_opts
        neut_industry = "industry" in neutralize_opts

        log_size = None
        if neut_size:
            db_basic = load_daily_basic_panel(close.index, fields=["total_mv"])
            total_mv = db_basic.get("total_mv", pd.DataFrame())
            if total_mv.empty:
                # Fallback to rolling amount
                total_mv = close.mul(volume, fill_value=0.0).rolling(60).mean()
            log_size = np.log(total_mv.replace(0, np.nan))

        industry = None
        if neut_industry:
            db_fund = load_fundamental_panel(close.index, fields=["industry"])
            industry = db_fund.get("industry", pd.DataFrame())

        # Pre-align variables to close index/columns to compile numpy matrices
        log_size_aligned = log_size.reindex(index=close.index, columns=close.columns) if log_size is not None else None
        industry_aligned = industry.reindex(index=close.index, columns=close.columns).fillna("Unknown") if industry is not None else None

        # Prepare arrays
        out_arr = out.values.copy()
        log_size_arr = log_size_aligned.values if log_size_aligned is not None else None

        ind_dummies_arrs = []
        if industry_aligned is not None:
            unique_industries = sorted(list(set(np.unique(industry_aligned.values))))
            if "Unknown" in unique_industries:
                unique_industries.remove("Unknown")
            for ind_name in unique_industries:
                dummy_panel = (industry_aligned == ind_name).astype(float)
                ind_dummies_arrs.append(dummy_panel.values)

        # Fast cross-sectional regression in pure numpy
        for i in range(len(close.index)):
            y = out_arr[i]
            valid_mask = ~np.isnan(y)
            if log_size_arr is not None:
                valid_mask &= ~np.isnan(log_size_arr[i])

            n_valid = np.sum(valid_mask)
            if n_valid < 30:
                continue

            X_cols = [np.ones(n_valid)]
            if log_size_arr is not None:
                X_cols.append(log_size_arr[i, valid_mask])
            for dummy_arr in ind_dummies_arrs:
                X_cols.append(dummy_arr[i, valid_mask])

            X_clean = np.column_stack(X_cols)
            y_clean = y[valid_mask]

            try:
                coef, _, _, _ = np.linalg.lstsq(X_clean, y_clean, rcond=None)
                resids = y_clean - X_clean @ coef
                out_arr[i, valid_mask] = resids
            except Exception:
                continue

        # Re-construct DataFrame and Z-score to restore scaling
        out = pd.DataFrame(out_arr, index=close.index, columns=close.columns)
        out = out.replace([np.inf, -np.inf], np.nan)
        out = (out.sub(out.mean(axis=1), axis=0)).div(out.std(axis=1) + 1e-10, axis=0)

    _PANEL_CACHE[key] = out
    _PANEL_ORDER.append(key)
    if len(_PANEL_ORDER) > _PANEL_CACHE_MAX:
        _PANEL_CACHE.pop(_PANEL_ORDER.pop(0), None)
    return out
