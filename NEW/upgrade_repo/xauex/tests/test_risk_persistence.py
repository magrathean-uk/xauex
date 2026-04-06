"""Tests for bot/state/risk_persistence.py."""

import json
import os
import tempfile

import pytest
from bot.risk.gates import RiskState
from bot.state.risk_persistence import (
    _save_risk_state_sync as save_risk_state,
    _load_risk_state_sync as load_risk_state,
    _risk_state_path,
)


@pytest.fixture
def tmp_state_path(tmp_path):
    """A temporary state.json path — risk_state.json goes in the same dir."""
    return str(tmp_path / "state.json")


class TestSaveRiskState:
    def test_creates_file(self, tmp_state_path):
        state = RiskState(consecutive_losses_today=2, weekly_pnl=-150.0)
        save_risk_state(state, tmp_state_path)
        path = _risk_state_path(tmp_state_path)
        assert os.path.exists(path)

    def test_content_is_valid_json(self, tmp_state_path):
        state = RiskState(consecutive_losses_today=1, weekly_pnl=-50.0, weekly_halted=True)
        save_risk_state(state, tmp_state_path)
        path = _risk_state_path(tmp_state_path)
        with open(path) as f:
            data = json.load(f)
        assert data["consecutive_losses_today"] == 1
        assert data["weekly_pnl"] == -50.0
        assert data["weekly_halted"] is True

    def test_saved_at_timestamp_present(self, tmp_state_path):
        save_risk_state(RiskState(), tmp_state_path)
        path = _risk_state_path(tmp_state_path)
        with open(path) as f:
            data = json.load(f)
        assert "_saved_at_utc" in data

    def test_idempotent_overwrite(self, tmp_state_path):
        """Writing twice should not leave temp files."""
        save_risk_state(RiskState(weekly_pnl=-10.0), tmp_state_path)
        save_risk_state(RiskState(weekly_pnl=-20.0), tmp_state_path)
        path = _risk_state_path(tmp_state_path)
        with open(path) as f:
            data = json.load(f)
        assert data["weekly_pnl"] == -20.0


class TestLoadRiskState:
    def test_returns_none_when_missing(self, tmp_state_path):
        result = load_risk_state(tmp_state_path)
        assert result is None

    def test_round_trip(self, tmp_state_path):
        original = RiskState(
            consecutive_losses_today=3,
            weekly_pnl=-200.0,
            week_start_balance=10000.0,
            weekly_halted=True,
            daily_halted=True,
        )
        save_risk_state(original, tmp_state_path)
        restored = load_risk_state(tmp_state_path)

        assert restored is not None
        assert restored.consecutive_losses_today == 3
        assert restored.weekly_pnl == -200.0
        assert restored.week_start_balance == 10000.0
        assert restored.weekly_halted is True
        assert restored.daily_halted is True

    def test_returns_none_on_corrupt_json(self, tmp_state_path):
        path = _risk_state_path(tmp_state_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("this is not json {{{{")
        result = load_risk_state(tmp_state_path)
        assert result is None

    def test_fresh_state_default_values(self, tmp_state_path):
        save_risk_state(RiskState(), tmp_state_path)
        restored = load_risk_state(tmp_state_path)
        assert restored is not None
        assert restored.consecutive_losses_today == 0
        assert restored.weekly_halted is False
        assert restored.weekly_pnl == 0.0
