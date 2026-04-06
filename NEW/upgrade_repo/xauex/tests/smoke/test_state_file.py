"""Smoke test: state file creation, schema, and atomic write."""

import json
import os
import asyncio
import pytest
from bot.state.writer import StateWriter


def test_state_file_smoke(minimal_config):
    """StateWriter creates a valid JSON file with all required top-level keys."""
    writer = StateWriter(minimal_config)
    asyncio.run(writer.write(bot_status="RUNNING"))

    path = minimal_config.state_file_path
    assert os.path.exists(path), f"state.json not found at {path}"

    with open(path) as f:
        data = json.load(f)

    required_keys = {
        "meta", "account", "risk", "levels",
        "open_positions", "closed_trades_today",
        "recent_h1_closes", "trade_entries_on_chart",
        "last_signal", "last_error", "observe_only",
    }
    missing = required_keys - data.keys()
    assert not missing, f"Missing keys in state.json: {missing}"
    assert data["meta"]["bot_status"] == "RUNNING"
    assert data["meta"]["version"] == 1
    assert data["observe_only"] is True


def test_no_temp_files_after_write(minimal_config):
    """Atomic write leaves no .tmp files behind."""
    writer = StateWriter(minimal_config)
    asyncio.run(writer.write())
    state_dir = os.path.dirname(minimal_config.state_file_path)
    leftovers = [f for f in os.listdir(state_dir) if f.endswith(".tmp")]
    assert leftovers == []


def test_second_write_overwrites_cleanly(minimal_config):
    """Two consecutive writes — second wins, no corruption."""
    writer = StateWriter(minimal_config)
    asyncio.run(writer.write(bot_status="RUNNING"))
    asyncio.run(writer.write(bot_status="OBSERVE_ONLY"))
    with open(minimal_config.state_file_path) as f:
        data = json.load(f)
    assert data["meta"]["bot_status"] == "OBSERVE_ONLY"
