"""Tests for analyst.trade_policy."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

from analyst.trade_policy import build_prompt, run


UTC = timezone.utc


def test_build_prompt_contains_news_and_macro():
    now = datetime(2026, 3, 23, 10, 0, tzinfo=UTC)
    state = {
        "meta": {"last_updated_utc": "2026-03-23T09:55:00Z"},
        "trend": {"alignment": "BEARISH", "reason": "OK", "daily_ema_8": 4900, "daily_ema_21": 5000},
        "signal_history": [{"time_utc": "2026-03-23T09:30:00Z", "action": "SKIP", "gate_result": "NO_CONFIRMATION_PATTERN", "pattern": None}],
    }
    news = {"events": [{"title": "CPI", "currency": "USD", "impact": "HIGH", "time_utc": "2026-03-23T12:30:00+00:00"}]}
    macro = {"regime": "XAU_BEARISH", "confidence": 0.8}
    prompt = build_prompt(state, news, macro, now, stale=False)
    assert "XAU_BEARISH" in prompt
    assert "CPI" in prompt
    assert "trade policy engine" in prompt


def test_run_writes_trade_policy_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news.json")
    macro_path = str(tmp_path / "macro.json")
    output_path = str(tmp_path / "trade_policy.json")
    with open(state_path, "w") as f:
        json.dump({"meta": {"last_updated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}}, f)
    with open(news_path, "w") as f:
        json.dump({"events": []}, f)
    with open(macro_path, "w") as f:
        json.dump({"regime": "XAU_BEARISH", "confidence": 0.8}, f)

    response = json.dumps(
        {
            "mode": "AGGRESSIVE",
            "direction": "SHORT_ONLY",
            "aggressiveness": 0.85,
            "allow_reentry": True,
            "pullback_zone_multiplier": 1.2,
            "sl_buffer_multiplier": 1.1,
            "tp_rr_multiplier": 0.9,
            "block_new_entries_until_utc": None,
            "expires_utc": "2026-03-23T18:00:00Z",
            "summary": "Bearish session, prefer shorts.",
            "catalysts": ["bearish EMA stack"],
        }
    )
    with patch("analyst.trade_policy.call_claude", return_value=response):
        run(state_path=state_path, news_cache_path=news_path, macro_regime_path=macro_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert result["mode"] == "AGGRESSIVE"
    assert result["direction"] == "SHORT_ONLY"
    assert result["aggressiveness"] == 0.85
