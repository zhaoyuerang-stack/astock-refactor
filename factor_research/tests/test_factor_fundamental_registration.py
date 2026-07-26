"""factors/fundamental.py::gross_margin 接入 @register_factor 的回归 + 对抗测试。

背景:2026-07-13 daily-round-6 metasearch 重跑发现 roe/net_profit_yoy/bp_proxy/gross_margin
是信息地图当前唯一开放的空白区,但 gross_margin 此前完全未接入任何白名单
(未走三处手工接线,也未走 @register_factor)。daily-round-7 补齐注册,searchable=False
(尚无 probe 证据,不得声称已验证)。

对抗性验收(护栏 C):证明"坏东西真的被拒"(旧码/绕过必红),再证明真实仓库现状通过。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factors.registry import FACTOR_REGISTRY, discover, register_factor
from factory.autoresearch.registry import ALLOWED_FACTORS
from scripts.ci import check_factor_registry as guard

# ── 真实仓库状态:gross_margin 已注册且未提升为 searchable ──────────────────

def test_gross_margin_registered_via_register_factor():
    discover()
    assert "gross_margin" in FACTOR_REGISTRY
    rec = FACTOR_REGISTRY["gross_margin"]
    assert rec.definition.strip()
    assert rec.data == ("fundamental/gross_margin",)


def test_gross_margin_not_searchable_without_evidence():
    """尚无 probe 证据 → 不得进搜索白名单(词表入口证据门)。"""
    discover()
    rec = FACTOR_REGISTRY["gross_margin"]
    assert rec.searchable is False
    assert "gross_margin" not in ALLOWED_FACTORS


# ── 对抗:若未来想把 gross_margin 提升为 searchable,没证据必被拒 ───────────

def test_promoting_gross_margin_without_evidence_fails():
    with pytest.raises(ValueError, match="evidence"):
        register_factor("gross_margin", definition="毛利率", searchable=True)


# ── 对抗:若绕过 @register_factor 手工把 gross_margin 塞进 ALLOWED_FACTORS 字面量,
#   C1 守卫必须拒绝(新增手工接线条目)───────────────────────────────────────

def test_handwiring_gross_margin_manually_would_be_rejected():
    keys = set(guard.LEGACY_HANDWIRED["whitelist"]) | {"gross_margin"}
    errors = guard.check_handwired_frozen(
        {**{k: set(v) for k, v in guard.LEGACY_HANDWIRED.items()}, "whitelist": keys})
    assert any("gross_margin" in e and "@register_factor" in e for e in errors)


# ── 真实数据 happy path:gross_margin 输出截面 z-score,非全 NaN ────────────

def test_gross_margin_computes_on_real_lake_data():
    from factors.fundamental import gross_margin
    from lake.load_lake import load_raw_close

    close = load_raw_close(start="2023-01-01")
    out = gross_margin(close)
    assert isinstance(out, pd.DataFrame)
    assert out.shape == close.shape
    non_na_ratio = out.notna().mean().mean()
    assert non_na_ratio > 0.3, f"gross_margin 几乎全 NaN(非空率={non_na_ratio:.2%}),疑似 PIT 对齐断裂"
    # 截面 z-score:逐日横截面均值应接近 0(mad_clip+safe_zscore 的标准产出)
    row_means = out.mean(axis=1, skipna=True).dropna()
    assert row_means.abs().mean() < 0.5


# ── 真实仓库集成:守卫全绿(含新注册的 gross_margin) ─────────────────────

def test_live_repo_guard_passes_with_gross_margin_registered():
    discover()
    assert guard.check() == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
