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


def test_market_snapshot_render_uses_gold_perspective_interpretation():
    """The LLM has been mistaking 'bias=BUY' on DXY for 'USD bullish'. The rendered
    report must instead say something like 'gold_signal=BULLISH (USD weakened — gold-supportive)'
    so the LLM cannot confuse asset direction with gold direction.
    """
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "series": {
                "usd_broad_index": {
                    "label": "Trade-weighted USD broad index",
                    "value": 118.39,
                    "change_1d": -0.28,
                    "bias": "BUY",
                },
                "us10y_yield": {
                    "label": "US 10Y Treasury yield",
                    "value": 4.36,
                    "change_1d": -0.07,
                    "bias": "BUY",
                },
                "us5y_breakeven_inflation": {
                    "label": "US 5Y breakeven inflation expectation",
                    "value": 2.61,
                    "change_1d": 0.03,
                    "bias": "BUY",
                },
                "vix": {
                    "label": "CBOE VIX",
                    "value": 17.39,
                    "change_1d": 0.01,
                    "bias": "BUY",
                },
                "btc_usd": {
                    "label": "Bitcoin USD (Coinbase)",
                    "value": 80047.2,
                    "change_1d": -1429.31,
                    "bias": "NEUTRAL",
                },
            },
        },
    )
    report = render_direct_report(payload)

    # Each series row must include a gold-perspective interpretation. The LLM
    # mislabeled DXY as "strengthening USD" when DXY went DOWN; the new
    # rendering must make the gold direction unambiguous.
    assert "gold_signal=BULLISH" in report
    assert "USD weakened" in report
    assert "10Y yield fell" in report
    assert "Inflation expectations rose" in report
    # The ambiguous legacy 'bias=BUY' phrasing on its own should not appear in
    # the structured market snapshot section. (It can still exist as data; we
    # check the rendered string here.)
    snapshot_section = report.split("## Structured Market Snapshot", 1)[1].split("##", 1)[0]
    assert "bias=BUY" not in snapshot_section
    assert "bias=SELL" not in snapshot_section


def _rising_upper_third_closes() -> list[float]:
    """Build a 20-close H1 series whose last value sits firmly in the upper
    third of the daily range and has positive avg momentum > ATR*0.4 threshold.

    Daily range below: low=4700, high=4720, span=20. Upper third starts at pos
    0.66 → price ≥ 4713.2. Last close 4719.0 sits at pos=0.95 (upper third).
    Average ATR computed on these closes is 0.6 → threshold is 0.24, well below
    avg momentum (≈12 over 12 bars). So price_bias triggers BUY before the
    regime filter runs.
    """
    return [4700.0, 4704.0, 4706.0, 4707.0, 4708.0, 4709.0, 4710.0, 4711.0,
            4712.0, 4713.0, 4714.0, 4715.0, 4715.5, 4716.0, 4716.5, 4717.0,
            4717.5, 4718.0, 4718.5, 4719.0]


def _falling_lower_third_closes() -> list[float]:
    """Mirror of _rising_upper_third_closes for downtrend testing."""
    return [4720.0, 4716.0, 4714.0, 4713.0, 4712.0, 4711.0, 4710.0, 4709.0,
            4708.0, 4707.0, 4706.0, 4705.0, 4704.5, 4704.0, 4703.5, 4703.0,
            4702.5, 4702.0, 4701.5, 4701.0]


def test_price_bias_keeps_buy_in_upper_third_when_daily_trend_is_up():
    """The anti-trend filter neutralized BUY in upper-third unconditionally,
    killing trend-continuation in real uptrends. With a bullish daily regime
    (trend.daily_bias > 0), BUY in upper-third must survive."""
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={
            "recent_h1_closes": _rising_upper_third_closes(),
            "levels": {"daily": {"low": 4700.0, "high": 4720.0}},
            "trend": {"daily_bias": 1, "alignment": "ALIGNED"},
        },
    )
    assert payload["price_features"]["price_bias"] == "BUY"
    assert payload["price_features"]["range_position"] == "UPPER_THIRD"
    assert payload["price_features"]["regime_filter"] == "TREND_ALIGNED_UPPER_THIRD_KEPT_BUY"


def test_price_bias_neutralizes_buy_in_upper_third_when_daily_trend_is_down():
    """When the daily trend is bearish but H1 momentum pushes BUY in the upper
    third, that's a fade-the-bounce setup that historically fails — keep
    neutralizing it (mean-reversion failure protection)."""
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={
            "recent_h1_closes": _rising_upper_third_closes(),
            "levels": {"daily": {"low": 4700.0, "high": 4720.0}},
            "trend": {"daily_bias": -1, "alignment": "ALIGNED"},
        },
    )
    assert payload["price_features"]["price_bias"] == "NEUTRAL"
    assert payload["price_features"]["regime_filter"] == "COUNTER_TREND_UPPER_THIRD_NEUTRALIZED_BUY"


def test_price_bias_neutralizes_buy_in_upper_third_when_no_trend_signal():
    """Without a trend signal, fall back to the conservative legacy behavior."""
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={
            "recent_h1_closes": _rising_upper_third_closes(),
            "levels": {"daily": {"low": 4700.0, "high": 4720.0}},
        },
    )
    # Conservative default: with no trend evidence, neutralize BUY in upper third.
    assert payload["price_features"]["price_bias"] == "NEUTRAL"
    assert payload["price_features"]["regime_filter"] == "NO_TREND_SIGNAL_NEUTRALIZED_BUY"


def test_price_bias_keeps_sell_in_lower_third_when_daily_trend_is_down():
    """Mirror case for shorts: SELL in lower-third with bearish daily trend
    should be kept (continuation), not neutralized."""
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={
            "recent_h1_closes": _falling_lower_third_closes(),
            "levels": {"daily": {"low": 4700.0, "high": 4720.0}},
            "trend": {"daily_bias": -1, "alignment": "ALIGNED"},
        },
    )
    assert payload["price_features"]["price_bias"] == "SELL"
    assert payload["price_features"]["range_position"] == "LOWER_THIRD"
    assert payload["price_features"]["regime_filter"] == "TREND_ALIGNED_LOWER_THIRD_KEPT_SELL"


def test_render_direct_report_handles_gold_bearish_inputs():
    """When DXY rises, the rendering should say 'USD strengthened — gold-pressuring'
    and gold_signal=BEARISH."""
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context",
        recent_runs=[],
        state_snapshot={"recent_h1_closes": [10, 11, 12, 13], "levels": {"daily": {"low": 9, "high": 15}}},
        market_snapshot={
            "series": {
                "usd_broad_index": {
                    "label": "Trade-weighted USD broad index",
                    "value": 119.5,
                    "change_1d": 0.4,
                    "bias": "SELL",
                },
                "us10y_yield": {
                    "label": "US 10Y Treasury yield",
                    "value": 4.55,
                    "change_1d": 0.12,
                    "bias": "SELL",
                },
            },
        },
    )
    report = render_direct_report(payload)
    assert "gold_signal=BEARISH" in report
    assert "USD strengthened" in report
    assert "10Y yield rose" in report
    assert "gold-pressuring" in report
