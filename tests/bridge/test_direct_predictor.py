from bridge.assets import resolve_asset
from bridge.direct_predictor import build_prediction_payload, build_recent_actions, render_direct_report


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
