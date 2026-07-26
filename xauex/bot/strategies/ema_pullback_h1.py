"""H1 EMA pullback strategy aligned with D1/H1 dual-EMA trend bias."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from xauex.config import Config
from xauex.bot.filters.trend import TrendFilter, TrendSnapshot, calculate_ema
from xauex.bot.patterns.detector import Candle, PatternDetector, PatternType


STRATEGY_MODE = "EMA_PULLBACK_H1"


@dataclass
class StrategyDecision:
    """Result of evaluating the EMA pullback strategy on a closed H1 candle."""

    strategy_mode: str
    setup_stage: str
    gate_result: str
    direction: Optional[int] = None
    pattern: Optional[PatternType] = None
    entry_source: str = STRATEGY_MODE
    reference_level: Optional[float] = None
    entry_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trend_snapshot: Optional[TrendSnapshot] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return (
            self.gate_result == "OK"
            and self.direction in (1, -1)
            and self.entry_price is not None
            and self.stop_loss_price is not None
            and self.take_profit_price is not None
        )


@dataclass
class _ConsolidationBreak:
    pattern: PatternType
    anchor_price: float
    cluster_size: int


@dataclass
class _ContinuationClose:
    pattern: PatternType
    anchor_price: float


def calculate_atr(bars: Sequence[dict[str, Any]], period: int) -> Optional[float]:
    """Return ATR from closed bars using classic true range."""
    if len(bars) < period + 1:
        return None

    trs: list[float] = []
    for idx in range(1, len(bars)):
        current = bars[idx]
        prev_close = bars[idx - 1]["close"]
        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - prev_close),
            abs(current["low"] - prev_close),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


class EMAPullbackH1Strategy:
    """Trend-continuation strategy using D1 bias and H1 EMA pullbacks."""

    timeframe = "H1"

    def __init__(self, config: Config, trend_filter: Optional[TrendFilter] = None):
        self.config = config
        self.trend_filter = trend_filter or TrendFilter()
        self.pattern_detector = PatternDetector(config)

    def evaluate(
        self,
        *,
        bars: Sequence[dict[str, Any]],
        signal_index: int,
        daily_closes: Sequence[float],
        next_open_price: float,
        current_spread: float = 0.0,
    ) -> StrategyDecision:
        if signal_index < 1 or len(bars) <= signal_index:
            return StrategyDecision(STRATEGY_MODE, "BAR_SELECTION", "BAR_SELECTION_FAILED")

        execution_bars = list(bars[: signal_index + 1])
        execution_closes = [bar["close"] for bar in execution_bars]
        bias, bias_reason, snapshot = self.trend_filter.aligned_bias(
            daily_closes=daily_closes,
            execution_closes=execution_closes,
        )
        decision = StrategyDecision(
            strategy_mode=STRATEGY_MODE,
            setup_stage="REGIME_FILTER",
            gate_result=bias_reason,
            trend_snapshot=snapshot,
        )
        if bias is None or snapshot is None:
            return decision

        atr = calculate_atr(execution_bars, self.config.atr_period)
        if atr is None:
            decision.gate_result = "ATR_DATA_UNAVAILABLE"
            return decision

        slope = self._ema_slope(
            execution_closes,
            period=50,
            lookback=self.config.ema_slope_lookback,
        )
        decision.reference_level = snapshot.exec_ema_50
        decision.metadata.update(
            {
                "atr": round(atr, 2),
                "ema50": round(snapshot.exec_ema_50, 2),
                "ema200": round(snapshot.exec_ema_200, 2),
                "ema50_slope": round(slope, 2),
            }
        )

        regime_reason = self._regime_block_reason(
            bias=bias,
            snapshot=snapshot,
            atr=atr,
            ema50_slope=slope,
        )
        if regime_reason is not None:
            decision.gate_result = regime_reason
            return decision

        zone = max(
            self.config.ema_pullback_proximity_dollars,
            atr * self.config.ema_pullback_atr_multiplier,
        )
        pullback_bars = self._collect_pullback_bars(
            bars=bars,
            signal_index=signal_index,
            ema50=snapshot.exec_ema_50,
            zone=zone,
        )
        decision.setup_stage = "PULLBACK_ZONE"
        decision.metadata["pullback_zone"] = round(zone, 2)
        decision.metadata["pullback_bars"] = len(pullback_bars)
        if not pullback_bars:
            decision.gate_result = "NO_PULLBACK_TOUCH"
            return decision

        prev = self._candle_from_bar(bars[signal_index - 1])
        signal = self._candle_from_bar(bars[signal_index])
        pattern_result = self.pattern_detector.detect(prev, signal, snapshot.exec_ema_50)

        pattern: Optional[PatternType] = None
        entry_source = STRATEGY_MODE
        anchor_price: Optional[float] = None

        if pattern_result.pattern in {
            PatternType.BULLISH_PIN_BAR,
            PatternType.BEARISH_PIN_BAR,
            PatternType.BULLISH_ENGULFING,
            PatternType.BEARISH_ENGULFING,
        }:
            if pattern_result.direction == bias and self._confirm_close_side(signal, snapshot.exec_ema_50, bias):
                pattern = pattern_result.pattern
                entry_source = "EMA_PULLBACK_PATTERN"
                anchor_price = self._pullback_anchor(pullback_bars, bias)

        if pattern is None:
            breakout = self._detect_consolidation_break(
                bars=bars,
                signal_index=signal_index,
                bias=bias,
                atr=atr,
                ema50=snapshot.exec_ema_50,
                zone=zone,
            )
            if breakout is not None:
                pattern = breakout.pattern
                entry_source = "EMA_PULLBACK_BREAK"
                anchor_price = breakout.anchor_price
                decision.metadata["cluster_size"] = breakout.cluster_size

        if pattern is None:
            continuation = self._detect_continuation_close(
                prev=prev,
                signal=signal,
                bias=bias,
                ema50=snapshot.exec_ema_50,
                zone=zone,
                pullback_bars=pullback_bars,
            )
            if continuation is not None:
                pattern = continuation.pattern
                entry_source = "EMA_PULLBACK_CONTINUATION"
                anchor_price = continuation.anchor_price

        decision.setup_stage = "CONFIRMATION_CANDLE"
        if pattern is None or anchor_price is None:
            decision.gate_result = "NO_CONFIRMATION_PATTERN"
            return decision

        stop_loss = self._build_stop_loss(
            direction=bias,
            entry_price=next_open_price,
            anchor_price=anchor_price,
            ema200=snapshot.exec_ema_200,
            atr=atr,
            current_spread=current_spread,
        )
        sl_distance = abs(next_open_price - stop_loss)
        take_profit = next_open_price + (bias * sl_distance * self.config.ema_pullback_take_profit_rr)

        decision.setup_stage = "ENTRY_ARMED"
        decision.gate_result = "OK"
        decision.direction = bias
        decision.pattern = pattern
        decision.entry_source = entry_source
        decision.entry_price = round(next_open_price, 2)
        decision.stop_loss_price = round(stop_loss, 2)
        decision.take_profit_price = round(take_profit, 2)
        decision.metadata["anchor_price"] = round(anchor_price, 2)
        decision.metadata["sl_distance"] = round(sl_distance, 2)
        return decision

    def _regime_block_reason(
        self,
        *,
        bias: int,
        snapshot: TrendSnapshot,
        atr: float,
        ema50_slope: float,
    ) -> Optional[str]:
        if atr < self.config.atr_min_dollars:
            return "ATR_TOO_LOW"

        ema_distance = abs(snapshot.exec_ema_50 - snapshot.exec_ema_200)
        if ema_distance < (atr * self.config.ema_min_separation_atr):
            return "EMA_TOO_TIGHT"

        min_slope = self.config.ema_min_slope_dollars
        if bias > 0 and ema50_slope < min_slope:
            return "EMA_SLOPE_FLAT"
        if bias < 0 and ema50_slope > -min_slope:
            return "EMA_SLOPE_FLAT"
        return None

    def _detect_continuation_close(
        self,
        *,
        prev: Candle,
        signal: Candle,
        bias: int,
        ema50: float,
        zone: float,
        pullback_bars: Sequence[dict[str, Any]],
    ) -> Optional[_ContinuationClose]:
        """Allow a simpler trend-continuation close after a pullback into EMA50."""
        if signal.range <= 0:
            return None

        close_position = (signal.close - signal.low) / signal.range
        body_ratio = signal.body / signal.range
        anchor_price = self._pullback_anchor(pullback_bars, bias)

        if bias > 0:
            touched = signal.low <= (ema50 + zone)
            if (
                touched
                and signal.is_bullish
                and signal.close > ema50
                and signal.close > prev.high
                and close_position >= 0.65
                and body_ratio >= 0.4
            ):
                return _ContinuationClose(
                    pattern=PatternType.BULLISH_CONTINUATION_CLOSE,
                    anchor_price=anchor_price,
                )
            return None

        touched = signal.high >= (ema50 - zone)
        if (
            touched
            and signal.is_bearish
            and signal.close < ema50
            and signal.close < prev.low
            and close_position <= 0.35
            and body_ratio >= 0.4
        ):
            return _ContinuationClose(
                pattern=PatternType.BEARISH_CONTINUATION_CLOSE,
                anchor_price=anchor_price,
            )
        return None

    def _ema_slope(self, closes: Sequence[float], period: int, lookback: int) -> float:
        current = calculate_ema(closes, period)
        if current is None or len(closes) <= lookback:
            return 0.0
        prior = calculate_ema(closes[:-lookback], period)
        if prior is None:
            return 0.0
        return current - prior

    def _collect_pullback_bars(
        self,
        *,
        bars: Sequence[dict[str, Any]],
        signal_index: int,
        ema50: float,
        zone: float,
    ) -> list[dict[str, Any]]:
        start = max(0, signal_index - self.config.ema_pullback_lookback_bars + 1)
        window = list(bars[start : signal_index + 1])
        touched: list[dict[str, Any]] = []
        for bar in reversed(window):
            if self._bar_touches_zone(bar, ema50, zone):
                touched.append(bar)
            elif touched:
                break
        touched.reverse()
        return touched

    @staticmethod
    def _bar_touches_zone(bar: dict[str, Any], center: float, zone: float) -> bool:
        return bar["low"] <= (center + zone) and bar["high"] >= (center - zone)

    @staticmethod
    def _confirm_close_side(signal: Candle, ema50: float, bias: int) -> bool:
        if bias > 0:
            return signal.close > ema50 and signal.is_bullish
        return signal.close < ema50 and signal.is_bearish

    def _detect_consolidation_break(
        self,
        *,
        bars: Sequence[dict[str, Any]],
        signal_index: int,
        bias: int,
        atr: float,
        ema50: float,
        zone: float,
    ) -> Optional[_ConsolidationBreak]:
        signal = bars[signal_index]
        max_range = atr * self.config.consolidation_range_atr_max
        max_candles = self.config.consolidation_max_candles
        min_candles = self.config.consolidation_min_candles

        for cluster_size in range(min_candles, max_candles + 1):
            cluster_start = signal_index - cluster_size
            if cluster_start < 1:
                continue
            cluster = list(bars[cluster_start:signal_index])
            if not cluster:
                continue
            if not all((bar["high"] - bar["low"]) <= max_range for bar in cluster):
                continue
            if not any(self._bar_touches_zone(bar, ema50, zone) for bar in cluster):
                continue

            if bias > 0:
                cluster_high = max(bar["high"] for bar in cluster)
                if signal["close"] > cluster_high and signal["close"] > ema50 and signal["close"] > signal["open"]:
                    return _ConsolidationBreak(
                        pattern=PatternType.BULLISH_CONSOLIDATION_BREAK,
                        anchor_price=min(bar["low"] for bar in cluster),
                        cluster_size=cluster_size,
                    )
            else:
                cluster_low = min(bar["low"] for bar in cluster)
                if signal["close"] < cluster_low and signal["close"] < ema50 and signal["close"] < signal["open"]:
                    return _ConsolidationBreak(
                        pattern=PatternType.BEARISH_CONSOLIDATION_BREAK,
                        anchor_price=max(bar["high"] for bar in cluster),
                        cluster_size=cluster_size,
                    )
        return None

    @staticmethod
    def _pullback_anchor(bars: Sequence[dict[str, Any]], bias: int) -> float:
        if bias > 0:
            return min(bar["low"] for bar in bars)
        return max(bar["high"] for bar in bars)

    def _build_stop_loss(
        self,
        *,
        direction: int,
        entry_price: float,
        anchor_price: float,
        ema200: float,
        atr: float,
        current_spread: float,
    ) -> float:
        buffer = max(
            self.config.sl_buffer_min_dollars,
            atr * self.config.sl_buffer_atr_multiplier,
            current_spread * 3.0,
        )

        if direction > 0:
            structural_stop = anchor_price - buffer
            ema_stop = ema200 - buffer
            stop = min(structural_stop, ema_stop)
            max_allowed = entry_price - self.config.sl_total_min_dollars
            if stop > max_allowed:
                stop = max_allowed
        else:
            structural_stop = anchor_price + buffer
            ema_stop = ema200 + buffer
            stop = max(structural_stop, ema_stop)
            min_allowed = entry_price + self.config.sl_total_min_dollars
            if stop < min_allowed:
                stop = min_allowed
        return stop

    @staticmethod
    def _candle_from_bar(bar: dict[str, Any]) -> Candle:
        return Candle(
            open=bar["open"],
            high=bar["high"],
            low=bar["low"],
            close=bar["close"],
            open_time=bar["open_time"],
        )
