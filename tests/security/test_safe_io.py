import stat

import pytest

from xauex.shared.safe_io import (
    FileTooLargeError,
    UnsafePathError,
    atomic_write_json,
    safe_load_json,
)


def test_atomic_write_json_uses_0600_and_round_trips(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"ok": True})
    assert safe_load_json(target) == {"ok": True}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_safe_load_json_rejects_large_file(tmp_path):
    target = tmp_path / "big.json"
    target.write_text('{"x":"' + ("a" * 100) + '"}', encoding="utf-8")
    with pytest.raises(FileTooLargeError):
        safe_load_json(target, max_bytes=10)


def test_refuses_symlink_read(tmp_path):
    real = tmp_path / "real.json"
    real.write_text('{"ok": true}', encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(UnsafePathError):
        safe_load_json(link)
