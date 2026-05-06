from xauex.signal.assets import resolve_asset
from xauex.signal.direct_predictor import (
    _calculate_atr,
    build_prediction_payload,
    build_recent_actions,
    render_direct_report,
)


def test_build_prediction_payload_includes_context_and_recent_history():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[
            {"action": "BUY", "confidence": 0.64, "reasoning": "Lower yields support gold."},
        ],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
    )
    assert payload["asset"] == "XAUUSD"
    assert "Fed is dovish" in payload["context_excerpt"]
    assert payload["recent_runs"][0]["action"] == "BUY"
    assert payload["weights"]["price_action"] == 0.45


def test_build_prediction_payload_compacts_context_items():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        context_items=[
            {
                "source_id": "policy-1",
                "source_name": "Federal Reserve Policy Context",
                "tier": 3,
                "freshness_score": 0.95,
                "title": "Policy Context Headline",
                "summary": "This is a compact context item that should survive compaction.",
            },
            *[
                {
                    "source_id": f"extra-{index}",
                    "source_name": f"Extra Source {index}",
                    "tier": index,
                    "freshness_score": 0.1,
                    "title": f"Extra Title {index}",
                    "summary": f"Extra Summary {index}",
                }
                for index in range(2, 9)
            ],
        ],
    )

    assert len(payload["context_items"]) == 6
    assert payload["context_items"][0]["source_id"] == "policy-1"
    assert payload["context_items"][0]["source_name"] == "Federal Reserve Policy Context"
    assert payload["context_items"][0]["tier"] == 3
    assert payload["context_items"][0]["freshness_score"] == 0.95
    assert payload["context_items"][0]["title"] == "Policy Context Headline"
    assert payload["context_items"][0]["summary"].startswith("This is a compact context item")


def test_build_prediction_payload_includes_compact_qdrant_memory():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        retrieved_memory=[
            {
                "id": "hit-1",
                "score": 0.91,
                "action": "BUY",
                "summary": "Similar dovish macro setup favored gold longs.",
                "source": "history:report-12",
            }
        ],
    )

    assert payload["retrieved_memory"][0]["action"] == "BUY"
    assert payload["retrieved_memory"][0]["summary"].startswith("Similar dovish")
    assert payload["retrieved_memory_count"] == 1
    assert "Retrieved Similar Memory" in render_direct_report(payload)
    assert any(action["agent_name"] == "retrieved_qdrant_memory" for action in build_recent_actions(payload))


def test_build_prediction_payload_includes_market_snapshot_and_input_freshness():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "series": {
                "usd_broad_index": {"value": 121.4, "change_1d": -0.3, "bias": "BUY"},
                "us10y_yield": {"value": 4.22, "change_1d": -0.08, "bias": "BUY"},
            },
            "fedwatch": {
                "status": "available",
                "bias": "BUY",
                "cut_probability": 0.62,
                "hold_probability": 0.38,
                "summary": "FedWatch implies a 62% chance of a 25bp cut.",
            },
            "event_flags": {"cpi_release_recent": True, "fed_event_recent": False},
            "input_freshness": {"market_snapshot_age_seconds": 4200, "context_age_seconds": 1800},
        },
    )

    assert payload["market_snapshot"]["series"]["usd_broad_index"]["bias"] == "BUY"
    assert payload["event_flags"]["cpi_release_recent"] is True
    assert payload["input_freshness"]["market_snapshot_age_seconds"] == 4200
    report = render_direct_report(payload)
    assert "Structured Market Snapshot" in report
    assert "usd_broad_index" in report
    assert "FedWatch Snapshot" in report


def test_prediction_payload_weights_and_surfaces_polymarket_context():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nPrediction markets are leaning bullish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "polymarket": {
                "status": "available",
                "available": True,
                "weight": 0.25,
                "overall_bias": "BUY",
                "summary": "Polymarket money-weighted bias is BUY.",
                "money_weighted_score": 0.08,
                "markets": [
                    {
                        "question": "Will Gold (XAUUSD) hit (HIGH) $4,800 in May?",
                        "yes_midpoint": 0.69,
                        "best_bid": 0.66,
                        "best_ask": 0.72,
                        "spread": 0.06,
                        "bias": "BUY",
                        "volume": 28071.95,
                        "liquidity": 1639.11,
                    }
                ],
            }
        },
    )

    assert payload["weights"]["prediction_markets"] == 0.25
    assert payload["weights"]["price_action"] == 0.3375
    assert payload["weights"]["macro_news"] == 0.2625
    report = render_direct_report(payload)
    actions = build_recent_actions(payload)
    polymarket_actions = [action for action in actions if action["agent_name"] == "polymarket"]

    assert "Prediction markets / Polymarket: 25%" in report
    assert "Polymarket Prediction Markets" in report
    assert "Will Gold (XAUUSD) hit (HIGH) $4,800 in May?" in report
    assert polymarket_actions
    assert polymarket_actions[0]["action_type"] == "BUY"
    assert "money_weighted_score=0.08" in polymarket_actions[0]["content"]


def test_build_prediction_payload_includes_latest_quote_snapshot():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFresh quote matters.",
        recent_runs=[],
        state_snapshot={
            "recent_h1_closes": [10, 11, 12, 13],
            "levels": {"daily": {"low": 9, "high": 15}},
            "runtime": {
                "latest_quote": {
                    "bid": 4781.12,
                    "ask": 4781.48,
                    "mid": 4781.30,
                    "updated_at_utc": "2026-04-15T07:00:05Z",
                }
            },
        },
    )

    assert payload["price_features"]["current_bid"] == 4781.12
    assert payload["price_features"]["current_ask"] == 4781.48
    assert payload["price_features"]["current_mid"] == 4781.30
    assert payload["price_features"]["quote_updated_at_utc"] == "2026-04-15T07:00:05Z"


def test_render_direct_report_preserves_fresh_context_structure():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\n## Danger\n### Nested heading\nBody text stays intact.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
    )

    report = render_direct_report(payload)
    assert "## Fresh Market Context" in report
    assert "```" not in report
    assert "\n## Danger\n" not in report
    assert "\n### Nested heading\n" not in report
    assert "    ## Danger" in report
    assert "    ### Nested heading" in report
    assert "Body text stays intact." in report


def test_render_direct_report_includes_policy_context():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "policy_context": {
                "status": "available",
                "next_fomc_date": "2026-05-05",
                "days_to_fomc": 21,
                "fomc_window_state": "approaching",
                "summary": "Official Fed policy context available for 2026-05-05.",
            }
        },
    )

    report = render_direct_report(payload)
    assert "Policy Context" in report
    assert "status: available" in report
    assert "next_fomc_date: 2026-05-05" in report
    assert "days_to_fomc: 21" in report
    assert "fomc_window_state: approaching" in report
    assert "summary: Official Fed policy context available for 2026-05-05." in report
    assert "FedWatch Snapshot" not in report


def test_report_and_actions_surface_only_supported_market_statuses():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "fedwatch": {
                "status": "unsupported",
                "bias": "BUY",
                "summary": "This should not be surfaced.",
            },
            "policy_context": {
                "status": "unsupported",
                "next_fomc_date": "2026-05-05",
                "days_to_fomc": 21,
                "fomc_window_state": "approaching",
                "summary": "This should not be surfaced.",
            },
        },
    )

    report = render_direct_report(payload)
    actions = build_recent_actions(payload)

    assert "FedWatch Snapshot" not in report
    assert "Policy Context" not in report
    assert all(action["agent_name"] not in {"fedwatch", "policy_context"} for action in actions)


def test_report_and_actions_surface_warning_market_statuses_consistently():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "fedwatch": {
                "status": "warning",
                "bias": "BUY",
                "summary": "FedWatch is partially available.",
            },
            "policy_context": {
                "status": "warning",
                "next_fomc_date": "2026-05-05",
                "days_to_fomc": 21,
                "fomc_window_state": "approaching",
                "summary": "Official Fed policy context is partially available.",
            }
        },
    )

    report = render_direct_report(payload)
    actions = build_recent_actions(payload)
    fedwatch_actions = [action for action in actions if action["agent_name"] == "fedwatch"]
    policy_actions = [action for action in actions if action["agent_name"] == "policy_context"]

    assert "FedWatch Snapshot" in report
    assert "Policy Context" in report
    assert fedwatch_actions and fedwatch_actions[0]["action_type"] == "NEUTRAL"
    assert "bias: BUY" in report
    assert policy_actions
    assert policy_actions[0]["action_type"] == "NEUTRAL"
    assert "status=warning" in policy_actions[0]["content"]
    assert "2026-05-05" in policy_actions[0]["content"]
    assert "approaching" in policy_actions[0]["content"]
    assert "Official Fed policy context is partially available." in policy_actions[0]["content"]


def test_report_and_actions_surface_cot_positioning():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nCOT is crowded.",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "cot": {
                "status": "available",
                "available": True,
                "report_date_utc": "2026-04-21T00:00:00Z",
                "managed_money_net_long": 250000,
                "managed_money_net_change_wow": 12000,
                "net_long_percentile_26w": 0.96,
                "net_long_zscore_26w": 2.1,
                "extreme_positioning": True,
                "bias": "SELL",
                "summary": "Crowded long positioning.",
            }
        },
    )

    report = render_direct_report(payload)
    actions = build_recent_actions(payload)
    cot_actions = [action for action in actions if action["agent_name"] == "cftc_cot"]

    assert "CFTC Gold Positioning" in report
    assert "net_long_percentile_26w: 0.96" in report
    assert cot_actions
    assert cot_actions[0]["action_type"] == "SELL"
    assert "mm_net_long=250000" in cot_actions[0]["content"]


def test_close_volatility_is_direction_symmetric():
    uptrend_closes = [float(value) for value in range(100, 115)]
    downtrend_closes = [float(value) for value in range(114, 99, -1)]

    assert _calculate_atr(uptrend_closes) == _calculate_atr(downtrend_closes)
    assert _calculate_atr(uptrend_closes) == 1.0
