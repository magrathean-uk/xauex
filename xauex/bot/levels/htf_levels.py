"""HTF (Weekly/Monthly) level management."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from xauex.config import Config

logger = logging.getLogger(__name__)

_XAUUSD_MIN = 500.0
_XAUUSD_MAX = 10000.0   # generous future-proof ceiling (gold at ~$5170 in 2026)
_DEDUP_DISTANCE = 1.0


@dataclass
class HTFLevels:
    """Container for previous daily, weekly, and monthly OHLC levels."""
    day_open: float
    day_high: float
    day_low: float
    day_close: float
    mn_open: float
    mn_high: float
    mn_low: float
    mn_close: float
    wk_open: float
    wk_high: float
    wk_low: float
    wk_close: float
    last_refresh_utc: datetime
    weekly_bar_open_time_utc: datetime


class LevelManager:
    """
    Manage HTF support/resistance levels.

    Extracts previous monthly and weekly OHLC from the cTrader API and
    refreshes them whenever the weekly bar boundary changes.

    Deduplication: two levels within $1.0 are merged to their average.
    Proximity: price is "at a level" when abs(price - level) <= LEVEL_PROXIMITY_DOLLARS.

    On startup, pass a LevelStore instance to restore persisted levels
    without requiring an immediate API call.
    """

    def __init__(self, config: Config, api_client, level_store=None):
        self.config = config
        self.api_client = api_client
        self._level_store = level_store
        self._raw: Optional[HTFLevels] = None
        self._deduped_levels: List[float] = []

        # Restore from disk if available
        if level_store is not None:
            restored = level_store._load_sync()
            if restored is not None:
                self._raw = restored
                self._deduped_levels = self._deduplicate(self._raw_values(restored))
                logger.info("[LEVELS] Restored from level store.")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Fetch D1, W1, and MN1 bars from API, extract and store levels."""
        try:
            daily = await self.api_client.get_trendbar("D1", 4)
            # Request 4 bars to ensure at least 2 fully closed bars are returned
            # (the current open bar may be included in the API response)
            monthly = await self.api_client.get_trendbar("MONTHLY", 4)
            weekly = await self.api_client.get_trendbar("WEEKLY", 4)
        except Exception as e:
            logger.error(f"[LEVELS] ERROR: Failed to fetch bars: {e}. Retaining previous levels.")
            return

        if len(daily) < 2 or len(monthly) < 2 or len(weekly) < 2:
            logger.error("[LEVELS] ERROR: Insufficient bars returned. Retaining previous levels.")
            return

        day = daily[-1]
        mn = monthly[-1]  # API never returns the forming bar, so -1 is the most recently closed monthly bar
        wk = weekly[-1]   # same for weekly

        candidate = HTFLevels(
            day_open=day["open"],
            day_high=day["high"],
            day_low=day["low"],
            day_close=day["close"],
            mn_open=mn["open"],
            mn_high=mn["high"],
            mn_low=mn["low"],
            mn_close=mn["close"],
            wk_open=wk["open"],
            wk_high=wk["high"],
            wk_low=wk["low"],
            wk_close=wk["close"],
            last_refresh_utc=datetime.now(timezone.utc),
            weekly_bar_open_time_utc=wk["open_time"],
        )

        if not self._validate(candidate):
            return  # validation already logged; retain previous

        self._raw = candidate
        self._deduped_levels = self._deduplicate(self._raw_values(candidate))

        if self._level_store is not None:
            await self._level_store.save(candidate)

        logger.info(
            f"[LEVELS] Refreshed. "
            f"D1: O={candidate.day_open:.2f} H={candidate.day_high:.2f} "
            f"L={candidate.day_low:.2f} C={candidate.day_close:.2f} | "
            f"MN: O={candidate.mn_open:.2f} H={candidate.mn_high:.2f} "
            f"L={candidate.mn_low:.2f} C={candidate.mn_close:.2f} | "
            f"WK: O={candidate.wk_open:.2f} H={candidate.wk_high:.2f} "
            f"L={candidate.wk_low:.2f} C={candidate.wk_close:.2f} | "
            f"Bar time: {candidate.weekly_bar_open_time_utc.isoformat()}Z"
        )

    async def refresh_if_needed(self) -> bool:
        """
        Check if weekly bar open_time has changed. Refresh if so.
        Returns True if a refresh was performed.
        """
        try:
            weekly = await self.api_client.get_trendbar("WEEKLY", 4)
        except Exception as e:
            logger.error(f"[LEVELS] ERROR: Could not check for refresh: {e}")
            return False

        if len(weekly) < 2:
            return False

        new_open_time = weekly[-1]["open_time"]  # -1 = most recently closed weekly bar

        if self._raw is None or new_open_time != self._raw.weekly_bar_open_time_utc:
            await self.refresh()
            return True

        return False

    def price_at_level(self, price: float) -> Optional[float]:
        """Return nearest level within proximity, else None."""
        proximity = self.config.level_proximity_dollars
        candidates = [
            (abs(price - lvl), lvl)
            for lvl in self._deduped_levels
            if abs(price - lvl) <= proximity
        ]
        if not candidates:
            return None
        nearest = min(candidates)[1]
        logger.info(f"[LEVELS] Price {price:.2f} within {proximity} of level {nearest:.2f}")
        return nearest

    def all_levels(self) -> List[float]:
        """Return all deduplicated levels sorted ascending."""
        return sorted(self._deduped_levels)

    def levels_near_range(self, low: float, high: float) -> List[float]:
        """
        Return all levels touched by, or within configured proximity of, a price range.

        This is more permissive than close-only checks and is better aligned with
        wick/body reaction patterns.
        """
        proximity = self.config.level_proximity_dollars
        lo = min(low, high) - proximity
        hi = max(low, high) + proximity
        midpoint = (low + high) / 2.0
        candidates = [lvl for lvl in self._deduped_levels if lo <= lvl <= hi]
        return sorted(candidates, key=lambda lvl: abs(lvl - midpoint))

    def next_level_from(self, price: float, direction: int) -> Optional[float]:
        """
        Return nearest level in given direction from price.
        direction: +1 = above (TP for longs), -1 = below (TP for shorts).
        Returns None if no level exists in that direction.
        """
        levels = sorted(self._deduped_levels)
        if direction == 1:
            above = [lvl for lvl in levels if lvl > price]
            return above[0] if above else None
        else:
            below = [lvl for lvl in levels if lvl < price]
            return below[-1] if below else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_values(self, raw: HTFLevels) -> List[float]:
        return [
            raw.day_open, raw.day_high, raw.day_low, raw.day_close,
            raw.mn_open, raw.mn_high, raw.mn_low, raw.mn_close,
            raw.wk_open, raw.wk_high, raw.wk_low, raw.wk_close,
        ]

    def _validate(self, candidate: HTFLevels) -> bool:
        """Validate extracted levels. Log and return False on failure."""
        values = self._raw_values(candidate)

        for v in values:
            if v <= 0 or not (_XAUUSD_MIN <= v <= _XAUUSD_MAX):
                logger.error(
                    f"[LEVELS] ERROR: Refresh validation failed (out of range: {v}). "
                    "Retaining previous levels."
                )
                return False

        if candidate.day_high <= candidate.day_low:
            logger.error(
                f"[LEVELS] ERROR: Refresh validation failed "
                f"(D1_H={candidate.day_high} <= D1_L={candidate.day_low}). "
                "Retaining previous levels."
            )
            return False

        if candidate.mn_high <= candidate.mn_low:
            logger.error(
                f"[LEVELS] ERROR: Refresh validation failed "
                f"(MN_H={candidate.mn_high} <= MN_L={candidate.mn_low}). "
                "Retaining previous levels."
            )
            return False

        if candidate.wk_high <= candidate.wk_low:
            logger.error(
                f"[LEVELS] ERROR: Refresh validation failed "
                f"(WK_H={candidate.wk_high} <= WK_L={candidate.wk_low}). "
                "Retaining previous levels."
            )
            return False

        return True

    def _deduplicate(self, levels: List[float]) -> List[float]:
        """
        Merge any two levels within $1.0 of each other to their average.
        Works iteratively until no more merges are needed.
        """
        changed = True
        result = sorted(levels)
        while changed:
            changed = False
            merged = []
            skip = set()
            for i, lvl in enumerate(result):
                if i in skip:
                    continue
                if i + 1 < len(result) and abs(result[i + 1] - lvl) <= _DEDUP_DISTANCE:
                    merged.append((lvl + result[i + 1]) / 2)
                    skip.add(i + 1)
                    changed = True
                else:
                    merged.append(lvl)
            result = merged
        return result
