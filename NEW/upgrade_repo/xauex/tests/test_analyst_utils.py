"""Tests for analyst._utils shared utilities."""
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from analyst._utils import (
    read_json_file,
    is_state_stale,
    atomic_write_json,
    append_to_json_list,
    load_cursor,
    save_cursor,
    find_claude,
    call_claude,
)


def test_read_json_file_missing_returns_none(tmp_path):
    result = read_json_file(str(tmp_path / "missing.json"))
    assert result is None


def test_read_json_file_returns_parsed(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"key": "value"}')
    assert read_json_file(str(p)) == {"key": "value"}


def test_read_json_file_corrupted_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json{{{")
    assert read_json_file(str(p)) is None


def test_is_state_stale_missing():
    assert is_state_stale(None, max_age_seconds=60) is True


def test_is_state_stale_fresh():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"meta": {"last_updated_utc": now}}
    assert is_state_stale(state, max_age_seconds=60) is False


def test_is_state_stale_old():
    state = {"meta": {"last_updated_utc": "2020-01-01T00:00:00Z"}}
    assert is_state_stale(state, max_age_seconds=60) is True


def test_atomic_write_json(tmp_path):
    p = str(tmp_path / "out.json")
    atomic_write_json(p, {"hello": "world"})
    assert json.loads(open(p).read()) == {"hello": "world"}


def test_atomic_write_json_is_atomic(tmp_path):
    """Write should replace file atomically (no partial read window)."""
    p = str(tmp_path / "out.json")
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(open(p).read()) == {"v": 2}


def test_append_to_json_list_new_file(tmp_path):
    p = str(tmp_path / "list.json")
    append_to_json_list(p, {"a": 1})
    assert json.loads(open(p).read()) == [{"a": 1}]


def test_append_to_json_list_existing(tmp_path):
    p = str(tmp_path / "list.json")
    append_to_json_list(p, {"a": 1})
    append_to_json_list(p, {"b": 2})
    assert json.loads(open(p).read()) == [{"a": 1}, {"b": 2}]


def test_load_cursor_missing_returns_default(tmp_path):
    p = str(tmp_path / "cursor.json")
    result = load_cursor(p, default={"ids": []})
    assert result == {"ids": []}


def test_load_cursor_existing(tmp_path):
    p = str(tmp_path / "cursor.json")
    atomic_write_json(p, {"ids": ["a", "b"]})
    assert load_cursor(p, default={"ids": []}) == {"ids": ["a", "b"]}


def test_load_cursor_corrupted_returns_default(tmp_path):
    p = str(tmp_path / "cursor.json")
    open(p, "w").write("not json")
    result = load_cursor(p, default={"ids": []})
    assert result == {"ids": []}


def test_save_cursor(tmp_path):
    p = str(tmp_path / "cursor.json")
    save_cursor(p, {"last_ts": "2026-01-01T00:00:00Z"})
    assert load_cursor(p, default={}) == {"last_ts": "2026-01-01T00:00:00Z"}


def test_find_claude_found():
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        from analyst._utils import find_claude
        assert find_claude() == "/usr/local/bin/claude"


def test_find_claude_missing():
    with patch("shutil.which", return_value=None):
        with patch("os.path.isfile", return_value=False):
            with patch("os.access", return_value=False):
                from analyst._utils import find_claude
                assert find_claude() is None


def test_call_claude_timeout():
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120)):
            with pytest.raises(RuntimeError, match="timed out"):
                call_claude("prompt", "claude-sonnet-4-6")
