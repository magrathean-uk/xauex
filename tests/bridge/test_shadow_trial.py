import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import xauex.signal.shadow_trial as shadow_trial
from xauex.signal.shadow_trial import (
    DEFAULT_LOOKBACK_DAYS,
    build_shadow_trial_record,
    evaluate_shadow_trial_record,
    find_latest_baseline_archive,
    render_shadow_trial_report,
    shadow_report_has_enough_history,
)


def test_find_latest_baseline_archive_prefers_newest_baseline_dir(tmp_path):
    older = tmp_path / "20260415T070007Z_xauusd_baseline"
    newer = tmp_path / "20260415T103007Z_xauusd_baseline"
    debate = tmp_path / "20260415T103007Z_xauusd_analyst_debate"
    older.mkdir()
    newer.mkdir()
    debate.mkdir()

    chosen = find_latest_baseline_archive(tmp_path)

    assert chosen == newer


def test_find_latest_baseline_archive_can_require_fresh_window(tmp_path):
    stale = tmp_path / "20260415T070007Z_xauusd_baseline"
    fresh_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    fresh = tmp_path / f"{fresh_time.strftime('%Y%m%dT%H%M%SZ')}_xauusd_baseline"
    stale.mkdir()
    fresh.mkdir()

    chosen = find_latest_baseline_archive(tmp_path, max_age_minutes=45)

    assert chosen == fresh


def test_build_shadow_trial_record_captures_signal_and_compare_prices(tmp_path):
    archive_dir = tmp_path / "20260415T070007Z_xauusd_baseline"
    archive_dir.mkdir()
    comparison_dir = tmp_path / "shadow" / "20260415T070300Z_xauusd"
    comparison_dir.mkdir(parents=True)

    comparison = {
        "baseline": {"action": "SELL", "confidence": 0.44, "estimated_total_cost_usd": 0.0032, "total_tokens": 9000},
        "analyst_debate": {"action": "BUY", "confidence": 0.61, "estimated_total_cost_usd": 0.0045, "total_tokens": 18000},
        "delta": {"action_changed": True},
    }
    archived_signal = {"timestamp_utc": "2026-04-15T07:00:07Z", "symbol": "XAUUSD"}
    prediction_payload = {"price_features": {"current_mid": 4781.30}}
    compare_quote = {"mid": 4783.10, "updated_at_utc": "2026-04-15T07:03:00Z"}

    record = build_shadow_trial_record(
        archive_dir=archive_dir,
        comparison_dir=comparison_dir,
        comparison=comparison,
        archived_signal=archived_signal,
        prediction_payload=prediction_payload,
        compare_quote=compare_quote,
        report_email="bolyki@bolyki.eu",
    )

    assert record["trial_id"] == "20260415T070007Z_xauusd"
    assert record["signal_mid_price"] == 4781.30
    assert record["compare_mid_price"] == 4783.10
    assert record["evaluation_due_at_utc"] == "2026-04-15T09:00:07Z"
    assert record["baseline"]["action"] == "SELL"
    assert record["analyst_debate"]["action"] == "BUY"
    assert record["report_email"] == "bolyki@bolyki.eu"


def test_evaluate_shadow_trial_record_marks_correct_variant():
    record = {
        "signal_mid_price": 4781.30,
        "baseline": {"action": "SELL"},
        "analyst_debate": {"action": "BUY"},
    }

    evaluated = evaluate_shadow_trial_record(
        record,
        evaluation_price=4768.80,
        evaluation_price_timestamp_utc="2026-04-15T09:00:00Z",
        hold_band_usd=2.0,
    )

    assert evaluated["outcome"]["status"] == "completed"
    assert evaluated["outcome"]["winner"] == "baseline"
    assert evaluated["outcome"]["move_usd"] == -12.5
    assert evaluated["outcome"]["baseline_correct"] is True
    assert evaluated["outcome"]["analyst_debate_correct"] is False


def test_render_shadow_trial_report_summarizes_completed_trials():
    rows = [
        {
            "trial_id": "t1",
            "signal_timestamp_utc": "2026-04-15T07:00:07Z",
            "baseline": {"action": "SELL", "confidence": 0.44},
            "analyst_debate": {"action": "BUY", "confidence": 0.61},
            "outcome": {"status": "completed", "winner": "baseline", "move_usd": -12.5},
        },
        {
            "trial_id": "t2",
            "signal_timestamp_utc": "2026-04-15T10:30:07Z",
            "baseline": {"action": "SELL", "confidence": 0.51},
            "analyst_debate": {"action": "SELL", "confidence": 0.55},
            "outcome": {"status": "completed", "winner": "both", "move_usd": -8.1},
        },
    ]

    subject, body = render_shadow_trial_report(rows, recipient="bolyki@bolyki.eu")

    assert "XAUEX shadow trial results" in subject
    assert "Completed trials: 2" in body
    assert "Baseline wins: 1" in body
    assert "Debate wins: 0" in body
    assert "Both correct: 1" in body


def test_shadow_trial_report_defaults_to_one_week_lookback():
    assert DEFAULT_LOOKBACK_DAYS == 7


def test_shadow_report_waits_for_minimum_history_window():
    rows = [
        {
            "trial_id": "t1",
            "signal_timestamp_utc": "2026-04-15T07:00:07Z",
            "outcome": {"status": "completed"},
        }
    ]

    assert shadow_report_has_enough_history(
        rows,
        min_history_days=6,
        now=datetime(2026, 4, 20, 12, tzinfo=timezone.utc),
    ) is False
    assert shadow_report_has_enough_history(
        rows,
        min_history_days=6,
        now=datetime(2026, 4, 21, 8, tzinfo=timezone.utc),
    ) is True


def test_send_shadow_trial_report_skips_email_until_minimum_history(tmp_path, monkeypatch):
    trial_dir = tmp_path / "20260415T070007Z_xauusd"
    trial_dir.mkdir()
    shadow_trial.write_json(
        trial_dir / "trial.json",
        {
            "trial_id": "20260415T070007Z_xauusd",
            "signal_timestamp_utc": "2026-04-15T07:00:07Z",
            "baseline": {"action": "SELL"},
            "analyst_debate": {"action": "BUY"},
            "outcome": {"status": "completed", "winner": "baseline", "move_usd": -12.5},
        },
    )
    sent = []
    monkeypatch.setattr(shadow_trial, "_send_mail", lambda *args, **kwargs: sent.append(args))

    subject, body = shadow_trial.send_shadow_trial_report(
        shadow_root=tmp_path,
        recipient="bolyki@bolyki.eu",
        lookback_days=7,
        min_history_days=999,
    )

    assert "skipped" in subject.lower()
    assert "insufficient history" in body.lower()
    assert sent == []
