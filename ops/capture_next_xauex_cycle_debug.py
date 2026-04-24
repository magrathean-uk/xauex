#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_text() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture the next XAUEX signal/confirm/execution cycle into one log.")
    parser.add_argument("--output-path", type=Path, default=Path("/var/log/xauex/next-cycle-debug.jsonl"))
    parser.add_argument("--status-path", type=Path, default=Path("/var/lib/xauex/next-cycle-debug-status.json"))
    parser.add_argument("--cmd-path", type=Path, default=Path("/var/lib/xauex/cmd.json"))
    parser.add_argument("--state-path", type=Path, default=Path("/var/lib/xauex/state.json"))
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=36 * 60 * 60)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_event(handle, *, event: str, payload: dict[str, Any]) -> None:
    record = {
        "ts_utc": utcnow_text(),
        "event": event,
        **payload,
    }
    handle.write(json.dumps(record, sort_keys=True) + "\n")
    handle.flush()


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8", "ignore")
    return hashlib.sha256(encoded).hexdigest()


def _summarize_signal(cmd_payload: dict[str, Any]) -> dict[str, Any]:
    signal = cmd_payload.get("xauex_signal") if isinstance(cmd_payload.get("xauex_signal"), dict) else {}
    return {
        "generated_at_utc": cmd_payload.get("generated_at_utc"),
        "kill_switch": bool(cmd_payload.get("kill_switch")),
        "signal_id": signal.get("timestamp_utc"),
        "window_label": signal.get("window_label"),
        "action": signal.get("action"),
        "confidence": signal.get("confidence"),
        "confirm_status": signal.get("confirm_status"),
        "confirm_reason": signal.get("confirm_reason"),
        "reasoning": signal.get("reasoning"),
        "decision_mode": signal.get("decision_mode"),
    }


def _summarize_state(state_payload: dict[str, Any]) -> dict[str, Any]:
    runtime = state_payload.get("runtime") if isinstance(state_payload.get("runtime"), dict) else {}
    risk = state_payload.get("risk") if isinstance(state_payload.get("risk"), dict) else {}
    return {
        "bot_status": state_payload.get("bot_status"),
        "account": state_payload.get("account"),
        "open_positions": state_payload.get("open_positions") or [],
        "news_gate": runtime.get("news_gate"),
        "latest_quote": runtime.get("latest_quote"),
        "xauex_max_trades_per_day": runtime.get("xauex_max_trades_per_day"),
        "xauex_signal_runs_london": runtime.get("xauex_signal_runs_london")
        or risk.get("xauex_signal_runs_london")
        or [],
        "xauex_trades_taken_london": runtime.get("xauex_trades_taken_london")
        or risk.get("xauex_trades_taken_london"),
    }


def _discover_log_paths(repo_root: Path) -> list[Path]:
    candidates = [Path("/var/log/xauex/xauex.log")]
    log_dir = repo_root / "logs"
    patterns = (
        "xauex-signal*.log",
        "xauex-confirm*.log",
    )
    if log_dir.exists():
        for pattern in patterns:
            candidates.extend(sorted(log_dir.glob(pattern)))
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _log_service_status(handle, *, units: list[str]) -> None:
    for unit in units:
        proc = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        _append_event(
            handle,
            event="service_status",
            payload={
                "unit": unit,
                "status": (proc.stdout or proc.stderr).strip(),
            },
        )


def main() -> int:
    args = _parse_args()
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.status_path.parent.mkdir(parents=True, exist_ok=True)

    existing_status = _load_json(args.status_path)
    if bool(existing_status.get("completed")):
        return 0

    armed_at = utcnow()
    armed_at_text = armed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    status: dict[str, Any] = {
        "status": "armed",
        "armed_at_utc": armed_at_text,
        "host": socket.gethostname(),
        "output_path": str(args.output_path),
        "target_signal_id": None,
        "completed": False,
    }
    if existing_status:
        status.update(existing_status)
        armed_at_text = str(status.get("armed_at_utc") or armed_at_text)
        try:
            armed_at = datetime.fromisoformat(armed_at_text.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            armed_at = utcnow()
            armed_at_text = armed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            status["armed_at_utc"] = armed_at_text
    _write_json_atomic(args.status_path, status)

    watched_logs: dict[str, int] = {}
    last_signal_hash = ""
    last_state_hash = ""
    target_signal_id: str | None = str(status.get("target_signal_id") or "") or None
    target_generated_at: str | None = str(status.get("generated_at_utc") or "") or None
    completion_logged = False

    with args.output_path.open("a", encoding="utf-8") as handle:
        if existing_status:
            _append_event(
                handle,
                event="resumed",
                payload={
                    "armed_at_utc": armed_at_text,
                    "host": socket.gethostname(),
                    "status": status.get("status"),
                    "target_signal_id": target_signal_id,
                },
            )
        else:
            _append_event(
                handle,
                event="armed",
                payload={
                    "armed_at_utc": armed_at_text,
                    "host": socket.gethostname(),
                    "cmd_path": str(args.cmd_path),
                    "state_path": str(args.state_path),
                },
            )
        _log_service_status(
            handle,
            units=[
                "xauex.service",
                "xauex-window-signal@morning.timer",
                "xauex-window-signal@midday.timer",
                "xauex-window-signal@us_open.timer",
                "xauex-window-confirm@morning.timer",
                "xauex-window-confirm@midday.timer",
                "xauex-window-confirm@us_open.timer",
            ],
        )

        deadline = time.monotonic() + max(1, int(args.timeout_seconds))
        while time.monotonic() < deadline:
            for path in _discover_log_paths(args.repo_root):
                key = str(path)
                if key not in watched_logs:
                    try:
                        watched_logs[key] = path.stat().st_size
                    except FileNotFoundError:
                        continue
                    _append_event(handle, event="log_attached", payload={"source": key})
                    continue
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                offset = watched_logs[key]
                if size < offset:
                    offset = 0
                if size == offset:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as source:
                    source.seek(offset)
                    for raw_line in source:
                        line = raw_line.rstrip("\n")
                        if not line:
                            continue
                        _append_event(
                            handle,
                            event="service_log_line",
                            payload={"source": key, "line": line},
                        )
                    watched_logs[key] = source.tell()

            cmd_payload = _load_json(args.cmd_path)
            signal_summary = _summarize_signal(cmd_payload)
            signal_hash = _hash_payload(signal_summary)
            if signal_hash != last_signal_hash:
                last_signal_hash = signal_hash
                _append_event(handle, event="cmd_snapshot", payload=signal_summary)
            generated_at = str(signal_summary.get("generated_at_utc") or "")
            if generated_at and generated_at > armed_at_text and generated_at != target_generated_at:
                target_generated_at = generated_at
                target_signal_id = str(signal_summary.get("signal_id") or "") or None
                status.update(
                    {
                        "status": "signal_generated",
                        "generated_at_utc": generated_at,
                        "target_signal_id": target_signal_id,
                        "window_label": signal_summary.get("window_label"),
                        "action": signal_summary.get("action"),
                        "confidence": signal_summary.get("confidence"),
                    }
                )
                _write_json_atomic(args.status_path, status)
                _append_event(handle, event="target_signal_selected", payload=signal_summary)

            state_payload = _load_json(args.state_path)
            state_summary = _summarize_state(state_payload)
            state_hash_input = {
                "bot_status": state_summary.get("bot_status"),
                "open_positions": state_summary.get("open_positions"),
                "news_gate": {
                    "clear": ((state_summary.get("news_gate") or {}) if isinstance(state_summary.get("news_gate"), dict) else {}).get("clear"),
                    "reason": ((state_summary.get("news_gate") or {}) if isinstance(state_summary.get("news_gate"), dict) else {}).get("reason"),
                },
                "xauex_signal_runs_london": state_summary.get("xauex_signal_runs_london"),
                "xauex_trades_taken_london": state_summary.get("xauex_trades_taken_london"),
            }
            if target_signal_id:
                state_hash_input["latest_quote"] = state_summary.get("latest_quote")
            state_hash = _hash_payload(state_hash_input)
            if state_hash != last_state_hash:
                last_state_hash = state_hash
                _append_event(handle, event="state_snapshot", payload=state_summary)

            if target_signal_id:
                runs = state_summary.get("xauex_signal_runs_london") or []
                matching_run = next(
                    (
                        item
                        for item in runs
                        if isinstance(item, dict) and str(item.get("signal_id") or "") == target_signal_id
                    ),
                    None,
                )
                if matching_run is not None:
                    _append_event(handle, event="signal_run_observed", payload={"run": matching_run})
                    if bool(matching_run.get("terminal")) and not completion_logged:
                        completion_logged = True
                        status.update(
                            {
                                "status": "completed",
                                "completed": True,
                                "completed_at_utc": utcnow_text(),
                                "final_run": matching_run,
                            }
                        )
                        _write_json_atomic(args.status_path, status)
                        _append_event(
                            handle,
                            event="cycle_complete",
                            payload={
                                "run": matching_run,
                                "signal": signal_summary,
                                "state": state_summary,
                            },
                        )
                        return 0

            time.sleep(max(0.5, float(args.poll_seconds)))

        status.update(
            {
                "status": "timed_out",
                "completed": False,
                "completed_at_utc": utcnow_text(),
            }
        )
        _write_json_atomic(args.status_path, status)
        _append_event(
            handle,
            event="cycle_timeout",
            payload={"timeout_seconds": int(args.timeout_seconds), "target_signal_id": target_signal_id},
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
