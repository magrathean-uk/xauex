"""Aggressive intraday M5 trend-continuation scalper."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from xauex.bot.filters.macro_regime import MacroRegime
from xauex.bot.filters.trend import TrendSnapshot, calculate_ema
from xauex.bot.patterns.detector import PatternType
from xauex.bot.strategies.ema_pullback_h1 import StrategyDecision, calculate_atr
from xauex.config import Config


STRATEGY_MODE = "SCALP_V1"


def _session_bias(
    *,
    daily_closes: Sequence[float],
    h1_closes: Sequence[float],
) -> tuple[Optional[int], str, Optional[TrendSnapshot], Optional[int], Optional[int]]:
    daily_fast = calculate_ema(daily_closes, 8)
    daily_slow = calculate_ema(daily_closes, 21)
    h1_fast = calculate_ema(h1_closes, 50)
    h1_slow = calculate_ema(h1_closes, 200)

    if None in (daily_fast, daily_slow, h1_fast, h1_slow):
        return None, "EMA_DATA_UNAVAILABLE", None, None, None

    snapshot = TrendSnapshot(
        daily_ema_8=daily_fast,
        daily_ema_21=daily_slow,
        exec_ema_50=h1_fast,
        exec_ema_200=h1_slow,
    )

    daily_bias = 1 if daily_fast > daily_slow else -1 if daily_slow > daily_fast else None
    h1_bias = 1 if h1_fast > h1_slow else -1 if h1_slow > h1_fast else None
    if h1_bias is None:
        return None, "H1_EMA_FLAT", snapshot, daily_bias, h1_bias
    if daily_bias == h1_bias:
        return h1_bias, "OK", snapshot, daily_bias, h1_bias
    if daily_bias is None:
        return None, "DAILY_EMA_FLAT", snapshot, daily_bias, h1_bias
    return None, "D1_H1_CONFLICT", snapshot, daily_bias, h1_bias


class M5ScalpStrategy:
    """M5 continuation scalper with D1/H1 bias and optional macro override."""

    timeframe = "M5"

    def __init__(self, config: Config):
        self.config = config

    def evaluate(
        self,
        *,
        m5_bars: Sequence[dict[str, Any]],
        signal_index: int,
        daily_closes: Sequence[float],
        h1_closes: Sequence[float],
        next_open_price: float,
        current_spread: float = 0.0,
        macro_regime: Optional[MacroRegime] = None,
        trade_policy: Optional[dict[str, Any]] = None,
    ) -> StrategyDecision:
        decision = StrategyDecision(
            strategy_mode=STRATEGY_MODE,
            setup_stage="BIAS",
            gate_result="EMA_DATA_UNAVAILABLE",
            entry_source=STRATEGY_MODE,
        )
        if signal_index < 1 or len(m5_bars) <= signal_index:
            decision.gate_result = "BAR_SELECTION_FAILED"
            return decision

        bias, bias_reason, snapshot, daily_bias, h1_bias = _session_bias(
            daily_closes=daily_closes,
            h1_closes=h1_closes,
        )
        decision.trend_snapshot = snapshot
        if snapshot is None:
            return decision

        macro_bias = macro_regime.bias if macro_regime else None
        macro_conf = macro_regime.confidence if macro_regime else 0.0
        policy_direction = None
        policy_mode = None
        pullback_multiplier = 1.0
        if trade_policy:
            policy_direction = str(trade_policy.get("direction", "BOTH")).upper()
            policy_mode = str(trade_policy.get("mode", "NORMAL")).upper()
            pullback_multiplier = float(trade_policy.get("pullback_zone_multiplier", 1.0))
        if bias is None:
            if (
                h1_bias is not None
                and macro_bias == h1_bias
                and macro_conf >= self.config.macro_regime_confidence_threshold
            ):
                bias = h1_bias
                bias_reason = "MACRO_OVERRIDE"
            else:
                decision.gate_result = bias_reason
                decision.metadata.update(
                    {
                        "daily_bias": daily_bias,
                        "h1_bias": h1_bias,
                        "macro_bias": macro_bias,
                        "macro_confidence": round(macro_conf, 2),
                        "policy_mode": policy_mode,
                        "policy_direction": policy_direction,
                    }
                )
                return decision

        m5_slice = list(m5_bars[: signal_index + 1])
        m5_closes = [bar["close"] for bar in m5_slice]
        atr = calculate_atr(m5_slice, self.config.scalp_atr_period)
        fast_ema = calculate_ema(m5_closes, self.config.scalp_fast_ema_period)
        slow_ema = calculate_ema(m5_closes, self.config.scalp_slow_ema_period)
        if None in (atr, fast_ema, slow_ema):
            decision.gate_result = "SCALP_DATA_UNAVAILABLE"
            return decision

        decision.reference_level = round(fast_ema, 2)
        decision.metadata.update(
            {
                "bias_reason": bias_reason,
                "daily_bias": daily_bias,
                "h1_bias": h1_bias,
                "macro_bias": macro_bias,
                "macro_confidence": round(macro_conf, 2),
                "policy_mode": policy_mode,
                "policy_direction": policy_direction,
                "m5_fast_ema": round(fast_ema, 2),
                "m5_slow_ema": round(slow_ema, 2),
                "m5_atr": round(atr, 2),
                "spread": round(current_spread, 2),
            }
        )

        decision.setup_stage = "REGIME_FILTER"
        if current_spread > self.config.scalp_spread_max_dollars:
            decision.gate_result = "SPREAD_TOO_WIDE"
            return decision
        if atr < self.config.scalp_atr_min_dollars:
            decision.gate_result = "SCALP_ATR_TOO_LOW"
            return decision
        if abs(fast_ema - slow_ema) < (atr * self.config.scalp_ema_distance_atr_min):
            decision.gate_result = "M5_EMA_TOO_TIGHT"
            return decision
        if policy_direction == "LONG_ONLY" and bias < 0:
            decision.gate_result = "POLICY_DIRECTION_BLOCK"
            return decision
        if policy_direction == "SHORT_ONLY" and bias > 0:
            decision.gate_result = "POLICY_DIRECTION_BLOCK"
            return decision

        zone = max(
            self.config.scalp_touch_proximity_dollars,
            atr * self.config.scalp_pullback_atr_multiplier * pullback_multiplier,
        )
        pullback_bars = self._collect_pullback_bars(
            bars=m5_bars,
            signal_index=signal_index,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            zone=zone,
        )
        decision.setup_stage = "PULLBACK_ZONE"
        decision.metadata["pullback_bars"] = len(pullback_bars)
        decision.metadata["pullback_zone"] = round(zone, 2)
        if not pullback_bars:
            decision.gate_result = "NO_PULLBACK_TOUCH"
            return decision

        prev = m5_bars[signal_index - 1]
        signal = m5_bars[signal_index]
        continuation_ok = self._is_continuation_close(
            prev=prev,
            signal=signal,
            bias=bias,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            atr=atr,
        )
        decision.setup_stage = "CONFIRMATION_CANDLE"
        if not continuation_ok:
            decision.gate_result = "NO_CONFIRMATION_PATTERN"
            return decision

        anchor = self._pullback_anchor(pullback_bars, bias)
        stop_loss = self._build_stop_loss(
            direction=bias,
            entry_price=next_open_price,
            anchor_price=anchor,
            atr=atr,
            current_spread=current_spread,
        )
        sl_distance = abs(next_open_price - stop_loss)
        if sl_distance < self.config.scalp_sl_min_dollars:
            decision.gate_result = "SCALP_SL_TOO_TIGHT"
            return decision
        if sl_distance > self.config.scalp_sl_max_dollars:
            stop_loss = self._cap_stop_loss(
                direction=bias,
                entry_price=next_open_price,
                max_distance=self.config.scalp_sl_max_dollars,
            )
            sl_distance = abs(next_open_price - stop_loss)
            decision.metadata["sl_capped"] = True

        take_profit = next_open_price + (bias * sl_distance * self.config.scalp_take_profit_rr)
        if trade_policy:
            take_profit = next_open_price + (
                bias * sl_distance * self.config.scalp_take_profit_rr * float(trade_policy.get("tp_rr_multiplier", 1.0))
            )
        decision.setup_stage = "ENTRY_ARMED"
        decision.gate_result = "OK"
        decision.direction = bias
        decision.pattern = (
            PatternType.BULLISH_CONTINUATION_CLOSE
            if bias > 0
            else PatternType.BEARISH_CONTINUATION_CLOSE
        )
        decision.entry_source = "SCALP_CONTINUATION"
        decision.entry_price = round(next_open_price, 2)
        decision.stop_loss_price = round(stop_loss, 2)
        decision.take_profit_price = round(take_profit, 2)
        decision.metadata["anchor_price"] = round(anchor, 2)
        decision.metadata["sl_distance"] = round(sl_distance, 2)
        if trade_policy:
            decision.metadata["policy_aggressiveness"] = trade_policy.get("aggressiveness")
        return decision

    def _collect_pullback_bars(
        self,
        *,
        bars: Sequence[dict[str, Any]],
        signal_index: int,
        fast_ema: float,
        slow_ema: float,
        zone: float,
    ) -> list[dict[str, Any]]:
        start = max(0, signal_index - self.config.scalp_pullback_lookback_bars + 1)
        touched: list[dict[str, Any]] = []
        for bar in bars[start : signal_index + 1]:
            if self._bar_touches(bar, fast_ema, zone) or self._bar_touches(bar, slow_ema, zone):
                touched.append(bar)
        return touched

    @staticmethod
    def _bar_touches(bar: dict[str, Any], level: float, zone: float) -> bool:
        return bar["low"] <= (level + zone) and bar["high"] >= (level - zone)

    def _is_continuation_close(
        self,
        *,
        prev: dict[str, Any],
        signal: dict[str, Any],
        bias: int,
        fast_ema: float,
        slow_ema: float,
        atr: float,
    ) -> bool:
        signal_range = signal["high"] - signal["low"]
        if signal_range <= 0:
            return False
        body_ratio = abs(signal["close"] - signal["open"]) / signal_range
        close_position = (signal["close"] - signal["low"]) / signal_range
        if body_ratio < (self.config.scalp_body_min_ratio * 0.85):
            return False

        threshold = self.config.scalp_close_position_threshold
        if bias > 0:
            breakout = signal["close"] > prev["high"]
            continuation_reclaim = (
                signal["close"] > signal["open"]
                and signal["close"] > fast_ema
                and signal["high"] >= (fast_ema - max(atr * 0.1, 1.0))
                and close_position >= (1.0 - min(0.5, threshold + 0.1))
            )
            return (
                signal["close"] > signal["open"]
                and signal["close"] > fast_ema
                and (signal["close"] > slow_ema or signal["close"] > prev["close"])
                and (breakout or continuation_reclaim)
            )
        breakout = signal["close"] < prev["low"]
        continuation_reject = (
            signal["close"] < signal["open"]
            and signal["close"] < fast_ema
            and signal["low"] <= (fast_ema + max(atr * 0.1, 1.0))
            and close_position <= min(0.5, threshold + 0.1)
        )
        return (
            signal["close"] < signal["open"]
            and signal["close"] < fast_ema
            and (signal["close"] < slow_ema or signal["close"] < prev["close"])
            and (breakout or continuation_reject)
        )

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
        atr: float,
        current_spread: float,
    ) -> float:
        buffer = max(
            self.config.scalp_stop_buffer_min_dollars,
            atr * self.config.scalp_stop_buffer_atr_multiplier,
            current_spread * 2.0,
        )
        if direction > 0:
            stop = anchor_price - buffer
            max_allowed = entry_price - self.config.scalp_sl_min_dollars
            if stop > max_allowed:
                stop = max_allowed
            return stop
        stop = anchor_price + buffer
        min_allowed = entry_price + self.config.scalp_sl_min_dollars
        if stop < min_allowed:
            stop = min_allowed
        return stop

    @staticmethod
    def _cap_stop_loss(
        *,
        direction: int,
        entry_price: float,
        max_distance: float,
    ) -> float:
        if direction > 0:
            return entry_price - max_distance
        return entry_price + max_distance

    @staticmethod
    def state_trend(
        *,
        daily_closes: Sequence[float],
        h1_closes: Sequence[float],
        macro_regime: Optional[MacroRegime],
        trade_policy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        bias, reason, snapshot, daily_bias, h1_bias = _session_bias(
            daily_closes=daily_closes,
            h1_closes=h1_closes,
        )
        macro_bias = macro_regime.bias if macro_regime else None
        if bias is None and h1_bias is not None and macro_bias == h1_bias and macro_regime and macro_regime.confidence >= 0.65:
            bias = h1_bias
            reason = "MACRO_OVERRIDE"
        policy_mode = None
        policy_direction = None
        if trade_policy:
            policy_mode = str(trade_policy.get("mode", "NORMAL")).upper()
            policy_direction = str(trade_policy.get("direction", "BOTH")).upper()
        trend = {
            "alignment": "BULLISH" if bias == 1 else "BEARISH" if bias == -1 else "MIXED",
            "reason": reason,
            "execution_timeframe": "M5",
            "daily_bias": daily_bias,
            "h1_bias": h1_bias,
            "policy_mode": policy_mode,
            "policy_direction": policy_direction,
        }
        if snapshot is not None:
            trend.update(
                {
                    "daily_ema_8": round(snapshot.daily_ema_8, 2),
                    "daily_ema_21": round(snapshot.daily_ema_21, 2),
                    "exec_ema_50": round(snapshot.exec_ema_50, 2),
                    "exec_ema_200": round(snapshot.exec_ema_200, 2),
                }
            )
        if macro_regime is not None:
            trend["macro_regime"] = macro_regime.regime
            trend["macro_confidence"] = round(macro_regime.confidence, 2)
        return trend
