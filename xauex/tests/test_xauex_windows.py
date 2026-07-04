from __future__ import annotations

import pytest
import importlib.util
import json
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
        xauex_event_journal_path="",
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
    assert orch._xauex_entry_slot(_dt("2026-04-07T08:09")) == "MORNING"
    assert orch._xauex_entry_slot(_dt("2026-04-07T08:11")) is None

    assert orch._xauex_entry_slot(_dt("2026-04-07T11:27")) is None
    assert orch._xauex_entry_slot(_dt("2026-04-07T11:32")) == "MIDDAY"
    assert orch._xauex_entry_slot(_dt("2026-04-07T11:40")) is None

    # US_OPEN entry is 08:45-08:55 New York — after the 08:30 ET data minute.
    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 32, tzinfo=timezone.utc)) is None
    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 47, tzinfo=timezone.utc)) == "US_OPEN"
    assert orch._xauex_entry_slot(datetime(2026, 4, 7, 12, 56, tzinfo=timezone.utc)) is None


def test_us_open_slot_handles_new_york_dst_without_breaking_london_day_count():
    orch = _build_orchestrator()
    now = datetime(2026, 11, 3, 13, 47, tzinfo=timezone.utc)

    assert orch._xauex_entry_slot(now) == "US_OPEN"
    assert orch._today_london(now) == "2026-11-03"


def test_weekends_are_not_tradable():
    orch = _build_orchestrator()
    assert orch._xauex_entry_slot(_dt("2026-04-04T08:02")) is None


def test_dashboard_refresh_does_not_retimestamp_cached_quote_without_new_tick():
    orch = _build_orchestrator()
    orch.api_client = SimpleNamespace(get_current_quote=lambda: (4707.26, 4707.66))
    orch._latest_quote = {
        "bid": 4707.26,
        "ask": 4707.66,
        "mid": 4707.46,
        "updated_at_utc": "2026-04-24T20:56:58Z",
    }

    orch._refresh_latest_quote_snapshot()

    assert orch._latest_quote == {
        "bid": 4707.26,
        "ask": 4707.66,
        "mid": 4707.46,
        "updated_at_utc": "2026-04-24T20:56:58Z",
    }


def test_tick_refresh_uses_broker_tick_timestamp_for_quote_freshness():
    orch = _build_orchestrator()
    orch.api_client = SimpleNamespace(get_current_quote=lambda: (4707.26, 4707.66))
    orch._latest_quote = {}

    orch._refresh_latest_quote_snapshot(
        mid=4707.46,
        timestamp=datetime(2026, 4, 24, 20, 56, 58, tzinfo=timezone.utc),
    )

    assert orch._latest_quote == {
        "bid": 4707.26,
        "ask": 4707.66,
        "mid": 4707.46,
        "updated_at_utc": "2026-04-24T20:56:58Z",
    }


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


def test_terminal_directional_skip_records_blocked_trade_candidate(tmp_path):
    orch = _build_orchestrator()
    journal_path = tmp_path / "events.jsonl"
    orch.config.xauex_event_journal_path = str(journal_path)
    now = datetime(2026, 4, 7, 8, 2, tzinfo=ZoneInfo("Europe/London")).astimezone(timezone.utc)

    orch._mark_slot_used(
        slot="MORNING",
        signal_id="signal-1",
        reason="ASSURANCE_RISK_BELOW_MIN_LOT",
        signal_time=now,
        signal_action="SELL",
        signal_confidence=0.60,
        window_label="morning",
        confirm_status="CONFIRMED",
        confirm_reason="CONFIRMED",
        terminal=True,
        blocked_trade={
            "entry_price": 4501.98,
            "stop_loss": 4519.44,
            "take_profit": 4475.79,
            "minimum_executable_risk": 17.46,
        },
    )

    events = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    blocked = [event for event in events if event["event_type"] == "blocked_trade_candidate"]

    assert len(blocked) == 1
    assert blocked[0]["correlation_id"] == "signal-1"
    assert blocked[0]["payload"]["reason"] == "ASSURANCE_RISK_BELOW_MIN_LOT"
    assert blocked[0]["payload"]["signal_action"] == "SELL"
    assert blocked[0]["payload"]["blocked_trade"]["entry_price"] == 4501.98


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
    start_service = _read_ops_file("xauex-start.service")
    signal_template = _read_ops_file("xauex-window-signal@.timer")
    confirm_template = _read_ops_file("xauex-window-confirm@.timer")
    stop_timer = _read_ops_file("xauex-stop.timer")
    journal_timer = _read_ops_file("xauex-trade-journal.timer")
    review_timer = _read_ops_file("xauex-weekly-review.timer")
    install_script = _read_ops_file("install_systemd.sh")

    assert "Persistent=true" in start_timer
    assert "OnCalendar=Mon-Fri *-*-* 07:25:00 Europe/London" in start_timer
    assert "Unit=xauex-start.service" in start_timer
    assert "ExecStart=/bin/systemctl start xauex.service" in start_service

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
    assert "OnCalendar=Mon-Fri *-*-* 15:45:00 Europe/London" in journal_timer
    assert "OnCalendar=Mon-Fri *-*-* 16:30:00 Europe/London" in journal_timer
    assert "OnCalendar=Mon-Fri *-*-* 19:00:00 Europe/London" in journal_timer

    assert "Persistent=true" in review_timer
    assert "OnCalendar=Fri *-*-* 19:15:00 Europe/London" in review_timer


@pytest.mark.asyncio
async def test_xauex_position_monitor_recovers_after_bad_position_payload(monkeypatch):
    orch = _build_orchestrator()
    orch.running = True
    orch.config.xauex_force_flat_london = "00:00"
    orch.config.xauex_cash_take_profit_gbp = 50.0
    orch.config.xauex_session_protect_r = 0.85
    orch.config.xauex_session_trail_r = 1.35
    orch.config.xauex_session_protect_lock_r = 0.30
    orch.symbol_spec = SimpleNamespace(lot_size=100.0, digits=2)
    orch.risk_gates = SimpleNamespace(set_open_position_count=lambda *_args, **_kwargs: None)
    orch._journal_event = lambda *_args, **_kwargs: None
    orch._xauex_force_flat_due = lambda _now: True

    bad_position = SimpleNamespace(
        position_id="bad-pos",
        current_price="not-a-price",
        unrealised_pnl=0.0,
        volume=0.01,
        owner="xauex",
    )
    good_position = SimpleNamespace(
        position_id="good-pos",
        current_price=4050.0,
        unrealised_pnl=-1.0,
        volume=0.01,
        owner="xauex",
    )
    tracked_by_id = {
        "bad-pos": SimpleNamespace(
            position_id="bad-pos",
            direction="SHORT",
            entry_price=4028.0,
            stop_loss=4053.0,
            take_profit=3990.0,
            lot_size=0.01,
            owner="xauex",
            metadata={"session": {"phase": "OBSERVE", "direction": "SHORT", "entry_price": 4028.0, "initial_risk_distance": 25.0}},
        ),
        "good-pos": SimpleNamespace(
            position_id="good-pos",
            direction="SHORT",
            entry_price=4028.0,
            stop_loss=4053.0,
            take_profit=3990.0,
            lot_size=0.01,
            owner="xauex",
            metadata={"session": {"phase": "OBSERVE", "direction": "SHORT", "entry_price": 4028.0, "initial_risk_distance": 25.0}},
        ),
    }

    class FakePositionManager:
        def get_position(self, position_id):
            return tracked_by_id.get(position_id)

        def get_open_positions(self):
            return list(tracked_by_id.values())

    class FakeExecutor:
        position_manager = FakePositionManager()

        @staticmethod
        def validate_sl_modification(*_args, **_kwargs):
            return True

        @staticmethod
        def validate_sl_against_market(*_args, **_kwargs):
            return True

    class FakeApi:
        def __init__(self):
            self.responses = [[bad_position], [good_position], []]
            self.closed = []

        async def get_open_positions(self):
            return self.responses.pop(0) if self.responses else []

        async def close_position(self, *, position_id, volume_lots):
            self.closed.append((position_id, volume_lots))
            return True

    fake_api = FakeApi()
    orch.api_client = fake_api
    orch.executor = FakeExecutor()

    sleep_calls = 0

    async def fast_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 3:
            orch.running = False

    monkeypatch.setattr(MODULE.asyncio, "sleep", fast_sleep)

    await orch._monitor_xauex_positions()

    assert fake_api.closed == [("good-pos", 0.01)]


def test_window_runner_scripts_are_executable_for_systemd_execstart():
    signal_runner = REPO_ROOT / "ops" / "run_xauex_signal.sh"
    confirm_runner = REPO_ROOT / "ops" / "run_xauex_confirm.sh"

    assert signal_runner.stat().st_mode & 0o111
    assert confirm_runner.stat().st_mode & 0o111


def test_window_confirm_service_loads_runtime_config_env_file():
    service = _read_ops_file("xauex-window-confirm@.service")

    assert "EnvironmentFile=__REPO_ROOT__/.env" in service
    assert "EnvironmentFile=__REPO_ROOT__/xauex/.env" in service


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


def test_install_systemd_keeps_caddyfile_readable_for_caddy_user():
    script = _read_ops_file("install_systemd.sh")

    assert 'install -m 644 "$tmp" /etc/caddy/Caddyfile' in script
    assert 'mv "$tmp" /etc/caddy/Caddyfile' not in script
