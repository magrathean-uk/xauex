"""EMA-based trend filter for higher- and execution-timeframe alignment."""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple


_DAILY_FAST = 8
_DAILY_SLOW = 21
_EXEC_FAST = 50
_EXEC_SLOW = 200


def calculate_ema(closes: Sequence[float], period: int) -> Optional[float]:
    """Return the latest EMA value from oldest->newest closes, or None if insufficient."""
    if len(closes) < period:
        return None

    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for close in closes[period:]:
        ema = (close * alpha) + (ema * (1.0 - alpha))
    return ema


@dataclass
class TrendSnapshot:
    """Computed EMA values for daily and execution timeframe."""

    daily_ema_8: float
    daily_ema_21: float
    exec_ema_50: float
    exec_ema_200: float


class TrendFilter:
    """Gate entries to only trade with daily and H1 EMA alignment."""

    def aligned_bias(
        self,
        daily_closes: Sequence[float],
        execution_closes: Sequence[float],
    ) -> Tuple[Optional[int], str, Optional[TrendSnapshot]]:
        daily_fast = calculate_ema(daily_closes, _DAILY_FAST)
        daily_slow = calculate_ema(daily_closes, _DAILY_SLOW)
        exec_fast = calculate_ema(execution_closes, _EXEC_FAST)
        exec_slow = calculate_ema(execution_closes, _EXEC_SLOW)

        if None in (daily_fast, daily_slow, exec_fast, exec_slow):
            return None, "EMA_DATA_UNAVAILABLE", None

        snapshot = TrendSnapshot(
            daily_ema_8=daily_fast,
            daily_ema_21=daily_slow,
            exec_ema_50=exec_fast,
            exec_ema_200=exec_slow,
        )

        if daily_fast > daily_slow and exec_fast > exec_slow:
            return 1, "OK", snapshot

        if daily_slow > daily_fast and exec_slow > exec_fast:
            return -1, "OK", snapshot

        if daily_fast > daily_slow:
            return None, "EXEC_EMA_SELL_ONLY", snapshot

        if daily_slow > daily_fast:
            return None, "EXEC_EMA_BUY_ONLY", snapshot

        return None, "DAILY_EMA_FLAT", snapshot

    def evaluate(
        self,
        direction: int,
        daily_closes: Sequence[float],
        execution_closes: Sequence[float],
    ) -> Tuple[bool, str, Optional[TrendSnapshot]]:
        bias, reason, snapshot = self.aligned_bias(daily_closes, execution_closes)
        if bias is None:
            return False, reason, snapshot

        if direction > 0:
            return (bias > 0), ("OK" if bias > 0 else "EMA_DIRECTION_MISMATCH"), snapshot

        if direction < 0:
            return (bias < 0), ("OK" if bias < 0 else "EMA_DIRECTION_MISMATCH"), snapshot

        return False, "INVALID_DIRECTION", snapshot
