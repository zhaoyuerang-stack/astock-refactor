"""Trust-calibration read-model tests.

Run:
    cd factor_research && python3 tests/test_trust_calibration_view.py

诚实不变量(与实盘数据无关,永真):
- banner 永不比其权威输入更绿(fail-closed);
- holdout 只陈述事实不自判完整性;
- decay 缺 decay_status.json 时如实标未监控,不用论点字段冒充实时;
- 逐行裁决复用权威 decide_nine_gate,不重算。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.read.trust_calibration import get_trust_calibration


def test_view_shape_and_banner_domain():
    v = get_trust_calibration()
    assert v.banner_status in {"ready", "attention", "blocked", "neutral"}
    assert v.headline
    assert v.honesty
    payload = v.model_dump()
    assert {"banner_status", "headline", "detail", "signals", "strategies"}.issubset(payload)


def test_banner_never_greener_than_authoritative_inputs():
    v = get_trust_calibration()
    verdicts = {r.verdict for r in v.strategies}
    # 无任一版本 PASSED 时,首屏禁绿(fail-closed 防 over-trust)。
    if "PASSED" not in verdicts:
        assert v.banner_status != "ready"
    # 在册版本存在权威 FAILED 时,必须 blocked。
    registered_failed = any(
        r.verdict in {"FAILED", "RUN_FAILED"} and r.stage in {"在册", "ACTIVE", "active"}
        for r in v.strategies
    )
    if registered_failed:
        assert v.banner_status == "blocked"


def test_holdout_signal_states_facts_without_self_verdict():
    v = get_trust_calibration()
    holdout = next((s for s in v.signals if s.key == "holdout"), None)
    assert holdout is not None
    assert holdout.status == "info"                      # 不自判 intact/broken
    assert "check_holdout_compliance" in holdout.authority


def test_decay_is_honestly_unmonitored_when_source_absent():
    v = get_trust_calibration()
    decay = next((s for s in v.signals if s.key == "decay_watch"), None)
    assert decay is not None
    assert decay.authority == "reports/decay_status.json"
    if not (ROOT / "reports" / "decay_status.json").exists():
        assert "未监控" in decay.evidence


def test_overfit_signal_cites_authoritative_verdict():
    v = get_trust_calibration()
    overfit = next((s for s in v.signals if s.key == "overfit_guard"), None)
    assert overfit is not None
    assert "decide_nine_gate" in overfit.authority or "DSR" in overfit.evidence


if __name__ == "__main__":
    test_view_shape_and_banner_domain()
    test_banner_never_greener_than_authoritative_inputs()
    test_holdout_signal_states_facts_without_self_verdict()
    test_decay_is_honestly_unmonitored_when_source_absent()
    test_overfit_signal_cites_authoritative_verdict()
    print("trust calibration view tests passed")
