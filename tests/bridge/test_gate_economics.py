from datetime import datetime, timedelta, timezone

from xauex.analyst.gate_economics import aggregate_gate_economics, replay_bracket


_BASE = datetime(2026, 6, 10, 7, 0, tzinfo=timezone.utc)


def _bars(*, drift_per_minute: float, count: int = 480, wick: float = 0.4) -> list[dict]:
    bars = []
    price = 4700.0
    for index in range(count):
        open_price = price
        price += drift_per_minute
        bars.append(
            {
                "open_time": _BASE + timedelta(minutes=index),
                "open": open_price,
                "high": max(open_price, price) + wick,
                "low": min(open_price, price) - wick,
                "close": price,
            }
        )
    return bars


def test_replay_bracket_buy_hits_take_profit():
    result = replay_bracket(
        bars=_bars(drift_per_minute=0.2),
        entry_start_utc=_BASE + timedelta(minutes=60),
        force_flat_utc=_BASE + timedelta(minutes=420),
        direction="BUY",
        stop_loss_distance=12.0,
        take_profit_distance=24.0,
    )
    assert result["exit_reason"] == "TP"
    assert result["result_r"] == 2.0
    assert result["result_usd_min_lot"] == 24.0


def test_replay_bracket_sell_against_uptrend_stops_out():
    result = replay_bracket(
        bars=_bars(drift_per_minute=0.2),
        entry_start_utc=_BASE + timedelta(minutes=60),
        force_flat_utc=_BASE + timedelta(minutes=420),
        direction="SELL",
        stop_loss_distance=12.0,
        take_profit_distance=24.0,
    )
    assert result["exit_reason"] == "SL"
    assert result["result_r"] == -1.0


def test_replay_bracket_flat_market_force_flats():
    result = replay_bracket(
        bars=_bars(drift_per_minute=0.001, wick=0.2),
        entry_start_utc=_BASE + timedelta(minutes=60),
        force_flat_utc=_BASE + timedelta(minutes=420),
        direction="BUY",
        stop_loss_distance=12.0,
        take_profit_distance=24.0,
    )
    assert result["exit_reason"] == "FORCE_FLAT"
    assert -1.0 < result["result_r"] < 1.0


def test_replay_bracket_returns_none_without_session_bars():
    result = replay_bracket(
        bars=_bars(drift_per_minute=0.1, count=30),
        entry_start_utc=_BASE + timedelta(days=2),
        force_flat_utc=_BASE + timedelta(days=2, hours=7),
        direction="BUY",
        stop_loss_distance=12.0,
        take_profit_distance=24.0,
    )
    assert result is None


def test_aggregate_groups_by_reason_within_window():
    now = datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)
    runs = [
        {
            "date_london": "2026-06-10",
            "window_label": "morning",
            "reason": "NEWS",
            "result": {"exit_reason": "TP", "result_r": 2.0, "result_usd_min_lot": 24.0},
        },
        {
            "date_london": "2026-06-11",
            "window_label": "midday",
            "reason": "NEWS",
            "result": {"exit_reason": "SL", "result_r": -1.0, "result_usd_min_lot": -12.0},
        },
        {
            "date_london": "2025-06-01",  # outside the 90-day window
            "window_label": "morning",
            "reason": "NEWS",
            "result": {"exit_reason": "TP", "result_r": 2.0, "result_usd_min_lot": 24.0},
        },
        {
            "date_london": "2026-06-11",
            "window_label": "us_open",
            "reason": "PRICE_CONFLICT",
            "result": {"exit_reason": "FORCE_FLAT", "result_r": 0.5, "result_usd_min_lot": 6.0},
        },
    ]
    aggregate = aggregate_gate_economics(runs, now_utc=now)
    news = aggregate["by_reason"]["NEWS"]
    assert news["n"] == 2
    assert news["tp"] == 1
    assert news["sl"] == 1
    assert news["expectancy_r"] == 0.5
    assert news["missed_usd_min_lot"] == 12.0
    assert aggregate["by_reason"]["PRICE_CONFLICT"]["n"] == 1
    assert aggregate["replayed"] == 3
