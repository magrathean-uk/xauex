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

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    journal_text = "No trades this week." if not journal else "\n".join(
        f"  [{e.get('trade_id')}] PnL={e.get('entry', {}).get('pnl', '?'):.2f} | {e.get('journal', '')[:120]}"
        for e in journal
    )

    scores_text = "No scored setups this week." if not scores else "\n".join(
        f"  [{e.get('signal_ts')}] {e.get('breakdown', '')[:100]}"
        for e in scores
    )

    signals_text = f"{len(signals)} signals in history (last 12 shown in state)"
    shadow_text = f"{len(shadow)} shadow signals in history"

    return f"""You are a senior trading analyst reviewing an automated XAUUSD bot's performance for the week of {week_start_str} to {week_end_str}.

RISK SUMMARY:
  Weekly PnL: {risk.get('weekly_pnl', 'N/A')}
  Weekly halted: {risk.get('weekly_halted', False)}
  Consecutive losses (end of week): {risk.get('consecutive_losses_today', 0)}

TRADE JOURNAL ({len(journal)} trades):
{journal_text}

SETUP SCORES ({len(scores)} setups):
{scores_text}

SIGNAL ACTIVITY:
  Primary strategy: {signals_text}
  Shadow strategy: {shadow_text}

Provide a strategic weekly review covering:
1. Overall performance: win rate, RR quality, patterns in outcomes
2. Setup quality: were high-score setups more profitable? Any low-score trades that worked (luck)?
3. Strategy divergence: did primary and shadow strategies agree or disagree? What does that suggest?
4. Risk management: were drawdown limits ever near? Any rule violations?
5. Recommendations: 1-2 concrete, specific parameter or behaviour changes to consider next week (or "no changes recommended" if performance was solid)

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
