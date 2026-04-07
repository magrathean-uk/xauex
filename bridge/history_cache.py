"""Local history helpers for the direct oracle predictor path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_recent_signal_history(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def load_recent_trade_memory(path: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = load_recent_signal_history(path)
    out: list[dict[str, Any]] = []
    for item in rows[-limit:]:
        trade = item.get('entry', item) if isinstance(item, dict) else {}
        direction = str(trade.get('direction', '')).upper()
        action = 'BUY' if direction == 'LONG' else 'SELL' if direction == 'SHORT' else direction
        out.append({
            'action': action or 'UNKNOWN',
            'direction': direction or 'UNKNOWN',
            'confidence': float(trade.get('confidence', 0.0) or 0.0),
            'pnl': float(trade.get('pnl', 0.0) or 0.0),
            'pattern': str(trade.get('pattern', '')),
            'journal': str(item.get('journal', '')),
            'entry_price': float(trade.get('entry_price', 0.0) or 0.0),
            'close_price': float(trade.get('close_price', 0.0) or 0.0),
            'close_time_utc': str(trade.get('close_time_utc', '')),
        })
    return out


def load_state_snapshot(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
