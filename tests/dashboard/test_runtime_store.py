import json

from xauex.app.runtime_store import RuntimeFileStore


def test_runtime_store_caches_unchanged_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"count": 1}), encoding="utf-8")
    store = RuntimeFileStore()
    assert store.load_json(path, {}) == {"count": 1}
    assert store.load_json(path, {}) == {"count": 1}
    assert store.stats.misses == 1
    assert store.stats.hits == 1


def test_runtime_store_invalidates_on_mtime_size_change(tmp_path):
    path = tmp_path / "state.json"
    store = RuntimeFileStore()
    path.write_text(json.dumps({"count": 1}), encoding="utf-8")
    assert store.load_json(path, {})["count"] == 1
    path.write_text(json.dumps({"count": 22}), encoding="utf-8")
    assert store.load_json(path, {})["count"] == 22
    assert store.stats.misses == 2


def test_runtime_store_missing_file_returns_default(tmp_path):
    store = RuntimeFileStore()
    assert store.load_json(tmp_path / "missing.json", {"default": True}) == {"default": True}
