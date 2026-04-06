"""Pattern detection for execution-timeframe candles."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from config import Config

logger = logging.getLogger(__name__)

# Default thresholds (overridden by config)
_PIN_MAX_BODY_RATIO = 0.30
_PIN_MIN_WICK_RATIO = 0.60


class PatternType(Enum):
    """Pattern classification."""
    NONE = 0
    BULLISH_PIN_BAR = 1
    BEARISH_PIN_BAR = 2
    BULLISH_ENGULFING = 3
    BEARISH_ENGULFING = 4
    INSIDE_BAR = 5
    BULLISH_CONSOLIDATION_BREAK = 6
    BEARISH_CONSOLIDATION_BREAK = 7
    BULLISH_CONTINUATION_CLOSE = 8
    BEARISH_CONTINUATION_CLOSE = 9


@dataclass
class Candle:
    """OHLC candle data."""
    open: float
    high: float
    low: float
    close: float
    open_time: datetime

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - self.body_high

    @property
    def lower_wick(self) -> float:
        return self.body_low - self.low


@dataclass
class PatternResult:
    """Result of pattern detection."""
    pattern: PatternType
    signal_candle: Candle
    prev_candle: Candle
    level: float
    direction: int        # +1 long, -1 short, 0 inside bar pending
    mother_bar_high: float
    mother_bar_low: float


class CandleWatcher:
    """
    Track execution-timeframe candle boundaries from incoming ticks.
    Returns True when a new candle has opened (previous one closed).
    """

    _PERIOD_MINUTES = {
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
    }

    def __init__(self, timeframe: str = "H1"):
        timeframe = timeframe.upper()
        if timeframe not in self._PERIOD_MINUTES:
            raise ValueError(f"Unsupported timeframe for CandleWatcher: {timeframe}")
        self.timeframe = timeframe
        self.period_minutes = self._PERIOD_MINUTES[timeframe]
        self.current_candle_open: Optional[datetime] = None

    def on_tick(self, tick_time: datetime) -> bool:
        """Returns True when a new configured candle has opened."""
        candle_open = self._bucket_open(tick_time)
        if self.current_candle_open is None:
            self.current_candle_open = candle_open
            return False
        if candle_open != self.current_candle_open:
            self.current_candle_open = candle_open
            return True
        return False

    def _bucket_open(self, tick_time: datetime) -> datetime:
        ts = int(tick_time.timestamp())
        bucket_seconds = self.period_minutes * 60
        floored = ts - (ts % bucket_seconds)
        tz = tick_time.tzinfo or timezone.utc
        return datetime.fromtimestamp(floored, tz=tz)


class PatternDetector:
    """
    Detect price action patterns on closed execution-timeframe candles.

    Pattern priority (highest to lowest):
    1. Engulfing (strongest signal)
    2. Pin Bar
    3. Inside Bar

    All patterns require level alignment check before being returned.
    Never raises — returns NONE on degenerate input.
    """

    def __init__(self, config: Config):
        self.config = config
        self._pin_max_body = getattr(config, 'pin_max_body_ratio', _PIN_MAX_BODY_RATIO)
        self._pin_min_wick = getattr(config, 'pin_min_wick_ratio', _PIN_MIN_WICK_RATIO)

    def detect(self, prev: Candle, signal: Candle, level: float) -> PatternResult:
        """
        Classify signal candle relative to prev and the HTF level.
        Returns PatternResult with pattern=NONE if no valid pattern found.
        """
        _none = PatternResult(
            pattern=PatternType.NONE,
            signal_candle=signal,
            prev_candle=prev,
            level=level,
            direction=0,
            mother_bar_high=prev.high,
            mother_bar_low=prev.low,
        )

        try:
            # Guard: degenerate zero-range candle
            if signal.range == 0:
                return _none

            # Priority 1: Engulfing
            engulfing = self._check_engulfing(prev, signal)
            if engulfing != PatternType.NONE:
                if self._engulfing_at_level(signal, level):
                    direction = 1 if engulfing == PatternType.BULLISH_ENGULFING else -1
                    return PatternResult(
                        pattern=engulfing,
                        signal_candle=signal,
                        prev_candle=prev,
                        level=level,
                        direction=direction,
                        mother_bar_high=prev.high,
                        mother_bar_low=prev.low,
                    )

            # Priority 2: Pin Bar
            pin = self._check_pin_bar(signal)
            if pin != PatternType.NONE:
                if self._pin_at_level(signal, pin, level):
                    direction = 1 if pin == PatternType.BULLISH_PIN_BAR else -1
                    return PatternResult(
                        pattern=pin,
                        signal_candle=signal,
                        prev_candle=prev,
                        level=level,
                        direction=direction,
                        mother_bar_high=prev.high,
                        mother_bar_low=prev.low,
                    )

            # Priority 3: Inside Bar
            # Priority 3: Inside Bar
            if self._check_inside_bar(prev, signal):
                if self._inside_bar_at_level(prev, level):
                    return PatternResult(
                        pattern=PatternType.INSIDE_BAR,
                        signal_candle=signal,
                        prev_candle=prev,
                        level=level,
                        direction=0,
                        mother_bar_high=prev.high,
                        mother_bar_low=prev.low,
                    )

            return _none

        except Exception as e:
            logger.error(f"[PATTERN] Unexpected error in detect(): {e}")
            return _none

    # ------------------------------------------------------------------
    # Pattern classifiers
    # ------------------------------------------------------------------

    def _check_pin_bar(self, candle: Candle) -> PatternType:
        """Classify a candle as bullish/bearish pin bar or NONE."""
        r = candle.range
        if r == 0:
            return PatternType.NONE

        body_ratio = candle.body / r
        if body_ratio > self._pin_max_body:
            return PatternType.NONE

        lower_ratio = candle.lower_wick / r
        upper_ratio = candle.upper_wick / r

        if lower_ratio >= self._pin_min_wick:
            return PatternType.BULLISH_PIN_BAR

        if upper_ratio >= self._pin_min_wick:
            return PatternType.BEARISH_PIN_BAR

        return PatternType.NONE

    def _check_engulfing(self, prev: Candle, signal: Candle) -> PatternType:
        """Classify as bullish/bearish engulfing or NONE. Body comparison only."""
        if prev.is_bearish and signal.is_bullish:
            if signal.body_low <= prev.body_low and signal.body_high >= prev.body_high:
                return PatternType.BULLISH_ENGULFING

        if prev.is_bullish and signal.is_bearish:
            if signal.body_high >= prev.body_high and signal.body_low <= prev.body_low:
                return PatternType.BEARISH_ENGULFING

        return PatternType.NONE

    def _check_inside_bar(self, prev: Candle, signal: Candle) -> bool:
        """True if signal is strictly inside prev (strict less-than)."""
        return signal.high < prev.high and signal.low > prev.low

    # ------------------------------------------------------------------
    # Level alignment checks
    # ------------------------------------------------------------------

    def _pin_at_level(self, signal: Candle, pin_type: PatternType, level: float) -> bool:
        """Pin bar: tip of dominant wick must be within proximity of level."""
        prox = self.config.level_proximity_dollars
        if pin_type == PatternType.BULLISH_PIN_BAR:
            return abs(signal.low - level) <= prox
        else:
            return abs(signal.high - level) <= prox

    def _engulfing_at_level(self, signal: Candle, level: float) -> bool:
        """Engulfing: signal body spans across or touches the level."""
        return signal.body_low <= level <= signal.body_high or \
               abs(signal.body_low - level) <= self.config.level_proximity_dollars or \
               abs(signal.body_high - level) <= self.config.level_proximity_dollars

    def _inside_bar_at_level(self, prev: Candle, level: float) -> bool:
        """Inside bar: mother bar high or low within proximity of level."""
        prox = self.config.level_proximity_dollars
        return abs(prev.high - level) <= prox or abs(prev.low - level) <= prox
