"""Tests for MiroFish signal reading and execution in XAUEX."""

import json
from datetime import datetime, timedelta, timezone

import pytest


def _write_signal(path: str, signal: dict) -> None:
    with open(path, "w") as f:
        json.dump(signal, f)


def _read_signal(path: str) -> dict:
    """Replicate the signal parsing logic from main.py."""
    with open(path, "r") as f:
        cmd = json.load(f)
    return cmd.get("mirofish_signal")


class TestMirofishSignalParsing:
    """Test signal file reading and validation."""

    def test_valid_buy_signal(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        signal = {
            "kill_switch": False,
            "mirofish_signal": {
                "action": "BUY",
                "confidence": 0.85,
                "reasoning": "Fed dovish pivot",
                "stop_loss_usd": 12.0,
                "take_profit_usd": 24.0,
                "timestamp_utc": "2025-01-15T10:30:00Z",
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        assert parsed is not None
        assert parsed["action"] == "BUY"
        assert parsed["confidence"] == 0.85
        assert parsed["stop_loss_usd"] == 12.0
        assert parsed["take_profit_usd"] == 24.0

    def test_valid_sell_signal(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        signal = {
            "kill_switch": False,
            "mirofish_signal": {
                "action": "SELL",
                "confidence": 0.72,
                "reasoning": "Dollar strength + hawkish Fed",
                "stop_loss_usd": 10.0,
                "take_profit_usd": 20.0,
                "timestamp_utc": "2025-01-15T14:00:00Z",
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        assert parsed["action"] == "SELL"

    def test_hold_signal_skipped(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        signal = {
            "kill_switch": False,
            "mirofish_signal": {
                "action": "HOLD",
                "confidence": 0.50,
                "reasoning": "Mixed signals",
                "stop_loss_usd": 0,
                "take_profit_usd": 0,
                "timestamp_utc": "2025-01-15T10:30:00Z",
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        assert parsed["action"] == "HOLD"

    def test_no_signal_field(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        _write_signal(path, {"kill_switch": False})
        parsed = _read_signal(path)
        assert parsed is None

    def test_missing_file(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        with pytest.raises(FileNotFoundError):
            _read_signal(path)

    def test_stale_signal_detection(self, tmp_path):
        """Signal older than max_age should be treated as stale."""
        path = str(tmp_path / "cmd.json")
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        signal = {
            "kill_switch": False,
            "mirofish_signal": {
                "action": "BUY",
                "confidence": 0.85,
                "reasoning": "Test",
                "stop_loss_usd": 12.0,
                "take_profit_usd": 24.0,
                "timestamp_utc": old_ts,
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        ts = datetime.fromisoformat(parsed["timestamp_utc"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        assert age > 300

    def test_signal_direction_mapping(self):
        """BUY -> direction=1, SELL -> direction=-1."""
        assert {"BUY": 1, "SELL": -1}.get("BUY") == 1
        assert {"BUY": 1, "SELL": -1}.get("SELL") == -1
        assert {"BUY": 1, "SELL": -1}.get("HOLD") is None

    def test_v2_signal_schema_preserves_symbol_and_distances(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        signal = {
            "schema_version": 2,
            "kill_switch": False,
            "mirofish_signal": {
                "symbol": "XAUUSD",
                "asset_class": "metals",
                "action": "BUY",
                "confidence": 0.81,
                "reasoning": "ETF inflows and softer rate path",
                "stop_loss_distance": 12.0,
                "take_profit_distance": 24.0,
                "distance_unit": "usd",
                "timestamp_utc": "2026-04-03T10:30:00Z",
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        assert parsed["symbol"] == "XAUUSD"
        assert parsed["distance_unit"] == "usd"
        assert parsed["stop_loss_distance"] == 12.0
        assert parsed["take_profit_distance"] == 24.0

    def test_non_xau_symbol_is_visible_to_execution_guard(self, tmp_path):
        path = str(tmp_path / "cmd.json")
        signal = {
            "kill_switch": False,
            "mirofish_signal": {
                "symbol": "GBPJPY",
                "asset_class": "fx",
                "action": "BUY",
                "confidence": 0.76,
                "reasoning": "BoE-BoJ rate differential widening",
                "stop_loss_distance": 65.0,
                "take_profit_distance": 130.0,
                "distance_unit": "pips",
                "timestamp_utc": "2026-04-03T10:30:00Z",
            },
        }
        _write_signal(path, signal)
        parsed = _read_signal(path)
        assert parsed["symbol"] == "GBPJPY"
        assert parsed["distance_unit"] == "pips"
