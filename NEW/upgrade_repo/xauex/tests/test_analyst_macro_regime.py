"""Tests for analyst.macro_regime."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

from analyst.macro_regime import build_news_section, build_prompt, run


UTC = timezone.utc


def test_build_news_section_filters_relevant_events():
    now = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
    cache = {
        "events": [
            {"title": "FOMC Statement", "currency": "USD", "impact": "HIGH", "time_utc": "2026-03-20T13:00:00+00:00"},
            {"title": "German Ifo", "currency": "EUR", "impact": "MEDIUM", "time_utc": "2026-03-20T09:00:00+00:00"},
        ]
    }
    section = build_news_section(cache, now)
    assert "FOMC Statement" in section
    assert "German Ifo" not in section


def test_build_prompt_contains_trend_and_news():
    now = datetime(2026, 3, 20, 10, 0, tzinfo=UTC)
    state = {
        "meta": {"last_updated_utc": "2026-03-20T09:55:00Z"},
        "trend": {"alignment": "BEARISH", "reason": "OK", "daily_ema_8": 4900, "daily_ema_21": 5000},
        "signal_history": [{"time_utc": "2026-03-20T09:00:00Z", "action": "SKIP", "gate_result": "NO_CONFIRMATION_PATTERN", "pattern": None}],
    }
    news = {"events": [{"title": "CPI", "currency": "USD", "impact": "HIGH", "time_utc": "2026-03-20T12:30:00+00:00"}]}
    prompt = build_prompt(state, news, now, stale=False)
    assert "BEARISH" in prompt
    assert "CPI" in prompt
    assert "JSON" in prompt


def test_run_writes_macro_regime_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news.json")
    output_path = str(tmp_path / "macro_regime.json")
    with open(state_path, "w") as f:
        json.dump({"meta": {"last_updated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}}, f)
    with open(news_path, "w") as f:
        json.dump({"events": []}, f)

    response = json.dumps(
        {
            "regime": "XAU_BEARISH",
            "confidence": 0.82,
            "block_new_entries_until_utc": None,
            "expires_utc": "2026-03-20T18:00:00Z",
            "summary": "USD-supportive macro tone favors gold downside.",
            "catalysts": ["Fed repricing"],
        }
    )
    with patch("analyst.macro_regime.call_claude", return_value=response):
        run(state_path=state_path, news_cache_path=news_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert result["regime"] == "XAU_BEARISH"
    assert result["confidence"] == 0.82
    assert result["summary"].startswith("USD-supportive")
