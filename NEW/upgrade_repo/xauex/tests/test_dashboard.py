import json
import pytest
from dashboard import (
    _load_json_safe,
    _format_morning_brief,
    _format_setup_score,
    _format_weekly_review,
    _format_journal_entries,
    _sparkline,
    _fmt_signed,
    _policy_summary,
)


# ── _load_json_safe ──────────────────────────────────────────────

def test_load_json_safe_returns_data(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"key": "value"}')
    assert _load_json_safe(str(p)) == {"key": "value"}

def test_load_json_safe_missing_file_returns_none():
    assert _load_json_safe("/nonexistent/path/file.json") is None

def test_load_json_safe_corrupt_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not valid json {{")
    assert _load_json_safe(str(p)) is None


# ── _format_morning_brief ────────────────────────────────────────

def test_format_morning_brief_none():
    out = _format_morning_brief(None)
    assert "no data" in out.lower()

def test_format_morning_brief_normal():
    data = {
        "generated_at_utc": "2026-03-19T07:45:01Z",
        "model": "claude-sonnet-4-6",
        "stale_state": False,
        "brief": "Gold is testing support at W-Low.",
    }
    out = _format_morning_brief(data)
    assert "2026-03-19T07:45:01Z" in out
    assert "claude-sonnet-4-6" in out
    assert "Gold is testing support at W-Low." in out
    assert "STALE" not in out

def test_format_morning_brief_stale():
    data = {
        "generated_at_utc": "2026-03-19T07:45:01Z",
        "model": "claude-sonnet-4-6",
        "stale_state": True,
        "brief": "Some text.",
    }
    out = _format_morning_brief(data)
    assert "STALE" in out


# ── _format_setup_score ──────────────────────────────────────────

def test_format_setup_score_none():
    out = _format_setup_score(None)
    assert "no data" in out.lower()

def test_format_setup_score_empty_list():
    out = _format_setup_score([])
    assert "no data" in out.lower()

def test_format_setup_score_uses_last_entry():
    scores = [
        {"signal_ts": "2026-03-19T06:00:00Z",
         "breakdown": "Old entry.", "signal": {"direction": "SHORT", "pattern": "Engulfing", "level_checked": "M-High"}},
        {"signal_ts": "2026-03-19T09:00:00Z",
         "breakdown": "Clean pinbar at W-Low.", "signal": {"direction": "LONG", "pattern": "Pinbar", "level_checked": "W-Low"}},
    ]
    out = _format_setup_score(scores)
    assert "LONG" in out
    assert "Pinbar" in out
    assert "W-Low" in out
    assert "Clean pinbar at W-Low." in out
    assert "Old entry." not in out


# ── _format_weekly_review ────────────────────────────────────────

def test_format_weekly_review_none():
    out = _format_weekly_review(None)
    assert "no data" in out.lower()

def test_format_weekly_review_normal():
    data = {
        "week_starting": "2026-03-09",
        "week_ending": "2026-03-15",
        "generated_at_utc": "2026-03-16T08:00:00Z",
        "model": "claude-opus-4-6",
        "trades_reviewed": 4,
        "setups_reviewed": 12,
        "review": "Win rate 75%. Strong performance.",
    }
    out = _format_weekly_review(data)
    assert "2026-03-09" in out
    assert "2026-03-15" in out
    assert "4 trades" in out
    assert "Win rate 75%" in out


# ── _format_journal_entries ──────────────────────────────────────

def test_format_journal_entries_none():
    out = _format_journal_entries(None)
    assert "no entries" in out.lower()

def test_format_journal_entries_empty():
    out = _format_journal_entries([])
    assert "no entries" in out.lower()

def test_format_journal_entries_shows_newest_first():
    entries = [
        {
            "trade_id": "pos001",
            "journalled_at_utc": "2026-03-19T09:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "LONG", "entry_price": 2839.50, "pnl": 110.0, "pattern": "Pinbar"},
            "journal": "First trade journalled.",
        },
        {
            "trade_id": "pos002",
            "journalled_at_utc": "2026-03-19T11:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "SHORT", "entry_price": 2861.00, "pnl": -45.0, "pattern": "Engulfing"},
            "journal": "Second trade journalled.",
        },
    ]
    out = _format_journal_entries(entries)
    assert out.index("pos002") < out.index("pos001")
    assert "2839.50" in out
    assert "First trade journalled." in out

def test_format_journal_entries_caps_at_20():
    entries = [
        {
            "trade_id": f"pos{i:03d}",
            "journalled_at_utc": f"2026-03-{i:02d}T09:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "LONG", "entry_price": 2840.0, "pnl": 10.0},
            "journal": f"Journal entry {i}.",
        }
        for i in range(1, 26)  # 25 entries
    ]
    out = _format_journal_entries(entries)
    assert "pos025" in out
    assert "pos006" in out
    assert "pos005" not in out
    assert "pos001" not in out


def test_sparkline_renders_compact_output():
    out = _sparkline([1, 2, 3, 4, 5])
    assert len(out) >= 5
    assert "(no data)" not in out


def test_fmt_signed_adds_sign():
    assert _fmt_signed(12.5) == "+12.50"
    assert _fmt_signed(-3.25) == "-3.25"


def test_policy_summary_formats_policy():
    out = _policy_summary({"mode": "CAUTIOUS", "direction": "SHORT_ONLY", "aggressiveness": 0.4})
    assert "CAUTIOUS" in out
    assert "SHORT_ONLY" in out
