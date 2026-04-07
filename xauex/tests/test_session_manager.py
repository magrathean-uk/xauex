import os
import sys
import importlib.util
from pathlib import Path


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
from bot.risk.sizing import calculate_mirofish_lot_size_from_cash_risk

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
