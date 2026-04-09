import os
import sys
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

XAUEX_ROOT = REPO_ROOT / "xauex"
if str(XAUEX_ROOT) not in sys.path:
    sys.path.insert(0, str(XAUEX_ROOT))

_SPEC = importlib.util.spec_from_file_location("xauex_main", XAUEX_ROOT / "main.py")
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

from config import load_config
from config import ConfigError
from bot.risk.sizing import calculate_mirofish_lot_size_from_cash_risk
from bot.levels.htf_levels import HTFLevels

build_mirofish_initial_stop_distance = _MODULE.build_mirofish_initial_stop_distance
advance_mirofish_session_phase = _MODULE.advance_mirofish_session_phase


def test_load_config_includes_mirofish_session_manager_settings(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    monkeypatch.setenv("MIROFISH_SESSION_PROTECT_R", "0.85")
    monkeypatch.setenv("MIROFISH_SESSION_TRAIL_R", "1.35")
    monkeypatch.setenv("MIROFISH_SESSION_ATR_MULTIPLIER", "1.4")
    monkeypatch.setenv("MIROFISH_SESSION_STRUCTURE_BUFFER_USD", "2.5")
    monkeypatch.setenv("MIROFISH_MANUAL_COMMAND_PATH", "/tmp/manual_trade_cmd.json")

    cfg = load_config()

    assert cfg.mirofish_session_protect_r == 0.85
    assert cfg.mirofish_session_trail_r == 1.35
    assert cfg.mirofish_session_atr_multiplier == 1.4
    assert cfg.mirofish_session_structure_buffer_usd == 2.5
    assert cfg.mirofish_manual_command_path == "/tmp/manual_trade_cmd.json"


def test_load_config_defaults_health_check_host_to_loopback(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    monkeypatch.delenv("HEALTH_CHECK_HOST", raising=False)

    cfg = load_config()

    assert cfg.health_check_host == "127.0.0.1"


def test_load_config_rejects_force_flat_before_second_window_finishes(monkeypatch):
    monkeypatch.chdir(XAUEX_ROOT)
    monkeypatch.setenv("MIROFISH_ENTRY_SECOND_START_LONDON", "11:30")
    monkeypatch.setenv("MIROFISH_ENTRY_SECOND_END_LONDON", "11:35")
    monkeypatch.setenv("MIROFISH_FORCE_FLAT_LONDON", "11:30")

    with pytest.raises(ConfigError, match="MIROFISH_FORCE_FLAT_LONDON must be later than MIROFISH_ENTRY_SECOND_END_LONDON"):
        load_config()


def test_oracle_initial_stop_uses_widest_signal_structure_or_atr():
    stop = build_mirofish_initial_stop_distance(
        signal_stop=12.0,
        atr_stop=16.5,
        structure_stop=14.0,
        min_stop=8.0,
        max_stop=30.0,
    )
    assert stop == 16.5


def test_oracle_wider_stop_reduces_lot_but_respects_cash_cap():
    lot = calculate_mirofish_lot_size_from_cash_risk(
        cash_risk=50.0,
        stop_distance=20.0,
        lot_size=100.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=100.0,
        max_lot_size=0.1,
    )
    assert lot == 0.02


def test_session_phase_moves_to_protect_then_trail():
    state = {
        "phase": "OBSERVE",
        "direction": "LONG",
        "entry_price": 100.0,
        "initial_risk_distance": 10.0,
        "confidence_bucket": "medium",
    }

    protect = advance_mirofish_session_phase(
        state,
        current_price=109.0,
        protect_r=0.85,
        trail_r=1.35,
    )
    trail = advance_mirofish_session_phase(
        protect,
        current_price=114.0,
        protect_r=0.85,
        trail_r=1.35,
    )

    assert protect["phase"] == "PROTECT"
    assert trail["phase"] == "TRAIL"


def test_structure_stop_distance_accepts_htflevels_container():
    orchestrator = _MODULE.BotOrchestrator.__new__(_MODULE.BotOrchestrator)
    orchestrator.config = SimpleNamespace(
        sl_min_dollars=10.0,
        mirofish_session_structure_buffer_usd=2.5,
    )
    orchestrator._recent_h1_closes = [4700.0, 4710.0, 4720.0]
    orchestrator.level_manager = SimpleNamespace(
        _raw=HTFLevels(
            day_open=4710.0,
            day_high=4740.0,
            day_low=4690.0,
            day_close=4725.0,
            mn_open=4600.0,
            mn_high=4800.0,
            mn_low=4500.0,
            mn_close=4700.0,
            wk_open=4680.0,
            wk_high=4760.0,
            wk_low=4660.0,
            wk_close=4720.0,
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=datetime.now(timezone.utc),
        )
    )

    distance = orchestrator._mirofish_structure_stop_distance(direction=-1, current_price=4725.0)

    assert distance == 17.5
