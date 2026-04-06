"""Tests for analyst.morning_brief."""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from analyst.morning_brief import (
    build_news_section,
    build_prompt,
    run,
)

FRESH_STATE = {
    "meta": {
        "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bot_status": "RUNNING",
    },
    "account": {"balance": 10000.0, "equity": 10050.0},
    "risk": {
        "consecutive_losses_today": 1,
        "weekly_pnl": -150.0,
        "weekly_halted": False,
        "daily_halted": False,
    },
    "levels": {
        "weekly": {"open": 3300.0, "high": 3310.0, "low": 3290.0, "close": 3305.0},
        "monthly": {"open": 3200.0, "high": 3350.0, "low": 3180.0, "close": 3305.0},
        "daily": {"open": 3302.0, "high": 3308.0, "low": 3298.0, "close": 3305.0},
    },
    "open_positions": [
        {
            "position_id": "p1",
            "direction": "LONG",
            "entry_price": 3301.0,
            "stop_loss": 3291.0,
            "take_profit": 3315.0,
            "lot_size": 0.01,
            "unrealised_pnl": 40.0,
            "pattern": "PINBAR",
            "level": 3300.0,
            "open_time_utc": "2026-03-18T09:00:00Z",
        }
    ],
    "signal_history": [
        {"time_utc": "2026-03-18T09:00:00Z", "pattern": "PINBAR", "action": "TRADE_PLACED", "gate_result": "OK", "level_checked": 3300.0},
        {"time_utc": "2026-03-18T08:00:00Z", "pattern": "ENGULFING", "action": "SKIPPED", "gate_result": "NEWS_BLOCK", "level_checked": 3310.0},
    ],
    "observe_only": False,
}

FRESH_NEWS_CACHE = {
    "last_refresh_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    "events": [
        {"title": "USD CPI", "currency": "USD", "impact": "HIGH", "time_utc": "2026-03-18T13:30:00+00:00"},
        {"title": "EUR GDP", "currency": "EUR", "impact": "HIGH", "time_utc": "2026-03-18T10:00:00+00:00"},
    ],
}


def test_build_news_section_filters_usd_high(tmp_path):
    cache_path = str(tmp_path / "news_calendar_cache.json")
    with open(cache_path, "w") as f:
        json.dump(FRESH_NEWS_CACHE, f)
    section = build_news_section(cache_path)
    assert "USD CPI" in section
    assert "EUR GDP" not in section   # EUR filtered out


def test_build_news_section_missing_cache(tmp_path):
    section = build_news_section(str(tmp_path / "missing.json"))
    assert "unavailable" in section.lower() or "no news" in section.lower()


def test_build_news_section_stale_cache(tmp_path):
    cache_path = str(tmp_path / "news_calendar_cache.json")
    stale = {"last_refresh_date": "2020-01-01", "events": []}
    with open(cache_path, "w") as f:
        json.dump(stale, f)
    section = build_news_section(cache_path)
    assert "stale" in section.lower() or "unavailable" in section.lower()


def test_build_prompt_contains_key_fields():
    prompt = build_prompt(FRESH_STATE, news_section="USD CPI at 13:30")
    assert "3301" in prompt          # entry price
    assert "PINBAR" in prompt
    assert "USD CPI" in prompt
    assert "consecutive" in prompt.lower()


def test_build_prompt_stale_warning():
    prompt = build_prompt(FRESH_STATE, news_section="n/a", stale=True)
    assert "stale" in prompt.lower() or "warning" in prompt.lower()


def test_run_writes_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news_calendar_cache.json")
    output_path = str(tmp_path / "morning_brief.json")
    with open(state_path, "w") as f:
        json.dump(FRESH_STATE, f)
    with open(news_path, "w") as f:
        json.dump(FRESH_NEWS_CACHE, f)

    with patch("analyst.morning_brief.call_claude", return_value="Markets look calm."):
        run(state_path=state_path, news_cache_path=news_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert result["brief"] == "Markets look calm."
    assert result["model"] == "claude-sonnet-4-6"
    assert "generated_at_utc" in result


def test_run_stale_state_still_writes_with_warning(tmp_path):
    state = dict(FRESH_STATE)
    state["meta"] = dict(state["meta"])
    state["meta"]["last_updated_utc"] = "2020-01-01T00:00:00Z"
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news_calendar_cache.json")
    output_path = str(tmp_path / "morning_brief.json")
    with open(state_path, "w") as f:
        json.dump(state, f)
    with open(news_path, "w") as f:
        json.dump(FRESH_NEWS_CACHE, f)

    with patch("analyst.morning_brief.call_claude", return_value="Stale data detected."):
        run(state_path=state_path, news_cache_path=news_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert result.get("stale_state") is True
