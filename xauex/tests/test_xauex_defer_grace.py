"""Deferred-signal grace: confirm may complete shortly after the entry window closes."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

XAUEX_ROOT = REPO_ROOT / "xauex"
if str(XAUEX_ROOT) not in sys.path:
    sys.path.insert(0, str(XAUEX_ROOT))

SPEC = importlib.util.spec_from_file_location("xauex_main_grace", XAUEX_ROOT / "main.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

LONDON = ZoneInfo("Europe/London")


def _orchestrator(grace_minutes: int = 5):
    orchestrator = MODULE.BotOrchestrator.__new__(MODULE.BotOrchestrator)
    orchestrator.config = SimpleNamespace(
        xauex_defer_grace_minutes=grace_minutes,
        xauex_signal_max_age_seconds=300,
    )
    return orchestrator


def _london(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=LONDON)


def _confirm_config(**overrides):
    defaults = {
        "xauex_signal_max_age_seconds": 300,
        "xauex_confirm_spread_max_dollars": 1.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _deferred_signal(timestamp: str) -> dict:
    return {
        "action": "BUY",
        "confidence": 0.66,
        "timestamp_utc": timestamp,
        "microstructure_deferred": True,
        "microstructure_defer_count": 1,
        "consensus_state": "aligned",
        "validator_status": "reviewed",
    }


def test_grace_slot_resolves_after_window_close_for_deferred_signal():
    orch = _orchestrator()
    sig = {"microstructure_deferred": True, "microstructure_defer_count": 1}
    # Wednesday 2026-06-10: morning entry window ends 08:10 London.
    assert orch._xauex_grace_entry_slot(_london("2026-06-10T08:12"), sig) == "MORNING"
    assert orch._xauex_grace_entry_slot(_london("2026-06-10T08:16"), sig) is None
    assert orch._xauex_grace_entry_slot(_london("2026-06-10T08:05"), sig) is None


def test_grace_slot_requires_deferred_marker():
    orch = _orchestrator()
    assert orch._xauex_grace_entry_slot(_london("2026-06-10T08:12"), {}) is None


def test_grace_slot_disabled_when_grace_is_zero():
    orch = _orchestrator(grace_minutes=0)
    sig = {"microstructure_deferred": True}
    assert orch._xauex_grace_entry_slot(_london("2026-06-10T08:12"), sig) is None


def test_grace_age_limit_extends_signal_budget():
    orch = _orchestrator()
    now = _london("2026-06-10T08:12")
    base = orch._xauex_signal_age_limit_seconds(slot="MORNING", grace_entry=False, now_utc=now)
    grace = orch._xauex_signal_age_limit_seconds(slot="MORNING", grace_entry=True, now_utc=now)
    assert base == 300
    # Signal 07:55 -> entry end 08:10 + 5 min grace = 1200s budget.
    assert grace == 1200


def test_confirm_uses_age_override_for_grace_retry():
    now = _london("2026-06-10T08:12").astimezone(timezone.utc)
    signal = _deferred_signal("2026-06-10T06:55:00Z")  # 07:55 London = 17 min old
    quote = {"bid": 4700.0, "ask": 4700.4}
    news_gate = {"clear": True}

    stale = MODULE.build_xauex_confirm_decision(
        signal=signal,
        now_utc=now,
        latest_quote=quote,
        news_gate=news_gate,
        trend_snapshot=None,
        shadow_signal=None,
        config=_confirm_config(),
    )
    assert stale["reason"] == "STALE_SIGNAL"

    confirmed = MODULE.build_xauex_confirm_decision(
        signal=signal,
        now_utc=now,
        latest_quote=quote,
        news_gate=news_gate,
        trend_snapshot=None,
        shadow_signal=None,
        config=_confirm_config(),
        signal_max_age_override_seconds=1200,
        grace_entry=True,
    )
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["reason"] == "CONFIRMED"


def test_grace_soft_confirm_reason_is_tagged():
    now = _london("2026-06-10T08:12").astimezone(timezone.utc)
    signal = _deferred_signal("2026-06-10T07:08:00Z")  # 08:08 London, 4 min old
    confirm = MODULE.build_xauex_confirm_decision(
        signal=signal,
        now_utc=now,
        latest_quote={"bid": 4700.0, "ask": 4700.4},
        news_gate={"clear": True},
        trend_snapshot={"alignment": "BEARISH"},
        shadow_signal=None,
        config=_confirm_config(),
        signal_max_age_override_seconds=1200,
        grace_entry=True,
    )
    assert confirm["status"] == "CONFIRMED"
    assert confirm["reason"] == "MICROSTRUCTURE_SOFT_CONFIRMED_GRACE"
    assert confirm["microstructure_policy"] == "soft_confirmed"


def test_grace_retry_still_vetoed_by_fresh_spread_check():
    now = _london("2026-06-10T08:12").astimezone(timezone.utc)
    signal = _deferred_signal("2026-06-10T06:55:00Z")
    confirm = MODULE.build_xauex_confirm_decision(
        signal=signal,
        now_utc=now,
        latest_quote={"bid": 4700.0, "ask": 4701.6},
        news_gate={"clear": True},
        trend_snapshot=None,
        shadow_signal=None,
        config=_confirm_config(),
        signal_max_age_override_seconds=1200,
        grace_entry=True,
    )
    assert confirm["reason"] == "SPREAD_TOO_WIDE"
