#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a one-off reminder to review the XAUEX debug capture.")
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--status-path", type=Path, required=True)
    parser.add_argument("--sendmail-bin", type=Path, default=Path("/usr/sbin/sendmail"))
    parser.add_argument("--smtp-host", default="127.0.0.1")
    parser.add_argument("--smtp-port", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail(path: Path, *, max_lines: int = 40) -> str:
    if not path.exists():
        return "(log file not found)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:]) or "(log file is empty)"


def _build_message(*, recipient: str, output_path: Path, status_path: Path) -> EmailMessage:
    status = _load_json(status_path)
    final_run = status.get("final_run") if isinstance(status.get("final_run"), dict) else {}
    body = "\n".join(
        [
            "Reminder to review the XAUEX next-cycle debug capture.",
            "",
            f"Log path: {output_path}",
            f"Status path: {status_path}",
            f"Watcher status: {status.get('status', 'unknown')}",
            f"Armed at UTC: {status.get('armed_at_utc', 'n/a')}",
            f"Target signal id: {status.get('target_signal_id', 'n/a')}",
            f"Completed: {status.get('completed', False)}",
            f"Final outcome: {final_run.get('reason', 'n/a')}",
            f"Final confirm status: {final_run.get('confirm_status', 'n/a')}",
            "",
            "Recent log tail:",
            _tail(output_path),
        ]
    )
    message = EmailMessage()
    message["From"] = "monit@bolyki.eu"
    message["To"] = recipient
    message["Subject"] = "[XAUEX] Check next-cycle debug capture"
    message.set_content(body)
    return message


def _send_via_sendmail(message: EmailMessage, sendmail_bin: Path) -> None:
    proc = subprocess.run(
        [str(sendmail_bin), "-t", "-oi"],
        input=message.as_string(),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"sendmail exited {proc.returncode}").strip())


def _send_via_smtp(message: EmailMessage, smtp_host: str, smtp_port: int) -> None:
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as client:
        client.send_message(message)


def main() -> int:
    args = _parse_args()
    message = _build_message(
        recipient=args.recipient,
        output_path=args.output_path,
        status_path=args.status_path,
    )
    if args.dry_run:
        print(message.as_string())
        return 0
    if args.sendmail_bin.exists():
        _send_via_sendmail(message, args.sendmail_bin)
    else:
        _send_via_smtp(message, args.smtp_host, args.smtp_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
