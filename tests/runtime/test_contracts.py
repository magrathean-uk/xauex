import pytest

from xauex.runtime.contracts import CommandBundle, ContractValidationError, parse_manual_command


def _ts() -> str:
    return "2026-04-07T07:55:00Z"


def test_command_bundle_valid_directional_signal():
    bundle = CommandBundle.from_mapping(
        {
            "schema_version": 2,
            "generated_at_utc": _ts(),
            "kill_switch": False,
            "xauex_signal": {
                "symbol": "XAUUSD",
                "action": "BUY",
                "confidence": 0.7,
                "timestamp_utc": _ts(),
                "stop_loss_distance": 10,
                "take_profit_distance": 20,
                "confirm_status": "CONFIRMED",
            },
        }
    )
    assert bundle.xauex_signal.action == "BUY"
    assert bundle.to_dict()["kill_switch"] is False


def test_directional_signal_requires_trade_geometry():
    with pytest.raises(ContractValidationError):
        CommandBundle.from_mapping(
            {
                "schema_version": 2,
                "generated_at_utc": _ts(),
                "xauex_signal": {
                    "action": "SELL",
                    "confidence": 0.7,
                    "timestamp_utc": _ts(),
                },
            }
        )


def test_manual_open_and_close_commands_parse():
    open_cmd = parse_manual_command(
        {
            "command": "open",
            "action": "BUY",
            "lot_size": 0.01,
            "stop_loss": 1,
            "take_profit": 2,
        }
    )
    close_cmd = parse_manual_command({"command": "close", "position_id": "m-1"})
    assert open_cmd.to_dict()["action"] == "BUY"
    assert close_cmd.to_dict()["position_id"] == "m-1"
