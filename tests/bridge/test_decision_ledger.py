import json
from datetime import datetime, timezone
from pathlib import Path

from xauex.analyst.decision_ledger import build_decision_ledger


_NOW = datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)


def _event(event_type: str, timestamp_utc: str, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "event_id": f"{event_type}-{timestamp_utc}",
        "correlation_id": "sig-1",
        "timestamp_utc": timestamp_utc,
        "source": "bot",
        "event_type": event_type,
        "payload": payload,
    }


def _write_journal(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def _write_archive(root: Path, *, timestamp: str, window_label: str, signal: dict) -> None:
    slug = timestamp.replace("-", "").replace(":", "")
    run_dir = root / f"{slug}_xauusd_baseline"
    run_dir.mkdir(parents=True)
    payload = {"timestamp_utc": timestamp, **signal}
    (run_dir / "signal.json").write_text(json.dumps(payload), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({"window_label": window_label}), encoding="utf-8")


def test_ledger_traded_and_blocked_windows(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    _write_journal(
        journal,
        [
            _event(
                "signal_decision",
                "2026-06-11T07:01:00Z",
                {
                    "slot": "MORNING",
                    "window_label": "morning",
                    "action": "BUY",
                    "confidence": 0.66,
                    "confirm_status": "CONFIRMED",
                    "confirm_reason": "CONFIRMED",
                },
            ),
            _event(
                "risk_result",
                "2026-06-11T07:02:00Z",
                {"slot": "MORNING", "window_label": "morning", "reason": "ORDER_PLACED", "action": "ORDER_PLACED", "terminal": True},
            ),
            _event(
                "signal_decision",
                "2026-06-11T10:31:00Z",
                {
                    "slot": "MIDDAY",
                    "window_label": "midday",
                    "action": "SELL",
                    "confidence": 0.62,
                    "confirm_status": "SKIP",
                    "confirm_reason": "SPREAD_TOO_WIDE",
                },
            ),
            _event(
                "blocked_trade_candidate",
                "2026-06-11T10:32:00Z",
                {
                    "slot": "MIDDAY",
                    "window_label": "midday",
                    "reason": "SPREAD_TOO_WIDE",
                    "signal_action": "SELL",
                    "signal_confidence": 0.62,
                },
            ),
        ],
    )
    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(tmp_path / "missing-runs"),
        days=3,
        now_utc=_NOW,
    )
    day = ledger["days"][0]
    assert day["date_london"] == "2026-06-11"
    assert day["windows"]["morning"]["outcome"] == "TRADED"
    midday = day["windows"]["midday"]
    assert midday["outcome"] == "BLOCKED"
    assert midday["reason"] == "SPREAD_TOO_WIDE"
    assert midday["signal_action"] == "SELL"
    assert any("SPREAD_TOO_WIDE" in step for step in midday["reason_chain"])
    assert day["windows"]["us_open"]["outcome"] == "NO_SIGNAL"
    assert ledger["totals"]["traded"] == 1
    assert ledger["totals"]["by_reason"]["SPREAD_TOO_WIDE"] == 1


def test_ledger_reports_executed_counter_signal_separately_from_source(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    _write_journal(
        journal,
        [
            _event(
                "signal_decision",
                "2026-06-11T07:01:00Z",
                {
                    "slot": "MORNING",
                    "window_label": "morning",
                    "action": "BUY",
                    "confidence": 0.53,
                    "confirm_status": "SKIP",
                    "confirm_reason": "MICROSTRUCTURE_CONFLICT",
                },
            ),
            _event(
                "counter_signal_candidate",
                "2026-06-11T07:01:01Z",
                {
                    "slot": "MORNING",
                    "window_label": "morning",
                    "source_action": "BUY",
                    "source_confidence": 0.53,
                    "source_confirm_reason": "MICROSTRUCTURE_CONFLICT",
                    "counter_action": "SELL",
                    "counter_confidence": 0.58,
                    "counter_confirm_reason": "COUNTER_SIGNAL_CONFIRMED",
                },
            ),
            _event(
                "risk_result",
                "2026-06-11T07:02:00Z",
                {
                    "slot": "MORNING",
                    "reason": "ORDER_PLACED",
                    "action": "ORDER_PLACED",
                    "signal_action": "SELL",
                    "confidence": 0.58,
                    "counter_signal": True,
                    "terminal": True,
                    "pattern_evidence": {"factor": "PATTERN_MISSING"},
                },
            ),
        ],
    )

    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(tmp_path / "missing-runs"),
        days=3,
        now_utc=_NOW,
    )

    morning = ledger["days"][0]["windows"]["morning"]
    assert morning["outcome"] == "TRADED"
    assert morning["signal_action"] == "SELL"
    assert morning["signal_confidence"] == 0.58
    assert morning["source_action"] == "BUY"
    assert morning["source_confidence"] == 0.53
    assert morning["executed_action"] == "SELL"
    assert morning["executed_confidence"] == 0.58
    assert morning["counter_signal"] is True
    assert morning["confirm_status"] == "CONFIRMED"
    assert morning["confirm_reason"] == "COUNTER_SIGNAL_CONFIRMED"
    assert morning["counter_confirm_reason"] == "COUNTER_SIGNAL_CONFIRMED"
    assert ledger["totals"]["pattern_coverage"] == {
        "evaluated": 1,
        "matches": 0,
        "missing": 1,
        "direction_mismatches": 0,
        "data_unavailable": 0,
        "match_rate": 0.0,
        "by_factor": {"PATTERN_MISSING": 1},
    }


def test_ledger_distinguishes_gate_manufactured_holds(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    _write_journal(
        journal,
        [
            _event(
                "risk_result",
                "2026-06-11T07:02:00Z",
                {"slot": "MORNING", "window_label": "morning", "reason": "HOLD", "action": "HOLD", "terminal": True},
            ),
        ],
    )
    runs_dir = tmp_path / "signal_runs"
    _write_archive(
        runs_dir,
        timestamp="2026-06-11T06:55:00Z",
        window_label="morning",
        signal={
            "action": "HOLD",
            "confidence": 0.0,
            "consensus_state": "blocked",
            "price_conflict_guard": {
                "policy": "blocked",
                "original_action": "BUY",
                "original_confidence": 0.64,
                "price_bias": "SELL",
            },
            "stop_loss_distance": 12.0,
            "take_profit_distance": 24.0,
        },
    )
    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(runs_dir),
        days=3,
        now_utc=_NOW,
    )
    morning = ledger["days"][0]["windows"]["morning"]
    assert morning["outcome"] == "GATE_HOLD"
    assert morning["reason"] == "PRICE_CONFLICT"
    assert morning["parser"]["manufactured_hold"]["original_action"] == "BUY"


def test_ledger_names_hard_stale_macro_holds_from_archived_reason(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    _write_journal(
        journal,
        [
            _event(
                "risk_result",
                "2026-06-11T07:02:00Z",
                {"slot": "MORNING", "window_label": "morning", "reason": "HOLD", "action": "HOLD", "terminal": True},
            ),
        ],
    )
    runs_dir = tmp_path / "signal_runs"
    _write_archive(
        runs_dir,
        timestamp="2026-06-11T06:55:00Z",
        window_label="morning",
        signal={
            "action": "HOLD",
            "confidence": 0.0,
            "consensus_state": "blocked",
            "validator_status": "skipped",
            "validator_summary": (
                "Daily-publishing macro series is hard-stale at 3 business days - "
                "refusing to trade until it refreshes within 2 business days."
            ),
            "reasoning": (
                "Daily-publishing macro series is hard-stale at 3 business days - "
                "refusing to trade until it refreshes within 2 business days."
            ),
        },
    )

    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(runs_dir),
        days=3,
        now_utc=_NOW,
    )

    morning = ledger["days"][0]["windows"]["morning"]
    assert morning["outcome"] == "GATE_HOLD"
    assert morning["reason"] == "HARD_STALE_MACRO_SNAPSHOT"
    assert morning["parser"]["manufactured_hold"]["gate"] == "HARD_STALE_MACRO_SNAPSHOT"


def test_ledger_flags_unconsumed_directional_signal(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    journal.write_text("", encoding="utf-8")
    runs_dir = tmp_path / "signal_runs"
    _write_archive(
        runs_dir,
        timestamp="2026-06-11T06:55:00Z",
        window_label="morning",
        signal={"action": "BUY", "confidence": 0.7, "consensus_state": "aligned"},
    )
    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(runs_dir),
        days=3,
        now_utc=_NOW,
    )
    morning = ledger["days"][0]["windows"]["morning"]
    assert morning["outcome"] == "NOT_CONSUMED"
    assert morning["reason"] == "SIGNAL_NEVER_REACHED_BOT"


def test_ledger_preserves_structured_hard_block_evidence(tmp_path: Path) -> None:
    journal = tmp_path / "events.jsonl"
    pattern_evidence = {
        "factor": "PATTERN_DIRECTION_MISMATCH",
        "pattern": "BULLISH_ENGULFING",
        "detail": "DIRECTION_MISMATCH",
        "timeframe": "M5",
    }
    _write_journal(
        journal,
        [
            _event(
                "signal_decision",
                "2026-06-11T07:01:00Z",
                {
                    "slot": "MORNING",
                    "window_label": "morning",
                    "action": "SELL",
                    "confidence": 0.66,
                    "confirm_status": "CONFIRMED",
                    "confirm_reason": "CONFIRMED",
                },
            ),
            _event(
                "risk_result",
                "2026-06-11T07:02:00Z",
                {
                    "slot": "MORNING",
                    "window_label": "morning",
                    "reason": "HARD_BLOCKER",
                    "signal_action": "SELL",
                    "terminal": True,
                    "policy_factors": ["PATTERN_DIRECTION_MISMATCH"],
                    "hard_block_score": 3,
                    "block_factors": ["PATTERN_DIRECTION_MISMATCH"],
                    "primary_block_factor": "PATTERN_DIRECTION_MISMATCH",
                    "assurance_score": 0.74,
                    "pattern_evidence": pattern_evidence,
                },
            ),
        ],
    )

    ledger = build_decision_ledger(
        journal_path=str(journal),
        state_path=str(tmp_path / "missing-state.json"),
        signal_runs_dir=str(tmp_path / "missing-runs"),
        days=3,
        now_utc=_NOW,
    )

    morning = ledger["days"][0]["windows"]["morning"]
    assert morning["outcome"] == "BLOCKED"
    assert morning["reason"] == "HARD_BLOCKER"
    assert morning["block_factors"] == ["PATTERN_DIRECTION_MISMATCH"]
    assert morning["primary_block_factor"] == "PATTERN_DIRECTION_MISMATCH"
    assert morning["hard_block_score"] == 3
    assert morning["assurance_score"] == 0.74
    assert morning["pattern_evidence"] == pattern_evidence


def test_dashboard_ledger_renders_primary_hard_block_factor() -> None:
    template = (Path(__file__).resolve().parents[2] / "xauex" / "app" / "templates" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "rec.primary_block_factor" in template
    assert "rec.block_factors" in template
