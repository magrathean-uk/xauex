from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys


MODULE_PATH = Path("ops/monitoring/check_xauex_signal_stall.py")
assert MODULE_PATH.exists(), "signal stall monitoring script must exist"
SPEC = importlib.util.spec_from_file_location("xauex_signal_stall", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_signal_run(
    archive_root: Path,
    *,
    run_id: str,
    timestamp_utc: str,
    action: str,
    window_label: str,
    reasoning: str,
    freshness: dict[str, object],
    validator_summary: str | None = None,
) -> None:
    run_dir = archive_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "signal.json").write_text(
        json.dumps(
            {
                "timestamp_utc": timestamp_utc,
                "action": action,
                "window_label": window_label,
                "reasoning": reasoning,
                "validator_summary": validator_summary or reasoning,
                "decision_packet": {
                    "input_freshness": freshness,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "evidence.json").write_text(
        json.dumps(
            {
                "input_freshness": freshness,
            }
        ),
        encoding="utf-8",
    )


def test_signal_stall_alert_sends_once_for_repeated_source_blocked_holds(tmp_path: Path) -> None:
    archive_root = tmp_path / "signal_runs"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"
    freshness = {
        "market_snapshot_state": "blocked",
        "hard_blocker": True,
        "missing_series_count": 3,
        "stale_block_series_count": 0,
        "cache_fallback_series_count": 0,
        "fed_h15_fallback_series_count": 0,
        "summary": "Structured market snapshot unavailable; missing FRED series.",
    }
    for index, window_label in enumerate(("morning", "midday", "us_open"), start=1):
        _write_signal_run(
            archive_root,
            run_id=f"20260610T0{index}0000Z_xauusd_baseline",
            timestamp_utc=f"2026-06-10T0{index}:00:00Z",
            action="HOLD",
            window_label=window_label,
            reasoning="Structured market snapshot unavailable; missing FRED series.",
            freshness=freshness,
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
        archive_root=archive_root,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc),
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX signal stall on bolykihu" in mail_text
    assert "Source-blocked HOLD runs: 3" in mail_text
    assert "Structured market snapshot unavailable; missing FRED series." in mail_text
    saved_state = json.loads(sent_state_path.read_text(encoding="utf-8"))
    assert "stall:2026-06-10" in saved_state["sent_alert_keys"]

    rc_again = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        archive_root=archive_root,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 6, 10, 13, 5, tzinfo=timezone.utc),
    )

    assert rc_again == 0
    assert mail_capture.read_text(encoding="utf-8") == mail_text


def test_signal_stall_ignores_normal_low_assurance_holds(tmp_path: Path) -> None:
    archive_root = tmp_path / "signal_runs"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"
    freshness = {
        "market_snapshot_state": "fresh",
        "hard_blocker": False,
        "missing_series_count": 0,
        "stale_block_series_count": 0,
        "cache_fallback_series_count": 0,
        "fed_h15_fallback_series_count": 0,
        "summary": "Structured market snapshot is fresh.",
    }
    for index, window_label in enumerate(("morning", "midday", "us_open"), start=1):
        _write_signal_run(
            archive_root,
            run_id=f"20260610T0{index}0000Z_xauusd_baseline",
            timestamp_utc=f"2026-06-10T0{index}:00:00Z",
            action="HOLD",
            window_label=window_label,
            reasoning="Low assurance; no trade.",
            freshness=freshness,
        )
    _write_executable(
        sendmail_path,
        f"""#!/usr/bin/env bash
cat >> "{mail_capture}"
""",
    )

    rc = MODULE.run_once(
        recipient="bolyki@bolyki.eu",
        archive_root=archive_root,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc),
    )

    assert rc == 0
    assert not mail_capture.exists()
    assert not sent_state_path.exists()


def test_signal_source_degradation_alerts_on_repeated_fallbacks(tmp_path: Path) -> None:
    archive_root = tmp_path / "signal_runs"
    sent_state_path = tmp_path / "sent.json"
    sendmail_path = tmp_path / "sendmail"
    mail_capture = tmp_path / "mail.txt"
    freshness = {
        "market_snapshot_state": "warning",
        "hard_blocker": False,
        "missing_series_count": 0,
        "stale_block_series_count": 2,
        "cache_fallback_series_count": 5,
        "fed_h15_fallback_series_count": 5,
        "summary": (
            "5 structured market series reused from archived fallback after source fetch failures. "
            "5 Treasury market series reused from Federal Reserve H.15 fallback after FRED fetch failures."
        ),
    }
    for index, action in enumerate(("BUY", "SELL", "SELL"), start=1):
        _write_signal_run(
            archive_root,
            run_id=f"20260610T1{index}0000Z_xauusd_baseline",
            timestamp_utc=f"2026-06-10T1{index}:00:00Z",
            action=action,
            window_label=("morning", "midday", "us_open")[index - 1],
            reasoning="Directional setup exists despite degraded inputs.",
            freshness=freshness,
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
        archive_root=archive_root,
        sent_state_path=sent_state_path,
        sendmail_bin=sendmail_path,
        smtp_host="127.0.0.1",
        smtp_port=25,
        hostname="bolykihu",
        sender_domain="bolyki.eu",
        now=datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc),
    )

    assert rc == 0
    mail_text = mail_capture.read_text(encoding="utf-8")
    assert "Subject: [Monit] XAUEX signal sources degraded on bolykihu" in mail_text
    assert "Degraded source runs: 3" in mail_text
    assert "archived fallback" in mail_text
    assert "Federal Reserve H.15 fallback" in mail_text


def test_signal_stall_alert_is_wired_into_monit_install() -> None:
    monit_config = Path("ops/monitoring/45-xauex-notify.monit").read_text(encoding="utf-8")
    installer = Path("ops/install_systemd.sh").read_text(encoding="utf-8")

    assert "check program xauex-signal-stall" in monit_config
    assert "check_xauex_signal_stall.py --recipient bolyki@bolyki.eu" in monit_config
    assert "check_xauex_signal_stall.py" in installer


def test_installed_signal_stall_script_finds_repo_package(tmp_path: Path) -> None:
    install_dir = tmp_path / "usr" / "local" / "lib" / "monitoring"
    install_dir.mkdir(parents=True)
    target = install_dir / "check_xauex_signal_stall.py"
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
