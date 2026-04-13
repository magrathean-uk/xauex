from xauex.signal.context_builder import ContextBuilder, ContextItem


def test_context_builder_dedupes_and_prioritizes_tier_and_freshness():
    items = [
        ContextItem(
            source_id="tier3",
            source_name="Narrative source",
            provider="Commentary",
            category="industry-news",
            tier=3,
            official=False,
            title="Gold rises on softer yields",
            url="https://example.com/1",
            published_utc="2026-04-14T00:00:00Z",
            summary="Gold rises on softer yields and weaker dollar.",
            freshness_hours=12.0,
            freshness_score=0.85,
        ),
        ContextItem(
            source_id="tier1",
            source_name="Federal Reserve",
            provider="Federal Reserve",
            category="central-bank",
            tier=1,
            official=True,
            title="Gold rises on softer yields",
            url="https://example.com/2",
            published_utc="2026-04-14T00:00:00Z",
            summary="Gold rises on softer yields and weaker dollar.",
            freshness_hours=2.0,
            freshness_score=1.0,
        ),
    ]

    ranked = ContextBuilder._dedupe_and_rank_items(items)

    assert len(ranked) == 1
    assert ranked[0].source_id == "tier1"
