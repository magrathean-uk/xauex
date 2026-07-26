"""State file writer for dashboard consumption."""

# ruff: noqa: E402

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

from xauex.config import Config

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.shared.diagnostics import build_diagnostics_snapshot
from xauex.shared.safe_io import atomic_write_json

logger = logging.getLogger(__name__)


class StateWriter:
    """
    Write bot state to state.json atomically (temp file + os.replace).

    The dashboard reads this file every 2 s. Writes are non-blocking:
    a temp file is written then atomically renamed over the live path so
    the dashboard never sees a partial file.
    """

    def __init__(self, config: Config):
        self.config = config
        self._last_state: Optional[Dict[str, Any]] = None

    async def write(
        self,
        *,
        bot_status: Optional[str] = None,
        account: Optional[Dict] = None,
        risk_state=None,            # RiskState dataclass or dict
        levels=None,                # HTFLevels dataclass
        open_positions: Optional[List] = None,
        closed_trades: Optional[List] = None,
        recent_h1_closes: Optional[List[float]] = None,
        trade_entries_on_chart: Optional[List[Dict]] = None,
        last_signal: Optional[Dict] = None,
        signal_history: Optional[List[Dict]] = None,
        strategy: Optional[Dict] = None,
        shadow_last_signal: Optional[Dict] = None,
        shadow_signal_history: Optional[List[Dict]] = None,
        macro_regime: Optional[Dict] = None,
        trade_policy: Optional[Dict] = None,
        trend: Optional[Dict] = None,
        runtime: Optional[Dict] = None,
        last_error: Optional[str] = None,
    ) -> None:
        """Write state to state.json atomically (non-blocking)."""
        previous_state = self._last_state or {}
        meta = previous_state.get("meta", {})

        state = self._build_state(
            bot_status=bot_status or meta.get("bot_status", "RUNNING"),
            account=account if account is not None else previous_state.get("account"),
            risk_state=risk_state if risk_state is not None else previous_state.get("risk"),
            levels=levels if levels is not None else previous_state.get("levels"),
            open_positions=open_positions if open_positions is not None else previous_state.get("open_positions", []),
            closed_trades=closed_trades if closed_trades is not None else previous_state.get("closed_trades_today", []),
            recent_h1_closes=recent_h1_closes if recent_h1_closes is not None else previous_state.get("recent_h1_closes", []),
            trade_entries_on_chart=(trade_entries_on_chart if trade_entries_on_chart is not None else previous_state.get("trade_entries_on_chart", [])),
            last_signal=last_signal if last_signal is not None else previous_state.get("last_signal"),
            signal_history=signal_history if signal_history is not None else previous_state.get("signal_history", []),
            strategy=strategy if strategy is not None else previous_state.get("strategy", {}),
            shadow_last_signal=(shadow_last_signal if shadow_last_signal is not None else previous_state.get("shadow_last_signal")),
            shadow_signal_history=(shadow_signal_history if shadow_signal_history is not None else previous_state.get("shadow_signal_history", [])),
            macro_regime=macro_regime if macro_regime is not None else previous_state.get("macro_regime", {}),
            trade_policy=trade_policy if trade_policy is not None else previous_state.get("trade_policy", {}),
            trend=trend if trend is not None else previous_state.get("trend", {}),
            runtime=runtime if runtime is not None else previous_state.get("runtime", {}),
            last_error=last_error if last_error is not None else previous_state.get("last_error"),
        )
        await asyncio.to_thread(self._atomic_write, state)
        self._last_state = state

    def _build_state(
        self,
        bot_status: str,
        account: Optional[Dict],
        risk_state,
        levels,
        open_positions: List,
        closed_trades: List,
        recent_h1_closes: List[float],
        trade_entries_on_chart: List[Dict],
        last_signal: Optional[Dict],
        signal_history: List[Dict],
        strategy: Dict,
        shadow_last_signal: Optional[Dict],
        shadow_signal_history: List[Dict],
        macro_regime: Dict,
        trade_policy: Dict,
        trend: Dict,
        runtime: Dict,
        last_error: Optional[str],
    ) -> Dict:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Risk section
        if risk_state is None:
            risk_section = {}
        elif hasattr(risk_state, "to_dict"):
            risk_section = risk_state.to_dict()
        else:
            risk_section = dict(risk_state)

        # Levels section
        if levels is None:
            levels_section = {}
        elif isinstance(levels, dict):
            levels_section = dict(levels)
        else:
            refresh_str = (
                levels.last_refresh_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                if levels.last_refresh_utc else None
            )
            levels_section = {
                "last_refresh_utc": refresh_str,
                "monthly": {
                    "open": levels.mn_open,
                    "high": levels.mn_high,
                    "low": levels.mn_low,
                    "close": levels.mn_close,
                },
                "daily": {
                    "open": levels.day_open,
                    "high": levels.day_high,
                    "low": levels.day_low,
                    "close": levels.day_close,
                },
                "weekly": {
                    "open": levels.wk_open,
                    "high": levels.wk_high,
                    "low": levels.wk_low,
                    "close": levels.wk_close,
                },
            }

        # Open positions section
        positions_section = []
        for p in open_positions:
            if hasattr(p, "__dict__"):
                pos = {
                    "position_id": p.position_id,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "lot_size": p.lot_size,
                    "open_time_utc": (
                        p.open_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if hasattr(p.open_time_utc, "strftime") else str(p.open_time_utc)
                    ),
                    "unrealised_pnl": getattr(p, "unrealised_pnl", 0.0),
                    "pattern": p.pattern.name if hasattr(p.pattern, "name") else str(p.pattern),
                    "level": p.level,
                    "owner": getattr(p, "owner", "strategy"),
                    "metadata": getattr(p, "metadata", {}) or {},
                }
            else:
                pos = dict(p)
            positions_section.append(pos)

        return {
            "meta": {
                "version": 1,
                "last_updated_utc": now,
                "bot_status": bot_status,
            },
            "account": account or {},
            "risk": risk_section,
            "levels": levels_section,
            "open_positions": positions_section,
            "closed_trades_today": closed_trades,
            "recent_h1_closes": recent_h1_closes,
            "trade_entries_on_chart": trade_entries_on_chart,
            "last_signal": last_signal,
            "signal_history": signal_history,
            "strategy": strategy,
            "shadow_last_signal": shadow_last_signal,
            "shadow_signal_history": shadow_signal_history,
            "macro_regime": macro_regime,
            "trade_policy": trade_policy,
            "trend": trend,
            "runtime": runtime,
            "last_error": last_error,
            "diagnostics": build_diagnostics_snapshot({
                "meta": {
                    "bot_status": bot_status,
                    "last_updated_utc": now,
                },
                "account": account or {},
                "risk": risk_section,
                "levels": levels_section,
                "open_positions": positions_section,
                "closed_trades_today": closed_trades,
                "recent_h1_closes": recent_h1_closes,
                "trade_entries_on_chart": trade_entries_on_chart,
                "last_signal": last_signal,
                "signal_history": signal_history,
                "strategy": strategy,
                "shadow_last_signal": shadow_last_signal,
                "shadow_signal_history": shadow_signal_history,
                "macro_regime": macro_regime,
                "trade_policy": trade_policy,
                "trend": trend,
                "runtime": runtime,
                "last_error": last_error,
            }),
        }

    def _atomic_write(self, state: Dict) -> None:
        """Write state to a temp file, then atomically rename over the live path."""
        try:
            atomic_write_json(self.config.state_file_path, state, mode=0o600)
        except OSError as exc:
            logger.error("[STATE] Failed to write state file: %s", exc)
