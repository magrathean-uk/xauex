"""
Level store — persist deduplicated HTF levels to disk.

Survives bot restart without requiring an API refetch.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.levels.htf_levels import HTFLevels

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/var/lib/xauex/levels.json"


class LevelStore:
    """
    Persist HTFLevels to a JSON file and restore on startup.

    Write is atomic (temp file + os.replace). The LevelManager calls
    save() after every successful refresh and load() on startup.
    """

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = path

    async def save(self, levels: HTFLevels) -> None:
        """Persist levels to disk atomically (non-blocking)."""
        await asyncio.to_thread(self._save_sync, levels)

    def _save_sync(self, levels: HTFLevels) -> None:
        """Synchronous write — run via asyncio.to_thread."""
        data = {
            "day_open": levels.day_open,
            "day_high": levels.day_high,
            "day_low": levels.day_low,
            "day_close": levels.day_close,
            "mn_open": levels.mn_open,
            "mn_high": levels.mn_high,
            "mn_low": levels.mn_low,
            "mn_close": levels.mn_close,
            "wk_open": levels.wk_open,
            "wk_high": levels.wk_high,
            "wk_low": levels.wk_low,
            "wk_close": levels.wk_close,
            "last_refresh_utc": (
                levels.last_refresh_utc.isoformat()
                if levels.last_refresh_utc else None
            ),
            "weekly_bar_open_time_utc": (
                levels.weekly_bar_open_time_utc.isoformat()
                if levels.weekly_bar_open_time_utc else None
            ),
        }
        store_dir = os.path.dirname(self.path)
        if store_dir:
            os.makedirs(store_dir, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=store_dir or ".", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
            logger.debug("[LEVEL STORE] Saved to %s", self.path)
        except OSError as exc:
            logger.error("[LEVEL STORE] Failed to save: %s", exc)
            try:
                os.unlink(tmp)
            except Exception:
                pass

    async def load(self) -> Optional[HTFLevels]:
        """Load persisted levels (non-blocking). Returns None if file missing or corrupt."""
        return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> Optional[HTFLevels]:
        try:
            with open(self.path) as f:
                data = json.load(f)

            def _parse_dt(s):
                if s is None:
                    return None
                return datetime.fromisoformat(s)

            return HTFLevels(
                day_open=data["day_open"],
                day_high=data["day_high"],
                day_low=data["day_low"],
                day_close=data["day_close"],
                mn_open=data["mn_open"],
                mn_high=data["mn_high"],
                mn_low=data["mn_low"],
                mn_close=data["mn_close"],
                wk_open=data["wk_open"],
                wk_high=data["wk_high"],
                wk_low=data["wk_low"],
                wk_close=data["wk_close"],
                last_refresh_utc=_parse_dt(data.get("last_refresh_utc")),
                weekly_bar_open_time_utc=_parse_dt(data.get("weekly_bar_open_time_utc")),
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("[LEVEL STORE] Failed to load: %s", exc)
            return None
