from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys

from xauex.shared.event_journal import append_event


MODULE_PATH = Path("ops/monitoring/check_xauex_trade_alerts.py")
SPEC = importlib.util.spec_from_file_location("xauex_trade_alerts", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_trade_alert_sends_position_open_email_once(tmp_path: Path) -> None:
    journal_path = tmp_path / "events.jsonl"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"

    append_event(
        journal_path,
        source="executor",
        event_type="order_intent",
        event_id="intent-1",
        correlation_id="sig-1",
        timestamp_utc="2026-04-26T07:49:04Z",
        payload={
            "symbol": "LTCUSD",
            "direction": "BUY",
            "lot_size": 0.01,
            "entry_price": 56.83,
            "stop_loss_price": 46.83,
            "take_profit_price": 66.83,
            "owner": "manual",
        },
    )
    append_event(
        journal_path,
        source="executor",
        event_type="position_opened",
        event_id="open-1",
        correlation_id="sig-1",
        timestamp_utc="2026-04-26T07:49:38Z",
        payload={
            "symbol": "LTCUSD",
            "direction": "LONG",
            "lot_size": 0.01,
            "entry_price": 56.83,
            "stop_loss": 46.83,
            "take_profit": 66.83,
            "owner": "manual",
            "order_id": "960960172",
            "position_id": "611304208",
        },
    )
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
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 26, 7, 50, tzinfo=timezone.utc),
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX trade opened LTCUSD LONG 0.01 on bolykihu" in mail_text
    assert "Trade opened: LTCUSD LONG" in mail_text
    assert "Owner: manual" in mail_text
    assert "Position id: 611304208" in mail_text
    assert "Order id: 960960172" in mail_text
    assert "Entry: 56.83" in mail_text
    assert "Stop loss: 46.83" in mail_text
    assert "Take profit: 66.83" in mail_text
    assert "Correlation id: sig-1" in mail_text
    saved_state = json.loads(sent_state_path.read_text(encoding="utf-8"))
    assert saved_state["sent_event_ids"] == ["open-1"]

    rc_again = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        journal_path=journal_path,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 4, 26, 7, 51, tzinfo=timezone.utc),
    )

    assert rc_again == 0
    assert mail_capture.read_text(encoding="utf-8") == mail_text


def test_trade_alert_is_wired_into_monit_install() -> None:
    monit_config = Path("ops/monitoring/45-xauex-notify.monit").read_text(encoding="utf-8")
    installer = Path("ops/install_systemd.sh").read_text(encoding="utf-8")

    assert "check program xauex-trade-alerts" in monit_config
    assert "check_xauex_trade_alerts.py --recipient bolyki@bolyki.eu" in monit_config
    assert "check_xauex_trade_alerts.py" in installer


def test_installed_monitoring_scripts_find_repo_package(tmp_path: Path) -> None:
    install_dir = tmp_path / "usr" / "local" / "lib" / "monitoring"
    install_dir.mkdir(parents=True)

    for script_name in (
        "check_xauex_trade_alerts.py",
        "check_xauex_morning_summary.py",
        "check_xauex_daily_trade_summary.py",
        "check_xauex_signal_stall.py",
    ):
        source = Path("ops/monitoring") / script_name
        target = install_dir / script_name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
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
