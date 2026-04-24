from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from bot.risk.gates import RiskState


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

XAUEX_ROOT = REPO_ROOT / "xauex"
if str(XAUEX_ROOT) not in sys.path:
    sys.path.insert(0, str(XAUEX_ROOT))

SPEC = importlib.util.spec_from_file_location("xauex_main", XAUEX_ROOT / "main.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _build_orchestrator() -> object:
    orchestrator = MODULE.BotOrchestrator.__new__(MODULE.BotOrchestrator)
    orchestrator.config = SimpleNamespace(
        xauex_entry_timezone="Europe/London",
        xauex_entry_start_london="08:00",
        xauex_entry_end_london="08:05",
        xauex_entry_second_start_london="11:30",
        xauex_entry_second_end_london="11:35",
        xauex_max_trades_per_day=3,
    )
    orchestrator.risk_state = RiskState()
    orchestrator.risk_state.xauex_signal_runs_london = []
    orchestrator._recent_h1_closes = [2350.0] * 20
    orchestrator._trade_entries_on_chart = []
    orchestrator._last_xauex_signal_id_by_slot = {}
    orchestrator._last_xauex_signal_seen_utc = {}
    orchestrator._xauex_close_requested = {}
    return orchestrator


def _dt(iso_london: str) -> datetime:
    return datetime.fromisoformat(f"{iso_london}").replace(tzinfo=ZoneInfo("Europe/London"))


def _read_ops_file(name: str) -> str:
    return (REPO_ROOT / "ops" / name).read_text(encoding="utf-8")


def test_entry_slot_selection_includes_morning_midday_and_us_open_windows():
    orch = _build_orchestrator()

    assert orch._xauex_entry_slot(_dt("2026-04-07T07:59")) is None
    assert orch._xauex_entry_slot(_dt("2026-04-07T08:02")) == "MORNING"
    assert orch._xauex_entry_slot(_dt("2026-04-07T08:06")) is None

    assert orch._xauex_entry_slot(_dt("2026-04-07T11:27")) is None
    assert orch._xauex_entry_slot(_dt("2026-04-07T11:32")) == "MIDDAY"
    assert orch._xauex_entry_slot(_dt("2026-04-07T11:40")) is None

    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 27, tzinfo=timezone.utc)) is None
    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 32, tzinfo=timezone.utc)) == "US_OPEN"
    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 40, tzinfo=timezone.utc)) is None


def test_us_open_slot_handles_new_york_dst_without_breaking_london_day_count():
    orch = _build_orchestrator()
    now = datetime(2026, 11, 3, 13, 32, tzinfo=timezone.utc)

    assert orch._xauex_entry_slot(now) == "US_OPEN"
    assert orch._today_london(now) == "2026-11-03"


def test_weekends_are_not_tradable():
    orch = _build_orchestrator()
    assert orch._xauex_entry_slot(_dt("2026-04-04T08:02")) is None


def test_slot_usage_is_tracked_and_persists_until_next_slot():
    orch = _build_orchestrator()
    now = _dt("2026-04-07T10:20")

    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is False
    orch._record_xauex_signal_run(
        slot="MORNING",
        signal_id="run-1",
        action="HOLD",
        signal_time=now,
    )
    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is True
    assert orch._has_run_slot_been_used_today("MIDDAY", now_utc=now) is False


def test_second_slot_is_still_available_if_morning_slot_was_used():
    orch = _build_orchestrator()
    now = _dt("2026-04-07T08:02")
    orch._record_xauex_signal_run(
        slot="MORNING",
        signal_id="run-1",
        action="HOLD",
        signal_time=now,
    )

    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is True
    assert orch._has_run_slot_been_used_today("MIDDAY", now_utc=now) is False
    orch._record_xauex_signal_run(
        slot="MIDDAY",
        signal_id="run-2",
        action="BUY",
        signal_time=_dt("2026-04-07T11:31"),
    )
    assert orch._has_run_slot_been_used_today("MIDDAY", now_utc=now) is True
    assert len(orch.risk_state.xauex_signal_runs_london) == 2


def test_third_slot_is_still_available_if_morning_and_midday_were_used():
    orch = _build_orchestrator()
    now = _dt("2026-04-07T11:32")

    orch._record_xauex_signal_run(
        slot="MORNING",
        signal_id="run-1",
        action="HOLD",
        signal_time=_dt("2026-04-07T08:02"),
    )
    orch._record_xauex_signal_run(
        slot="MIDDAY",
        signal_id="run-2",
        action="HOLD",
        signal_time=now,
    )

    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is True
    assert orch._has_run_slot_been_used_today("MIDDAY", now_utc=now) is True
    assert orch._has_run_slot_been_used_today("US_OPEN", now_utc=now) is False


def test_non_terminal_reasons_do_not_block_slot():
    orch = _build_orchestrator()
    now = datetime(2026, 4, 7, 8, 2, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)

    orch._mark_slot_used(
        slot="MORNING",
        signal_id="signal-1",
        reason="NO_QUOTE",
        signal_time=now,
        terminal=False,
    )

    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is False

    orch._mark_slot_used(
        slot="MORNING",
        signal_id="signal-2",
        reason="HOLD",
        signal_time=now,
    )
    assert orch._has_run_slot_been_used_today("MORNING", now_utc=now) is True


def test_stale_previous_day_signal_does_not_consume_morning_slot():
    orch = _build_orchestrator()
    now = datetime(2026, 4, 8, 8, 0, 2, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
    stale_signal_time = datetime(2026, 4, 7, 15, 0, 40, tzinfo=timezone.utc)

    assert orch._stale_signal_should_consume_slot(
        slot="MORNING",
        signal_time=stale_signal_time,
        now_utc=now,
    ) is False


def test_stale_morning_signal_does_not_consume_midday_slot():
    orch = _build_orchestrator()
    now = datetime(2026, 4, 8, 11, 30, 3, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
    stale_signal_time = datetime(2026, 4, 8, 7, 0, 6, tzinfo=timezone.utc)

    assert orch._stale_signal_should_consume_slot(
        slot="MIDDAY",
        signal_time=stale_signal_time,
        now_utc=now,
    ) is False


def test_stale_signal_created_inside_slot_still_consumes_slot():
    orch = _build_orchestrator()
    now = datetime(2026, 4, 8, 8, 20, 0, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)
    stale_signal_time = datetime(2026, 4, 8, 8, 1, 0, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)

    assert orch._stale_signal_should_consume_slot(
        slot="MORNING",
        signal_time=stale_signal_time,
        now_utc=now,
    ) is True


def test_retry_debounces_duplicate_signal_within_backoff():
    orch = _build_orchestrator()
    base = datetime(2026, 4, 7, 8, 2, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)

    orch._refresh_signal_window_tracking(slot="MORNING", signal_id="signal-1", signal_time=base)
    assert orch._should_retry_signal_in_slot("MORNING", "signal-1", base) is False
    assert orch._should_retry_signal_in_slot(
        "MORNING",
        "signal-1",
        base + timedelta(seconds=30),
    ) is False
    assert orch._should_retry_signal_in_slot(
        "MORNING",
        "signal-1",
        base + timedelta(seconds=90),
    ) is True


def test_stale_close_requests_expire_after_ttl():
    orch = _build_orchestrator()
    now = datetime(2026, 4, 7, 8, 2, tzinfo=timezone.utc)
    orch._xauex_close_requested["pos-1"] = now - timedelta(seconds=200)
    orch._xauex_close_requested["pos-2"] = now

    orch._clear_stale_close_requests(open_position_ids={"pos-1", "pos-2"}, now_utc=now + timedelta(seconds=1))

    assert "pos-1" not in orch._xauex_close_requested
    assert orch._xauex_close_requested["pos-2"] >= now


def test_trade_entries_on_chart_are_capped_to_recent_window():
    orch = _build_orchestrator()

    for idx in range(25):
        orch._append_trade_entry_on_chart(
            None,
            "LONG" if idx % 2 == 0 else "SHORT",
            2350.0 + idx,
            "xauex",
        )

    assert len(orch._trade_entries_on_chart) == len(orch._recent_h1_closes)


def test_live_timers_catch_up_after_restarts_and_run_after_force_flat():
    start_timer = _read_ops_file("xauex-start.timer")
    signal_template = _read_ops_file("xauex-window-signal@.timer")
    confirm_template = _read_ops_file("xauex-window-confirm@.timer")
    stop_timer = _read_ops_file("xauex-stop.timer")
    journal_timer = _read_ops_file("xauex-trade-journal.timer")
    review_timer = _read_ops_file("xauex-weekly-review.timer")
    install_script = _read_ops_file("install_systemd.sh")

    assert "Persistent=true" in start_timer
    assert "OnCalendar=Mon-Fri *-*-* 07:25:00 Europe/London" in start_timer

    assert signal_template.count("OnCalendar=") == 1
    assert "OnCalendar=__ON_CALENDAR__" in signal_template
    assert "Unit=xauex-window-signal@__WINDOW_LABEL__.service" in signal_template

    assert confirm_template.count("OnCalendar=") == 1
    assert "OnCalendar=__ON_CALENDAR__" in confirm_template
    assert "Unit=xauex-window-confirm@__WINDOW_LABEL__.service" in confirm_template

    assert "xauex-window-signal@morning.timer" in install_script
    assert "xauex-window-signal@midday.timer" in install_script
    assert "xauex-window-signal@us_open.timer" in install_script
    assert "xauex-window-confirm@morning.timer" in install_script
    assert "xauex-window-confirm@midday.timer" in install_script
    assert "xauex-window-confirm@us_open.timer" in install_script
    assert "xauex.live_windows" in install_script

    assert "Persistent=true" in stop_timer
    assert "OnCalendar=Fri *-*-* 15:06:00 Europe/London" in stop_timer

    assert "Persistent=true" in journal_timer
    assert "OnCalendar=Mon-Fri *-*-* 15:07:00 Europe/London" in journal_timer

    assert "Persistent=true" in review_timer
    assert "OnCalendar=Fri *-*-* 15:15:00 Europe/London" in review_timer


def test_xauex_web_service_uses_repo_placeholders_instead_of_local_user():
    service = _read_ops_file("xauex-web.service")
    script = _read_ops_file("run_xauex_web.sh")

    assert "User=__RUN_USER__" in service
    assert "EnvironmentFile=__REPO_ROOT__/.env" in service
    assert "EnvironmentFile=__REPO_ROOT__/xauex/.env" in service
    assert "User=bolyki" not in service

    assert 'ORACLE_DASHBOARD_HOST="${ORACLE_DASHBOARD_HOST:-127.0.0.1}"' in script
    assert 'ORACLE_DASHBOARD_PORT="${ORACLE_DASHBOARD_PORT:-8089}"' in script


def test_signal_service_runs_without_legacy_backend_requirement():
    service = _read_ops_file("xauex-signal.service")

    assert "After=network-online.target xauex.service" in service
    assert "Requires=xauex.service" in service


def test_logrotate_policy_stays_root_for_systemd_append_logs():
    policy = _read_ops_file("logrotate-xauex.conf")

    assert "/var/log/xauex/*.log {" in policy
    assert "__REPO_ROOT__/logs/*.log {" in policy
    assert "copytruncate" in policy
    assert policy.count("su __RUN_USER__ __RUN_USER__") == 1
    assert policy.index("/var/log/xauex/*.log {") < policy.index("su __RUN_USER__ __RUN_USER__")
    assert policy.index("__REPO_ROOT__/logs/*.log {") > policy.index("su __RUN_USER__ __RUN_USER__")


def test_install_systemd_only_restarts_xauex_when_runtime_changed():
    script = _read_ops_file("install_systemd.sh")

    assert "XAUEX_RUNTIME_CHANGED=0" in script
    assert 'if [[ "$XAUEX_RUNTIME_CHANGED" -eq 1 ]]; then' in script
    assert "systemctl restart xauex.service" in script
