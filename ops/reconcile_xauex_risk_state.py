#!/usr/bin/env python3
"""Repair the known XAUEX Monday risk-state split after a guarded dry run."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

def _as_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _closed_xauex_trades_for_day(state_payload: Mapping[str, Any], today_utc: str) -> list[dict[str, Any]]:
    raw_trades = state_payload.get("closed_trades_today")
    if not isinstance(raw_trades, list):
        return []
    trades: list[dict[str, Any]] = []
    for raw_trade in raw_trades:
        if not isinstance(raw_trade, dict):
            continue
        if str(raw_trade.get("owner") or "").lower() != "xauex":
            continue
        closed_at = _parse_timestamp(raw_trade.get("close_time_utc"))
        if closed_at is None or closed_at.strftime("%Y-%m-%d") != today_utc:
            continue
        trades.append(dict(raw_trade))
    return sorted(
        trades,
        key=lambda trade: _parse_timestamp(trade.get("close_time_utc")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def _pnl(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _consecutive_losses(trades: list[dict[str, Any]]) -> int:
    losses = 0
    for trade in trades:
        if _pnl(trade.get("pnl")) <= 0:
            losses += 1
        else:
            losses = 0
    return losses


def plan_repair(
    *,
    state_payload: Mapping[str, Any],
    risk_payload: Mapping[str, Any],
    now_utc: datetime,
) -> dict[str, Any]:
    """Return a Monday-only risk-state correction without writing any files."""
    now = now_utc.astimezone(timezone.utc)
    if now.weekday() != 0:
        raise ValueError("repair is allowed only on Monday UTC")
    today_utc = now.strftime("%Y-%m-%d")
    risk = _as_dict(risk_payload, label="risk payload")
    if str(risk.get("day_start_date_utc") or "") != today_utc:
        raise ValueError("risk day baseline does not match the current Monday UTC date")
    day_start_balance = _pnl(risk.get("day_start_balance"))
    if day_start_balance <= 0:
        raise ValueError("risk day baseline balance must be positive")

    trades = _closed_xauex_trades_for_day(state_payload, today_utc)
    gross_pnl = round(sum(_pnl(trade.get("pnl")) for trade in trades), 2)
    repaired = dict(risk)
    repaired.update(
        {
            "daily_pnl": gross_pnl,
            "weekly_pnl": gross_pnl,
            "consecutive_losses_today": _consecutive_losses(trades),
            "losses_date_utc": today_utc,
            "week_start_balance": day_start_balance,
            "week_start_date_utc": today_utc,
            "daily_halted": bool(risk.get("daily_halted", False)),
            "weekly_halted": bool(risk.get("weekly_halted", False)),
        }
    )
    return {
        "date_utc": today_utc,
        "trade_count": len(trades),
        "gross_pnl": gross_pnl,
        "risk": repaired,
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse {path}: {exc}") from exc
    return _as_dict(value, label=str(path))


def apply_repair(*, risk_path: Path, plan: Mapping[str, Any], now_utc: datetime) -> Path:
    """Back up and atomically write a previously validated repair plan."""
    if risk_path.is_symlink():
        raise ValueError(f"refusing to back up symlink risk state: {risk_path}")
    existing_stat = risk_path.stat()
    risk = _as_dict(plan.get("risk"), label="repair risk payload")
    timestamp = now_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = risk_path.with_name(f"{risk_path.name}.bak-{timestamp}")
    shutil.copy2(risk_path, backup_path)
    risk["_saved_at_utc"] = now_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _atomic_write_json(
        risk_path,
        risk,
        owner_uid=existing_stat.st_uid,
        owner_gid=existing_stat.st_gid,
    )
    return backup_path


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    owner_uid: int,
    owner_gid: int,
) -> None:
    """Write a private replacement file without relying on repo imports."""
    if path.is_symlink():
        raise ValueError(f"refusing to write symlink risk state: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, owner_uid, owner_gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.is_symlink():
            raise ValueError(f"refusing to replace symlink risk state: {path}")
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _summary(plan: Mapping[str, Any], *, applied: bool, backup_path: Path | None = None) -> dict[str, Any]:
    risk = _as_dict(plan.get("risk"), label="repair risk payload")
    return {
        "mode": "applied" if applied else "dry-run",
        "date_utc": plan.get("date_utc"),
        "closed_xauex_trade_count": plan.get("trade_count"),
        "gross_pnl": plan.get("gross_pnl"),
        "risk": {
            key: risk.get(key)
            for key in (
                "daily_pnl",
                "weekly_pnl",
                "consecutive_losses_today",
                "day_start_balance",
                "day_start_date_utc",
                "week_start_balance",
                "week_start_date_utc",
            )
        },
        "backup_path": str(backup_path) if backup_path is not None else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or apply a guarded XAUEX Monday risk-state repair.")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/xauex/state.json"))
    parser.add_argument("--risk", type=Path, default=Path("/var/lib/xauex/risk_state.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        state = _load_json_object(args.state)
        risk = _load_json_object(args.risk)
        plan = plan_repair(state_payload=state, risk_payload=risk, now_utc=now)
        backup_path = apply_repair(risk_path=args.risk, plan=plan, now_utc=now) if args.apply else None
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(_summary(plan, applied=args.apply, backup_path=backup_path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
