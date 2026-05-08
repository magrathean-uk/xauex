# ruff: noqa: E402
"""Yesterday's outcomes section in the morning brief.

The legacy morning brief showed only the current-state snapshot — open
positions and the last few signals. Operators arriving at the London open
had no view of yesterday's actual trade outcomes (direction skew, pattern
hits, PnL split). Add a deterministic "Yesterday's outcomes" block that
the analyst LLM can quote directly.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from xauex.analyst.morning_brief import (
    build_yesterday_outcomes_section,
    filter_recent_journal_entries,
)


def _entry(close_iso: str, direction: str, pnl: float, pattern: str = "NONE") -> dict:
    return {
        "trade_id": f"t-{close_iso}-{pnl}",
        "entry": {
            "direction": direction,
            "pnl": pnl,
            "pattern": pattern,
            "close_time_utc": close_iso,
        },
        "journalled_at_utc": close_iso,
    }


def test_filter_recent_journal_entries_keeps_last_24h():
    now = datetime(2026, 5, 9, 7, 0, 0, tzinfo=timezone.utc)
    entries = [
        _entry("2026-05-08T14:00:00Z", "LONG", 7.0),     # 17h ago — within 24h
        _entry("2026-05-08T08:00:00Z", "SHORT", -18.0),  # 23h ago — within 24h
        _entry("2026-05-08T07:30:00Z", "LONG", 5.0),     # 23.5h ago — within 24h
        _entry("2026-05-07T06:00:00Z", "SHORT", -10.0),  # >24h ago — outside
    ]
    recent = filter_recent_journal_entries(entries, now=now, lookback_hours=24)
    assert len(recent) == 3
    pnls = [e["entry"]["pnl"] for e in recent]
    assert -10.0 not in pnls


def test_build_yesterday_outcomes_section_returns_no_trades_when_empty():
    section = build_yesterday_outcomes_section(
        journal_entries=[],
        now=datetime(2026, 5, 9, 7, 0, 0, tzinfo=timezone.utc),
    )
    assert "No trades closed in the last 24 hours" in section


def test_build_yesterday_outcomes_section_summarises_direction_split():
    entries = [
        _entry("2026-05-08T08:00:00Z", "SHORT", -18.0),
        _entry("2026-05-08T11:00:00Z", "SHORT", -17.0),
        _entry("2026-05-08T13:00:00Z", "SHORT", 5.0),
        _entry("2026-05-08T14:00:00Z", "LONG", 7.0, pattern="BULLISH_ENGULFING"),
    ]
    section = build_yesterday_outcomes_section(
        journal_entries=entries,
        now=datetime(2026, 5, 9, 7, 0, 0, tzinfo=timezone.utc),
    )
    assert "4 trades closed" in section
    assert "LONG: 1 trade" in section or "LONG: 1 trades" in section
    assert "SHORT: 3 trades" in section
    assert "BULLISH_ENGULFING" in section
    assert "Net PnL" in section


def test_build_yesterday_outcomes_section_flags_direction_skew_above_threshold():
    entries = [_entry("2026-05-08T08:00:00Z", "SHORT", -3.0) for _ in range(8)]
    entries.append(_entry("2026-05-08T15:00:00Z", "LONG", 5.0))
    section = build_yesterday_outcomes_section(
        journal_entries=entries,
        now=datetime(2026, 5, 9, 7, 0, 0, tzinfo=timezone.utc),
    )
    assert "DIRECTION SKEW" in section


def test_build_yesterday_outcomes_section_flags_pattern_starvation():
    entries = [_entry("2026-05-08T08:00:00Z", "SHORT", -3.0, pattern="NONE") for _ in range(5)]
    section = build_yesterday_outcomes_section(
        journal_entries=entries,
        now=datetime(2026, 5, 9, 7, 0, 0, tzinfo=timezone.utc),
    )
    assert "PATTERN HIT RATE" in section
