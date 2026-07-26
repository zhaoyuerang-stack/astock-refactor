"""数据 vintage 指纹 + 漂移检测(每日更新后盖章)。

Run:
    cd factor_research && python3 tests/test_data_vintage.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lake.fingerprint import panel_fingerprint  # noqa: E402
from scripts.data.update_lake import _is_drift  # noqa: E402


def _panel(seed=0):
    idx = pd.bdate_range("2024-01-01", periods=80)
    rng = np.random.default_rng(seed)
    return pd.DataFrame(rng.standard_normal((80, 120)), index=idx,
                        columns=[f"{600000+i}" for i in range(120)])


def test_fingerprint_deterministic_and_sensitive():
    p = _panel(0)
    assert panel_fingerprint(p) == panel_fingerprint(p.copy())   # 同数据→同指纹
    q = p.copy(); q.iloc[-1, 0] += 1e-6
    assert panel_fingerprint(p) != panel_fingerprint(q)          # 一格变→指纹变
    print("✅ 指纹确定且对单格变动敏感")


def test_drift_detection():
    prev = {"last_date": "2026-06-12", "fingerprint": "abc123"}
    # 末日不变 + 指纹变 = 同日改写 = 漂移
    assert _is_drift(prev, "2026-06-12", "def456") is True
    # 末日不变 + 指纹同 = 无漂移
    assert _is_drift(prev, "2026-06-12", "abc123") is False
    # 末日推进(正常增量)= 非漂移
    assert _is_drift(prev, "2026-06-13", "def456") is False
    # 无历史 = 非漂移
    assert _is_drift(None, "2026-06-12", "def456") is False
    print("✅ 漂移检测:同日改写=报警,正常增量/首次=不报")


if __name__ == "__main__":
    test_fingerprint_deterministic_and_sensitive()
    test_drift_detection()
    print("\n🎉 data vintage tests passed!")
