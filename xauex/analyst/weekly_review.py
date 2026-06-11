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
MARKDOWN_OUTPUT_PATH = os.environ.get("XAUEX_WEEKLY_MARKDOWN_OUTPUT", "/var/lib/xauex/weekly_review.md")
REVIEW_MODE = os.environ.get("XAUEX_WEEKLY_REVIEW_MODE", "previous_week").strip().lower()


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
    for trade in trades:
        entry = trade.get("entry") or {}
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

        if confidence >= 0.6:
            high_conf_count += 1
            high_conf_pnl += pnl
        elif confidence > 0:
            low_conf_count += 1
            low_conf_pnl += pnl

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
        "direction_skew_ratio": round(direction_skew_ratio, 3),
        "direction_skew_alert": direction_skew_alert,
        "pattern_hit_rate_alert": pattern_hit_rate_alert,
        "high_confidence_count": high_conf_count,
        "low_confidence_count": low_conf_count,
        "high_confidence_pnl": round(high_conf_pnl, 2),
        "low_confidence_pnl": round(low_conf_pnl, 2),
    }


def format_trade_metrics(metrics: Dict[str, Any]) -> str:
    """Render the metrics block for the analyst prompt."""
    lines = [
        "DIRECTION BREAKDOWN:",
        f"  LONG trades: {metrics['long_count']} (PnL {metrics['long_pnl']:+.2f})",
        f"  SHORT trades: {metrics['short_count']} (PnL {metrics['short_pnl']:+.2f})",
    ]
    if metrics.get("unknown_direction_count"):
        lines.append(f"  Unknown-direction trades: {metrics['unknown_direction_count']}")
    lines.append(
        f"  Direction skew ratio: {metrics['direction_skew_ratio']:.2f} "
        f"(threshold {_DIRECTION_SKEW_ALERT_THRESHOLD:.2f})"
    )
    if metrics.get("direction_skew_alert"):
        lines.append(
            "  ⚠ DIRECTION SKEW ALERT: more than 70% of trades went in one direction. "
            "Investigate whether the bot has a systemic directional bias."
        )

    lines.append("")
    lines.append("PATTERN QUALITY:")
    lines.append(
        f"  Pattern hit rate: {metrics['pattern_hit_rate']:.2f} "
        f"({metrics['pattern_hits']}/{metrics['trade_count']} trades had a confirmed pattern)"
    )
    if metrics.get("pattern_hit_rate_alert"):
        lines.append(
            "  ⚠ PATTERN HIT RATE ALERT: fewer than 25% of trades had a confirmed pattern. "
            "Trades without pattern confirmation have historically lost money."
        )

    lines.append("")
    lines.append("OUTCOME SUMMARY:")
    lines.append(
        f"  Wins {metrics['wins']} / Losses {metrics['losses']} (win rate {metrics['win_rate']:.2f})"
    )
    lines.append(f"  Net PnL: {metrics['net_pnl']:+.2f}")
    if metrics.get("high_confidence_count") or metrics.get("low_confidence_count"):
        lines.append(
            f"  High-conf (≥0.6) trades: {metrics['high_confidence_count']} "
            f"PnL {metrics['high_confidence_pnl']:+.2f}; "
            f"Low-conf trades: {metrics['low_confidence_count']} "
            f"PnL {metrics['low_confidence_pnl']:+.2f}"
        )
    return "\n".join(lines)


def build_review_prompt(
    state: Dict,
    journal: List[Dict],
    scores: List[Dict],
    week_start: datetime,
    week_end: datetime,
) -> str:
    """Build the analyst prompt for the weekly review."""
    risk = state.get("risk", {})
    signals = state.get("signal_history", [])
    shadow = state.get("shadow_signal_history", [])
    runtime = state.get("runtime", {}) if isinstance(state.get("runtime"), dict) else {}
    candidate_metrics = runtime.get("candidate_metrics", {}) if isinstance(runtime.get("candidate_metrics"), dict) else {}

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    metrics = compute_trade_metrics(journal)
    metrics_text = format_trade_metrics(metrics)

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

    signals_text = f"{len(signals)} signals in history (last 12 shown in state)"
    shadow_text = f"{len(shadow)} shadow signals in history"
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

    return f"""You are a senior trading analyst reviewing an automated XAUUSD bot's performance for the week of {week_start_str} to {week_end_str}.

RISK SUMMARY:
  Weekly PnL: {risk.get('weekly_pnl', 'N/A')}
  Weekly halted: {risk.get('weekly_halted', False)}
  Consecutive losses (end of week): {risk.get('consecutive_losses_today', 0)}

{metrics_text}

TRADE JOURNAL ({len(journal)} trades):
{journal_text}

SETUP SCORES ({len(scores)} setups):
{scores_text}

SIGNAL ACTIVITY:
  Primary strategy: {signals_text}
  Shadow strategy: {shadow_text}
  Candidate lane: {candidate_text}
  Blocked-trade economics: {gate_text}

Provide a strategic weekly review covering:
1. Overall performance: win rate, RR quality, patterns in outcomes — but ALWAYS lead with the direction breakdown if a skew alert fires.
2. Pattern discipline: low pattern hit rate has been correlated with losses; flag this if the alert is set.
3. Setup quality: were high-confidence trades more profitable than low-confidence? Did high-score setups outperform?
4. Strategy divergence: did primary and shadow strategies agree or disagree? What does that suggest?
5. Risk management: were drawdown limits ever near? Any rule violations? Avoid recommending "increase risk appetite" when a direction-skew or pattern-hit-rate alert is active.
6. Recommendations: 1-2 concrete, specific parameter or behaviour changes to consider next week (or "no changes recommended" if performance was solid). NEVER recommend increasing risk on a system that just fired one of the alerts above.

Be analytical and direct. Focus on actionable insights, not platitudes."""


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

    journal = filter_to_week(journal_all, "journalled_at_utc", week_start, week_end)
    scores = filter_to_week(scores_all, "scored_at_utc", week_start, week_end)

    logger.info(
        "[WEEKLY] Week %s–%s: %d trades, %d scored setups.",
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
        len(journal),
        len(scores),
    )

    prompt = build_review_prompt(state, journal, scores, week_start, week_end)
    logger.info("[WEEKLY] Calling analyst model (%s)...", MODEL)
    review_text = call_claude(prompt, MODEL)

    result = {
        "week_ending": week_end.strftime("%Y-%m-%d"),
        "week_starting": week_start.strftime("%Y-%m-%d"),
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "review_mode": review_mode,
        "trades_reviewed": len(journal),
        "setups_reviewed": len(scores),
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
