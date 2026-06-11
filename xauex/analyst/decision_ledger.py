"""Why-no-trade decision ledger.

Aggregates the event journal, the bot's per-day signal runs, and archived
signal-pipeline runs into one per-window record per London day: did the window
trade, and if not, which gate stopped it. The bot already journals every block
(blocked_trade_candidate, risk_result, signal_decision) but nothing reads them
back — this module is the deterministic reader. No LLM involved.

Output: /var/lib/xauex/decision_ledger.json (XAUEX_DECISION_LEDGER_PATH).
Scheduled Mon-Fri 15:10 London via xauex-decision-ledger.timer.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from xauex.shared.event_journal import read_events
from xauex.shared.safe_io import atomic_write_json

logger = logging.getLogger(__name__)

LONDON_TZ = ZoneInfo("Europe/London")

EVENT_JOURNAL_PATH = os.getenv("XAUEX_EVENT_JOURNAL_PATH", "/var/lib/xauex/events.jsonl")
STATE_PATH = os.getenv("XAUEX_STATE_FILE", os.getenv("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
SIGNAL_RUNS_DIR = os.getenv("XAUEX_SIGNAL_ARCHIVE_DIR", "/var/lib/xauex/signal_runs")
LEDGER_PATH = os.getenv("XAUEX_DECISION_LEDGER_PATH", "/var/lib/xauex/decision_ledger.json")

WINDOW_LABELS = ("morning", "midday", "us_open")

# Journal event types that describe one window's decision flow.
_DECISION_EVENT_TYPES = {
    "signal_decision",
    "risk_result",
    "blocked_trade_candidate",
    "confirm_veto",
    "counter_signal_candidate",
    "pattern_gate_block",
}


def _parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _london_date(moment: datetime) -> str:
    return moment.astimezone(LONDON_TZ).strftime("%Y-%m-%d")


def _window_label_for_event(payload: dict[str, Any]) -> Optional[str]:
    label = str(payload.get("window_label") or "").strip().lower()
    if label in WINDOW_LABELS:
        return label
    slot = str(payload.get("slot") or "").strip().lower()
    if slot in WINDOW_LABELS:
        return slot
    return None


def _gate_manufactured_hold(signal: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Recover the original direction when a parser gate manufactured the HOLD."""
    guard = signal.get("price_conflict_guard")
    if isinstance(guard, dict) and guard.get("original_action") in ("BUY", "SELL"):
        return {
            "gate": "PRICE_CONFLICT",
            "original_action": guard.get("original_action"),
            "original_confidence": guard.get("original_confidence"),
        }
    persistence = signal.get("directional_persistence")
    if isinstance(persistence, dict):
        policy = str(persistence.get("policy") or "")
        if policy.startswith("FLIP_BLOCKED"):
            return {
                "gate": policy,
                "original_action": None,
                "original_confidence": None,
            }
    if str(signal.get("consensus_state") or "") == "blocked":
        return {"gate": "PARSER_BLOCKED", "original_action": None, "original_confidence": None}
    return None


def _load_archived_runs(signal_runs_dir: str, *, since_utc: datetime) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (london_date, window_label) -> parser-level signal facts."""
    root = Path(signal_runs_dir)
    if not root.exists():
        return {}
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        candidates = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError as exc:
        logger.warning("[LEDGER] Cannot scan %s: %s", signal_runs_dir, exc)
        return {}
    for run_dir in candidates:
        try:
            signal = json.loads((run_dir / "signal.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        moment = _parse_utc(signal.get("timestamp_utc"))
        if moment is None or moment < since_utc:
            continue
        window_label = None
        try:
            results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
            window_label = str(results.get("window_label") or "").strip().lower() or None
        except (OSError, json.JSONDecodeError):
            pass
        if window_label not in WINDOW_LABELS:
            continue
        entry: dict[str, Any] = {
            "action": str(signal.get("action") or "HOLD").upper(),
            "confidence": signal.get("confidence"),
            "reasoning": str(signal.get("reasoning") or "")[:200],
            "consensus_state": signal.get("consensus_state"),
            "validator_status": signal.get("validator_status"),
            "stop_loss_distance": signal.get("stop_loss_distance"),
            "take_profit_distance": signal.get("take_profit_distance"),
            "archive_dir": run_dir.name,
        }
        manufactured = _gate_manufactured_hold(signal)
        if manufactured:
            entry["manufactured_hold"] = manufactured
        # Last run per window wins (confirm passes overwrite the signal pass).
        runs[(_london_date(moment), window_label)] = entry
    return runs


def _final_outcome(events: list[dict[str, Any]]) -> tuple[str, str]:
    """Reduce a window's ordered events to (outcome, reason)."""
    outcome = "NO_DECISION"
    reason = "NO_TERMINAL_EVENT"
    for event in events:
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload") or {}
        if event_type == "risk_result":
            event_reason = str(payload.get("reason") or payload.get("action") or "")
            if event_reason == "ORDER_PLACED":
                return "TRADED", "ORDER_PLACED"
            if bool(payload.get("terminal")):
                action = str(payload.get("signal_action") or payload.get("action") or "").upper()
                if event_reason == "HOLD" or action not in ("BUY", "SELL"):
                    outcome, reason = "HOLD", event_reason or "HOLD"
                else:
                    outcome, reason = "BLOCKED", event_reason or "UNKNOWN"
        elif event_type == "blocked_trade_candidate":
            outcome, reason = "BLOCKED", str(payload.get("reason") or "UNKNOWN")
        elif event_type == "pattern_gate_block":
            outcome, reason = "BLOCKED", str(payload.get("reason") or "PATTERN_GATE")
    return outcome, reason


def _reason_chain(events: list[dict[str, Any]]) -> list[str]:
    chain: list[str] = []
    for event in events:
        payload = event.get("payload") or {}
        moment = _parse_utc(event.get("timestamp_utc"))
        stamp = moment.astimezone(LONDON_TZ).strftime("%H:%M") if moment else "--:--"
        event_type = str(event.get("event_type") or "")
        if event_type == "signal_decision":
            descriptor = (
                f"{payload.get('action')}@{payload.get('confidence')}"
                f" confirm={payload.get('confirm_status')}:{payload.get('confirm_reason')}"
            )
        elif event_type == "counter_signal_candidate":
            descriptor = f"counter {payload.get('source_action')}->{payload.get('counter_action')}"
        else:
            descriptor = str(payload.get("reason") or payload.get("confirm_reason") or event_type)
        entry = f"{event_type}@{stamp} {descriptor}"
        if not chain or chain[-1] != entry:
            chain.append(entry)
    return chain[-12:]


def build_decision_ledger(
    *,
    journal_path: str = EVENT_JOURNAL_PATH,
    state_path: str = STATE_PATH,
    signal_runs_dir: str = SIGNAL_RUNS_DIR,
    days: int = 30,
    now_utc: Optional[datetime] = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = now - timedelta(days=days)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in read_events(journal_path):
        if str(event.get("event_type") or "") not in _DECISION_EVENT_TYPES:
            continue
        moment = _parse_utc(event.get("timestamp_utc"))
        if moment is None or moment < since:
            continue
        payload = event.get("payload") or {}
        window_label = _window_label_for_event(payload if isinstance(payload, dict) else {})
        if window_label is None:
            continue
        grouped.setdefault((_london_date(moment), window_label), []).append(event)

    archived = _load_archived_runs(signal_runs_dir, since_utc=since)

    dates = sorted(
        {date for date, _ in grouped} | {date for date, _ in archived},
        reverse=True,
    )
    day_records: list[dict[str, Any]] = []
    reason_totals: Counter[str] = Counter()
    outcome_totals: Counter[str] = Counter()
    for date in dates:
        windows: dict[str, Any] = {}
        for window_label in WINDOW_LABELS:
            key = (date, window_label)
            events = sorted(
                grouped.get(key, []),
                key=lambda item: str(item.get("timestamp_utc") or ""),
            )
            run = archived.get(key)
            if not events and not run:
                windows[window_label] = {"outcome": "NO_SIGNAL", "reason": "NO_PIPELINE_RUN"}
                outcome_totals["NO_SIGNAL"] += 1
                reason_totals["NO_PIPELINE_RUN"] += 1
                continue
            if events:
                outcome, reason = _final_outcome(events)
            elif run and run.get("action") in ("BUY", "SELL"):
                # Pipeline produced a direction but the bot never processed it
                # (bot down, key mismatch, window missed) — the worst gap.
                outcome, reason = "NOT_CONSUMED", "SIGNAL_NEVER_REACHED_BOT"
            else:
                outcome, reason = "HOLD", "HOLD_PARSER"
            record: dict[str, Any] = {"outcome": outcome, "reason": reason}
            if events:
                record["reason_chain"] = _reason_chain(events)
                first_decision = next(
                    (
                        event.get("payload") or {}
                        for event in events
                        if str(event.get("event_type") or "") == "signal_decision"
                    ),
                    {},
                )
                if first_decision:
                    record["signal_action"] = first_decision.get("action")
                    record["signal_confidence"] = first_decision.get("confidence")
                    record["confirm_status"] = first_decision.get("confirm_status")
                    record["confirm_reason"] = first_decision.get("confirm_reason")
            if run:
                record["parser"] = run
                manufactured = run.get("manufactured_hold")
                if outcome == "HOLD" and manufactured:
                    record["outcome"] = "GATE_HOLD"
                    record["reason"] = str(manufactured.get("gate") or "PARSER_GATE")
                    outcome, reason = record["outcome"], record["reason"]
            windows[window_label] = record
            outcome_totals[outcome] += 1
            reason_totals[reason] += 1
        day_records.append({"date_london": date, "windows": windows})

    traded = outcome_totals.get("TRADED", 0)
    total_windows = sum(outcome_totals.values())
    return {
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_days": days,
        "days": day_records,
        "totals": {
            "windows": total_windows,
            "traded": traded,
            "by_outcome": dict(outcome_totals),
            "by_reason": dict(reason_totals.most_common()),
        },
    }


def write_decision_ledger(ledger: dict[str, Any], output_path: str = LEDGER_PATH) -> None:
    atomic_write_json(output_path, ledger)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ledger = build_decision_ledger()
    write_decision_ledger(ledger)
    totals = ledger.get("totals", {})
    logger.info(
        "[LEDGER] Wrote %s: %s windows, %s traded, top reasons: %s",
        LEDGER_PATH,
        totals.get("windows"),
        totals.get("traded"),
        dict(list((totals.get("by_reason") or {}).items())[:5]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
