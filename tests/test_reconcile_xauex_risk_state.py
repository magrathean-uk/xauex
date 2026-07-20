from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path("ops/reconcile_xauex_risk_state.py")
SPEC = importlib.util.spec_from_file_location("reconcile_xauex_risk_state", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _monday_state() -> dict:
    return {
        "closed_trades_today": [
            {
                "position_id": "654382289",
                "owner": "xauex",
                "pnl": -16.64,
                "close_time_utc": "2026-07-20T08:16:15Z",
            },
            {
                "position_id": "manual-1",
                "owner": "manual",
                "pnl": 40.00,
                "close_time_utc": "2026-07-20T09:00:00Z",
            },
        ],
    }


def _broken_risk() -> dict:
    return {
        "daily_pnl": 0.0,
        "weekly_pnl": 0.0,
        "consecutive_losses_today": 0,
        "losses_date_utc": "2026-07-20",
        "day_start_balance": 2947.75,
        "day_start_date_utc": "2026-07-20",
        "week_start_balance": 2931.77,
        "week_start_date_utc": "2026-07-20",
        "daily_halted": False,
        "weekly_halted": False,
    }


def test_plan_repair_uses_monday_baseline_and_closed_xauex_trades():
    plan = MODULE.plan_repair(
        state_payload=_monday_state(),
        risk_payload=_broken_risk(),
        now_utc=datetime(2026, 7, 20, 13, tzinfo=timezone.utc),
    )

    assert plan["risk"]["daily_pnl"] == -16.64
    assert plan["risk"]["weekly_pnl"] == -16.64
    assert plan["risk"]["consecutive_losses_today"] == 1
    assert plan["risk"]["week_start_balance"] == 2947.75
    assert plan["risk"]["week_start_date_utc"] == "2026-07-20"
    assert plan["trade_count"] == 1


def test_plan_repair_refuses_non_monday_or_missing_day_baseline():
    with pytest.raises(ValueError, match="Monday"):
        MODULE.plan_repair(
            state_payload=_monday_state(),
            risk_payload=_broken_risk(),
            now_utc=datetime(2026, 7, 21, 13, tzinfo=timezone.utc),
        )

    broken = _broken_risk()
    broken["day_start_date_utc"] = "2026-07-19"
    with pytest.raises(ValueError, match="day baseline"):
        MODULE.plan_repair(
            state_payload=_monday_state(),
            risk_payload=broken,
            now_utc=datetime(2026, 7, 20, 13, tzinfo=timezone.utc),
        )


def test_risk_repair_tool_is_installed_with_other_xauex_operations():
    installer = Path("ops/install_systemd.sh").read_text(encoding="utf-8")

    assert "reconcile_xauex_risk_state.py" in installer
    assert "xauex-reconcile-risk-state" in installer


def test_apply_repair_preserves_existing_risk_state_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    risk_path = tmp_path / "risk_state.json"
    risk_path.write_text("{}\n", encoding="utf-8")
    ownership_calls: list[tuple[int, int]] = []
    original_fchown = MODULE.os.fchown

    def record_fchown(fd: int, uid: int, gid: int) -> None:
        ownership_calls.append((uid, gid))
        original_fchown(fd, uid, gid)

    monkeypatch.setattr(MODULE.os, "fchown", record_fchown)
    plan = {
        "risk": _broken_risk(),
    }

    MODULE.apply_repair(
        risk_path=risk_path,
        plan=plan,
        now_utc=datetime(2026, 7, 20, 13, tzinfo=timezone.utc),
    )

    stat_result = risk_path.stat()
    assert ownership_calls == [(stat_result.st_uid, stat_result.st_gid)]
    assert (stat_result.st_uid, stat_result.st_gid) == (os.getuid(), os.getgid())
