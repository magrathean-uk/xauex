from datetime import datetime, timezone

from xauex.signal.assets import resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.polymarket import _normalize_polymarket_payloads, fetch_polymarket_snapshot


def test_fetch_polymarket_snapshot_returns_disabled_when_config_off(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    monkeypatch.delenv("XAUEX_SIGNAL_POLYMARKET_CONTEXT_ENABLED", raising=False)
    cfg = SignalConfig.from_env()

    snapshot = fetch_polymarket_snapshot(config=cfg, asset=resolve_asset("XAUUSD"))

    assert snapshot["status"] == "disabled"
    assert snapshot["available"] is False
    assert snapshot["source"] == "polymarket_gamma+clob"


def test_normalize_polymarket_payloads_money_weights_open_markets():
    search_payloads = [
        {
            "events": [
                {
                    "title": "What will Gold (XAUUSD) hit in May 2026?",
                    "slug": "what-price-will-xauusd-hit-in-may-2026",
                    "markets": [
                        {
                            "question": "Will Gold (XAUUSD) hit (HIGH) $4,800 in May?",
                            "active": True,
                            "closed": False,
                            "volumeNum": 28071.95,
                            "liquidityNum": 1639.11,
                            "clobTokenIds": '["gold-high-yes", "gold-high-no"]',
                            "outcomes": '["Yes", "No"]',
                        },
                        {
                            "question": "Will Gold (XAUUSD) hit (LOW) $4,400 in May?",
                            "active": True,
                            "closed": False,
                            "volumeNum": 24263.40,
                            "liquidityNum": 1319.71,
                            "clobTokenIds": '["gold-low-yes", "gold-low-no"]',
                            "outcomes": '["Yes", "No"]',
                        },
                        {
                            "question": "Will Gold (XAUUSD) hit (LOW) $4,700 in May?",
                            "active": True,
                            "closed": True,
                            "volumeNum": 999999.0,
                            "liquidityNum": 999999.0,
                            "clobTokenIds": '["closed-low-yes", "closed-low-no"]',
                            "outcomes": '["Yes", "No"]',
                        },
                    ],
                },
                {
                    "title": "Fed Decision in June?",
                    "slug": "fed-decision-in-june-825",
                    "markets": [
                        {
                            "question": "Will there be no change in Fed interest rates after the June 2026 meeting?",
                            "active": True,
                            "closed": False,
                            "volumeNum": 3095905.97,
                            "liquidityNum": 525244.33,
                            "clobTokenIds": '["fed-hold-yes", "fed-hold-no"]',
                            "outcomes": '["Yes", "No"]',
                        }
                    ],
                },
            ]
        }
    ]

    snapshot = _normalize_polymarket_payloads(
        search_payloads=search_payloads,
        token_books={
            "gold-high-yes": {"midpoint": 0.69, "best_bid": 0.66, "best_ask": 0.72},
            "gold-low-yes": {"midpoint": 0.28, "best_bid": 0.26, "best_ask": 0.30},
            "fed-hold-yes": {"midpoint": 0.955, "best_bid": 0.95, "best_ask": 0.96},
        },
        fetched_at=datetime(2026, 5, 7, 6, 30, tzinfo=timezone.utc),
        weight=0.25,
        max_markets=8,
    )

    assert snapshot["status"] == "available"
    assert snapshot["available"] is True
    assert snapshot["weight"] == 0.25
    assert snapshot["overall_bias"] == "BUY"
    assert snapshot["market_count"] == 3
    assert snapshot["markets"][0]["question"] == "Will Gold (XAUUSD) hit (HIGH) $4,800 in May?"
    assert snapshot["markets"][0]["yes_midpoint"] == 0.69
    assert snapshot["markets"][0]["bias"] == "BUY"
    assert all("closed-low" not in str(row) for row in snapshot["markets"])
    assert "money-weighted" in snapshot["summary"]
