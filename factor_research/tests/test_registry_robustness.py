"""strategy_registry 台账健壮性回归测试(原子写/legacy 防清空/版本自然序)。

对抗性说明(每项在旧代码上必失败):
  · legacy list 旧代码静默返回空 → 后续 _save 清空台账;新代码必须抛 ValueError。
  · _save 旧代码 truncate-then-write;新代码必须经临时文件 os.replace 原子替换。
  · 版本排序旧代码字符串序('v1.10' < 'v1.2')。
全部测试用 tmp_path 假台账,绝不触碰真实 strategy_versions.json。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import strategy_registry as sr  # noqa: E402


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    fp = tmp_path / "strategy_versions.json"
    monkeypatch.setattr(sr, "REGISTRY", fp)
    return fp


def test_load_legacy_list_raises_instead_of_wiping(tmp_registry):
    tmp_registry.write_text(json.dumps([{"version": "v1.0"}]))
    with pytest.raises(ValueError):
        sr._load()
    # 台账文件必须原封未动(未被清空覆盖)
    assert json.loads(tmp_registry.read_text()) == [{"version": "v1.0"}]


def test_save_is_atomic_via_replace(tmp_registry, monkeypatch):
    import os as _os
    calls = []
    real_replace = _os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(sr.os, "replace", spy)
    sr._save({"families": []})
    assert calls, "_save 必须经 os.replace 原子替换,不得直接 write_text 目标文件"
    assert json.loads(tmp_registry.read_text()) == {"families": []}
    # 临时文件不残留
    leftovers = list(tmp_registry.parent.glob("*.tmp.*"))
    assert not leftovers, f"临时文件残留: {leftovers}"


def test_version_natural_sort(tmp_registry):
    sr.register_family("fam-x", "测试族")
    for v in ["v1.2", "v1.10", "v1.1"]:
        sr.register("fam-x", v, "d", {}, {"source": "data_lake"},
                    {"annual": 0.01, "maxdd": -0.05}, status="候选")
    data = json.loads(tmp_registry.read_text())
    fam = next(f for f in data["families"] if f["id"] == "fam-x")
    assert [x["version"] for x in fam["versions"]] == ["v1.1", "v1.2", "v1.10"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
