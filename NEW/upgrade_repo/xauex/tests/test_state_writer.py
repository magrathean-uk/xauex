"""Tests for StateWriter — schema, atomic write, no live dependencies."""

import json
import os
import pytest
from datetime import datetime, timezone

from bot.state.writer import StateWriter
from bot.risk.gates import RiskState
from bot.levels.htf_levels import HTFLevels
from bot.patterns.detector import PatternType
from bot.execution.executor import TrackedPosition


@pytest.fixture
def writer(minimal_config):
    return StateWriter(minimal_config)


@pytest.fixture
def sample_levels():
    return HTFLevels(
        day_open=2710.0, day_high=2742.0, day_low=2698.0, day_close=2731.0,
        mn_open=2680.0, mn_high=2750.0, mn_low=2610.0, mn_close=2740.0,
        wk_open=2720.0, wk_high=2745.0, wk_low=2700.0, wk_close=2735.0,
        last_refresh_utc=datetime(2026, 3, 9, 21, 0, tzinfo=timezone.utc),
        weekly_bar_open_time_utc=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_position():
    return TrackedPosition(
        position_id="789012",
        direction="LONG",
        entry_price=2720.45,
        stop_loss=2708.0,
        take_profit=2745.0,
        lot_size=0.03,
        open_time_utc=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        pattern=PatternType.BULLISH_PIN_BAR,
        level=2720.0,
    )


class TestStateWriterSchema:

    async def test_creates_file(self, writer, minimal_config):
        """Write creates the state file at the configured path."""
        await writer.write()
        assert os.path.exists(minimal_config.state_file_path)

    async def test_valid_json(self, writer, minimal_config):
        """Written file is valid JSON."""
        await writer.write()
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    async def test_top_level_keys_present(self, writer, minimal_config):
        """All required top-level keys from 08-DASHBOARD.md are present."""
        await writer.write()
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        required = {"meta", "account", "risk", "levels", "open_positions",
                    "closed_trades_today", "recent_h1_closes",
                    "trade_entries_on_chart", "last_signal", "signal_history",
                    "strategy", "shadow_last_signal", "shadow_signal_history",
                    "macro_regime", "trend", "runtime", "last_error", "observe_only"}
        assert required.issubset(data.keys())

    async def test_meta_fields(self, writer, minimal_config):
        """meta section has version, last_updated_utc, bot_status."""
        await writer.write(bot_status="RUNNING")
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["meta"]["version"] == 1
        assert data["meta"]["bot_status"] == "RUNNING"
        assert "last_updated_utc" in data["meta"]

    async def test_observe_only_field(self, writer, minimal_config):
        """observe_only matches config value."""
        await writer.write()
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["observe_only"] is True

    async def test_levels_serialised(self, writer, minimal_config, sample_levels):
        """HTFLevels dataclass serialised to daily/monthly/weekly dicts."""
        await writer.write(levels=sample_levels)
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["levels"]["daily"]["open"] == 2710.0
        assert data["levels"]["monthly"]["open"] == 2680.0
        assert data["levels"]["monthly"]["high"] == 2750.0
        assert data["levels"]["weekly"]["low"] == 2700.0

    async def test_open_positions_serialised(self, writer, minimal_config, sample_position):
        """TrackedPosition serialised to list of dicts."""
        await writer.write(open_positions=[sample_position])
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        pos = data["open_positions"][0]
        assert pos["position_id"] == "789012"
        assert pos["direction"] == "LONG"
        assert pos["pattern"] == "BULLISH_PIN_BAR"

    async def test_risk_state_serialised(self, writer, minimal_config):
        """RiskState serialised into risk section."""
        risk = RiskState()
        risk.consecutive_losses_today = 2
        risk.weekly_pnl = -75.0
        await writer.write(risk_state=risk)
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["risk"]["consecutive_losses_today"] == 2
        assert data["risk"]["weekly_pnl"] == -75.0

    async def test_atomic_write_no_temp_file_left(self, writer, minimal_config):
        """After write, no .tmp files remain in the state directory."""
        await writer.write()
        state_dir = os.path.dirname(minimal_config.state_file_path)
        tmp_files = [f for f in os.listdir(state_dir) if f.endswith(".tmp")]
        assert tmp_files == []

    async def test_write_is_idempotent(self, writer, minimal_config):
        """Multiple writes → last write wins, no corruption."""
        await writer.write(bot_status="RUNNING")
        await writer.write(bot_status="OBSERVE_ONLY")
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["meta"]["bot_status"] == "OBSERVE_ONLY"

    async def test_partial_write_preserves_previous_state_sections(self, writer, minimal_config):
        await writer.write(
            bot_status="OBSERVE_ONLY",
            account={"balance": 1000.0},
            strategy={"active_mode": "EMA_PULLBACK_H1", "shadow_mode": "SCALP_V1"},
            runtime={"reconnect_count": 1},
            closed_trades=[{"position_id": "old"}],
        )

        await writer.write(
            open_positions=[],
            closed_trades=[{"position_id": "new"}],
        )

        with open(minimal_config.state_file_path) as f:
            data = json.load(f)

        assert data["meta"]["bot_status"] == "OBSERVE_ONLY"
        assert data["account"]["balance"] == 1000.0
        assert data["strategy"]["active_mode"] == "EMA_PULLBACK_H1"
        assert data["runtime"]["reconnect_count"] == 1
        assert data["closed_trades_today"] == [{"position_id": "new"}]

    async def test_trend_and_runtime_serialised(self, writer, minimal_config):
        await writer.write(
            strategy={"active_mode": "LEGACY_LEVELS", "shadow_mode": "EMA_PULLBACK_H1"},
            shadow_last_signal={"time_utc": "2026-03-17T10:00:00Z", "gate_result": "NO_PULLBACK_TOUCH"},
            shadow_signal_history=[{"time_utc": "2026-03-17T10:00:00Z", "gate_result": "NO_PULLBACK_TOUCH"}],
            macro_regime={"regime": "XAU_BEARISH", "confidence": 0.8},
            trend={"alignment": "BULLISH", "daily_ema_8": 5010.0},
            runtime={"reconnect_count": 2, "kill_switch_active": False},
            signal_history=[{"time_utc": "2026-03-17T09:00:00Z", "gate_result": "NO_PATTERN"}],
        )
        with open(minimal_config.state_file_path) as f:
            data = json.load(f)
        assert data["strategy"]["shadow_mode"] == "EMA_PULLBACK_H1"
        assert data["shadow_last_signal"]["gate_result"] == "NO_PULLBACK_TOUCH"
        assert data["shadow_signal_history"][0]["gate_result"] == "NO_PULLBACK_TOUCH"
        assert data["macro_regime"]["regime"] == "XAU_BEARISH"
        assert data["trend"]["alignment"] == "BULLISH"
        assert data["runtime"]["reconnect_count"] == 2
        assert data["signal_history"][0]["gate_result"] == "NO_PATTERN"
