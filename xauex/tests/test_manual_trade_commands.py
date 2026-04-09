import json
import sys
import importlib.util
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


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

count_oracle_open_positions = _MODULE.count_oracle_open_positions
count_tradeable_open_positions = _MODULE.count_tradeable_open_positions
load_manual_trade_command = _MODULE.load_manual_trade_command
manual_trade_global_block_reason = _MODULE.manual_trade_global_block_reason
match_recovered_position_metadata = _MODULE.match_recovered_position_metadata
should_manage_with_oracle_session_manager = _MODULE.should_manage_with_oracle_session_manager
validate_manual_trade_command = _MODULE.validate_manual_trade_command
validate_manual_trade_prices = _MODULE.validate_manual_trade_prices

from bot.execution.executor import TrackedPosition, Executor
from bot.patterns.detector import PatternType


def test_manual_trade_command_is_loaded_and_cleared(tmp_path):
    path = tmp_path / "manual_trade_cmd.json"
    path.write_text(
        json.dumps(
            {
                "command": "open",
                "action": "BUY",
                "lot_size": 0.02,
                "stop_loss": 4690.0,
                "take_profit": 4710.0,
            }
        ),
        encoding="utf-8",
    )

    cmd = load_manual_trade_command(path)

    assert cmd["action"] == "BUY"
    assert not path.exists()


def test_manual_trade_command_validation_rejects_bad_side():
    valid, reason = validate_manual_trade_command(
        {
            "command": "open",
            "action": "HOLD",
            "lot_size": 0.01,
            "stop_loss": 4690.0,
            "take_profit": 4710.0,
        }
    )
    assert valid is False
    assert "action" in reason.lower()


def test_manual_positions_do_not_count_toward_oracle_daily_limit():
    positions = [
        {"position_id": "1", "owner": "manual"},
        {"position_id": "2", "owner": "oracle"},
    ]
    assert count_oracle_open_positions(positions) == 1
    assert count_tradeable_open_positions(positions) == 1


def test_oracle_manager_skips_manual_positions():
    manual = {"position_id": "1", "owner": "manual", "metadata": {}}
    oracle = {"position_id": "2", "owner": "oracle", "metadata": {}}
    assert should_manage_with_oracle_session_manager(manual) is False
    assert should_manage_with_oracle_session_manager(oracle) is True


def test_manual_trade_prices_must_be_on_correct_side_of_market():
    valid_buy, reason_buy = validate_manual_trade_prices(
        {
            "command": "open",
            "action": "BUY",
            "stop_loss": 4705.0,
            "take_profit": 4720.0,
        },
        current_price=4700.0,
    )
    valid_sell, reason_sell = validate_manual_trade_prices(
        {
            "command": "open",
            "action": "SELL",
            "stop_loss": 4695.0,
            "take_profit": 4680.0,
        },
        current_price=4700.0,
    )
    assert valid_buy is False
    assert "buy" in reason_buy.lower()
    assert valid_sell is False
    assert "sell" in reason_sell.lower()


def test_restart_recovery_can_match_position_from_pending_market_order():
    position = {
        "position_id": "998",
        "direction": "LONG",
        "volume": 0.03,
        "stop_loss": 4680.0,
        "take_profit": 4720.0,
    }
    matched = match_recovered_position_metadata(
        position,
        prior_positions={},
        prior_pending_market_orders={
            "949818631": {
                "direction": "LONG",
                "lot_size": 0.03,
                "stop_loss": 4680.0,
                "take_profit": 4720.0,
                "owner": "oracle",
                "metadata": {"session": {"phase": "OBSERVE"}},
            }
        },
    )
    assert matched["owner"] == "oracle"
    assert matched["metadata"]["session"]["phase"] == "OBSERVE"


def test_manual_trade_open_honors_global_safety_blockers():
    assert manual_trade_global_block_reason(observe_only=True, kill_switch_active=False, auth_failure=False) == "OBSERVE_ONLY"
    assert manual_trade_global_block_reason(observe_only=False, kill_switch_active=True, auth_failure=False) == "KILL_SWITCH"
    assert manual_trade_global_block_reason(observe_only=False, kill_switch_active=False, auth_failure=True) == "AUTH_FAILURE"
    assert manual_trade_global_block_reason(observe_only=False, kill_switch_active=False, auth_failure=False) is None


def test_trailing_engine_skips_manual_and_oracle_positions():
    executor = Executor(
        config=SimpleNamespace(observe_only=False),
        api_client=SimpleNamespace(get_current_quote=lambda: (4700.0, 4700.2)),
        level_manager=None,
    )
    executor.position_manager.add(
        TrackedPosition(
            position_id="manual-1",
            direction="LONG",
            entry_price=4690.0,
            stop_loss=4680.0,
            take_profit=4720.0,
            lot_size=0.02,
            open_time_utc=None,
            pattern=PatternType.NONE,
            level=4690.0,
            owner="manual",
        )
    )
    executor.position_manager.add(
        TrackedPosition(
            position_id="oracle-1",
            direction="LONG",
            entry_price=4690.0,
            stop_loss=4680.0,
            take_profit=4720.0,
            lot_size=0.02,
            open_time_utc=None,
            pattern=PatternType.NONE,
            level=4690.0,
            owner="oracle",
        )
    )

    assert executor.should_apply_generic_trailing(executor.position_manager.get_position("manual-1")) is False
    assert executor.should_apply_generic_trailing(executor.position_manager.get_position("oracle-1")) is False


def test_manual_position_close_does_not_touch_risk_gates():
    class _RiskGates:
        def __init__(self):
            self.closed = []

        def record_trade_closed(self, pnl):
            self.closed.append(pnl)

    executor = Executor(
        config=SimpleNamespace(observe_only=False),
        api_client=SimpleNamespace(get_current_quote=lambda: (4700.0, 4700.2)),
        level_manager=None,
        risk_gates=_RiskGates(),
    )
    executor.position_manager.add(
        TrackedPosition(
            position_id="manual-closed",
            direction="SHORT",
            entry_price=4690.0,
            stop_loss=4705.0,
            take_profit=4660.0,
            lot_size=0.01,
            open_time_utc=None,
            pattern=PatternType.NONE,
            level=4690.0,
            owner="manual",
        )
    )

    asyncio.run(executor.on_position_closed("manual-closed", close_price=4691.0, pnl=-0.61))

    assert executor.risk_gates.closed == []


def test_closed_trades_today_resets_on_new_utc_day():
    executor = Executor(
        config=SimpleNamespace(observe_only=False),
        api_client=SimpleNamespace(get_current_quote=lambda: (4700.0, 4700.2)),
        level_manager=None,
    )
    executor._closed_trades_date_utc = "2026-04-07"
    executor._closed_trades_today = [{"position_id": "old-trade"}]

    trades = executor.get_closed_trades_today(datetime(2026, 4, 8, 0, 1, tzinfo=timezone.utc))

    assert trades == []
    assert executor._closed_trades_today == []
    assert executor._closed_trades_date_utc == "2026-04-08"
