from datetime import datetime, timedelta, timezone

from bot.filters.trend import TrendSnapshot
from bot.patterns.detector import PatternType
from bot.strategies.ema_pullback_h1 import EMAPullbackH1Strategy, calculate_atr


UTC = timezone.utc


def make_bar(index: int, open_price: float, high: float, low: float, close: float) -> dict:
    return {
        "open_time": datetime(2026, 3, 18, 0, 0, tzinfo=UTC) + timedelta(hours=index),
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
    }


def test_calculate_atr_returns_average_true_range():
    bars = [
        make_bar(0, 5000.0, 5004.0, 4998.0, 5002.0),
        make_bar(1, 5002.0, 5008.0, 5000.0, 5006.0),
        make_bar(2, 5006.0, 5010.0, 5003.0, 5008.0),
    ]
    assert calculate_atr(bars, 2) == 7.5


def test_strategy_returns_ready_on_trend_aligned_engulfing_pullback(minimal_config):
    strategy = EMAPullbackH1Strategy(minimal_config)
    bars = []
    price = 4800.0
    for idx in range(58):
        close = price + 4.0
        bars.append(make_bar(idx, price, close + 1.0, price - 1.0, close))
        price = close
    bars.append(make_bar(58, 5004.0, 5005.0, 4996.0, 4998.0))
    bars.append(make_bar(59, 4997.0, 5011.0, 4995.0, 5009.0))

    def aligned_bias(**kwargs):
        return 1, "OK", TrendSnapshot(
            daily_ema_8=5105.0,
            daily_ema_21=5088.0,
            exec_ema_50=5000.0,
            exec_ema_200=4970.0,
        )

    strategy.trend_filter.aligned_bias = aligned_bias

    decision = strategy.evaluate(
        bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=[1.0] * 30,
        next_open_price=5010.0,
        current_spread=0.3,
    )

    assert decision.is_ready is True
    assert decision.pattern == PatternType.BULLISH_ENGULFING
    assert decision.direction == 1
    assert decision.entry_source == "EMA_PULLBACK_PATTERN"
    assert decision.stop_loss_price < decision.entry_price
    assert decision.take_profit_price > decision.entry_price


def test_strategy_rejects_when_no_pullback_touch(minimal_config):
    strategy = EMAPullbackH1Strategy(minimal_config)
    bars = []
    price = 4800.0
    for idx in range(60):
        close = price + 4.0
        bars.append(make_bar(idx, price, close + 1.0, price - 1.0, close))
        price = close

    def aligned_bias(**kwargs):
        return 1, "OK", TrendSnapshot(
            daily_ema_8=5105.0,
            daily_ema_21=5088.0,
            exec_ema_50=4950.0,
            exec_ema_200=4920.0,
        )

    strategy.trend_filter.aligned_bias = aligned_bias

    decision = strategy.evaluate(
        bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=[1.0] * 30,
        next_open_price=5045.0,
        current_spread=0.3,
    )

    assert decision.is_ready is False
    assert decision.gate_result == "NO_PULLBACK_TOUCH"


def test_strategy_detects_consolidation_break(minimal_config):
    strategy = EMAPullbackH1Strategy(minimal_config)
    bars = []
    price = 4800.0
    for idx in range(56):
        close = price + 4.0
        bars.append(make_bar(idx, price, close + 1.0, price - 1.0, close))
        price = close
    bars.extend(
        [
            make_bar(56, 5000.5, 5002.4, 4999.6, 5001.0),
            make_bar(57, 5001.0, 5002.6, 4999.8, 5000.7),
            make_bar(58, 5000.7, 5002.5, 4999.7, 5001.1),
            make_bar(59, 5002.0, 5012.0, 5000.0, 5010.0),
        ]
    )

    def aligned_bias(**kwargs):
        return 1, "OK", TrendSnapshot(
            daily_ema_8=5105.0,
            daily_ema_21=5088.0,
            exec_ema_50=5000.0,
            exec_ema_200=4972.0,
        )

    strategy.trend_filter.aligned_bias = aligned_bias

    decision = strategy.evaluate(
        bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=[1.0] * 30,
        next_open_price=5011.0,
        current_spread=0.3,
    )

    assert decision.is_ready is True
    assert decision.pattern == PatternType.BULLISH_CONSOLIDATION_BREAK
    assert decision.entry_source == "EMA_PULLBACK_BREAK"


def test_strategy_detects_bearish_continuation_close(minimal_config):
    strategy = EMAPullbackH1Strategy(minimal_config)
    bars = []
    price = 5200.0
    for idx in range(58):
        close = price - 6.0
        bars.append(make_bar(idx, price, price + 2.0, close - 2.0, close))
        price = close
    bars.append(make_bar(58, 4760.0, 4778.0, 4758.0, 4772.0))
    bars.append(make_bar(59, 4771.0, 4773.0, 4742.0, 4745.0))

    def aligned_bias(**kwargs):
        return -1, "OK", TrendSnapshot(
            daily_ema_8=4920.0,
            daily_ema_21=5010.0,
            exec_ema_50=4760.0,
            exec_ema_200=4940.0,
        )

    strategy.trend_filter.aligned_bias = aligned_bias

    decision = strategy.evaluate(
        bars=bars,
        signal_index=len(bars) - 1,
        daily_closes=[1.0] * 30,
        next_open_price=4744.0,
        current_spread=0.3,
    )

    assert decision.is_ready is True
    assert decision.pattern == PatternType.BEARISH_CONTINUATION_CLOSE
    assert decision.entry_source == "EMA_PULLBACK_CONTINUATION"
    assert decision.direction == -1
    assert decision.stop_loss_price > decision.entry_price
    assert decision.take_profit_price < decision.entry_price


def test_stop_loss_respects_total_minimum_distance(minimal_config):
    strategy = EMAPullbackH1Strategy(minimal_config)
    stop = strategy._build_stop_loss(
        direction=1,
        entry_price=5000.0,
        anchor_price=4998.0,
        ema200=4997.0,
        atr=4.0,
        current_spread=0.2,
    )

    assert stop == 4990.0
