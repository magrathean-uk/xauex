from dataclasses import replace
import threading
import time

from xauex.signal.assets import AssetProfile, resolve_asset
from xauex.signal.config import SignalConfig
from xauex.signal.market_snapshot import _FRED_SERIES, _assess_market_snapshot_freshness, build_market_snapshot


def test_usd_major_index_uses_current_fred_substitute():
    assert _FRED_SERIES["usd_major_index"]["series_id"] == "DTWEXAFEGS"


def test_build_market_snapshot_maps_gold_driver_biases(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.3, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.8, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.85, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.28, "change_1d": -0.08, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DFII10": {"value": 1.9, "previous_value": 1.95, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.4, "previous_value": 2.35, "change_1d": 0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.5, "previous_value": 2.46, "change_1d": 0.04, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "VIXCLS": {"value": 18.2, "previous_value": 17.5, "change_1d": 0.7, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 81.0, "change_1d": 1.5, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 64200.0, "change_1d": 800.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: fake_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Official Fed policy context available for 2026-05-05.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "FedWatch available.",
            "bias": "NEUTRAL",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Managed Money net long mid-range.",
            "bias": "NEUTRAL",
            "extreme_positioning": False,
        },
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[{"source_id": "bls_cpi_rss"}, {"source_id": "fed_press_all"}],
    )

    assert snapshot["overall_bias"] == "BUY"
    assert snapshot["series"]["usd_broad_index"]["bias"] == "BUY"
    assert snapshot["series"]["usd_major_index"]["bias"] == "BUY"
    assert snapshot["series"]["us10y_yield"]["bias"] == "BUY"
    assert snapshot["series"]["us5y_breakeven_inflation"]["bias"] == "BUY"
    assert snapshot["series"]["us10y_breakeven_inflation"]["bias"] == "BUY"
    assert snapshot["series"]["wti_oil"]["bias"] == "BUY"
    assert snapshot["series"]["btc_usd"]["bias"] == "NEUTRAL"
    assert snapshot["cot"]["available"] is True
    assert snapshot["event_flags"]["cpi_release_recent"] is True
    assert snapshot["event_flags"]["fed_event_recent"] is True
    assert snapshot["input_freshness"]["missing_series_count"] == 0


def test_falling_breakevens_signal_sell_bias_for_gold(monkeypatch):
    """Falling inflation expectations should bias gold to SELL."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    falling_breakeven_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.0, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.5, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.8, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.2, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DFII10": {"value": 1.9, "previous_value": 1.9, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.20, "previous_value": 2.30, "change_1d": -0.10, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.30, "previous_value": 2.40, "change_1d": -0.10, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "VIXCLS": {"value": 18.2, "previous_value": 18.2, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 82.5, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 65000.0, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }
    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: falling_breakeven_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )
    assert snapshot["series"]["us5y_breakeven_inflation"]["bias"] == "SELL"
    assert snapshot["series"]["us10y_breakeven_inflation"]["bias"] == "SELL"
    assert snapshot["overall_bias"] == "SELL"


def test_extreme_cot_long_positioning_adds_sell_bias(monkeypatch):
    """When Managed Money net long is extreme high, COT contributes SELL bias."""
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    neutral_rows = {sid: {"value": 1.0, "previous_value": 1.0, "change_1d": 0.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0} for sid in (
        "DTWEXBGS", "DTWEXAFEGS", "DGS2", "DGS10", "DFII10",
        "T5YIE", "T10YIE", "VIXCLS", "DCOILWTICO", "CBBTCUSD",
    )}
    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: neutral_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "extreme_positioning": True,
            "bias": "SELL",
            "summary": "Crowded long, squeeze risk.",
            "managed_money_net_long": 250000,
            "net_long_percentile_26w": 0.96,
        },
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )
    assert snapshot["overall_bias"] == "SELL"
    assert snapshot["cot"]["bias"] == "SELL"
    assert snapshot["cot"]["extreme_positioning"] is True


def test_market_snapshot_blocks_stale_inputs_during_live_window():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        stale_block_series_count=3,
        window_label="morning",
    )

    assert freshness["market_snapshot_state"] == "blocked"
    assert freshness["hard_blocker"] is True


def test_market_snapshot_blocks_stale_inputs_during_us_open_window():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        stale_block_series_count=3,
        window_label="us_open",
    )

    assert freshness["market_snapshot_state"] == "blocked"
    assert freshness["hard_blocker"] is True


def test_market_snapshot_warns_about_same_staleness_outside_live_window():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        window_label="current",
    )

    assert freshness["market_snapshot_state"] == "warning"
    assert freshness["hard_blocker"] is False


def test_market_snapshot_warns_when_only_one_series_is_block_stale():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        window_label="morning",
        stale_block_series_count=1,
    )

    assert freshness["market_snapshot_state"] == "warning"
    assert freshness["hard_blocker"] is False
    assert freshness["stale_block_series_count"] == 1


def test_market_snapshot_warns_when_two_series_are_block_stale():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        window_label="morning",
        stale_block_series_count=2,
    )

    assert freshness["market_snapshot_state"] == "warning"
    assert freshness["hard_blocker"] is False
    assert freshness["stale_block_series_count"] == 2


def test_market_snapshot_blocks_when_three_series_are_block_stale():
    freshness = _assess_market_snapshot_freshness(
        market_snapshot_age_seconds=9 * 24 * 3600,
        missing_series_count=0,
        window_label="morning",
        stale_block_series_count=3,
    )

    assert freshness["market_snapshot_state"] == "blocked"
    assert freshness["hard_blocker"] is True
    assert freshness["stale_block_series_count"] == 3


def test_build_market_snapshot_includes_fedwatch_snapshot(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.3, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.8, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.85, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.28, "change_1d": -0.08, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DFII10": {"value": 1.9, "previous_value": 1.95, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.4, "previous_value": 2.35, "change_1d": 0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.5, "previous_value": 2.46, "change_1d": 0.04, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "VIXCLS": {"value": 18.2, "previous_value": 17.5, "change_1d": 0.7, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 81.0, "change_1d": 1.5, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 64200.0, "change_1d": 800.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: fake_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Official Fed policy context available for 2026-05-05.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "meeting_date": "2026-05-06",
            "cut_probability": 0.62,
            "hold_probability": 0.38,
            "hike_probability": 0.0,
            "expected_change_bps": -25.0,
            "bias": "BUY",
            "summary": "FedWatch implies a 62% chance of a 25bp cut at the next meeting.",
        },
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )

    assert snapshot["fedwatch"]["status"] == "available"
    assert snapshot["fedwatch"]["bias"] == "BUY"
    assert snapshot["input_freshness"]["fedwatch_state"] == "available"


def test_build_market_snapshot_includes_policy_context_and_freshness(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.3, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.8, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.85, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.28, "change_1d": -0.08, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DFII10": {"value": 1.9, "previous_value": 1.95, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.4, "previous_value": 2.35, "change_1d": 0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.5, "previous_value": 2.46, "change_1d": 0.04, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "VIXCLS": {"value": 18.2, "previous_value": 17.5, "change_1d": 0.7, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 81.0, "change_1d": 1.5, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 64200.0, "change_1d": 800.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: fake_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "warning",
            "available": True,
            "summary": "FedWatch is partially available.",
            "bias": "NEUTRAL",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "warning",
            "available": True,
            "summary": "Official Fed policy context is partially available (next_fomc_date); missing calendar.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )

    assert snapshot["policy_context"]["status"] == "warning"
    assert snapshot["policy_context"]["source"] == "fed_fomc_calendar+fred"
    assert snapshot["input_freshness"]["policy_context_state"] == "warning"
    assert snapshot["input_freshness"]["policy_context_summary"] == "Official Fed policy context is partially available (next_fomc_date); missing calendar."


def test_build_market_snapshot_fetches_fred_series_in_parallel(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.3, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.8, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.85, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.28, "change_1d": -0.08, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DFII10": {"value": 1.9, "previous_value": 1.95, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.4, "previous_value": 2.35, "change_1d": 0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.5, "previous_value": 2.46, "change_1d": 0.04, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "VIXCLS": {"value": 18.2, "previous_value": 17.5, "change_1d": 0.7, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 81.0, "change_1d": 1.5, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 64200.0, "change_1d": 800.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }
    active_calls = 0
    max_active_calls = 0
    lock = threading.Lock()

    def fake_fetch_fred_series(client, series_id):
        nonlocal active_calls, max_active_calls
        with lock:
            active_calls += 1
            max_active_calls = max(max_active_calls, active_calls)
        time.sleep(0.05)
        with lock:
            active_calls -= 1
        return fake_rows[series_id]

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        fake_fetch_fred_series,
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Official Fed policy context available for 2026-05-05.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "FedWatch available.",
            "bias": "NEUTRAL",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
    )

    assert snapshot["missing_series"] == []
    assert list(snapshot["series"].keys()) == [
        "usd_broad_index",
        "usd_major_index",
        "us2y_yield",
        "us10y_yield",
        "us10y_real_yield",
        "us5y_breakeven_inflation",
        "us10y_breakeven_inflation",
        "vix",
        "wti_oil",
        "btc_usd",
    ]
    assert max_active_calls >= 2


def test_build_market_snapshot_returns_non_blocking_unsupported_snapshot(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: (_ for _ in ()).throw(AssertionError("fetch_policy_context must not be called for unsupported assets")),
    )

    snapshot = build_market_snapshot(
        asset=AssetProfile(
            symbol="EURUSD",
            display_name="EURUSD",
            asset_class="fx",
            aliases=("EURUSD",),
            project_name="Test",
            simulation_requirement="Test",
            parser_brief="Test",
            distance_unit="pips",
            min_stop_loss_distance=1.0,
            max_stop_loss_distance=1.0,
            default_stop_loss_distance=1.0,
            min_take_profit_rr=1.0,
            max_take_profit_rr=1.0,
            execution_supported=False,
        ),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
        window_label="morning",
    )

    assert snapshot["series"] == {}
    assert snapshot["fedwatch"]["status"] == "unsupported"
    assert snapshot["policy_context"]["status"] == "unsupported"
    assert snapshot["policy_context"]["available"] is False
    assert snapshot["input_freshness"]["hard_blocker"] is False
    assert snapshot["input_freshness"]["market_snapshot_state"] == "warning"
    assert snapshot["input_freshness"]["policy_context_state"] == "unsupported"
    assert snapshot["input_freshness"]["policy_context_summary"] == "Official Fed policy context is only evaluated for XAUUSD."


def test_build_market_snapshot_blocks_live_window_when_series_fail(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {"value": 121.0, "previous_value": 121.3, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DTWEXAFEGS": {"value": 105.5, "previous_value": 105.8, "change_1d": -0.3, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS2": {"value": 3.8, "previous_value": 3.85, "change_1d": -0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DGS10": {"value": 4.2, "previous_value": 4.28, "change_1d": -0.08, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T5YIE": {"value": 2.4, "previous_value": 2.35, "change_1d": 0.05, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "T10YIE": {"value": 2.5, "previous_value": 2.46, "change_1d": 0.04, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "DCOILWTICO": {"value": 82.5, "previous_value": 81.0, "change_1d": 1.5, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
        "CBBTCUSD": {"value": 65000.0, "previous_value": 64200.0, "change_1d": 800.0, "date_utc": "2026-04-14T00:00:00Z", "age_seconds": 3600.0},
    }

    def fake_fetch_fred_series(client, series_id):
        if series_id in fake_rows:
            return fake_rows[series_id]
        raise RuntimeError(f"upstream failure for {series_id}")

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        fake_fetch_fred_series,
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Official Fed policy context available for 2026-05-05.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "FedWatch available.",
            "bias": "NEUTRAL",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
        window_label="morning",
    )

    assert snapshot["input_freshness"]["market_snapshot_state"] == "blocked"
    assert snapshot["input_freshness"]["hard_blocker"] is True
    assert snapshot["input_freshness"]["missing_series_count"] == 2


def test_build_market_snapshot_warns_when_only_one_series_is_long_stale(monkeypatch):
    monkeypatch.setenv("XAUEX_SIGNAL_LLM_API_KEY", "test-key")
    cfg = SignalConfig.from_env()

    fake_rows = {
        "DTWEXBGS": {
            "value": 121.0,
            "previous_value": 121.3,
            "change_1d": -0.3,
            "date_utc": "2026-04-10T00:00:00Z",
            "age_seconds": float(9 * 24 * 3600),
        },
        "DTWEXAFEGS": {
            "value": 105.5,
            "previous_value": 105.8,
            "change_1d": -0.3,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "DGS2": {
            "value": 3.8,
            "previous_value": 3.85,
            "change_1d": -0.05,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "DGS10": {
            "value": 4.2,
            "previous_value": 4.28,
            "change_1d": -0.08,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "DFII10": {
            "value": 1.9,
            "previous_value": 1.95,
            "change_1d": -0.05,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "T5YIE": {
            "value": 2.4,
            "previous_value": 2.35,
            "change_1d": 0.05,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "T10YIE": {
            "value": 2.5,
            "previous_value": 2.46,
            "change_1d": 0.04,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "VIXCLS": {
            "value": 18.2,
            "previous_value": 17.5,
            "change_1d": 0.7,
            "date_utc": "2026-04-17T00:00:00Z",
            "age_seconds": float(24 * 3600),
        },
        "DCOILWTICO": {
            "value": 82.5,
            "previous_value": 81.0,
            "change_1d": 1.5,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
        "CBBTCUSD": {
            "value": 65000.0,
            "previous_value": 64200.0,
            "change_1d": 800.0,
            "date_utc": "2026-04-16T00:00:00Z",
            "age_seconds": float(2 * 24 * 3600),
        },
    }

    monkeypatch.setattr(
        "xauex.signal.market_snapshot._fetch_fred_series",
        lambda client, series_id: fake_rows[series_id],
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "Official Fed policy context available for 2026-05-05.",
            "source": "fed_fomc_calendar+fred",
            "next_fomc_date": "2026-05-05",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_fedwatch_snapshot",
        lambda config: {
            "status": "available",
            "available": True,
            "summary": "FedWatch available.",
            "bias": "NEUTRAL",
        },
    )
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_cot_snapshot",
        lambda config: {"status": "unavailable", "available": False, "summary": ""},
    )

    snapshot = build_market_snapshot(
        asset=resolve_asset("XAUUSD"),
        config=replace(cfg, source_timeout_seconds=1.0),
        context_items=[],
        window_label="morning",
    )

    assert snapshot["input_freshness"]["market_snapshot_state"] == "warning"
    assert snapshot["input_freshness"]["hard_blocker"] is False
    assert snapshot["input_freshness"]["stale_block_series_count"] == 1
