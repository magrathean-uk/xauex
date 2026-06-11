"""FOMC blackout policy: decision day hard-blocks; the day after warns by default."""

from dataclasses import replace
from datetime import datetime, timezone

from xauex.signal.assets import resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.market_snapshot import build_market_snapshot


_FRESH_ROW = {
    "value": 4.2,
    "previous_value": 4.25,
    "change_1d": -0.05,
    "date_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "age_seconds": 3600.0,
    "business_age_days": 0,
}


def _build_snapshot(monkeypatch, *, fomc_window_state: str) -> dict:
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()
    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: dict(_FRESH_ROW),
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {"status": "ok", "available": True, "summary": "", "bias": "NEUTRAL"},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {"status": "ok", "available": True, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_polymarket_snapshot",
        lambda config, asset: {"status": "disabled", "available": False, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "ok",
            "available": True,
            "summary": "Fed policy context available.",
            "source": "fed_fomc_calendar+fred",
            "fomc_window_state": fomc_window_state,
        },
    )
    return build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )


def test_fomc_decision_day_stays_hard_blocked(monkeypatch):
    snapshot = _build_snapshot(monkeypatch, fomc_window_state="today")
    freshness = snapshot["input_freshness"]
    assert freshness["hard_blocker"] is True
    assert freshness["market_snapshot_state"] == "blocked"
    assert "FOMC decision window (today)" in freshness["summary"]


def test_fomc_day_after_demotes_to_warning_by_default(monkeypatch):
    monkeypatch.delenv("XAUEX_FOMC_RECENT_POLICY", raising=False)
    snapshot = _build_snapshot(monkeypatch, fomc_window_state="recent")
    freshness = snapshot["input_freshness"]
    assert freshness["hard_blocker"] is not True
    assert freshness["market_snapshot_state"] == "warning"
    assert "Day after FOMC decision - warning state" in freshness["summary"]


def test_fomc_day_after_blocks_when_policy_is_block(monkeypatch):
    monkeypatch.setenv("XAUEX_FOMC_RECENT_POLICY", "block")
    snapshot = _build_snapshot(monkeypatch, fomc_window_state="recent")
    freshness = snapshot["input_freshness"]
    assert freshness["hard_blocker"] is True
    assert freshness["market_snapshot_state"] == "blocked"


def test_fomc_normal_window_leaves_freshness_untouched(monkeypatch):
    snapshot = _build_snapshot(monkeypatch, fomc_window_state="normal")
    freshness = snapshot["input_freshness"]
    assert freshness["hard_blocker"] is not True
    assert freshness["market_snapshot_state"] == "fresh"
