"""Weekly review generation."""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from xauex.analyst._utils import (
    default_model,
    atomic_write_json,
    call_claude,
    is_state_stale,
    read_json_file,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = default_model()
STALE_SECONDS = 60 * 60  # 1 hour (weekly review is less time-sensitive)

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
JOURNAL_PATH = os.environ.get("XAUEX_JOURNAL_OUTPUT", "/var/lib/xauex/trade_journal.json")
SCORES_PATH = os.environ.get("XAUEX_SCORES_OUTPUT", "/var/lib/xauex/setup_scores.json")
OUTPUT_PATH = os.environ.get("XAUEX_WEEKLY_OUTPUT", "/var/lib/xauex/weekly_review.json")
GATE_ECONOMICS_PATH = os.environ.get("XAUEX_GATE_ECONOMICS_PATH", "/var/lib/xauex/gate_economics.json")
DECISION_LEDGER_PATH = os.environ.get("XAUEX_DECISION_LEDGER_PATH", "/var/lib/xauex/decision_ledger.json")
MARKDOWN_OUTPUT_PATH = os.environ.get("XAUEX_WEEKLY_MARKDOWN_OUTPUT", "/var/lib/xauex/weekly_review.md")
REVIEW_MODE = os.environ.get("XAUEX_WEEKLY_REVIEW_MODE", "previous_week").strip().lower()
WEEKLY_ANALYST_MAX_TOKENS = 4800
_REVIEW_COMPLETION_MARKER = "REVIEW_COMPLETE"


def get_previous_week_bounds(run_at: datetime) -> Tuple[datetime, datetime]:
    """Return (Monday 00:00 UTC, Sunday 23:59:59 UTC) of the week prior to run_at."""
    # run_at is Monday; go back 7 days to get last Monday
    last_monday = run_at - timedelta(days=7)
    week_start = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


def get_current_week_bounds(run_at: datetime) -> Tuple[datetime, datetime]:
    """Return (Monday 00:00 UTC, run_at) for the current week."""
    week_start = (run_at - timedelta(days=run_at.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return week_start, run_at


def filter_to_week(entries: List[Dict], ts_key: str, start: datetime, end: datetime) -> List[Dict]:
    """Return entries whose ts_key falls within [start, end]."""
    result = []
    for entry in entries:
        ts_str = entry.get(ts_key, "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if start <= ts <= end:
                result.append(entry)
        except (ValueError, AttributeError, TypeError):
            continue
    return result


def filter_journal_to_week(entries: List[Dict], start: datetime, end: datetime) -> List[Dict]:
    """Filter journal entries by trade close time, falling back to journal time."""
    result = []
    for entry in entries:
        trade = entry.get("entry") if isinstance(entry.get("entry"), dict) else {}
        ts_str = trade.get("close_time_utc") or entry.get("journalled_at_utc", "")
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            if start <= ts <= end:
                result.append(entry)
        except (ValueError, AttributeError, TypeError):
            continue
    return result


_DIRECTION_SKEW_ALERT_THRESHOLD = 0.70
_PATTERN_HIT_RATE_ALERT_THRESHOLD = 0.25


def compute_trade_metrics(journal: List[Dict]) -> Dict[str, Any]:
    """Aggregate direction-aware metrics from a list of journal entries.

    The legacy weekly review only looked at total PnL and recommended
    "increase risk appetite" on a system that was 82% short and bleeding on
    those shorts. This function exposes:

    * LONG/SHORT counts and PnL split.
    * Pattern hit rate (proportion of trades with a non-NONE pattern).
    * Direction skew ratio and an explicit alert when one side dominates >70%.
    * Confidence-weighted PnL when the journal records ``signal_confidence``.
    """
    trades = list(journal or [])
    long_pnl = 0.0
    short_pnl = 0.0
    long_count = 0
    short_count = 0
    unknown_direction_count = 0
    wins = 0
    losses = 0
    pattern_hits = 0
    high_conf_pnl = 0.0
    low_conf_pnl = 0.0
    high_conf_count = 0
    low_conf_count = 0
    counter_signal_count = 0
    counter_signal_pnl = 0.0
    counter_signal_wins = 0
    counter_signal_losses = 0
    baseline_count = 0
    baseline_pnl = 0.0
    patternless_pnl = 0.0
    matched_pattern_pnl = 0.0
    requested_cash_risk = 0.0
    effective_cash_risk = 0.0
    risk_floor_lift_count = 0
    risk_telemetry_count = 0
    confidence_by_lane: Dict[str, Dict[str, float | int]] = {
        "baseline_high": {"count": 0, "pnl": 0.0},
        "baseline_low": {"count": 0, "pnl": 0.0},
        "counter_high": {"count": 0, "pnl": 0.0},
        "counter_low": {"count": 0, "pnl": 0.0},
    }
    for trade in trades:
        entry = trade.get("entry") or {}
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        session = metadata.get("session") if isinstance(metadata.get("session"), dict) else {}
        direction = str(entry.get("direction") or "").upper()
        try:
            pnl = float(entry.get("pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        pattern = str(entry.get("pattern") or "NONE").upper()
        try:
            confidence = float(entry.get("signal_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        if direction == "LONG":
            long_count += 1
            long_pnl += pnl
        elif direction == "SHORT":
            short_count += 1
            short_pnl += pnl
        else:
            unknown_direction_count += 1

        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        if pattern not in ("", "NONE"):
            pattern_hits += 1
            matched_pattern_pnl += pnl
        else:
            patternless_pnl += pnl

        counter_signal = bool(session.get("counter_signal"))
        if counter_signal:
            counter_signal_count += 1
            counter_signal_pnl += pnl
            if pnl > 0:
                counter_signal_wins += 1
            elif pnl < 0:
                counter_signal_losses += 1
        else:
            baseline_count += 1
            baseline_pnl += pnl

        requested_risk = session.get("requested_cash_risk")
        effective_risk = session.get("effective_cash_risk", session.get("actual_cash_risk"))
        if requested_risk is not None and effective_risk is not None:
            try:
                requested_value = float(requested_risk)
                effective_value = float(effective_risk)
            except (TypeError, ValueError):
                pass
            else:
                risk_telemetry_count += 1
                requested_cash_risk += requested_value
                effective_cash_risk += effective_value
                if bool(session.get("minimum_risk_floor_applied")) or effective_value > requested_value + 0.01:
                    risk_floor_lift_count += 1

        if confidence >= 0.6:
            high_conf_count += 1
            high_conf_pnl += pnl
            lane_key = "counter_high" if counter_signal else "baseline_high"
            confidence_by_lane[lane_key]["count"] += 1
            confidence_by_lane[lane_key]["pnl"] += pnl
        elif confidence > 0:
            low_conf_count += 1
            low_conf_pnl += pnl
            lane_key = "counter_low" if counter_signal else "baseline_low"
            confidence_by_lane[lane_key]["count"] += 1
            confidence_by_lane[lane_key]["pnl"] += pnl

    trade_count = len(trades)
    pattern_hit_rate = (pattern_hits / trade_count) if trade_count else 0.0
    win_rate = (wins / (wins + losses)) if (wins + losses) else 0.0
    if trade_count and (long_count or short_count):
        direction_skew_ratio = max(long_count, short_count) / max(1, long_count + short_count)
    else:
        direction_skew_ratio = 0.0
    direction_skew_alert = direction_skew_ratio >= _DIRECTION_SKEW_ALERT_THRESHOLD and trade_count >= 4
    pattern_hit_rate_alert = pattern_hit_rate < _PATTERN_HIT_RATE_ALERT_THRESHOLD and trade_count >= 3

    return {
        "trade_count": trade_count,
        "long_count": long_count,
        "short_count": short_count,
        "unknown_direction_count": unknown_direction_count,
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "net_pnl": round(long_pnl + short_pnl, 2),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 3),
        "pattern_hits": pattern_hits,
        "pattern_hit_rate": round(pattern_hit_rate, 3),
        "patternless_count": trade_count - pattern_hits,
        "patternless_pnl": round(patternless_pnl, 2),
        "matched_pattern_pnl": round(matched_pattern_pnl, 2),
        "direction_skew_ratio": round(direction_skew_ratio, 3),
        "direction_skew_alert": direction_skew_alert,
        "pattern_hit_rate_alert": pattern_hit_rate_alert,
        "high_confidence_count": high_conf_count,
        "low_confidence_count": low_conf_count,
        "high_confidence_pnl": round(high_conf_pnl, 2),
        "low_confidence_pnl": round(low_conf_pnl, 2),
        "counter_signal_count": counter_signal_count,
        "counter_signal_pnl": round(counter_signal_pnl, 2),
        "counter_signal_wins": counter_signal_wins,
        "counter_signal_losses": counter_signal_losses,
        "baseline_count": baseline_count,
        "baseline_pnl": round(baseline_pnl, 2),
        "risk_telemetry_count": risk_telemetry_count,
        "requested_cash_risk": round(requested_cash_risk, 2),
        "effective_cash_risk": round(effective_cash_risk, 2),
        "risk_floor_lift_count": risk_floor_lift_count,
        "confidence_by_lane": {
            key: {"count": int(value["count"]), "pnl": round(float(value["pnl"]), 2)}
            for key, value in confidence_by_lane.items()
        },
    }


def compute_decision_metrics(
    ledger: Dict[str, Any],
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, Any]:
    """Deterministic weekly window and pattern coverage from the decision ledger."""
    start_date = week_start.strftime("%Y-%m-%d")
    end_date = week_end.strftime("%Y-%m-%d")
    records: list[dict[str, Any]] = []
    for day in ledger.get("days", []) if isinstance(ledger, dict) else []:
        date_london = str(day.get("date_london") or "")
        if not (start_date <= date_london <= end_date):
            continue
        for window_label, record in (day.get("windows") or {}).items():
            if isinstance(record, dict):
                records.append({"window_label": window_label, **record})

    outcomes: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    pattern_factors: Dict[str, int] = {}
    for record in records:
        outcome = str(record.get("outcome") or "UNKNOWN")
        reason = str(record.get("reason") or "UNKNOWN")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        reasons[reason] = reasons.get(reason, 0) + 1
        evidence = record.get("pattern_evidence")
        factor = str(evidence.get("factor") or "").upper() if isinstance(evidence, dict) else ""
        if factor:
            pattern_factors[factor] = pattern_factors.get(factor, 0) + 1

    evaluated = sum(pattern_factors.values())
    matches = pattern_factors.get("PATTERN_MATCH", 0)
    match_rate = round(matches / evaluated, 3) if evaluated else 0.0
    return {
        "window_count": len(records),
        "traded": outcomes.get("TRADED", 0),
        "blocked": outcomes.get("BLOCKED", 0),
        "gate_holds": outcomes.get("GATE_HOLD", 0),
        "no_signal": outcomes.get("NO_SIGNAL", 0),
        "by_outcome": outcomes,
        "by_reason": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "pattern_evaluated": evaluated,
        "pattern_matches": matches,
        "pattern_missing": pattern_factors.get("PATTERN_MISSING", 0),
        "pattern_direction_mismatches": pattern_factors.get("PATTERN_DIRECTION_MISMATCH", 0),
        "pattern_data_unavailable": pattern_factors.get("PATTERN_DATA_UNAVAILABLE", 0),
        "pattern_match_rate": match_rate,
        "pattern_coverage_alert": evaluated >= 3 and match_rate < _PATTERN_HIT_RATE_ALERT_THRESHOLD,
        "pattern_by_factor": pattern_factors,
        "counter_signal_trades": sum(
            1 for record in records if record.get("outcome") == "TRADED" and bool(record.get("counter_signal"))
        ),
    }


def format_trade_metrics(metrics: Dict[str, Any], currency: str = "GBP") -> str:
    """Render the metrics block for the analyst prompt."""
    lines = [
        "DIRECTION BREAKDOWN:",
        f"  LONG trades: {metrics['long_count']} (gross PnL {metrics['long_pnl']:+.2f} {currency})",
        f"  SHORT trades: {metrics['short_count']} (gross PnL {metrics['short_pnl']:+.2f} {currency})",
    ]
    if metrics.get("unknown_direction_count"):
        lines.append(f"  Unknown-direction trades: {metrics['unknown_direction_count']}")
    lines.append(
        f"  Direction skew ratio: {metrics['direction_skew_ratio']:.2f} "
        f"(threshold {_DIRECTION_SKEW_ALERT_THRESHOLD:.2f})"
    )
    if metrics.get("direction_skew_alert"):
        lines.append(
            "  DIRECTION SKEW ALERT=true: at least 70% of trades went in one direction. "
            "Investigate whether the bot has a systemic directional bias."
        )
    else:
        lines.append("  Direction skew alert: false")

    lines.append("")
    lines.append("PATTERN QUALITY:")
    lines.append(
        f"  Pattern hit rate: {metrics['pattern_hit_rate']:.2f} "
        f"({metrics['pattern_hits']}/{metrics['trade_count']} trades had a confirmed pattern)"
    )
    if metrics.get("pattern_hit_rate_alert"):
        lines.append(
            "  PATTERN COVERAGE ALERT=true: fewer than 25% of trades had a confirmed pattern. "
            "Audit detector coverage and compare outcomes; PATTERN_MISSING is an approved reduced-risk warning, not a rule violation."
        )
    else:
        lines.append("  Pattern coverage alert: false")
    lines.append(
        f"  Patternless trades: {metrics['patternless_count']} gross PnL "
        f"{metrics['patternless_pnl']:+.2f} {currency}; matched-pattern PnL "
        f"{metrics['matched_pattern_pnl']:+.2f} {currency}"
    )

    lines.append("")
    lines.append("OUTCOME SUMMARY:")
    lines.append(
        f"  Wins {metrics['wins']} / Losses {metrics['losses']} (win rate {metrics['win_rate']:.2f})"
    )
    lines.append(f"  Gross realized PnL: {metrics['net_pnl']:+.2f} {currency}")
    if metrics.get("high_confidence_count") or metrics.get("low_confidence_count"):
        lines.append(
            f"  High-conf (≥0.6) trades: {metrics['high_confidence_count']} "
            f"PnL {metrics['high_confidence_pnl']:+.2f} {currency}; "
            f"Low-conf trades: {metrics['low_confidence_count']} "
            f"PnL {metrics['low_confidence_pnl']:+.2f} {currency}"
        )
        lane_cells = metrics.get("confidence_by_lane") or {}
        lines.append(
            "  Confidence/lane composition: "
            f"baseline-high {lane_cells.get('baseline_high', {}).get('count', 0)}, "
            f"baseline-low {lane_cells.get('baseline_low', {}).get('count', 0)}, "
            f"counter-high {lane_cells.get('counter_high', {}).get('count', 0)}, "
            f"counter-low {lane_cells.get('counter_low', {}).get('count', 0)}"
        )
    lines.append(
        f"  Baseline lane: {metrics['baseline_count']} trades, PnL "
        f"{metrics['baseline_pnl']:+.2f} {currency}; counter-signal lane: "
        f"{metrics['counter_signal_count']} trades, PnL {metrics['counter_signal_pnl']:+.2f} {currency} "
        f"({metrics['counter_signal_wins']}W/{metrics['counter_signal_losses']}L)"
    )
    if metrics.get("risk_telemetry_count"):
        lines.append(
            f"  Effective-risk telemetry: {metrics['risk_floor_lift_count']}/{metrics['risk_telemetry_count']} "
            f"trades used a minimum-risk floor; requested {metrics['requested_cash_risk']:.2f} {currency}, "
            f"actual {metrics['effective_cash_risk']:.2f} {currency}"
        )
    else:
        lines.append("  Effective-risk telemetry: unavailable for these historical trades")
    return "\n".join(lines)


def format_decision_metrics(metrics: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "WINDOW DECISIONS:",
            f"  Windows: {metrics['window_count']}; traded {metrics['traded']}; blocked {metrics['blocked']}; "
            f"gate holds {metrics['gate_holds']}; no signal {metrics['no_signal']}",
            f"  Pattern evaluations: {metrics['pattern_evaluated']}; matches {metrics['pattern_matches']}; "
            f"missing {metrics['pattern_missing']}; direction mismatches "
            f"{metrics['pattern_direction_mismatches']}; data unavailable {metrics['pattern_data_unavailable']}",
            f"  Pattern match rate: {metrics['pattern_match_rate']:.3f}",
            f"  Pattern coverage alert: {str(bool(metrics['pattern_coverage_alert'])).lower()}",
            f"  Executed counter-signals: {metrics['counter_signal_trades']}",
            f"  Reasons: {metrics['by_reason']}",
        ]
    )


def build_review_prompt(
    state: Dict,
    journal: List[Dict],
    scores: List[Dict],
    week_start: datetime,
    week_end: datetime,
    ledger: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the analyst prompt for the weekly review."""
    risk = state.get("risk", {})
    runtime = state.get("runtime", {}) if isinstance(state.get("runtime"), dict) else {}
    candidate_metrics = runtime.get("candidate_metrics", {}) if isinstance(runtime.get("candidate_metrics"), dict) else {}

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    metrics = compute_trade_metrics(journal)
    currency = str((state.get("account") or {}).get("currency") or "GBP").upper()
    metrics_text = format_trade_metrics(metrics, currency)
    decision_metrics = compute_decision_metrics(ledger or {}, week_start, week_end)
    decision_text = format_decision_metrics(decision_metrics)

    journal_text = "No trades this week." if not journal else "\n".join(
        f"  [{e.get('trade_id')}] {e.get('entry', {}).get('direction', '?'):>5s} "
        f"pattern={e.get('entry', {}).get('pattern', 'NONE'):<25s} "
        f"PnL={e.get('entry', {}).get('pnl', '?'):.2f} | "
        f"{e.get('journal', '')[:120]}"
        for e in journal
    )

    scores_text = "No scored setups this week." if not scores else "\n".join(
        f"  [{e.get('signal_ts')}] {e.get('breakdown', '')[:100]}"
        for e in scores
    )

    candidate_text = f"candidate lane total={candidate_metrics.get('total', 0)}"
    gate_economics = read_json_file(GATE_ECONOMICS_PATH) or {}
    gate_reasons = gate_economics.get("by_reason") or {}
    if gate_reasons:
        gate_lines = ", ".join(
            f"{reason}: n={stats.get('n', 0)} expectancy={stats.get('expectancy_r', 0.0)}R"
            f" missed_usd={stats.get('missed_usd_min_lot', 0.0)}"
            for reason, stats in list(gate_reasons.items())[:6]
        )
        gate_text = (
            f"replayed blocked trades over {gate_economics.get('window_days', 90)}d — {gate_lines}"
        )
    else:
        gate_text = "no replayed blocked trades yet"
    continuation_shadow = gate_economics.get("continuation_parent_shadow") or {}
    if continuation_shadow:
        eligible_shadow = continuation_shadow.get("eligible") or {}
        unknown_shadow = continuation_shadow.get("unknown_legacy") or {}
        continuation_text = (
            f"policy={continuation_shadow.get('policy', 'UNKNOWN')}; execution_changed="
            f"{str(bool(continuation_shadow.get('execution_changed'))).lower()}; "
            f"labeled eligible n={eligible_shadow.get('n', 0)} expectancy="
            f"{eligible_shadow.get('expectancy_r', 0.0)}R; unknown legacy n="
            f"{unknown_shadow.get('n', 0)} expectancy={unknown_shadow.get('expectancy_r', 0.0)}R"
        )
    else:
        continuation_text = "no labeled continuation shadow cohort yet"

    return f"""You are a senior trading analyst reviewing an automated XAUUSD bot's performance for the week of {week_start_str} to {week_end_str}.

RISK SUMMARY:
  Account currency: {currency}
  Weekly gross realized PnL: {risk.get('weekly_pnl', 'N/A')} {currency}
  Weekly halted: {risk.get('weekly_halted', False)}
  Consecutive losses (end of week): {risk.get('consecutive_losses_today', 0)}

{metrics_text}

{decision_text}

TRADE JOURNAL ({len(journal)} trades):
{journal_text}

SETUP SCORES ({len(scores)} setups):
{scores_text}

SIGNAL ACTIVITY:
  Candidate lane: {candidate_text}
  Blocked-trade economics: {gate_text}
  Continuation shadow: {continuation_text}

POLICY FACTS THAT MUST NOT BE CONTRADICTED:
  - A direction-skew alert fires only when direction_skew_alert=true; the ratio threshold is >= 0.70.
  - PATTERN_MISSING is intentionally allowed at reduced requested risk and must not be called a violation.
  - Do not recommend a hard pattern gate from coverage alone. Recommend detector audit or a shadow-only trial instead.
  - Separate baseline and counter-signal performance; do not relabel counter-signal results as a confidence effect.
  - If confidence buckets and execution lanes are confounded, explicitly say so and do not claim that confidence caused the outcome difference.
  - Live PnL above is {currency}. missed_usd in blocked-trade replay is hypothetical USD at 0.01 lot, excludes costs, and is not directly comparable.
  - Blocked-trade replay is hypothesis-generating, not authorization to relax a live gate. CONTINUATION_PARENT_NOT_PROTECTED may only be refined in the labeled shadow cohort until sufficient forward evidence exists.
  - The end-of-week risk snapshot cannot prove whether drawdown limits were approached intraweek. Do not claim they were never threatened unless trajectory evidence is provided.

Provide a strategic weekly review covering:
1. Overall performance: win rate, RR quality, patterns in outcomes — but ALWAYS lead with the direction breakdown if a skew alert fires.
2. Pattern coverage: report deterministic coverage and observed PnL without asserting causality from this small sample.
3. Setup quality: report high- versus low-confidence outcomes, then test whether lane composition confounds that comparison before drawing any inference. Did high-score setups outperform?
4. Execution lanes: compare baseline and counter-signal outcomes only when both have observations; otherwise state that no lane comparison is available.
5. Risk management: state when drawdown proximity or rule-compliance history is unavailable from the supplied evidence. Avoid recommending "increase risk appetite" when a direction-skew or pattern-coverage alert is active.
6. Recommendations: 1-2 concrete, specific parameter or behaviour changes to consider next week (or "no changes recommended" if performance was solid). NEVER recommend increasing risk on a system that just fired one of the alerts above.

Keep the response under 650 words. Do not claim an alert fired unless its deterministic boolean is true. Be analytical and direct. Focus on actionable insights, not platitudes. End with the standalone marker REVIEW_COMPLETE."""


def build_deterministic_summary(
    *,
    state: Dict[str, Any],
    trade_metrics: Dict[str, Any],
    decision_metrics: Dict[str, Any],
) -> str:
    currency = str((state.get("account") or {}).get("currency") or "GBP").upper()
    return "\n".join(
        [
            "DETERMINISTIC WEEKLY FACTS",
            f"Account currency: {currency}",
            f"Trades: {trade_metrics['trade_count']} | Wins: {trade_metrics['wins']} | "
            f"Losses: {trade_metrics['losses']} | Gross PnL: {trade_metrics['net_pnl']:+.2f} {currency}",
            f"Direction skew: {trade_metrics['direction_skew_ratio']:.2f} | "
            f"Alert: {str(bool(trade_metrics['direction_skew_alert'])).lower()}",
            f"Pattern match rate: {decision_metrics['pattern_match_rate']:.3f} "
            f"({decision_metrics['pattern_matches']}/{decision_metrics['pattern_evaluated']} evaluations) | "
            f"Coverage alert: {str(bool(decision_metrics['pattern_coverage_alert'])).lower()}",
            f"Baseline lane: {trade_metrics['baseline_count']} trades, "
            f"{trade_metrics['baseline_pnl']:+.2f} {currency} | Counter-signal lane: "
            f"{trade_metrics['counter_signal_count']} trades, {trade_metrics['counter_signal_pnl']:+.2f} {currency}",
            f"Windows: {decision_metrics['window_count']} | Traded: {decision_metrics['traded']} | "
            f"Blocked: {decision_metrics['blocked']}",
        ]
    )


def finalize_analyst_commentary(commentary: str) -> tuple[str, bool]:
    """Strip the completion marker or append a safe ending when output was truncated."""
    text = str(commentary or "").strip()
    if _REVIEW_COMPLETION_MARKER in text:
        return text.rsplit(_REVIEW_COMPLETION_MARKER, 1)[0].rstrip(), True
    fallback = """DETERMINISTIC FALLBACK RECOMMENDATIONS
1. Audit pattern-detector coverage; keep PATTERN_MISSING as the approved reduced-risk warning rather than a hard gate.
2. Continue the near-protected-parent continuation variant in shadow only. Do not change live execution until the labeled forward cohort is large enough to evaluate."""
    return f"{text}\n\n{fallback}".strip(), False


def render_markdown(result: Dict[str, Any]) -> str:
    return f"""# XAUEX Weekly Review

- Week starting: {result["week_starting"]}
- Week ending: {result["week_ending"]}
- Generated at UTC: {result["generated_at_utc"]}
- Model: {result["model"]}
- Trades reviewed: {result["trades_reviewed"]}
- Setups reviewed: {result["setups_reviewed"]}

## Review

{result["review"]}
"""


def run(
    state_path: str = STATE_PATH,
    journal_path: str = JOURNAL_PATH,
    scores_path: str = SCORES_PATH,
    output_path: str = OUTPUT_PATH,
    markdown_output_path: str = MARKDOWN_OUTPUT_PATH,
    decision_ledger_path: str = DECISION_LEDGER_PATH,
    review_mode: str = REVIEW_MODE,
    _run_at: Optional[datetime] = None,
) -> None:
    run_at = _run_at or datetime.now(timezone.utc)
    if review_mode == "current_week":
        week_start, week_end = get_current_week_bounds(run_at)
    else:
        week_start, week_end = get_previous_week_bounds(run_at)

    state = read_json_file(state_path)
    if is_state_stale(state, max_age_seconds=STALE_SECONDS):
        logger.warning("[WEEKLY] state.json stale or missing.")
        state = state or {}

    journal_all = read_json_file(journal_path) or []
    scores_all = read_json_file(scores_path) or []
    ledger = read_json_file(decision_ledger_path) or {}

    journal = filter_journal_to_week(journal_all, week_start, week_end)
    scores = filter_to_week(scores_all, "scored_at_utc", week_start, week_end)

    logger.info(
        "[WEEKLY] Week %s–%s: %d trades, %d scored setups.",
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
        len(journal),
        len(scores),
    )

    trade_metrics = compute_trade_metrics(journal)
    decision_metrics = compute_decision_metrics(ledger, week_start, week_end)
    prompt = build_review_prompt(state, journal, scores, week_start, week_end, ledger=ledger)
    logger.info("[WEEKLY] Calling analyst model (%s)...", MODEL)
    analyst_commentary_raw = call_claude(
        prompt,
        MODEL,
        max_tokens=WEEKLY_ANALYST_MAX_TOKENS,
    )
    analyst_commentary, analyst_commentary_complete = finalize_analyst_commentary(
        analyst_commentary_raw
    )
    if not analyst_commentary_complete:
        logger.warning("[WEEKLY] Analyst commentary lacked completion marker; appended deterministic fallback.")
    deterministic_summary = build_deterministic_summary(
        state=state,
        trade_metrics=trade_metrics,
        decision_metrics=decision_metrics,
    )
    review_text = f"{deterministic_summary}\n\nANALYST COMMENTARY\n{analyst_commentary}"

    result = {
        "week_ending": week_end.strftime("%Y-%m-%d"),
        "week_starting": week_start.strftime("%Y-%m-%d"),
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "review_mode": review_mode,
        "trades_reviewed": len(journal),
        "setups_reviewed": len(scores),
        "account_currency": str((state.get("account") or {}).get("currency") or "GBP").upper(),
        "trade_metrics": trade_metrics,
        "decision_metrics": decision_metrics,
        "analyst_commentary_complete": analyst_commentary_complete,
        "review": review_text,
    }
    atomic_write_json(output_path, result)
    with open(markdown_output_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(result))
    logger.info("[WEEKLY] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[WEEKLY] Failed: %s", e)
        sys.exit(1)
