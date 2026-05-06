"""
Risk state persistence — save and restore RiskState across bot restarts.

The RiskState contains daily/weekly counters that MUST survive a process
restart (e.g., systemd restart after a crash) so the bot doesn't accidentally
reset its loss counter mid-day.

Persistence strategy:
  - Save to a dedicated risk_state.json file (separate from state.json)
  - Write atomically via temp-file + os.replace
  - Restore on startup; silently start fresh if file is missing or corrupted
  - File location: same directory as state.json, named "risk_state.json"
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.risk.gates import RiskState
from xauex.shared.safe_io import JsonLoadError, atomic_write_json, safe_load_json

logger = logging.getLogger(__name__)


def _risk_state_path(state_file_path: str) -> str:
    """Derive risk_state.json path from the configured state_file_path."""
    parent = Path(state_file_path).parent
    return str(parent / "risk_state.json")


async def save_risk_state(state: RiskState, state_file_path: str) -> None:
    """
    Atomically write RiskState to risk_state.json (non-blocking).
    """
    await asyncio.to_thread(_save_risk_state_sync, state, state_file_path)


def _save_risk_state_sync(state: RiskState, state_file_path: str) -> None:
    path = _risk_state_path(state_file_path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    data = state.to_dict()
    data["_saved_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        atomic_write_json(path, data, mode=0o600)
        logger.debug("[RISK PERSIST] Saved risk state to %s", path)
    except OSError as exc:
        logger.error("[RISK PERSIST] Failed to save risk state: %s", exc)


async def load_risk_state(state_file_path: str) -> Optional[RiskState]:
    """
    Load RiskState from risk_state.json (non-blocking).
    """
    return await asyncio.to_thread(_load_risk_state_sync, state_file_path)


def _load_risk_state_sync(state_file_path: str) -> Optional[RiskState]:
    path = _risk_state_path(state_file_path)
    try:
        loaded = safe_load_json(path, allow_missing=False)
        if not isinstance(loaded, dict):
            raise TypeError("risk_state.json root must be an object")
        data = dict(loaded)
        # Remove our metadata key before feeding to from_dict
        data.pop("_saved_at_utc", None)
        state = RiskState.from_dict(data)
        logger.info(
            "[RISK PERSIST] Restored risk state from %s: "
            "consecutive_losses=%d, weekly_pnl=%.2f, weekly_halted=%s",
            path,
            state.consecutive_losses_today,
            state.weekly_pnl,
            state.weekly_halted,
        )
        return state
    except FileNotFoundError:
        logger.info("[RISK PERSIST] No risk_state.json found — starting fresh.")
        return None
    except (JsonLoadError, KeyError, TypeError) as exc:
        logger.warning("[RISK PERSIST] Corrupted risk_state.json (%s) — starting fresh.", exc)
        return None
