from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from xauex.config import load_config
from xauex.live_windows import get_live_window
from xauex.main import build_xauex_confirm_decision
from xauex.signal.signal_writer import write_signal


logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO"), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Confirm the latest XAUEX signal for a live window.")
    parser.add_argument("--window-label", choices=("morning", "midday", "us_open"), required=True)
    parser.add_argument("--signal-path", default=os.getenv("CMD_FILE_PATH", "/var/lib/xauex/cmd.json"))
    parser.add_argument("--state-path", default=os.getenv("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_archive_confirm(signal: dict[str, object]) -> None:
    source = signal.get("source") if isinstance(signal.get("source"), dict) else {}
    archive_dir_text = str(source.get("archive_dir") or "").strip()
    if not archive_dir_text:
        return
    archive_dir = Path(archive_dir_text).expanduser()
    signal_path = archive_dir / "signal.json"
    if not signal_path.exists():
        return
    try:
        payload = json.loads(signal_path.read_text(encoding="utf-8"))
        payload["confirm_status"] = signal.get("confirm_status")
        payload["confirm_reason"] = signal.get("confirm_reason")
        payload["confirm_timestamp_utc"] = signal.get("confirm_timestamp_utc")
        signal_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist confirm result into archive %s: %s", signal_path, exc)


def main() -> None:
    args = _parse_args()
    window = get_live_window(window_label=args.window_label)
    if window is None:
        return
    if os.environ.get("XAUEX_ENFORCE_WINDOW_SCHEDULE", "").lower() in {"1", "true", "yes"}:
        if not window.phase_due(datetime.now(timezone.utc), phase="confirm"):
            logger.info("Skipping %s confirm run because the shared timer fired outside that window's schedule.", args.window_label)
            return

    signal_path = Path(args.signal_path)
    state_path = Path(args.state_path)
    command_payload = _load_json(signal_path)
    signal = command_payload.get("xauex_signal") if isinstance(command_payload, dict) else {}
    if not isinstance(signal, dict):
        return
    if str(signal.get("window_label") or "").lower() != args.window_label:
        logger.info("Latest signal window %s does not match %s; skipping confirm.", signal.get("window_label"), args.window_label)
        return

    state = _load_json(state_path)
    runtime = state.get("runtime") if isinstance(state, dict) and isinstance(state.get("runtime"), dict) else {}
    news_gate = runtime.get("news_gate") if isinstance(runtime.get("news_gate"), dict) else {}
    latest_quote = runtime.get("latest_quote") if isinstance(runtime.get("latest_quote"), dict) else {}
    trend = state.get("trend") if isinstance(state.get("trend"), dict) else {}
    shadow_signal = state.get("shadow_last_signal") if isinstance(state.get("shadow_last_signal"), dict) else {}

    config = load_config()
    confirm = build_xauex_confirm_decision(
        signal=signal,
        now_utc=datetime.now(timezone.utc),
        latest_quote=latest_quote,
        news_gate=news_gate,
        trend_snapshot=trend,
        shadow_signal=shadow_signal,
        config=config,
    )
    signal["confirm_status"] = confirm["status"]
    signal["confirm_reason"] = confirm["reason"]
    signal["confirm_timestamp_utc"] = confirm["timestamp_utc"]
    write_signal(signal, str(signal_path))
    _save_archive_confirm(signal)
    logger.info(
        "Confirm %s -> %s (%s)",
        args.window_label,
        signal["confirm_status"],
        signal["confirm_reason"],
    )


if __name__ == "__main__":
    main()
