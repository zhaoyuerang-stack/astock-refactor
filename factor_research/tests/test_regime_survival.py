"""WS6 item2 对抗测试:跨 regime 生存适应度(min |ICIR|)+ 调度默认开启。

护栏 C 四类中的三类:
① 门真杀假——晴天因子(单段行情堆出的高 ICIR)被 min 聚合打下,全样本口径会放行;
② 修复真传播——walk-forward 截断下 2024 两段无数据:旧实现把"无数据"混同 ICIR=0,
   min 恒为 0、fitness 对 ICIR 失明;新实现(None 段跳过)必须给出 |段3|。
   旧代码在 test_truncated_panel_missing_segments_dont_zero_edge 上必然失败;
③ 接线真生效——AST 断言调度对 run_autoresearch_walk_forward 传 regime_aware=True
   (旧调度代码无此参数 → 测试红)。
"""
import ast as pyast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory.autoresearch.islands import (  # noqa: E402
    REGIME_SEGMENTS,
    _regime_survival_edge,
)


def test_sunny_day_factor_is_killed_by_min_aggregation():
    """晴天因子:牛市段 ICIR=0.9、踩踏段仅 0.05 → 生存 edge=0.05(被打下)。
    对照:全样本口径(fallback=0.9)会给 0.9 放行——这正是 min 聚合存在的意义。"""
    sunny = (0.05, 0.9, 0.9)
    assert _regime_survival_edge(sunny, fallback_edge=0.9) == 0.05
    # 全天候因子(各段都稳)不受惩罚
    all_weather = (0.4, 0.5, 0.45)
    assert _regime_survival_edge(all_weather, fallback_edge=0.5) == 0.4


def test_truncated_panel_missing_segments_dont_zero_edge():
    """walk-forward 截断(调度 cutoff≈2023 年末):2024 两段无数据=None。
    旧实现(None 混同 0.0)→ min=0,所有候选 edge 归零、fitness 失明;
    新实现只聚合可用段 → edge=|段3|。此测试在旧代码上必然失败。"""
    truncated = (None, None, 0.6)
    assert _regime_survival_edge(truncated, fallback_edge=0.6) == 0.6
    # 混合:一段可用一段真 0(有数据无区分度)→ 真 0 参与 min
    assert _regime_survival_edge((None, 0.0, 0.6), fallback_edge=0.6) == 0.0


def test_all_segments_missing_falls_back_to_full_sample_edge():
    """全段不可用(极端截断/因子算不出段内值)→ 退回全样本 edge,搜索不瞎。"""
    assert _regime_survival_edge((None, None, None), fallback_edge=0.37) == 0.37


def test_regime_segments_are_all_before_holdout_boundary():
    """三段日期必须全部 < holdout boundary(2025-01-01),min 聚合绝不偷看金库。"""
    for name, _, end in REGIME_SEGMENTS:
        assert end < "2025-01-01", f"{name} 段 {end} 越过 holdout 金库边界"


def _call_kwargs(source: str, func_name: str) -> dict:
    """从源码提取对 func_name 的调用的关键字参数(常量值)。"""
    tree = pyast.parse(source)
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Call):
            f = node.func
            name = f.id if isinstance(f, pyast.Name) else getattr(f, "attr", None)
            if name == func_name:
                out = {}
                for kw in node.keywords:
                    if kw.arg and isinstance(kw.value, pyast.Constant):
                        out[kw.arg] = kw.value.value
                return out
    return {}


def test_scheduled_search_passes_regime_aware_true():
    """接线对抗:调度对 run_autoresearch_walk_forward 必须显式传 regime_aware=True。
    旧调度代码(无此参数)在此必红——任何回退(删参/改 False)同样被抓。"""
    src = (ROOT / "scripts" / "ops" / "scheduled_factor_search.py").read_text(encoding="utf-8")
    kwargs = _call_kwargs(src, "run_autoresearch_walk_forward")
    assert kwargs.get("regime_aware") is True, "调度未开跨 regime 生存(regime_aware=True)"


def test_walk_forward_service_exposes_and_forwards_regime_aware():
    """services 层签名必须有 regime_aware 并转发给 walk-forward 引擎(引擎经 **island_kw 透传)。"""
    import inspect

    from services.actions.autoresearch_search import run_autoresearch_walk_forward
    sig = inspect.signature(run_autoresearch_walk_forward)
    assert "regime_aware" in sig.parameters
    assert sig.parameters["regime_aware"].default is False  # 只有调度显式开;其它调用方行为不变
    src = (ROOT / "services" / "actions" / "autoresearch_search.py").read_text(encoding="utf-8")
    assert "regime_aware" in {kw.arg for node in pyast.walk(pyast.parse(src))
                              if isinstance(node, pyast.Call)
                              and getattr(node.func, "id", getattr(node.func, "attr", None)) == "run_walk_forward_search"
                              for kw in node.keywords}, "services 未把 regime_aware 转发给 walk-forward 引擎"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
