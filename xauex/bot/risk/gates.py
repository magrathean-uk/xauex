"""Trading gates based on risk management rules."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Tuple

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class RiskState:
    """
    Persistent risk state. Written to state.json on every change.
    Restored on startup to handle mid-day restarts.
    """
    consecutive_losses_today: int = 0
    losses_date_utc: str = ""            # "YYYY-MM-DD"
    weekly_pnl: float = 0.0
    week_start_balance: float = 0.0
    week_start_date_utc: str = ""        # "YYYY-MM-DD" (Monday)
    daily_pnl: float = 0.0
    day_start_balance: float = 0.0
    day_start_date_utc: str = ""         # "YYYY-MM-DD"
    weekly_halted: bool = False
    daily_halted: bool = False
    xauex_trade_date_london: str = ""
    xauex_trades_taken_london: int = 0
    xauex_signal_runs_london: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "consecutive_losses_today": self.consecutive_losses_today,
            "losses_date_utc": self.losses_date_utc,
            "weekly_pnl": self.weekly_pnl,
            "week_start_balance": self.week_start_balance,
            "week_start_date_utc": self.week_start_date_utc,
            "daily_pnl": self.daily_pnl,
            "day_start_balance": self.day_start_balance,
            "day_start_date_utc": self.day_start_date_utc,
            "weekly_halted": self.weekly_halted,
            "daily_halted": self.daily_halted,
            "xauex_trade_date_london": self.xauex_trade_date_london,
            "xauex_trades_taken_london": self.xauex_trades_taken_london,
            "xauex_signal_runs_london": list(self.xauex_signal_runs_london),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RiskState":
        return cls(
            consecutive_losses_today=d.get("consecutive_losses_today", 0),
            losses_date_utc=d.get("losses_date_utc", ""),
            weekly_pnl=d.get("weekly_pnl", 0.0),
            week_start_balance=d.get("week_start_balance", 0.0),
            week_start_date_utc=d.get("week_start_date_utc", ""),
            daily_pnl=d.get("daily_pnl", 0.0),
            day_start_balance=d.get("day_start_balance", 0.0),
            day_start_date_utc=d.get("day_start_date_utc", ""),
            weekly_halted=d.get("weekly_halted", False),
            daily_halted=d.get("daily_halted", False),
            xauex_trade_date_london=d.get("xauex_trade_date_london", ""),
            xauex_trades_taken_london=d.get("xauex_trades_taken_london", 0),
            xauex_signal_runs_london=list(d.get("xauex_signal_runs_london", [])),
        )


class RiskGates:
    """
    Enforce the three trading gates:
    1. Weekly drawdown limit (5% of week-start balance)
    2. Daily consecutive loss limit (3 losses in one calendar day UTC)
    3. Position cap (max 2 simultaneous open trades)

    Gates evaluated in sequence — first failure short-circuits evaluation.
    Weekly halt persists through the week once triggered; only resets Monday.
    Daily counter resets on new UTC day and on any winning trade.
    """

    def __init__(self, config: Config, state: RiskState):
        self.config = config
        self.state = state
        self._open_position_count: int = 0

    # ------------------------------------------------------------------
    # Gate evaluation
    # ------------------------------------------------------------------

    def can_trade(self, account_balance: float | None = None) -> Tuple[bool, str]:
        """
        Returns (True, 'OK') or (False, reason_code).
        reason_code: WEEKLY_DRAWDOWN_LIMIT | DAILY_DRAWDOWN_LIMIT | DAILY_CONSECUTIVE_LOSS_LIMIT | MAX_POSITIONS_REACHED
        """
        if account_balance is not None:
            self.ensure_period_baselines(account_balance)
        else:
            self._maybe_reset_daily()
            self._maybe_reset_weekly()

        # Gate 1: weekly drawdown
        if self.state.week_start_balance > 0 and self.state.weekly_pnl < 0:
            drawdown_pct = abs(self.state.weekly_pnl) / self.state.week_start_balance * 100
            if drawdown_pct >= self.config.weekly_stop_pct:
                self.state.weekly_halted = True
                return False, "WEEKLY_DRAWDOWN_LIMIT"

        if self.state.weekly_halted:
            return False, "WEEKLY_DRAWDOWN_LIMIT"

        # Gate 2: daily drawdown
        if self.state.day_start_balance > 0 and self.state.daily_pnl < 0:
            drawdown_pct = abs(self.state.daily_pnl) / self.state.day_start_balance * 100
            if drawdown_pct >= self.config.daily_stop_pct:
                self.state.daily_halted = True
                return False, "DAILY_DRAWDOWN_LIMIT"

        if self.state.daily_halted:
            return False, "DAILY_DRAWDOWN_LIMIT"

        # Gate 3: daily consecutive losses
        if self.state.consecutive_losses_today >= self.config.max_consecutive_losses:
            self.state.daily_halted = True
            return False, "DAILY_CONSECUTIVE_LOSS_LIMIT"

        # Gate 4: position cap
        if self._open_position_count >= self.config.max_open_trades:
            return False, "MAX_POSITIONS_REACHED"

        return True, "OK"

    def record_trade_closed(self, pnl: float) -> None:
        """
        Called on every position close. Updates consecutive loss counter and weekly P&L.
        A winning trade (pnl > 0) resets the daily consecutive loss counter.
        """
        self._maybe_reset_daily()

        self.state.weekly_pnl += pnl
        self.state.daily_pnl += pnl

        if pnl <= 0:
            self.state.consecutive_losses_today += 1
            logger.info(
                f"[RISK] Loss recorded. Consecutive today: {self.state.consecutive_losses_today}/"
                f"{self.config.max_consecutive_losses}. Weekly P&L: {self.state.weekly_pnl:.2f}"
            )
        else:
            self.state.consecutive_losses_today = 0
            self.state.daily_halted = False
            logger.info(
                f"[RISK] Win recorded (+{pnl:.2f}). Loss counter reset. "
                f"Weekly P&L: {self.state.weekly_pnl:.2f}"
            )

        # Persist date for daily reset detection
        self.state.losses_date_utc = _today_utc()

    def record_week_start(self, balance: float) -> None:
        """Snapshot week-start balance. Call at Monday 00:00 UTC."""
        self.state.week_start_balance = balance
        self.state.week_start_date_utc = _today_utc()
        self.state.weekly_pnl = 0.0
        self.state.weekly_halted = False
        logger.info(f"[RISK] Week started. Balance snapshot: {balance:.2f}")

    def record_day_start(self, balance: float) -> None:
        """Snapshot day-start balance. Call at startup and on each new UTC day."""
        self.state.day_start_balance = balance
        self.state.day_start_date_utc = _today_utc()
        self.state.daily_pnl = 0.0
        self.state.daily_halted = False
        self.state.consecutive_losses_today = 0
        self.state.losses_date_utc = self.state.day_start_date_utc
        logger.info(f"[RISK] Day started. Balance snapshot: {balance:.2f}")

    def ensure_period_baselines(self, balance: float) -> None:
        """Initialize or roll day/week baselines from the latest account balance."""
        today = _today_utc()
        if not self.state.week_start_date_utc:
            self.record_week_start(balance)
        else:
            self._maybe_reset_weekly()
            if self.state.week_start_balance <= 0:
                self.record_week_start(balance)

        if not self.state.day_start_date_utc:
            self.record_day_start(balance)
        elif self.state.day_start_date_utc != today:
            self.record_day_start(balance)
        elif self.state.day_start_balance <= 0:
            self.record_day_start(balance)

    def set_open_position_count(self, count: int) -> None:
        """Update from PositionManager. Called before gate check."""
        self._open_position_count = count

    # ------------------------------------------------------------------
    # Internal reset helpers
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self) -> None:
        """Reset daily consecutive counter if UTC date has changed."""
        today = _today_utc()
        if self.state.losses_date_utc and self.state.losses_date_utc != today:
            self.state.consecutive_losses_today = 0
            self.state.daily_halted = False
            self.state.daily_pnl = 0.0
            self.state.day_start_date_utc = today
            self.state.losses_date_utc = today
            logger.info("[RISK] New UTC day. Daily loss counter reset.")

    def _maybe_reset_weekly(self) -> None:
        """Reset weekly halt and P&L only when a new UTC Monday has begun."""
        today = _today_utc()
        if not self.state.week_start_date_utc:
            return
        start = datetime.strptime(self.state.week_start_date_utc, "%Y-%m-%d").date()
        now = datetime.strptime(today, "%Y-%m-%d").date()
        if now > start and now.weekday() == 0:
            logger.info("[RISK] New Monday detected. Resetting weekly halt (balance snapshot needed).")
            self.state.weekly_halted = False
            self.state.weekly_pnl = 0.0
            self.state.week_start_date_utc = today


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
