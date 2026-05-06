"""Persistent replay protection for signed manual commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from xauex.shared.safe_io import JsonLoadError, atomic_write_json, safe_load_json


class ReplayLedgerError(RuntimeError):
    """Raised when the replay ledger cannot be trusted."""


@dataclass(frozen=True)
class ReplayEntry:
    command_id: str
    seen_at_utc: str

    def to_dict(self) -> dict[str, str]:
        return {"command_id": self.command_id, "seen_at_utc": self.seen_at_utc}


class CommandReplayGuard:
    """Small persistent command-id ledger."""

    def __init__(self, ledger_path: str | Path, *, max_entries: int = 2048):
        self.ledger_path = Path(ledger_path)
        self.max_entries = max(1, int(max_entries))

    def _load_entries(self) -> list[ReplayEntry]:
        try:
            payload = safe_load_json(self.ledger_path, default={"commands": []})
        except JsonLoadError as exc:
            raise ReplayLedgerError(str(exc)) from exc
        rows = payload.get("commands", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ReplayLedgerError("manual command replay ledger has invalid shape")
        entries: list[ReplayEntry] = []
        for row in rows[-self.max_entries:]:
            if not isinstance(row, dict):
                continue
            command_id = str(row.get("command_id") or "").strip()
            seen_at = str(row.get("seen_at_utc") or "").strip()
            if command_id:
                entries.append(ReplayEntry(command_id, seen_at))
        return entries

    def seen_ids(self) -> set[str]:
        return {entry.command_id for entry in self._load_entries()}

    def contains(self, command_id: str | None) -> bool:
        if not command_id:
            return False
        return str(command_id) in self.seen_ids()

    def remember(self, command_id: str, *, now_utc: datetime | None = None) -> None:
        command_id = str(command_id or "").strip()
        if not command_id:
            raise ReplayLedgerError("cannot remember empty command id")
        now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
        entries = [entry for entry in self._load_entries() if entry.command_id != command_id]
        entries.append(ReplayEntry(command_id, now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")))
        entries = entries[-self.max_entries:]
        atomic_write_json(
            self.ledger_path,
            {"schema_version": 1, "commands": [entry.to_dict() for entry in entries]},
            mode=0o600,
            sort_keys=True,
        )

    def remember_many(self, command_ids: Iterable[str]) -> None:
        for command_id in command_ids:
            self.remember(command_id)
