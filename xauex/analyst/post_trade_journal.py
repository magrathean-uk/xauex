"""Post-trade journal — polls every 5 min Mon-Fri via cron."""

import logging
import os
import sys
from typing import Dict, List

from xauex.analyst._utils import (
    default_model,
    append_to_json_list,
    call_claude,
    is_state_stale,
    load_cursor,
    read_json_file,
    save_cursor,
    utcnow_str,
)
from xauex.shared.event_journal import read_events

logger = logging.getLogger(__name__)

MODEL = default_model()
STALE_SECONDS = 10 * 60  # 10 minutes

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
CURSOR_PATH = os.environ.get("XAUEX_JOURNAL_CURSOR", "/var/lib/xauex/trade_journal_cursor.json")
JOURNAL_PATH = os.environ.get("XAUEX_JOURNAL_OUTPUT", "/var/lib/xauex/trade_journal.json")
EVENT_JOURNAL_PATH = os.environ.get("XAUEX_EVENT_JOURNAL_PATH", "/var/lib/xauex/events.jsonl")

_DEFAULT_CURSOR = {"journalled_ids": []}


def find_new_trades(trades: List[Dict], cursor: Dict) -> List[Dict]:
    """Return trades whose position_id is not in cursor['journalled_ids']."""
    seen = set(cursor.get("journalled_ids", []))
    return [t for t in trades if t.get("position_id") not in seen]


def _first_value(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _direction_from_payload(*values) -> str:
    for value in values:
        text = str(value or "").strip().upper()
        if text in {"LONG", "BUY"}:
            return "LONG"
        if text in {"SHORT", "SELL"}:
            return "SHORT"
    return "UNKNOWN"


def _closed_trades_from_events(event_journal_path: str) -> List[Dict]:
    """Reconstruct closed trade records from the append-only event journal."""
    events = read_events(event_journal_path)
    intents_by_correlation: Dict[str, Dict] = {}
    opens_by_position: Dict[str, Dict] = {}
    correlation_by_position: Dict[str, str] = {}
    closes: List[Dict] = []

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        correlation_id = str(event.get("correlation_id") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "order_intent" and correlation_id:
            intents_by_correlation[correlation_id] = dict(payload)
        elif event_type == "order_ack":
            position_id = str(payload.get("position_id") or "")
            if position_id and correlation_id:
                correlation_by_position[position_id] = correlation_id
        elif event_type == "position_opened":
            position_id = str(payload.get("position_id") or "")
            if position_id:
                opens_by_position[position_id] = dict(payload)
                if correlation_id:
                    correlation_by_position[position_id] = correlation_id
        elif event_type == "position_closed":
            closes.append(event)

    trades: List[Dict] = []
    for event in closes:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        position_id = str(payload.get("position_id") or "")
        if not position_id:
            continue
        correlation_id = correlation_by_position.get(position_id) or str(event.get("correlation_id") or "")
        intent = intents_by_correlation.get(correlation_id, {})
        opened = opens_by_position.get(position_id, {})
        metadata = intent.get("metadata") if isinstance(intent.get("metadata"), dict) else {}
        signal_context = metadata.get("signal_context") if isinstance(metadata.get("signal_context"), dict) else {}
        entry_price = _first_value(payload.get("entry_price"), opened.get("entry_price"), intent.get("entry_price"), 0.0)
        trade = {
            "position_id": position_id,
            "direction": _direction_from_payload(payload.get("direction"), opened.get("direction"), intent.get("direction")),
            "entry_price": entry_price,
            "close_price": _first_value(payload.get("close_price"), 0.0),
            "stop_loss": _first_value(payload.get("stop_loss"), opened.get("stop_loss"), intent.get("stop_loss_price"), 0.0),
            "take_profit": _first_value(payload.get("take_profit"), opened.get("take_profit"), intent.get("take_profit_price"), 0.0),
            "lot_size": _first_value(payload.get("lot_size"), opened.get("lot_size"), intent.get("lot_size"), 0.0),
            "pnl": _first_value(payload.get("pnl"), 0.0),
            "pattern": _first_value(payload.get("pattern"), opened.get("pattern"), intent.get("pattern"), "NONE"),
            "level": _first_value(payload.get("level"), opened.get("level"), intent.get("entry_price"), entry_price),
            "owner": _first_value(payload.get("owner"), opened.get("owner"), intent.get("owner"), "unknown"),
            "metadata": metadata,
            "close_time_utc": str(event.get("timestamp_utc") or "unknown"),
        }
        for context_key in _SIGNAL_CONTEXT_KEYS:
            if signal_context and context_key in signal_context:
                trade[context_key] = signal_context[context_key]
            elif context_key in metadata:
                trade[context_key] = metadata[context_key]
        trades.append(trade)
    return trades


def _merge_trades(*trade_lists: List[Dict]) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    for trades in trade_lists:
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            position_id = str(trade.get("position_id") or "")
            if not position_id:
                continue
            merged.setdefault(position_id, trade)
    return list(merged.values())


_SIGNAL_CONTEXT_KEYS = (
    "signal_confidence",
    "signal_action",
    "consensus_state",
    "validator_status",
    "validator_summary",
    "decision_mode",
    "daily_trend_bias",
    "range_position",
    "regime_filter",
    "market_snapshot_age_seconds",
    "market_snapshot_state",
)


def _signal_context_block(trade: Dict) -> str:
    metadata = trade.get("metadata") or {}
    nested = metadata.get("signal_context") if isinstance(metadata.get("signal_context"), dict) else {}
    fields = {}
    for key in _SIGNAL_CONTEXT_KEYS:
        if key in trade and trade[key] not in (None, ""):
            fields[key] = trade[key]
        elif key in nested and nested[key] not in (None, ""):
            fields[key] = nested[key]
        elif key in metadata and metadata[key] not in (None, ""):
            fields[key] = metadata[key]
    if not fields:
        return ""
    lines = ["", "SIGNAL CONTEXT (recorded at entry):"]
    for key, value in fields.items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def build_trade_prompt(trade: Dict) -> str:
    """Build the analyst prompt for a single closed trade."""
    direction = trade["direction"]
    entry = trade["entry_price"]
    close = trade["close_price"]
    sl = trade["stop_loss"]
    tp = trade["take_profit"]
    lots = trade["lot_size"]
    pnl = trade["pnl"]
    pattern = trade["pattern"]
    level = trade["level"]
    close_time = trade.get("close_time_utc", "unknown")

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    rr_planned = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    pnl_per_lot = round(pnl / lots, 2) if lots > 0 else 0
    outcome = "WIN" if pnl >= 0 else "LOSS"

    signal_block = _signal_context_block(trade)
    context_instruction = (
        " Comment on whether the outcome matches what the signal confidence and consensus state suggested."
        if signal_block
        else ""
    )

    return f"""You are a trading journal assistant for an automated XAUUSD bot. Write a concise post-trade journal entry (3-5 sentences) for the following trade.

TRADE SUMMARY:
  Outcome: {outcome}
  Direction: {direction}
  Pattern: {pattern}
  HTF Level: {level}
  Entry: {entry}  Close: {close}  Close time: {close_time}
  Stop Loss: {sl} (distance: {sl_dist:.2f} USD)
  Take Profit: {tp} (distance: {tp_dist:.2f} USD)
  Planned RR: {rr_planned}
  Lot size: {lots}
  P&L: {pnl:.2f} USD ({pnl_per_lot:.2f} USD/lot){signal_block}

Write a journal entry covering: what the setup looked like, whether execution followed the rules, and what can be learned from this trade.{context_instruction}"""


def run(
    state_path: str = STATE_PATH,
    cursor_path: str = CURSOR_PATH,
    journal_path: str = JOURNAL_PATH,
    event_journal_path: str = EVENT_JOURNAL_PATH,
) -> None:
    state = read_json_file(state_path)
    state_stale = is_state_stale(state, max_age_seconds=STALE_SECONDS)

    state_trades = [] if state_stale or not isinstance(state, dict) else state.get("closed_trades_today", [])
    event_trades = _closed_trades_from_events(event_journal_path)
    trades = _merge_trades(state_trades if isinstance(state_trades, list) else [], event_trades)
    if not trades:
        if state_stale:
            logger.warning("[JOURNAL] state.json stale or missing and no closed events found — skipping.")
        else:
            logger.info("[JOURNAL] No closed trades today.")
        return

    cursor = load_cursor(cursor_path, default=_DEFAULT_CURSOR)
    new_trades = find_new_trades(trades, cursor)
    if not new_trades:
        logger.info("[JOURNAL] No new trades to journal.")
        return

    journalled_ids = list(cursor.get("journalled_ids", []))

    for trade in new_trades:
        trade_id = trade["position_id"]
        logger.info("[JOURNAL] Journalling trade %s...", trade_id)
        prompt = build_trade_prompt(trade)
        entry_text = call_claude(prompt, MODEL)

        entry = {
            "trade_id": trade_id,
            "journalled_at_utc": utcnow_str(),
            "model": MODEL,
            "entry": trade,
            "journal": entry_text,
        }
        append_to_json_list(journal_path, entry)
        journalled_ids.append(trade_id)
        save_cursor(cursor_path, {"journalled_ids": journalled_ids})
        logger.info("[JOURNAL] Journalled trade %s.", trade_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[JOURNAL] Failed: %s", e)
        sys.exit(1)
