"""Gate-economics replay: price what each blocking gate cost (or saved).

For every window where a directional signal existed but no trade happened,
replay the bracket trade it implied (entry at window open, SL/TP from the
signal, force-flat at 15:00 London) against broker M1 bars, first-touch.
Aggregated per block-reason into /var/lib/xauex/gate_economics.json so gate
thresholds can be loosened (or vindicated) with measured expectancy instead
of guesswork.

Invoked from the shadow-evaluate run (xauex-shadow-evaluate.timer fires at
10:15 London, 13:45 London, 10:45 New York — the last lands after force-flat
so morning windows replay same-day). Replays are idempotent per
(date, window) via the runs ledger JSONL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from xauex.analyst.decision_ledger import build_decision_ledger
from xauex.live_windows import get_live_window
from xauex.shared.safe_io import atomic_write_json

logger = logging.getLogger(__name__)

LONDON_TZ = ZoneInfo("Europe/London")

RUNS_PATH = os.getenv("XAUEX_GATE_ECONOMICS_RUNS_PATH", "/var/lib/xauex/gate_economics_runs.jsonl")
OUTPUT_PATH = os.getenv("XAUEX_GATE_ECONOMICS_PATH", "/var/lib/xauex/gate_economics.json")

DEFAULT_SL_USD = 12.0
DEFAULT_TP_USD = 24.0
AGGREGATE_WINDOW_DAYS = 90
# Replayable outcomes: a direction existed but no order was placed.
_REPLAYABLE_OUTCOMES = {"BLOCKED", "GATE_HOLD", "NOT_CONSUMED"}


def _force_flat_utc(date_london: str, *, hhmm: str = "15:00") -> Optional[datetime]:
    try:
        hour, minute = (int(part) for part in hhmm.split(":", 1))
        day = datetime.strptime(date_london, "%Y-%m-%d")
    except ValueError:
        return None
    return day.replace(hour=hour, minute=minute, tzinfo=LONDON_TZ).astimezone(timezone.utc)


def _entry_start_utc(date_london: str, window_label: str) -> Optional[datetime]:
    window = get_live_window(window_label=window_label)
    if window is None:
        return None
    try:
        day = datetime.strptime(date_london, "%Y-%m-%d")
    except ValueError:
        return None
    local_probe = day.replace(hour=12, minute=0, tzinfo=LONDON_TZ).astimezone(timezone.utc)
    return window.entry_start_dt_utc(local_probe)


def collect_replay_candidates(
    *,
    days: int = 7,
    now_utc: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Blocked-but-directional windows from the ledger, newest first."""
    ledger = build_decision_ledger(days=days, now_utc=now_utc)
    candidates: list[dict[str, Any]] = []
    for day in ledger.get("days", []):
        date_london = str(day.get("date_london") or "")
        for window_label, record in (day.get("windows") or {}).items():
            outcome = str(record.get("outcome") or "")
            if outcome not in _REPLAYABLE_OUTCOMES:
                continue
            parser = record.get("parser") or {}
            manufactured = parser.get("manufactured_hold") or {}
            direction = str(
                record.get("signal_action")
                or manufactured.get("original_action")
                or parser.get("action")
                or ""
            ).upper()
            if direction not in ("BUY", "SELL"):
                continue
            sl = float(parser.get("stop_loss_distance") or 0.0) or DEFAULT_SL_USD
            tp = float(parser.get("take_profit_distance") or 0.0) or DEFAULT_TP_USD
            candidates.append(
                {
                    "date_london": date_london,
                    "window_label": window_label,
                    "reason": str(record.get("reason") or "UNKNOWN"),
                    "direction": direction,
                    "confidence": record.get("signal_confidence") or parser.get("confidence"),
                    "stop_loss_distance": round(sl, 2),
                    "take_profit_distance": round(tp, 2),
                }
            )
    return candidates


def replay_bracket(
    *,
    bars: list[dict[str, Any]],
    entry_start_utc: datetime,
    force_flat_utc: datetime,
    direction: str,
    stop_loss_distance: float,
    take_profit_distance: float,
) -> Optional[dict[str, Any]]:
    """First-touch bracket walk over M1 bars. Conservative: SL wins ambiguity."""
    if stop_loss_distance <= 0 or take_profit_distance <= 0:
        return None
    session = [
        bar
        for bar in bars
        if entry_start_utc <= bar["open_time"].astimezone(timezone.utc) < force_flat_utc
    ]
    if not session:
        return None
    entry_price = float(session[0]["open"])
    sign = 1.0 if direction == "BUY" else -1.0
    stop_price = entry_price - sign * stop_loss_distance
    target_price = entry_price + sign * take_profit_distance
    exit_price = float(session[-1]["close"])
    exit_reason = "FORCE_FLAT"
    for bar in session:
        low = float(bar["low"])
        high = float(bar["high"])
        hit_stop = low <= stop_price if direction == "BUY" else high >= stop_price
        hit_target = high >= target_price if direction == "BUY" else low <= target_price
        if hit_stop:
            # Both touched in one bar: intrabar order is unknown — book the loss.
            exit_price, exit_reason = stop_price, "SL" if not hit_target else "SL_AMBIGUOUS"
            break
        if hit_target:
            exit_price, exit_reason = target_price, "TP"
            break
    result_r = (exit_price - entry_price) * sign / stop_loss_distance
    return {
        "entry_price": round(entry_price, 2),
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "result_r": round(result_r, 3),
        # 0.01 lot XAUUSD = 1 oz, so USD at min lot = R x stop distance.
        "result_usd_min_lot": round(result_r * stop_loss_distance, 2),
        "bars_used": len(session),
    }


def _load_runs(runs_path: str = RUNS_PATH) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    try:
        lines = Path(runs_path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return runs
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            runs.append(row)
    return runs


def _append_run(row: dict[str, Any], runs_path: str = RUNS_PATH) -> None:
    target = Path(runs_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def aggregate_gate_economics(
    runs: list[dict[str, Any]],
    *,
    window_days: int = AGGREGATE_WINDOW_DAYS,
    now_utc: Optional[datetime] = None,
) -> dict[str, Any]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = (now - timedelta(days=window_days)).astimezone(LONDON_TZ).strftime("%Y-%m-%d")
    by_reason: dict[str, dict[str, Any]] = {}
    for run in runs:
        if str(run.get("date_london") or "") < cutoff:
            continue
        result = run.get("result") or {}
        reason = str(run.get("reason") or "UNKNOWN")
        stats = by_reason.setdefault(
            reason,
            {"n": 0, "tp": 0, "sl": 0, "force_flat": 0, "sum_r": 0.0, "sum_usd_min_lot": 0.0},
        )
        stats["n"] += 1
        exit_reason = str(result.get("exit_reason") or "")
        if exit_reason == "TP":
            stats["tp"] += 1
        elif exit_reason.startswith("SL"):
            stats["sl"] += 1
        else:
            stats["force_flat"] += 1
        stats["sum_r"] += float(result.get("result_r") or 0.0)
        stats["sum_usd_min_lot"] += float(result.get("result_usd_min_lot") or 0.0)
    for stats in by_reason.values():
        n = max(1, int(stats["n"]))
        stats["expectancy_r"] = round(stats.pop("sum_r") / n, 3)
        stats["missed_usd_min_lot"] = round(stats.pop("sum_usd_min_lot"), 2)
    ordered = dict(
        sorted(by_reason.items(), key=lambda item: item[1]["n"], reverse=True)
    )
    return {
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": window_days,
        "replayed": sum(stats["n"] for stats in ordered.values()),
        "by_reason": ordered,
    }


async def replay_pending_gate_economics(
    *,
    days: int = 7,
    runs_path: str = RUNS_PATH,
    output_path: str = OUTPUT_PATH,
    now_utc: Optional[datetime] = None,
) -> int:
    """Replay blocked windows that have no run yet; rewrite the aggregate."""
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates = collect_replay_candidates(days=days, now_utc=now)
    existing = {(str(run.get("date_london")), str(run.get("window_label"))) for run in _load_runs(runs_path)}
    pending: list[dict[str, Any]] = []
    for candidate in candidates:
        key = (candidate["date_london"], candidate["window_label"])
        if key in existing:
            continue
        flat = _force_flat_utc(candidate["date_london"])
        entry = _entry_start_utc(candidate["date_london"], candidate["window_label"])
        if flat is None or entry is None or now < flat:
            continue  # session still open — replay after force-flat
        candidate["_entry_utc"] = entry
        candidate["_flat_utc"] = flat
        pending.append(candidate)
    replayed = 0
    if pending:
        from xauex.config import load_config
        from xauex.bot.api.client import ApiClient

        oldest_entry = min(candidate["_entry_utc"] for candidate in pending)
        minutes_needed = int((now - oldest_entry).total_seconds() // 60) + 60
        bar_count = max(120, min(4000, minutes_needed))
        config = load_config()
        client = ApiClient(config)
        await client.connect()
        try:
            await client.get_symbol_spec("XAUUSD")
            bars = await client.get_trendbar("M1", bar_count)
        finally:
            await client.disconnect()
        for candidate in pending:
            result = replay_bracket(
                bars=bars,
                entry_start_utc=candidate.pop("_entry_utc"),
                force_flat_utc=candidate.pop("_flat_utc"),
                direction=candidate["direction"],
                stop_loss_distance=candidate["stop_loss_distance"],
                take_profit_distance=candidate["take_profit_distance"],
            )
            if result is None:
                logger.info(
                    "[GATE-ECON] No bars to replay %s/%s — skipping.",
                    candidate["date_london"],
                    candidate["window_label"],
                )
                continue
            row = {**candidate, "result": result, "replayed_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            _append_run(row, runs_path)
            replayed += 1
    aggregate = aggregate_gate_economics(_load_runs(runs_path), now_utc=now)
    atomic_write_json(output_path, aggregate)
    logger.info(
        "[GATE-ECON] Replayed %d window(s); aggregate covers %d run(s) across %d reason(s).",
        replayed,
        aggregate.get("replayed", 0),
        len(aggregate.get("by_reason", {})),
    )
    return replayed


def main() -> int:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(replay_pending_gate_economics())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
