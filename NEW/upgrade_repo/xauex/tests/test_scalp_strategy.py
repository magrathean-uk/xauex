from datetime import datetime, timedelta, timezone

from bot.filters.macro_regime import MacroRegime
from bot.patterns.detector import PatternType
from bot.strategies.scalp_v1 import M5ScalpStrategy


UTC = timezone.utc


def make_bar(index: int, open_price: float, high: float, low: float, close: float) -> dict:
    return {
        "open_time": datetime(2026, 3, 20, 8, 0, tzinfo=UTC) + timedelta(minutes=5 * index),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def descending_series(start: float, step: float, count: int) -> list[float]:
    values = []
    current = start
    for _ in range(count):
        values.append(current)
        current -= step
    return values


def ascending_series(start: float, step: float, count: int) -> list[float]:
    values = []
    current = start
    for _ in range(count):
        values.append(current)
        current += step
    return values


def test_scalp_strategy_returns_ready_on_bearish_pullback(minimal_config):
    minimal_config.scalp_pullback_lookback_bars = 2
    strategy = M5ScalpStrategy(minimal_config)
    daily_closes = descending_series(5200.0, 4.0, 40)
    h1_closes = descending_series(5000.0, 1.5, 240)

    bars = []
    price = 4900.0
    for idx in range(24):
        close = price - 1.0
        bars.append(make_bar(idx, price, price + 1.5, close - 1.5, close))
        price = close
    bars.append(make_bar(24, 4876.0, 4882.0, 4875.0, 4880.0))
    bars.append(make_bar(25, 4879.0, 4880.0, 4870.0, 4872.0))

    decision = strategy.evaluate(
        m5_bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=daily_closes,
        h1_closes=h1_closes,
        next_open_price=4872.0,
        current_spread=0.35,
        macro_regime=None,
    )

    assert decision.is_ready is True
    assert decision.direction == -1
    assert decision.pattern == PatternType.BEARISH_CONTINUATION_CLOSE
    assert decision.entry_source == "SCALP_CONTINUATION"
    assert decision.stop_loss_price > decision.entry_price
    assert decision.take_profit_price < decision.entry_price


def test_scalp_strategy_uses_macro_override_when_daily_conflicts(minimal_config):
    minimal_config.scalp_pullback_lookback_bars = 2
    strategy = M5ScalpStrategy(minimal_config)
    daily_closes = ascending_series(4700.0, 3.0, 40)
    h1_closes = descending_series(5000.0, 1.5, 240)

    bars = []
    price = 4900.0
    for idx in range(24):
        close = price - 1.0
        bars.append(make_bar(idx, price, price + 1.5, close - 1.5, close))
        price = close
    bars.append(make_bar(24, 4876.0, 4882.0, 4875.0, 4880.0))
    bars.append(make_bar(25, 4879.0, 4880.0, 4870.0, 4872.0))

    macro = MacroRegime(
        regime="XAU_BEARISH",
        confidence=0.9,
        summary="Hawkish Fed.",
        generated_at_utc=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        expires_utc=datetime(2026, 3, 20, 18, 0, tzinfo=UTC),
    )

    decision = strategy.evaluate(
        m5_bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=daily_closes,
        h1_closes=h1_closes,
        next_open_price=4872.0,
        current_spread=0.35,
        macro_regime=macro,
    )

    assert decision.is_ready is True
    assert decision.metadata["bias_reason"] == "MACRO_OVERRIDE"


def test_scalp_strategy_blocks_when_spread_too_wide(minimal_config):
    minimal_config.scalp_pullback_lookback_bars = 2
    strategy = M5ScalpStrategy(minimal_config)
    daily_closes = descending_series(5200.0, 4.0, 40)
    h1_closes = descending_series(5000.0, 1.5, 240)
    bars = []
    price = 4900.0
    for idx in range(26):
        close = price - 2.0
        bars.append(make_bar(idx, price, price + 2.5, close - 2.5, close))
        price = close

    decision = strategy.evaluate(
        m5_bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=daily_closes,
        h1_closes=h1_closes,
        next_open_price=4842.0,
        current_spread=2.0,
        macro_regime=None,
    )

    assert decision.is_ready is False
    assert decision.gate_result == "SPREAD_TOO_WIDE"


def test_scalp_strategy_accepts_bearish_continuation_without_prev_low_break(minimal_config):
    minimal_config.scalp_pullback_lookback_bars = 3
    strategy = M5ScalpStrategy(minimal_config)
    daily_closes = descending_series(5200.0, 4.0, 40)
    h1_closes = descending_series(5000.0, 1.5, 240)

    bars = []
    price = 4900.0
    for idx in range(24):
        close = price - 1.2
        bars.append(make_bar(idx, price, price + 1.5, close - 1.5, close))
        price = close
    bars.append(make_bar(24, 4872.0, 4888.0, 4870.0, 4884.0))
    bars.append(make_bar(25, 4883.5, 4885.0, 4873.0, 4875.5))

    decision = strategy.evaluate(
        m5_bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=daily_closes,
        h1_closes=h1_closes,
        next_open_price=4875.0,
        current_spread=0.2,
        macro_regime=None,
    )

    assert decision.is_ready is True
    assert decision.direction == -1
    assert decision.pattern == PatternType.BEARISH_CONTINUATION_CLOSE


def test_scalp_strategy_caps_wide_stop_instead_of_skipping(minimal_config):
    minimal_config.scalp_pullback_lookback_bars = 4
    minimal_config.scalp_sl_max_dollars = 12.0
    strategy = M5ScalpStrategy(minimal_config)
    daily_closes = descending_series(5200.0, 4.0, 40)
    h1_closes = descending_series(5000.0, 1.5, 240)

    bars = []
    price = 4900.0
    for idx in range(24):
        close = price - 1.0
        bars.append(make_bar(idx, price, price + 1.5, close - 1.5, close))
        price = close
    bars.append(make_bar(24, 4876.0, 4895.0, 4874.0, 4890.0))
    bars.append(make_bar(25, 4889.0, 4891.0, 4870.0, 4871.0))

    decision = strategy.evaluate(
        m5_bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=daily_closes,
        h1_closes=h1_closes,
        next_open_price=4871.0,
        current_spread=0.2,
        macro_regime=None,
    )

    assert decision.is_ready is True
    assert decision.metadata["sl_capped"] is True
    assert round(abs(decision.entry_price - decision.stop_loss_price), 2) == 12.0
