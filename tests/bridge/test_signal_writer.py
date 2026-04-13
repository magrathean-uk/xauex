import json

from xauex.signal.signal_writer import write_signal as xauex_write_signal
from xauex.signal.signal_writer import write_signal


def test_write_signal_uses_xauex_signal_key_and_preserves_notes(tmp_path):
    output_path = tmp_path / "cmd.json"
    output_path.write_text(
        json.dumps({"kill_switch": True, "notes": ["keep-me"]}),
        encoding="utf-8",
    )

    write_signal(
        {
            "symbol": "XAUUSD",
            "action": "BUY",
            "confidence": 0.81,
        },
        str(output_path),
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["kill_switch"] is True
    assert payload["notes"] == ["keep-me"]
    assert "xauex_signal" in payload
    assert "mirofish_signal" not in payload
    assert payload["xauex_signal"]["action"] == "BUY"


def test_signal_writer_import_resolves_to_xauex_implementation():
    assert xauex_write_signal is write_signal
