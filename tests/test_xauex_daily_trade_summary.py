from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys


MODULE_PATH = Path("ops/monitoring/check_xauex_daily_trade_summary.py")
assert MODULE_PATH.exists(), "daily trade summary monitoring script must exist"
SPEC = importlib.util.spec_from_file_location("xauex_daily_trade_summary", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _journal_entry(
    *,
    position_id: str,
    direction: str,
    pnl: float,
    close_time_utc: str,
    confidence: float = 0.70,
) -> dict:
    return {
        "trade_id": position_id,
        "journalled_at_utc": close_time_utc,
        "entry": {
            "position_id": position_id,
            "direction": direction,
            "entry_price": 4150.0,
            "close_price": 4160.0,
            "lot_size": 0.01,
            "pnl": pnl,
            "pattern": "NONE",
            "owner": "xauex",
            "close_time_utc": close_time_utc,
            "signal_confidence": confidence,
            "metadata": {
                "session": {
                    "window_label": "morning",
                    "assurance_reason": "HIGH_ASSURANCE",
                }
            },
        },
    }


def test_daily_trade_summary_sends_once_after_london_session_with_results(tmp_path: Path) -> None:
    journal_path = tmp_path / "trade_journal.json"
    event_journal_path = tmp_path / "events.jsonl"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    journal_path.write_text(
        json.dumps(
            [
                _journal_entry(
                    position_id="p-win",
                    direction="LONG",
                    pnl=12.50,
                    close_time_utc="2026-04-21T09:30:00Z",
                    confidence=0.74,
                ),
                _journal_entry(
                    position_id="p-loss",
                    direction="SHORT",
                    pnl=-7.25,
                    close_time_utc="2026-04-21T12:59:00Z",
                    confidence=0.62,
                ),
                _journal_entry(
                    position_id="p-old",
                    direction="SHORT",
                    pnl=-3.00,
                    close_time_utc="2026-04-20T12:59:00Z",
                ),
            ]
        ),
        encoding="utf-8",
    )
    event_journal_path.write_text("", encoding="utf-8")
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat >> "{mail_capture}"
printf '\\n---MESSAGE---\\n' >> "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 21, 15, 50, tzinfo=MODULE.LONDON_TZ),
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX daily trade results 2026-04-21 on bolykihu" in mail_text
    assert "Closed trades: 2" in mail_text
    assert "Wins: 1" in mail_text
    assert "Losses: 1" in mail_text
    assert "Net PnL: +5.25" in mail_text
    assert "LONG: 1 trades, PnL +12.50" in mail_text
    assert "SHORT: 1 trades, PnL -7.25" in mail_text
    assert "p-win LONG WIN +12.50" in mail_text
    assert "p-loss SHORT LOSS -7.25" in mail_text
    assert "p-old" not in mail_text
    saved_state = json.loads(sent_state_path.read_text(encoding="utf-8"))
    assert saved_state["sent_dates"] == ["2026-04-21"]

    rc_again = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 21, 16, 0, tzinfo=MODULE.LONDON_TZ),
    )

    assert rc_again == 0
    assert mail_capture.read_text(encoding="utf-8") == mail_text


def test_daily_trade_summary_sends_zero_trade_day_after_cutoff(tmp_path: Path) -> None:
    journal_path = tmp_path / "trade_journal.json"
    event_journal_path = tmp_path / "events.jsonl"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    journal_path.write_text("[]", encoding="utf-8")
    event_journal_path.write_text("", encoding="utf-8")
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat > "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 22, 15, 50, tzinfo=MODULE.LONDON_TZ),
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Closed trades: 0" in mail_text
    assert "No XAUEX trades closed on this London date." in mail_text


def test_daily_trade_summary_skips_before_cutoff_and_weekends(tmp_path: Path) -> None:
    journal_path = tmp_path / "trade_journal.json"
    event_journal_path = tmp_path / "events.jsonl"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    journal_path.write_text("[]", encoding="utf-8")
    event_journal_path.write_text("", encoding="utf-8")
    _write_executable(sendmail_path, f"#!/usr/bin/env bash\ncat > \"{mail_capture}\"\n")

    before_cutoff = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 22, 15, 40, tzinfo=MODULE.LONDON_TZ),
    )
    weekend = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        event_journal_path=event_journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 25, 15, 50, tzinfo=MODULE.LONDON_TZ),
    )

    assert before_cutoff == 0
    assert weekend == 0
    assert not mail_capture.exists()
    assert not sent_state_path.exists()


def test_daily_trade_summary_is_wired_into_monit_install() -> None:
    monit_config = Path("ops/monitoring/45-xauex-notify.monit").read_text(encoding="utf-8")
    installer = Path("ops/install_systemd.sh").read_text(encoding="utf-8")

    assert "check program xauex-daily-trade-summary" in monit_config
    assert "check_xauex_daily_trade_summary.py --recipient bolyki@bolyki.eu" in monit_config
    assert "check_xauex_daily_trade_summary.py" in installer


def test_installed_daily_trade_summary_script_finds_repo_package(tmp_path: Path) -> None:
    install_dir = tmp_path / "usr" / "local" / "lib" / "monitoring"
    install_dir.mkdir(parents=True)
    target = install_dir / "check_xauex_daily_trade_summary.py"
    target.write_text(MODULE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import importlib.util, sys; "
                f"spec = importlib.util.spec_from_file_location('installed_script', {str(target)!r}); "
                "module = importlib.util.module_from_spec(spec); "
                "sys.modules[spec.name] = module; "
                "spec.loader.exec_module(module)"
            ),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
