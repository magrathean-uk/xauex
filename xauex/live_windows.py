from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


LONDON_TZ = ZoneInfo("Europe/London")
LONDON_TRADE_DAY = "Europe/London"


@dataclass(frozen=True)
class LiveWindow:
    slot: str
    window_label: str
    timezone: str
    signal_time_local: str
    confirm_time_local: str
    entry_start_local: str
    entry_end_local: str
    description: str
    holding_horizon: str
    dominant_drivers: tuple[str, ...]

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def _parse_hhmm(self, value: str) -> tuple[int, int]:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text), int(minute_text)

    def _local_dt(self, now_utc: datetime, hhmm: str) -> datetime:
        local_now = now_utc.astimezone(self.tzinfo)
        hour, minute = self._parse_hhmm(hhmm)
        return local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def signal_dt_utc(self, now_utc: datetime) -> datetime:
        return self._local_dt(now_utc, self.signal_time_local).astimezone(timezone.utc)

    def confirm_dt_utc(self, now_utc: datetime) -> datetime:
        return self._local_dt(now_utc, self.confirm_time_local).astimezone(timezone.utc)

    def entry_start_dt_utc(self, now_utc: datetime) -> datetime:
        return self._local_dt(now_utc, self.entry_start_local).astimezone(timezone.utc)

    def entry_end_dt_utc(self, now_utc: datetime) -> datetime:
        return self._local_dt(now_utc, self.entry_end_local).astimezone(timezone.utc)

    def summary_cutoff_dt_utc(self, now_utc: datetime) -> datetime:
        return self.entry_end_dt_utc(now_utc) + timedelta(minutes=1)

    def phase_due(self, now_utc: datetime, *, phase: str, tolerance_seconds: int = 90) -> bool:
        phase_name = str(phase or "").lower()
        if phase_name == "signal":
            target = self.signal_dt_utc(now_utc)
        elif phase_name == "confirm":
            target = self.confirm_dt_utc(now_utc)
        else:
            return False
        delta = abs((now_utc.astimezone(timezone.utc) - target).total_seconds())
        return delta <= max(1, int(tolerance_seconds))

    def detect_entry_slot(self, now_utc: datetime) -> bool:
        local_now = now_utc.astimezone(self.tzinfo)
        if local_now.weekday() >= 5:
            return False
        return self.entry_start_dt_utc(now_utc) <= now_utc < self.entry_end_dt_utc(now_utc)

    def detect_signal_context(self, now_utc: datetime) -> bool:
        local_now = now_utc.astimezone(self.tzinfo)
        if local_now.weekday() >= 5:
            return False
        start = self.signal_dt_utc(now_utc) - timedelta(minutes=5)
        end = self.entry_end_dt_utc(now_utc)
        return start <= now_utc <= end

    def session_profile(self) -> dict[str, object]:
        return {
            "slot": self.slot,
            "window_label": self.window_label,
            "timezone": self.timezone,
            "description": self.description,
            "holding_horizon": self.holding_horizon,
            "dominant_drivers": list(self.dominant_drivers),
            "signal_time_local": self.signal_time_local,
            "confirm_time_local": self.confirm_time_local,
            "entry_window_local": f"{self.entry_start_local}-{self.entry_end_local}",
        }


LIVE_WINDOWS: tuple[LiveWindow, ...] = (
    LiveWindow(
        slot="MORNING",
        window_label="morning",
        timezone="Europe/London",
        signal_time_local="07:55",
        confirm_time_local="07:59",
        entry_start_local="08:00",
        entry_end_local="08:10",
        description="London morning opening window for the first XAUEX session of the day.",
        holding_horizon="London morning impulse through the early cash-session flow.",
        dominant_drivers=(
            "overnight macro repricing",
            "European cash open flows",
            "USD and Treasury yield continuation",
        ),
    ),
    LiveWindow(
        slot="MIDDAY",
        window_label="midday",
        timezone="Europe/London",
        signal_time_local="11:25",
        confirm_time_local="11:29",
        entry_start_local="11:30",
        entry_end_local="11:40",
        description="Late-London continuation window after the morning move has revealed its character.",
        holding_horizon="Late London continuation into the pre-US handoff.",
        dominant_drivers=(
            "London trend continuation or fade",
            "Europe-to-US positioning adjustments",
            "pre-US data and yield drift",
        ),
    ),
    LiveWindow(
        slot="US_OPEN",
        window_label="us_open",
        timezone="America/New_York",
        # 08:30 ET is the US macro release minute (CPI/NFP/PPI). Signalling at
        # 08:25 decided blind right before a known catalyst, and entering at
        # 08:30 sat entirely inside the news gate's blackout of the release —
        # so US_OPEN could never trade a US-data day. Signal now reads the
        # post-release tape and entry starts after the first reprice.
        signal_time_local="08:40",
        confirm_time_local="08:44",
        entry_start_local="08:45",
        entry_end_local="08:55",
        description="US open impulse window around the New York cash and macro handoff.",
        holding_horizon="US cash-open burst and the first post-open reprice.",
        dominant_drivers=(
            "US rates repricing",
            "New York cash open flows",
            "USD impulse and cross-asset risk tone",
        ),
    ),
)

_WINDOWS_BY_SLOT = {window.slot: window for window in LIVE_WINDOWS}
_WINDOWS_BY_LABEL = {window.window_label: window for window in LIVE_WINDOWS}


def all_live_windows() -> tuple[LiveWindow, ...]:
    return LIVE_WINDOWS


def get_live_window(*, slot: str | None = None, window_label: str | None = None) -> LiveWindow | None:
    if slot:
        return _WINDOWS_BY_SLOT.get(str(slot).upper())
    if window_label:
        return _WINDOWS_BY_LABEL.get(str(window_label).lower())
    return None


def active_entry_slot(now_utc: datetime) -> str | None:
    for window in LIVE_WINDOWS:
        if window.detect_entry_slot(now_utc.astimezone(timezone.utc)):
            return window.slot
    return None


def detect_window_label(now_utc: datetime) -> str:
    now_utc = now_utc.astimezone(timezone.utc)
    for window in LIVE_WINDOWS:
        if window.detect_signal_context(now_utc):
            return window.window_label
    return "current"


def london_trade_day(now_utc: datetime) -> str:
    return now_utc.astimezone(LONDON_TZ).strftime("%Y-%m-%d")
