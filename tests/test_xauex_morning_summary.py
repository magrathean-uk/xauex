from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import stat
import sys


MODULE_PATH = Path("ops/monitoring/check_xauex_morning_summary.py")
SPEC = importlib.util.spec_from_file_location("xauex_morning_summary", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_morning_summary_sends_once_for_terminal_morning_signal(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    state_path.write_text(
        json.dumps(
            {
                "risk": {
                    "xauex_signal_runs_london": [
                        {
                            "date_london": "2026-04-21",
                            "slot": "MORNING",
                            "signal_id": "2026-04-21T06:55:10Z",
                            "reason": "ORDER_PLACED",
                            "terminal": True,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    cmd_path.write_text(
        json.dumps(
            {
                "xauex_signal": {
                    "timestamp_utc": "2026-04-21T06:55:10Z",
                    "action": "SELL",
                    "confidence": 0.73,
                    "reasoning": "Momentum and rising yields favor a short.",
                    "stop_loss_distance": 12.0,
                    "take_profit_distance": 30.0,
                    "distance_unit": "usd",
                }
            }
        ),
        encoding="utf-8",
    )
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat > "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        now=datetime(2026, 4, 21, 8, 12, tzinfo=MODULE.LONDON_TZ),
        state_path=state_path,
        cmd_path=cmd_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX morning SELL on bolykihu" in mail_text
    assert "Signal: SELL" in mail_text
    assert "Live trade opened: SHORT." in mail_text
    saved_state = json.loads(sent_state_path.read_text(encoding="utf-8"))
    assert saved_state["date_london"] == "2026-04-21"
    assert saved_state["signal_id"] == "2026-04-21T06:55:10Z"

    rc_again = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        now=datetime(2026, 4, 21, 8, 13, tzinfo=MODULE.LONDON_TZ),
        state_path=state_path,
        cmd_path=cmd_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
    )
    assert rc_again == 0
    assert mail_capture.read_text(encoding="utf-8") == mail_text


def test_morning_summary_skips_before_cutoff(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    state_path.write_text(json.dumps({}), encoding="utf-8")
    cmd_path.write_text(json.dumps({}), encoding="utf-8")
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat > "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        now=datetime(2026, 4, 21, 8, 5, tzinfo=MODULE.LONDON_TZ),
        state_path=state_path,
        cmd_path=cmd_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
    )

    assert rc == 0
    assert not mail_capture.exists()
    assert not sent_state_path.exists()


def test_morning_summary_uses_smtp_when_no_sendmail_is_provided(tmp_path: Path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    sent_state_path = tmp_path / "sent.json"

    state_path.write_text(
        json.dumps(
            {
                "risk": {
                    "xauex_signal_runs_london": [
                        {
                            "date_london": "2026-04-21",
                            "slot": "MORNING",
                            "signal_id": "2026-04-21T06:55:10Z",
                            "reason": "HOLD",
                            "terminal": True,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    cmd_path.write_text(
        json.dumps(
            {
                "xauex_signal": {
                    "timestamp_utc": "2026-04-21T06:55:10Z",
                    "action": "HOLD",
                    "confidence": 0.0,
                    "reasoning": "Low assurance; no trade.",
                    "distance_unit": "usd",
                }
            }
        ),
        encoding="utf-8",
    )

    sent = {}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int = 0) -> None:
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout
            sent["message"] = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(MODULE.smtplib, "SMTP", FakeSMTP)

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        now=datetime(2026, 4, 21, 8, 12, tzinfo=MODULE.LONDON_TZ),
        state_path=state_path,
        cmd_path=cmd_path,
        sent_state_path=sent_state_path,
        sendmail_bin=None,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
    )

    assert rc == 0
    assert sent["host"] == "127.0.0.1"
    assert sent["port"] == 25
    assert sent["message"]["Subject"] == "[Monit] XAUEX morning HOLD on bolykihu"


def test_summary_sends_once_for_terminal_us_open_signal(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    cmd_path = tmp_path / "cmd.json"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    state_path.write_text(
        json.dumps(
            {
                "risk": {
                    "xauex_signal_runs_london": [
                        {
                            "date_london": "2026-04-21",
                            "slot": "US_OPEN",
                            "signal_id": "2026-04-21T12:25:10Z",
                            "reason": "ORDER_PLACED",
                            "confirm_status": "CONFIRMED",
                            "confirm_reason": "CONFIRMED",
                            "terminal": True,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    cmd_path.write_text(
        json.dumps(
            {
                "xauex_signal": {
                    "timestamp_utc": "2026-04-21T12:25:10Z",
                    "action": "BUY",
                    "confidence": 0.68,
                    "reasoning": "US open flow and softer yields favor upside.",
                    "window_label": "us_open",
                    "confirm_status": "CONFIRMED",
                    "confirm_reason": "CONFIRMED",
                    "stop_loss_distance": 11.0,
                    "take_profit_distance": 27.0,
                    "distance_unit": "usd",
                }
            }
        ),
        encoding="utf-8",
    )
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat > "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        now=datetime(2026, 4, 21, 13, 58, tzinfo=MODULE.LONDON_TZ),
        state_path=state_path,
        cmd_path=cmd_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX us_open BUY on bolykihu" in mail_text
    assert "Confirm status: CONFIRMED" in mail_text
